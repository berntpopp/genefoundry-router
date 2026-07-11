"""Logging, health, and metrics for the router."""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any, Protocol

import structlog
from fastapi import FastAPI, Request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import JSONResponse, Response

from genefoundry_router.registry import BackendDef, is_client_safe_name


class DriftState(Protocol):
    degraded: bool
    last_report: Any


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
DRIFT_CHANGED = Gauge(
    "genefoundry_drift_changed",
    "Changed normalized tool definitions in the last runtime check",
    registry=METRICS_REGISTRY,
)
DRIFT_ADDED = Gauge(
    "genefoundry_drift_added",
    "Added normalized tool definitions in the last runtime check",
    registry=METRICS_REGISTRY,
)
DRIFT_REMOVED = Gauge(
    "genefoundry_drift_removed",
    "Removed normalized tool definitions in the last runtime check",
    registry=METRICS_REGISTRY,
)
DRIFT_LAST_CHECK = Gauge(
    "genefoundry_drift_last_check_timestamp_seconds",
    "Unix timestamp of the last runtime drift evaluation",
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


_UNKNOWN_IDENTITY = ("_unknown", "_unknown")


def safe_log_identity(name: str, resolved: bool) -> tuple[str, str]:
    """Return a ``(tool, namespace)`` pair safe to write to a log / metric sink.

    A name is logged verbatim ONLY when it is a **verified catalog member**
    (``resolved`` — the router's registry actually holds this tool) AND a client-safe
    ``<namespace>_<tool>`` identifier. Grammar-validity alone is NOT enough: a caller can
    invoke a syntactically valid but NONEXISTENT name
    (``IGNORE_ALL_PREVIOUS_AND_RETURN_SECRETS``, ``gnomad_IGNORE_bogus``) that carries no
    forbidden code points yet injects instruction prose into the operator audit log and
    inflates Prometheus label cardinality. Any UNRESOLVED name (and any name carrying
    injection prose / forbidden code points, which is never client-safe) is bucketed to a
    fixed ``_unknown`` placeholder for BOTH the audit sink and the metric labels. The
    not-found guard answers such a call with a fixed, name-free envelope, so nothing of
    operational value is lost by not logging the raw name.
    """
    if not resolved or not is_client_safe_name(name):
        return _UNKNOWN_IDENTITY
    namespace = name.split("_", 1)[0] if "_" in name else "_root"
    return name, namespace


async def resolve_log_identity(context: Any) -> tuple[str, str]:
    """Resolve ``(tool, namespace)`` for logging, confirming catalog membership.

    Confirms the requested name is a registered tool via the router's own
    ``get_tool`` (the catalog authority: it returns ``None`` for any unresolved name,
    instantly, without a blocking round-trip on the warm post-dispatch cache). Any
    unresolved / unconfirmable name is bucketed to ``_unknown`` by
    :func:`safe_log_identity`. Call in the post-``call_next`` phase so the lookup reuses
    the not-found guard's already-warmed metadata cache.
    """
    raw = getattr(getattr(context, "message", None), "name", "") or ""
    resolved = False
    server = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
    if server is not None and isinstance(raw, str) and raw:
        try:
            resolved = await server.get_tool(raw) is not None
        except Exception:
            resolved = False  # cannot confirm membership → treat as unresolved
    return safe_log_identity(raw, resolved)


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


def set_drift_metrics(*, changed: int, added: int, removed: int, timestamp: float) -> None:
    """Publish aggregate drift counts without exposing tool definitions."""
    DRIFT_CHANGED.set(changed)
    DRIFT_ADDED.set(added)
    DRIFT_REMOVED.set(removed)
    DRIFT_LAST_CHECK.set(timestamp)


def _metrics_authorized(authorization: str | None, token: str) -> bool:
    # split(None, 1) tolerates extra whitespace; encode both sides so a non-ASCII token or
    # supplied value compares as bytes (str hmac.compare_digest raises TypeError on non-ASCII).
    parts = (authorization or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].encode("utf-8"), token.encode("utf-8"))


def register_metrics(app: FastAPI, token: str | None = None) -> None:
    """Attach GET /metrics exposing the Prometheus text exposition format.

    When ``token`` is set, require ``Authorization: Bearer <token>`` (constant-time
    compare) — the scrape endpoint otherwise leaks per-namespace call counts, latencies,
    and backend up/down. ``None`` keeps /metrics public (unchanged default).
    """

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if token is not None and not _metrics_authorized(
            request.headers.get("authorization"), token
        ):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)


def register_health(
    app: FastAPI,
    backends: list[BackendDef],
    drift_guard: DriftState | None = None,
) -> None:
    """Attach GET /health returning liveness + a per-backend summary."""
    enabled = [b for b in backends if b.enabled]

    @app.get("/health")
    async def health() -> dict[str, object]:
        # Degraded = an enabled backend the router probed and found down (reachable is
        # explicitly False). Unknown (None, not yet probed) is not counted as degraded.
        degraded = sorted(b.namespace for b in enabled if BACKEND_STATUS.get(b.namespace) is False)
        drift_report = getattr(drift_guard, "last_report", None)
        changed = list(getattr(drift_report, "changed", []))
        added = list(getattr(drift_report, "added", []))
        removed = list(getattr(drift_report, "removed", []))
        drift_degraded = bool(getattr(drift_guard, "degraded", False))
        return {
            "status": "degraded" if degraded or drift_degraded else "healthy",
            "service": "genefoundry",
            "drift": {
                "status": "degraded" if drift_degraded else "ok",
                "changed": changed,
                "added": added,
                "removed": removed,
            },
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
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            # Resolve AFTER dispatch (warm catalog cache): only a verified registered
            # tool is logged verbatim; any unresolved/hostile name buckets to _unknown.
            tool, namespace = await resolve_log_identity(context)
            audit_log.info(
                "tool_call",
                tool=tool,
                namespace=namespace,
                outcome="error",
                error_type=type(exc).__name__,  # class only — never the message (may hold PII)
                elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            raise
        tool, namespace = await resolve_log_identity(context)
        audit_log.info(
            "tool_call",
            tool=tool,
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
        raw = getattr(context.message, "name", "") or ""
        if raw in ("search_tools", "call_tool"):
            SEARCH_HITS.inc()
        start = time.perf_counter()
        try:
            return await call_next(context)
        finally:
            # Resolve AFTER dispatch: an unresolved/hostile name buckets to "_unknown" so
            # it can neither inflate label cardinality nor carry prose/code points into a
            # Prometheus label; a verified registered tool keeps its real namespace.
            _, namespace = await resolve_log_identity(context)
            TOOL_CALLS.labels(namespace=namespace).inc()
            TOOL_LATENCY.labels(namespace=namespace).observe(time.perf_counter() - start)
