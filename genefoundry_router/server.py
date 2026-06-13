"""Assemble the genefoundry FastMCP server and its FastAPI host."""

from __future__ import annotations

from typing import Any

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastmcp import FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.config import RouterSettings
from genefoundry_router.observability import configure_logging, register_health
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
    server: FastMCP = FastMCP("genefoundry")
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
    """Build the FastAPI host: /health + Origin validation + mounted MCP app.

    NOTE (extended in later tasks): Task 17 adds /metrics + MetricsMiddleware; Task 23
    replaces the bare ``lifespan=mcp_app.lifespan`` with a composed lifespan that also
    runs async normalization (Task 15) and starts/stops the polling refresher (Task 22).
    """
    configure_logging(settings.GF_LOG_LEVEL)
    # enable_search=False: the composed lifespan (Task 23) applies tool-search AFTER
    # normalization so the BM25 index reflects final names/tags.
    server = build_server(settings, registry, proxy_targets=proxy_targets, enable_search=False)
    mcp_app = server.http_app(path="/")  # ASGI sub-app; lifespan must be forwarded
    app = FastAPI(title="GeneFoundry Router", lifespan=mcp_app.lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)  # R1.4 — MCP Origin MUST
    register_health(app, registry)
    app.mount(settings.GF_MCP_PATH, mcp_app)
    return app
