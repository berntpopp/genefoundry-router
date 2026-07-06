"""Assemble the genefoundry FastMCP server and its FastAPI host."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastmcp import FastMCP

from genefoundry_router import __version__
from genefoundry_router.auth import build_auth
from genefoundry_router.composition import register_backend
from genefoundry_router.config import RouterSettings
from genefoundry_router.discovery import PollingRefresher
from genefoundry_router.hints import NamespaceHintMiddleware
from genefoundry_router.instructions import build_instructions
from genefoundry_router.limits import add_request_limits
from genefoundry_router.normalization import apply_normalizations
from genefoundry_router.observability import (
    AuditLogMiddleware,
    MetricsMiddleware,
    configure_logging,
    namespace_tool_counts,
    register_health,
    register_metrics,
    set_backend_up,
)
from genefoundry_router.registry import BackendDef
from genefoundry_router.security import add_origin_validation
from genefoundry_router.tool_search import apply_tool_search, resolve_entrypoints

log = structlog.get_logger(__name__)


def build_server(
    settings: RouterSettings,
    registry: list[BackendDef],
    proxy_targets: dict[str, Any] | None = None,
    enable_search: bool = True,
) -> FastMCP:
    """Build the genefoundry FastMCP server from a resolved registry.

    Disabled backends and backends with no resolved URL are skipped with a warning.
    ``proxy_targets`` maps a backend name to an in-process target (tests only).
    ``enable_search`` applies the BM25 tool-search surface after mounting; the app
    path sets it False so the composed lifespan (Task 23) can order normalization
    before search.
    """
    proxy_targets = proxy_targets or {}
    auth = build_auth(settings)  # caller auth at the edge; never forwarded upstream (R1.6)
    # instructions: orient the host's model on the two-layer search surface so a
    # capability absent from the top-level listing isn't read as missing (issue #3).
    server: FastMCP = FastMCP(
        "genefoundry", version=__version__, auth=auth, instructions=build_instructions(registry)
    )
    server.add_middleware(MetricsMiddleware())  # R1.7 — before transforms so all calls count
    server.add_middleware(AuditLogMiddleware())  # PII-safe per-call audit (GDPR Art. 30/32)
    if settings.GF_REWRITE_HINTS:
        # Finding 1 — namespace bare tool references embedded in backend responses so the
        # fleet's self-healing hints resolve through call_tool. Enabled backends only.
        namespaces = {b.namespace for b in registry if b.enabled}
        server.add_middleware(NamespaceHintMiddleware(namespaces))
    for backend in registry:
        if not backend.enabled:
            log.info("backend_skipped", backend=backend.name, reason="disabled")
            continue
        target = proxy_targets.get(backend.name)
        if target is None and backend.url is None:
            log.warning("backend_skipped", backend=backend.name, reason="missing_url")
            continue
        register_backend(server, backend, proxy_target=target, timeout=settings.GF_BACKEND_TIMEOUT)
    if enable_search:
        apply_tool_search(server, settings, always_visible=resolve_entrypoints(registry))
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
    # enable_search=False: the composed lifespan applies tool-search AFTER normalization
    # so the BM25 index reflects final names/tags.
    server = build_server(settings, registry, proxy_targets=proxy_targets, enable_search=False)
    mcp_app = server.http_app(  # ASGI sub-app; its lifespan must be entered
        path=settings.GF_MCP_PATH,
        stateless_http=True,
        json_response=True,
    )

    async def _relist() -> None:
        await apply_normalizations(server, registry)
        await _seed_reachability(server, registry)  # refresh /health from the fresh harvest

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        async with mcp_app.lifespan(_app):
            await apply_normalizations(server, registry)  # R1.2 — async, after mount
            apply_tool_search(  # ordering: after normalization
                server, settings, always_visible=resolve_entrypoints(registry)
            )
            await _seed_reachability(server, registry)  # /health from live harvest, not config
            refresher = PollingRefresher(settings.GF_POLL_INTERVAL, _relist)
            await refresher.start()
            try:
                yield
            finally:
                await refresher.stop()

    app = FastAPI(title="GeneFoundry Router", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)  # R1.4 — MCP Origin MUST
    add_request_limits(
        app,
        settings.GF_MAX_BODY_BYTES,
        settings.GF_RATE_LIMIT_RPM,
        trusted_proxy_hops=settings.GF_TRUSTED_PROXY_HOPS,
    )  # DoS guard
    register_health(app, registry)
    register_metrics(app, token=settings.GF_METRICS_TOKEN)  # R1.7 — /metrics
    # R1.5 — serve the auth provider's well-known routes (Protected-Resource-Metadata,
    # RFC 9728) on the OUTER app at root, matching the resource_metadata URL advertised
    # in WWW-Authenticate. The MCP app is sub-mounted at GF_MCP_PATH, so its own routes
    # would otherwise land under that prefix and miss the root well-known path.
    auth_provider = server.auth
    if auth_provider is not None:
        app.router.routes.extend(auth_provider.get_routes())
    # Root mount: baked GF_MCP_PATH owns /mcp; /health and /metrics registered first.
    app.mount("/", mcp_app)
    return app


async def _seed_reachability(server: FastMCP, registry: list[BackendDef]) -> None:
    """Seed /health reachability from the LIVE tool harvest — not from config.

    A backend that contributed >=1 namespaced tool is up; one that harvested 0 tools
    (down, 307-redirecting `/mcp`, TLS-broken, or otherwise transport-non-conformant) is
    marked down and logged at ERROR. This is the fix for the class of failure where a
    registered backend is advertised in the instructions/pins yet serves nothing: the
    reachability signal now matches the surface the model can actually reach. A total
    enumeration failure leaves prior state untouched rather than false-alarming all
    backends. Called at startup and on every polling relist.
    """
    # NOTE: the public `server.list_tools()` is filtered down to the pins + search_tools/
    # call_tool once the BM25 search transform is applied, so it cannot see per-backend
    # tools. `server._list_tools()` returns the full, unfiltered federated catalog both
    # before and after the transform — the same set the search index is built over — which
    # is what reachability must be measured against. Guarded by a test so a FastMCP API
    # change surfaces loudly rather than silently zeroing every backend.
    try:
        present = [t.name for t in await server._list_tools()]  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - defensive; a mounted proxy normally degrades solo
        log.warning("reachability_probe_failed", error=str(exc))
        return
    counts = namespace_tool_counts(present)
    for backend in registry:
        if not backend.enabled:
            continue
        n = counts.get(backend.namespace, 0)
        set_backend_up(backend, up=n > 0, tools=n)
        if n == 0:
            log.error(
                "backend_unreachable",
                backend=backend.name,
                namespace=backend.namespace,
                detail=(
                    "0 tools harvested — backend down or transport non-conformant "
                    "(e.g. 307-redirecting /mcp); advertised but unusable. Run `make fleet-probe`."
                ),
            )
