"""Assemble the genefoundry FastMCP server and its FastAPI host."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastmcp import FastMCP

from genefoundry_router.auth import build_auth
from genefoundry_router.composition import register_backend
from genefoundry_router.config import RouterSettings
from genefoundry_router.discovery import PollingRefresher
from genefoundry_router.hints import NamespaceHintMiddleware
from genefoundry_router.instructions import build_instructions
from genefoundry_router.normalization import apply_normalizations
from genefoundry_router.observability import (
    MetricsMiddleware,
    configure_logging,
    register_health,
    register_metrics,
    set_backend_up,
)
from genefoundry_router.registry import BackendDef
from genefoundry_router.security import add_origin_validation
from genefoundry_router.tool_search import apply_tool_search

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
    server: FastMCP = FastMCP("genefoundry", auth=auth, instructions=build_instructions(registry))
    server.add_middleware(MetricsMiddleware())  # R1.7 — before transforms so all calls count
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
        register_backend(server, backend, proxy_target=target)
    if enable_search:
        apply_tool_search(server, settings)
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
    mcp_app = server.http_app(path="/")  # ASGI sub-app; its lifespan must be entered

    async def _relist() -> None:
        await apply_normalizations(server, registry)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        async with mcp_app.lifespan(_app):
            await apply_normalizations(server, registry)  # R1.2 — async, after mount
            apply_tool_search(server, settings)  # ordering: after normalization
            targets = proxy_targets or {}
            for b in registry:  # seed /health reachability
                if b.enabled:
                    set_backend_up(b, up=targets.get(b.name) is not None or b.url is not None)
            refresher = PollingRefresher(settings.GF_POLL_INTERVAL, _relist)
            await refresher.start()
            try:
                yield
            finally:
                await refresher.stop()

    app = FastAPI(title="GeneFoundry Router", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)  # R1.4 — MCP Origin MUST
    register_health(app, registry)
    register_metrics(app)  # R1.7 — /metrics
    # R1.5 — serve the auth provider's well-known routes (Protected-Resource-Metadata,
    # RFC 9728) on the OUTER app at root, matching the resource_metadata URL advertised
    # in WWW-Authenticate. The MCP app is sub-mounted at GF_MCP_PATH, so its own routes
    # would otherwise land under that prefix and miss the root well-known path.
    auth_provider = server.auth
    if auth_provider is not None:
        app.router.routes.extend(auth_provider.get_routes())
    app.mount(settings.GF_MCP_PATH, mcp_app)
    return app
