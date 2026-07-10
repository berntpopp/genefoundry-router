"""Assemble the genefoundry FastMCP server and its FastAPI host."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any, Literal

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ToolTransformConfig

from genefoundry_router import __version__
from genefoundry_router.auth import build_auth
from genefoundry_router.authorization import WriteAuthorizationMiddleware
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
from genefoundry_router.runtime_drift import (
    definitions_from_tools,
    fingerprint_definitions,
    load_runtime_guard,
)
from genefoundry_router.security import add_host_origin_validation
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
    server.add_middleware(WriteAuthorizationMiddleware())
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
    guard = load_runtime_guard(settings)
    applied_quarantine: set[str] = set()
    mcp_app = server.http_app(  # ASGI sub-app; its lifespan must be entered
        path=settings.GF_MCP_PATH,
        stateless_http=True,
        json_response=True,
        host_origin_protection=False,
    )

    async def _refresh_catalog(phase: Literal["startup", "poll"]) -> None:
        tools = await apply_normalizations(server, registry)
        unreachable = _seed_reachability(registry, tools)
        guard.evaluate(
            fingerprint_definitions(definitions_from_tools(tools)),
            phase=phase,
            unreachable=unreachable,
        )
        newly_quarantined = set(guard.quarantined) - applied_quarantine
        if newly_quarantined:
            server.add_transform(
                ToolTransform(
                    {name: ToolTransformConfig(enabled=False) for name in sorted(newly_quarantined)}
                )
            )
            applied_quarantine.update(newly_quarantined)

    async def _relist() -> None:
        await _refresh_catalog("poll")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        async with mcp_app.lifespan(_app):
            await _refresh_catalog("startup")
            apply_tool_search(  # ordering: after normalization
                server, settings, always_visible=resolve_entrypoints(registry)
            )
            refresher = PollingRefresher(settings.GF_POLL_INTERVAL, _relist)
            await refresher.start()
            try:
                yield
            finally:
                await refresher.stop()

    app = FastAPI(title="GeneFoundry Router", lifespan=lifespan)
    app.state.mcp_server = server
    app.state.runtime_drift_guard = guard
    app.state.refresh_catalog = _refresh_catalog
    # Correlation-id added LAST so it is the OUTERMOST middleware (Starlette wraps the
    # last-added first): every short-circuit rejection below (403 origin / 413 body /
    # 429 rate) is then produced inside the correlation context and carries X-Request-ID.
    add_request_limits(
        app,
        settings.GF_MAX_BODY_BYTES,
        settings.GF_RATE_LIMIT_RPM,
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
            log.error(
                "backend_unreachable",
                backend=backend.name,
                namespace=backend.namespace,
                detail=(
                    "0 tools harvested — backend down or transport non-conformant "
                    "(e.g. 307-redirecting /mcp); advertised but unusable. Run `make fleet-probe`."
                ),
            )
    return unreachable
