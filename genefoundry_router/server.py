"""Assemble the genefoundry FastMCP server and its FastAPI host."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request
from fastmcp import FastMCP
from fastmcp.skills.provider import SkillsDirectoryProvider
from fastmcp.tools import Tool
from starlette.responses import JSONResponse

from genefoundry_router import __version__, community_guard
from genefoundry_router.argument_guard import ArgumentGuard
from genefoundry_router.auth import build_auth, resolve_refresh_observability_hmac_key
from genefoundry_router.authorization import WriteAuthorizationMiddleware
from genefoundry_router.cache import ToolResponseCache, ToolResponseCacheMiddleware
from genefoundry_router.composition import (
    close_shared_upstream_transport,
    harvest_settling,
    register_backend,
)
from genefoundry_router.config import RouterSettings
from genefoundry_router.discovery import PollingRefresher
from genefoundry_router.exceptions import ConfigurationError
from genefoundry_router.hints import NamespaceHintMiddleware
from genefoundry_router.instructions import PinnedModeInstructions, build_instructions
from genefoundry_router.limits import (
    account_rate_limit_middleware,
    add_request_limits,
    ip_rate_limit_rpm,
)
from genefoundry_router.normalization import apply_normalizations
from genefoundry_router.notfound_guard import NotFoundGuard, install_protocol_error_handler
from genefoundry_router.notfound_log_scrub import install_notfound_log_filter
from genefoundry_router.observability import (
    BACKEND_STATUS,
    AuditLogMiddleware,
    MetricsMiddleware,
    _metrics_authorized,
    configure_logging,
    namespace_tool_counts,
    register_health,
    register_metrics,
    restore_refresh_metrics,
    set_backend_up,
)
from genefoundry_router.provenance import register_provenance
from genefoundry_router.refresh_models import REFRESH_HEARTBEAT_INTERVAL_SECONDS
from genefoundry_router.refresh_observability import RefreshLedger
from genefoundry_router.registry import BackendDef
from genefoundry_router.runtime_drift import (
    definitions_from_tools,
    fingerprint_definitions,
    load_runtime_guard,
)
from genefoundry_router.schema_slim import SlimParamsMiddleware
from genefoundry_router.schema_strict import StrictNullMiddleware
from genefoundry_router.search_rerank import SystemOneReranker
from genefoundry_router.security import add_host_origin_validation, wrap_mcp_cors
from genefoundry_router.skill_search import skill_tools
from genefoundry_router.tool_search import (
    apply_tool_search,
    listed_pins,
    pin_policy,
    resolve_entrypoints,
    slim_keep,
)

log = structlog.get_logger(__name__)

# Re-harvest a startup that found backends down (they boot alongside the router, #325):
# ~60 s of backoff before a still-missing backend is logged at ERROR.
STARTUP_HARVEST_RETRY_DELAYS: tuple[float, ...] = (5.0, 10.0, 15.0, 30.0)


async def _run_refresh_heartbeat(ledger: RefreshLedger) -> None:
    """Keep durable writer freshness current independently of refresh volume."""
    try:
        while True:
            await asyncio.sleep(REFRESH_HEARTBEAT_INTERVAL_SECONDS)
            worker = asyncio.create_task(asyncio.to_thread(ledger.heartbeat, time.time()))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    await worker
                except Exception as exc:
                    log.error(
                        "refresh_observability_heartbeat_failed",
                        error_type=type(exc).__name__,
                    )
                return
            except Exception as exc:
                log.error(
                    "refresh_observability_heartbeat_failed",
                    error_type=type(exc).__name__,
                )
    except asyncio.CancelledError:
        return


def build_server(
    settings: RouterSettings,
    registry: list[BackendDef],
    proxy_targets: dict[str, Any] | None = None,
    enable_search: bool = True,
    refresh_ledger: RefreshLedger | None = None,
) -> FastMCP:
    """Build the genefoundry FastMCP server from a resolved registry.

    Disabled backends and backends with no resolved URL are skipped with a warning.
    ``proxy_targets`` maps a backend name to an in-process target (tests only).
    ``enable_search`` applies the BM25 tool-search surface after mounting; the app
    path sets it False so the composed lifespan (Task 23) can order normalization
    before search.
    """
    proxy_targets = proxy_targets or {}
    community = [b.name for b in registry if b.trust == "community"]
    if community and not (
        settings.GF_COMMUNITY_SERVERS_FILE and settings.GF_ALLOW_COMMUNITY_BACKENDS
    ):
        # Federation Standard v1 §2: the core endpoint never serves community backends.
        raise ConfigurationError(f"community backends {community} on a non-community instance")
    if community:
        community_guard.configure(settings)
    # Keep the historical one-argument call when observability is disabled so existing
    # auth factories and tests remain compatible.
    auth = (
        build_auth(settings, refresh_ledger=refresh_ledger)
        if refresh_ledger is not None
        else build_auth(settings)
    )  # caller auth at the edge; never forwarded upstream (R1.6)
    skills_dir = Path(settings.GF_SKILLS_DIR)
    if not skills_dir.is_dir():  # the provider would silently serve zero skills
        raise ConfigurationError(f"GF_SKILLS_DIR {str(skills_dir)!r} is not a directory")
    skills = skill_tools(skills_dir)
    # instructions: orient the host's model on the two-layer search surface so a
    # capability absent from the top-level listing isn't read as missing (issue #3).
    server: FastMCP = FastMCP(
        "genefoundry",
        version=__version__,
        auth=auth,
        instructions=build_instructions(registry, listed_pins(registry, settings), skills),
    )
    server.add_middleware(WriteAuthorizationMiddleware())
    server.add_middleware(MetricsMiddleware())  # R1.7 — before transforms so all calls count
    server.add_middleware(AuditLogMiddleware())  # PII-safe per-call audit (GDPR Art. 30/32)
    # FastMCP-core not-found reflection guard (Response-Envelope v1.1 fast-follow).
    # Layer 1 (tool-name preflight) + Layer 2 (resource-read boundary). Registered before
    # ToolResponseCacheMiddleware so quarantined tools cannot be served from cache.
    notfound_guard = NotFoundGuard(BACKEND_STATUS)
    policy = pin_policy(registry, settings)
    server.add_middleware(
        PinnedModeInstructions(registry, policy, lambda: notfound_guard.unavailable, skills)
    )
    server.add_middleware(StrictNullMiddleware())  # #293: before guard + cache (stable keys)
    server.add_middleware(SlimParamsMiddleware(policy, settings.GF_SCHEMA_MODE))  # outside cache
    server.add_middleware(notfound_guard)
    server.notfound_guard = notfound_guard  # type: ignore[attr-defined]
    server.add_middleware(ArgumentGuard())  # #158: after the name guard (unknown -> not_found)
    tool_cache = ToolResponseCache(registry)
    server.add_middleware(ToolResponseCacheMiddleware(tool_cache, settings.GF_PINNED_MODE))
    server.tool_cache = tool_cache  # type: ignore[attr-defined]
    if settings.GF_REWRITE_HINTS:
        # Finding 1 — namespace bare tool references embedded in backend responses so the
        # fleet's self-healing hints resolve through call_tool. Registered innermost so
        # hint rewrites are cached (issue #230). Enabled backends only.
        namespaces = {b.namespace for b in registry if b.enabled}
        # Call form follows the request's X-GF-Pinned-Mode (#320); the cache keys on it (#230).
        server.add_middleware(NamespaceHintMiddleware(namespaces, policy.listed))
    for backend in registry:
        if not backend.enabled:
            log.info("backend_skipped", backend=backend.name, reason="disabled")
            continue
        target = proxy_targets.get(backend.name)
        if target is None and backend.url is None:
            log.warning("backend_skipped", backend=backend.name, reason="missing_url")
            continue
        register_backend(server, backend, proxy_target=target, timeout=settings.GF_BACKEND_TIMEOUT)
    server.add_provider(SkillsDirectoryProvider(skills_dir))
    if enable_search:
        apply_tool_search(
            server,
            settings,
            always_visible=resolve_entrypoints(registry),
            slim_keep=slim_keep(registry),
        )
    # Layer 3 (protocol-handler backstop) + Layer 5 (log-scrub filter) of the not-found
    # guard. Installed here — after every proxy mount — so the CallTool/ReadResource/
    # GetPrompt handlers exist. The low-level request-handler callables are stable across
    # the later `add_transform` (tool-search), so this wrapper survives the app path where
    # search is applied in the lifespan. The log filter attaches to FastMCP/MCP source
    # loggers (incl. the non-propagating Rich handler) so caller input never reaches a sink.
    install_protocol_error_handler(server)
    install_notfound_log_filter()
    return server


def build_app(
    settings: RouterSettings,
    registry: list[BackendDef],
    proxy_targets: dict[str, Any] | None = None,
) -> FastAPI:
    """Build the FastAPI host with a composed lifespan.

    On startup (inside the MCP app's own lifespan so the session manager initializes):
    run async normalization (R1.2) -> apply tool-search after it (ordering, Task 16) ->
    seed /health reachability -> start the polling refresher (R1.7). On shutdown: stop it.
    """
    configure_logging(settings.GF_LOG_LEVEL)
    community = [b for b in registry if b.trust == "community"]
    if any(b.cache_ttl != settings.GF_POLL_INTERVAL for b in community):
        # Federation v1 §4: every poll re-harvests exactly when the tools/list cache expires.
        raise ConfigurationError("community backends require GF_POLL_INTERVAL == cache_ttl")
    refresh_ledger = None
    if settings.GF_AUTH_MODE == "oauth" and settings.GF_REFRESH_OBSERVABILITY_DB:
        refresh_ledger = RefreshLedger(
            settings.GF_REFRESH_OBSERVABILITY_DB,
            hmac_key=resolve_refresh_observability_hmac_key(settings),
        )
    # enable_search=False: the composed lifespan applies tool-search AFTER normalization
    # so the BM25 index reflects final names/tags.
    try:
        server = build_server(
            settings,
            registry,
            proxy_targets=proxy_targets,
            enable_search=False,
            refresh_ledger=refresh_ledger,
        )
    except BaseException:
        if refresh_ledger is not None:
            refresh_ledger.close()
        raise
    guard = load_runtime_guard(settings)
    guard.bind_community(
        community,
        pins_dir=settings.GF_COMMUNITY_PINS_DIR,
        kill_switch_file=settings.GF_KILL_SWITCH_FILE,
        state_file=settings.GF_QUARANTINE_FILE,
    )
    server.add_transform(guard)
    skills = skill_tools(Path(settings.GF_SKILLS_DIR))  # build_server checked the dir
    mcp_app = server.http_app(  # ASGI sub-app; its lifespan must be entered
        path=settings.GF_MCP_PATH,
        stateless_http=True,
        json_response=True,
        host_origin_protection=False,
        # Appended AFTER FastMCP's auth middleware, so it sees the verified token only.
        middleware=account_rate_limit_middleware(settings),
    )

    async def _refresh_catalog(
        phase: Literal["startup", "poll"], *, settling: bool = False
    ) -> set[str]:
        token = harvest_settling.set(settling)
        try:
            tools = await apply_normalizations(server, registry)
            unreachable = _seed_reachability(registry, tools)
        finally:
            harvest_settling.reset(token)
        # Advertise only what the live catalog serves (issue #256): instructions/pins named
        # every configured backend even when it harvested 0 tools, so agents called dead pins.
        live = [b for b in registry if b.namespace not in unreachable]
        server.instructions = build_instructions(live, listed_pins(registry, settings), skills)
        if (notfound := getattr(server, "notfound_guard", None)) is not None:
            notfound.unavailable = frozenset(unreachable)
            notfound.catalog = frozenset(t.name for t in tools)
        guard.evaluate(
            fingerprint_definitions(definitions_from_tools(tools)),
            phase=phase,
            unreachable=unreachable,
        )
        return unreachable

    async def _relist() -> None:
        await _refresh_catalog("poll")

    async def _settle_startup_harvest() -> None:
        last = len(STARTUP_HARVEST_RETRY_DELAYS) - 1
        for attempt, delay in enumerate(STARTUP_HARVEST_RETRY_DELAYS):
            await asyncio.sleep(delay)
            try:
                if not await _refresh_catalog("poll", settling=attempt < last):
                    return
            except Exception as exc:  # a retry must never take the router down
                log.warning("startup_harvest_retry_failed", error_type=type(exc).__name__)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        boot_id = None
        teardown_complete = False
        try:
            async with mcp_app.lifespan(_app):
                down = await _refresh_catalog("startup", settling=True)
                search = apply_tool_search(  # ordering: after normalization
                    server,
                    settings,
                    always_visible=resolve_entrypoints(registry),
                    slim_keep=slim_keep(registry),
                )
                refresher = PollingRefresher(settings.GF_POLL_INTERVAL, _relist)
                await refresher.start()
                if refresh_ledger is not None:
                    restore_refresh_metrics(
                        refresh_ledger.counter_snapshot(),
                        source_id=str(refresh_ledger.path.resolve()),
                    )
                    boot_id = refresh_ledger.record_startup(version=__version__, at=time.time())
                    heartbeat_task = asyncio.create_task(_run_refresh_heartbeat(refresh_ledger))
                else:
                    heartbeat_task = None
                settle = asyncio.create_task(_settle_startup_harvest()) if down else None
                try:
                    yield
                finally:
                    if settle is not None:
                        settle.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await settle
                    if heartbeat_task is not None:
                        heartbeat_task.cancel()
                        await heartbeat_task
                    await refresher.stop()
                    if isinstance(search.reranker, SystemOneReranker):
                        await search.reranker.aclose()
                    await close_shared_upstream_transport()
            teardown_complete = True
        finally:
            if refresh_ledger is not None:
                if teardown_complete and boot_id is not None:
                    try:
                        refresh_ledger.record_shutdown(
                            boot_id,
                            version=__version__,
                            at=time.time(),
                        )
                    except Exception as exc:
                        log.error(
                            "refresh_observability_shutdown_marker_failed",
                            error_type=type(exc).__name__,
                        )
                try:
                    refresh_ledger.close()
                except Exception as exc:
                    log.error(
                        "refresh_observability_close_failed",
                        error_type=type(exc).__name__,
                    )

    app = FastAPI(title="GeneFoundry Router", lifespan=lifespan)
    app.state.mcp_server = server
    app.state.runtime_drift_guard = guard
    app.state.refresh_catalog = _refresh_catalog
    app.state.refresh_ledger = refresh_ledger
    app.state.tool_cache = getattr(server, "tool_cache", None)

    def _verify_cache_admin_auth(request: Request) -> bool:
        if settings.GF_METRICS_TOKEN is None:
            return False
        return _metrics_authorized(request.headers.get("authorization"), settings.GF_METRICS_TOKEN)

    @app.get(settings.GF_MCP_PATH)
    async def mcp_discovery(request: Request) -> JSONResponse:
        origin = request.headers.get("origin")
        headers: dict[str, str] = {}
        if origin and origin in settings.GF_ALLOWED_ORIGINS:
            headers["access-control-allow-origin"] = origin
            headers["vary"] = "Origin"

        accept = request.headers.get("accept", "")
        # MCP Streamable HTTP transport: if client strictly requests SSE event-stream, answer 405
        if (
            "text/event-stream" in accept
            and "application/json" not in accept
            and "*/*" not in accept
        ):
            return JSONResponse(
                {"error": "SSE transport is not offered; use Streamable HTTP POST /mcp"},
                status_code=405,
                headers={**headers, "allow": "POST"},
            )

        endpoints: dict[str, str] = {
            "mcp": settings.GF_MCP_PATH,
            "health": "/health",
            "metrics": "/metrics",
            "provenance": "/provenance",
        }
        if settings.GF_AUTH_MODE == "oauth":
            endpoints["oauth_protected_resource"] = (
                f"/.well-known/oauth-protected-resource{settings.GF_MCP_PATH}"
            )

        payload: dict[str, Any] = {
            "name": "genefoundry",
            "version": __version__,
            "protocol_version": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "endpoints": endpoints,
            "docs": "https://github.com/berntpopp/genefoundry-router",
        }
        return JSONResponse(payload, headers=headers)

    @app.post("/api/cache/invalidate")
    async def invalidate_cache(
        request: Request,
        key: str | None = None,
        tool: str | None = None,
        namespace: str | None = None,
    ) -> JSONResponse:
        if not _verify_cache_admin_auth(request):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        cache: ToolResponseCache | None = getattr(app.state, "tool_cache", None)
        if cache is None:
            return JSONResponse(
                {"status": "error", "message": "cache not configured"}, status_code=500
            )

        target_key = key
        target_tool = tool
        target_namespace = namespace
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    target_key = target_key or body.get("key")
                    target_tool = target_tool or body.get("tool") or body.get("tool_name")
                    target_namespace = target_namespace or body.get("namespace")
            except Exception as exc:
                log.debug("cache_invalidate_body_parse_failed", error=str(exc))

        invalidated = cache.invalidate(
            key=target_key, tool_name=target_tool, namespace=target_namespace
        )
        return JSONResponse({"status": "ok", "invalidated": invalidated, "stats": cache.stats})

    @app.post("/api/cache/clear")
    async def clear_cache(request: Request) -> JSONResponse:
        if not _verify_cache_admin_auth(request):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        cache: ToolResponseCache | None = getattr(app.state, "tool_cache", None)
        if cache is None:
            return JSONResponse(
                {"status": "error", "message": "cache not configured"}, status_code=500
            )
        cleared = cache.clear()
        return JSONResponse({"status": "ok", "cleared": cleared, "stats": cache.stats})

    # Correlation-id added LAST so it is the OUTERMOST middleware (Starlette wraps the
    # last-added first): every short-circuit rejection below (403 origin / 413 body /
    # 429 rate) is then produced inside the correlation context and carries X-Request-ID.
    add_request_limits(
        app,
        settings.GF_MAX_BODY_BYTES,
        ip_rate_limit_rpm(settings),  # pre-auth per-IP DoS floor
        trusted_proxy_hops=settings.GF_TRUSTED_PROXY_HOPS,
    )  # DoS guard
    add_host_origin_validation(
        app,
        allowed_hosts=settings.GF_ALLOWED_HOSTS,
        allowed_origins=settings.GF_ALLOWED_ORIGINS,
    )
    app.add_middleware(CorrelationIdMiddleware)
    register_health(app, registry, drift_guard=guard)
    register_metrics(app, token=settings.GF_METRICS_TOKEN)  # R1.7 — /metrics
    register_provenance(app)  # GET /provenance and GET /api/fleet/provenance
    # R1.5 — serve the auth provider's well-known routes (Protected-Resource-Metadata,
    # RFC 9728) on the OUTER app at root, matching the resource_metadata URL advertised
    # in WWW-Authenticate. The MCP app is sub-mounted at GF_MCP_PATH, so its own routes
    # would otherwise land under that prefix and miss the root well-known path.
    auth_provider = server.auth
    if auth_provider is not None and settings.GF_AUTH_MODE != "oauth":
        app.router.routes.extend(auth_provider.get_routes())
    # Root mount: baked GF_MCP_PATH owns /mcp; /health and /metrics registered first.
    # The CORS wrapper is applied to the MOUNTED app only (issue #162), so /mcp answers a
    # browser preflight while the OAuth routes above keep the MCP SDK's own cors_middleware
    # as their single ACAO source. It sits INSIDE the Host/Origin guard, which is unchanged.
    app.mount("/", wrap_mcp_cors(mcp_app, settings.GF_ALLOWED_ORIGINS, settings.GF_MCP_PATH))
    return app


def _seed_reachability(registry: list[BackendDef], tools: Sequence[Tool]) -> set[str]:
    """Seed /health reachability from the LIVE tool harvest — not from config.

    A backend that contributed >=1 namespaced tool is up; one that harvested 0 tools
    (down, 307-redirecting `/mcp`, TLS-broken, or otherwise transport-non-conformant) is
    marked down and logged at ERROR. This is the fix for the class of failure where a
    registered backend is advertised in the instructions/pins yet serves nothing: the
    reachability signal now matches the surface the model can actually reach. Called at
    startup and on every polling relist.
    """
    counts = namespace_tool_counts([tool.name for tool in tools])
    unreachable: set[str] = set()
    for backend in registry:
        if not backend.enabled:
            continue
        n = counts.get(backend.namespace, 0)
        set_backend_up(backend, up=n > 0, tools=n)
        if n == 0:
            unreachable.add(backend.namespace)
            (log.warning if harvest_settling.get() else log.error)(
                "backend_unreachable",
                backend=backend.name,
                namespace=backend.namespace,
                detail=(
                    "0 tools harvested — backend down or transport non-conformant "
                    "(e.g. 307-redirecting /mcp); advertised but unusable. Run `make fleet-probe`."
                ),
            )
    return unreachable
