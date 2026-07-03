"""Logging, health, and metrics for the router."""

from __future__ import annotations

import logging
import time

import structlog
from fastapi import FastAPI
from fastmcp.server.middleware import Middleware, MiddlewareContext
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

from genefoundry_router.registry import BackendDef

_LOG_CONFIGURED = False

# Dedicated audit logger. request_id is merged automatically from contextvars
# (configure_logging adds merge_contextvars; asgi-correlation-id binds it per request).
audit_log = structlog.get_logger("genefoundry.audit")

# --- Prometheus metrics (R1.7: counters are incremented by MetricsMiddleware below) ---
METRICS_REGISTRY = CollectorRegistry()

BACKEND_UP = Gauge(
    "genefoundry_backend_up",
    "Backend reachability (1=up, 0=down)",
    ["backend"],
    registry=METRICS_REGISTRY,
)
TOOL_CALLS = Counter(
    "genefoundry_tool_calls_total",
    "Federated tool-call count",
    ["namespace"],
    registry=METRICS_REGISTRY,
)
SEARCH_HITS = Counter(
    "genefoundry_search_hits_total",
    "search_tools invocations",
    registry=METRICS_REGISTRY,
)
TOOL_LATENCY = Histogram(
    "genefoundry_tool_latency_seconds",
    "Federated tool-call latency",
    ["namespace"],
    registry=METRICS_REGISTRY,
)

# Cached reachability for /health, keyed by namespace. Seeded from the live tool
# harvest at startup and refreshed by the polling relist (see server._seed_reachability):
# a backend is "up" iff it contributed >=1 tool to the federated surface. NOT a mere
# config echo — a registered-but-unreachable backend (down, 307-redirecting, TLS-broken)
# harvests 0 tools and MUST read as down here so /health can never be falsely green.
BACKEND_STATUS: dict[str, bool] = {}
# Per-namespace count of tools actually harvested from each backend (0 == unreachable).
BACKEND_TOOL_COUNT: dict[str, int] = {}


def namespace_tool_counts(tool_names: list[str]) -> dict[str, int]:
    """Count harvested tools per backend namespace (the ``<namespace>_<leaf>`` prefix).

    Root tools (``search_tools``/``call_tool``) split to non-namespace keys that no
    backend claims, so they are harmless — callers look up counts by known namespace.
    """
    counts: dict[str, int] = {}
    for name in tool_names:
        if "_" in name:
            ns = name.split("_", 1)[0]
            counts[ns] = counts.get(ns, 0) + 1
    return counts


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON to stdout. Safe to call repeatedly."""
    global _LOG_CONFIGURED
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _LOG_CONFIGURED = True


def set_backend_up(backend: BackendDef, up: bool, tools: int | None = None) -> None:
    """Record a backend's reachability for /metrics (gauge) and /health (cached map).

    ``tools`` is the number of tools harvested from the backend; when provided it is
    cached for the /health per-namespace tool-count summary.
    """
    BACKEND_UP.labels(backend=backend.name).set(1 if up else 0)
    BACKEND_STATUS[backend.namespace] = up
    if tools is not None:
        BACKEND_TOOL_COUNT[backend.namespace] = tools


def register_metrics(app: FastAPI) -> None:
    """Attach GET /metrics exposing the Prometheus text exposition format."""

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)


def register_health(app: FastAPI, backends: list[BackendDef]) -> None:
    """Attach GET /health returning liveness + a per-backend summary."""
    enabled = [b for b in backends if b.enabled]

    @app.get("/health")
    async def health() -> dict[str, object]:
        # Degraded = an enabled backend the router probed and found down (reachable is
        # explicitly False). Unknown (None, not yet probed) is not counted as degraded.
        degraded = sorted(b.namespace for b in enabled if BACKEND_STATUS.get(b.namespace) is False)
        return {
            "status": "degraded" if degraded else "healthy",
            "service": "genefoundry",
            "backends": {
                "total": len(backends),
                "enabled": len(enabled),
                "namespaces": [b.namespace for b in enabled],
                "reachable": {b.namespace: BACKEND_STATUS.get(b.namespace) for b in enabled},
                "tools": {b.namespace: BACKEND_TOOL_COUNT.get(b.namespace, 0) for b in enabled},
                "degraded": degraded,
            },
        }


class AuditLogMiddleware(Middleware):
    """Emit a PII-safe audit record per tool call (GDPR Art. 30/32 accountability).

    Logs the tool, namespace, outcome, and elapsed time — plus the request/correlation
    id (merged from contextvars) and, when authenticated, the caller. It deliberately
    NEVER logs tool arguments, results, or exception messages, which can carry
    patient-derived data (variant coordinates, phenotype text). Data minimisation by design.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        name = getattr(context.message, "name", "") or ""
        namespace = name.split("_", 1)[0] if "_" in name else "_root"
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            audit_log.info(
                "tool_call",
                tool=name,
                namespace=namespace,
                outcome="error",
                error_type=type(exc).__name__,  # class only — never the message (may hold PII)
                elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            raise
        audit_log.info(
            "tool_call",
            tool=name,
            namespace=namespace,
            outcome="ok",
            elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return result


class MetricsMiddleware(Middleware):
    """Increment tool-call/search counters + latency (R1.7 — counters were dead).

    on_call_tool/on_list_tools hooks verified against fastmcp 3.4.2; ``context.message``
    is a request-params dataclass whose ``name`` is the invoked tool.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        name = getattr(context.message, "name", "") or ""
        namespace = name.split("_", 1)[0] if "_" in name else "_root"
        if name in ("search_tools", "call_tool"):
            SEARCH_HITS.inc()
        start = time.perf_counter()
        try:
            return await call_next(context)
        finally:
            TOOL_CALLS.labels(namespace=namespace).inc()
            TOOL_LATENCY.labels(namespace=namespace).observe(time.perf_counter() - start)
