"""Logging, health, and metrics for the router."""

from __future__ import annotations

import hmac
import logging
import threading
import time
from typing import Any, Protocol

import structlog
from asgi_correlation_id import correlation_id
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

from genefoundry_router.audit_identity import (
    resolve_dispatch_namespace,
    resolve_log_identity,
)
from genefoundry_router.notfound_guard import redacted_message
from genefoundry_router.refresh_observability import (
    CLIENT_CLASSES,
    FAILURE_REASONS,
    CounterSnapshot,
)
from genefoundry_router.registry import BackendDef
from genefoundry_router.tool_error_taxonomy import (
    error_code,
    upstream_request_id,
    upstream_status,
)


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
OAUTH_REFRESH_ATTEMPTS = Counter(
    "genefoundry_oauth_refresh_attempts_total",
    "OAuth connector refresh attempts",
    ["client_class"],
    registry=METRICS_REGISTRY,
)
OAUTH_REFRESH_SUCCESS = Counter(
    "genefoundry_oauth_refresh_success_total",
    "Successful OAuth connector refresh rotations",
    ["client_class"],
    registry=METRICS_REGISTRY,
)
OAUTH_REFRESH_FAILURES = Counter(
    "genefoundry_oauth_refresh_failures_total",
    "Failed OAuth connector refreshes by bounded reason",
    ["client_class", "reason"],
    registry=METRICS_REGISTRY,
)
_REFRESH_RESTORE_LOCK = threading.Lock()
_RESTORED_REFRESH_SOURCES: set[str] = set()


def record_refresh_metrics(client_class: str, outcome: str, reason: str | None = None) -> None:
    """Increment only closed-vocabulary refresh metrics."""
    record_refresh_attempt(client_class)
    record_refresh_outcome(client_class, outcome, reason)


def record_refresh_attempt(client_class: str) -> None:
    """Increment one bounded refresh-attempt denominator at load time."""
    if client_class not in CLIENT_CLASSES:
        raise ValueError("OAuth refresh client class is not bounded")
    OAUTH_REFRESH_ATTEMPTS.labels(client_class=client_class).inc()


def record_refresh_outcome(client_class: str, outcome: str, reason: str | None = None) -> None:
    """Increment one terminal refresh outcome without duplicating its attempt."""
    if client_class not in CLIENT_CLASSES:
        raise ValueError("OAuth refresh client class is not bounded")
    if outcome not in {"success", "failure"}:
        raise ValueError("OAuth refresh outcome is not bounded")
    if outcome == "failure" and reason not in FAILURE_REASONS:
        raise ValueError("OAuth refresh failure reason is not bounded")
    if outcome == "success" and reason is not None:
        raise ValueError("successful OAuth refresh must not carry a failure reason")
    if outcome == "success":
        OAUTH_REFRESH_SUCCESS.labels(client_class=client_class).inc()
    else:
        assert reason is not None
        OAUTH_REFRESH_FAILURES.labels(client_class=client_class, reason=reason).inc()


def restore_refresh_metrics(snapshot: CounterSnapshot, *, source_id: str) -> None:
    """Restore durable totals once for one ledger in this process."""
    for client_class in (*snapshot.attempts, *snapshot.successes):
        if client_class not in CLIENT_CLASSES:
            raise ValueError("persisted OAuth refresh client class is not bounded")
    for client_class, reason in snapshot.failures:
        if client_class not in CLIENT_CLASSES or reason not in FAILURE_REASONS:
            raise ValueError("persisted OAuth refresh failure labels are not bounded")
    with _REFRESH_RESTORE_LOCK:
        if source_id in _RESTORED_REFRESH_SOURCES:
            return
        for client_class, value in snapshot.attempts.items():
            OAUTH_REFRESH_ATTEMPTS.labels(client_class=client_class).inc(value)
        for client_class, value in snapshot.successes.items():
            OAUTH_REFRESH_SUCCESS.labels(client_class=client_class).inc(value)
        for (client_class, reason), value in snapshot.failures.items():
            OAUTH_REFRESH_FAILURES.labels(client_class=client_class, reason=reason).inc(value)
        _RESTORED_REFRESH_SOURCES.add(source_id)


_OAUTH_SENSITIVE_MARKERS = (
    "CIMD refresh failed for ",
    "Client %s matched upstream client_id",
    "Refresh token not found for client=",
    "Refresh token client_id mismatch",
    "FastMCP refresh token validation failed",
    "JTI mapping not found for refresh token",
    "Upstream token set not found",
    "Refreshing upstream token (jti=",
    "Upstream token refresh failed",
    "Issued FastMCP tokens",
    "Issued new FastMCP tokens",
    "Authorization code not found",
    "Authorization code expired",
    "Authorization code client ID mismatch",
    "Starting OAuth transaction",
    "Resource mismatch:",
    "Client registered with redirect_uri:",
    "Registered client ",
    "Stored encrypted upstream tokens",
    "IdP token exchange failed",
    "IdP callback error:",
    "IdP callback with invalid transaction ID:",
    "Blocked IdP callback error redirect",
    "Blocked IdP callback redirect",
    "Transaction %s missing consent_token",
    "Consent binding cookie missing or invalid",
    "Transparent upstream refresh failed",
    "Token swap validation failed",
    "Forwarding to client callback",
    "Error in IdP callback handler:",
    "Failed to revoke token with upstream server",
    "Unregistered client_id=",
    "CIMD document fetched and validated:",
    "CIMD fetch failed for ",
    "CIMD client resolved:",
    "Ignoring invalid Cache-Control max-age value:",
    "Ignoring invalid Expires header on CIMD response:",
    "JWT assertion validated successfully for client ",
    "Issued access token for client=",
    "Issued refresh token for client=",
    "Token verified successfully for subject=",
    "Blocked consent denial redirect to disallowed URI for transaction ",
    "Silent consent skipped for transaction ",
    "CSRF double-submit check failed for transaction ",
)
_OAUTH_REDACTED_MESSAGE = "OAuth detail omitted (sensitive value redacted)."

# Source loggers the filter is installed on. Named here (not inline in the installer) so the
# regression fence in tests/unit/test_oauth_log_privacy.py audits exactly the modules the
# filter actually governs, and cannot silently drift from them.
OAUTH_PRIVACY_LOGGERS: tuple[str, ...] = (
    "fastmcp.server.auth.oauth_proxy.proxy",
    "fastmcp.server.auth.handlers.authorize",
    "fastmcp.server.auth.cimd",
    "fastmcp.server.auth.jwt_issuer",
    "fastmcp.server.auth.oauth_proxy.consent",
)


class OAuthProxyPrivacyFilter(logging.Filter):
    """Redact the sensitive VALUE in a FastMCP OAuth record, not the diagnostic reason.

    Replacing the whole ``record.msg`` (the previous behaviour) cost the operator every
    OAuth failure mode at once: 279 identical "OAuth detail omitted" lines, and a 45-hour,
    114-request authentication failure whose entire router-side trace was 29 copies of that
    one sentence. The secrets were never in the prose — 43 of the 44 markers name a
    ``%``-style call site whose template is a compile-time string literal and whose values
    live in ``record.args``. Deleting the template threw away the only thing that said what
    happened (`"...it was already rotated, expired, or revoked ... which forces the client
    to re-authenticate"`) to protect data that was never in it. This is the same lesson
    ``notfound_guard`` records for 2026-08-07, and it reuses that module's interpolation.

    The two shapes are told apart by ``record.args``, which is a sound test rather than a
    heuristic:

    * **args present** -> the message is a ``%``-style template, i.e. a literal in the
      framework's source, so it cannot contain a runtime secret. Interpolate ``<redacted>``
      in place of every placeholder: the input-free prose survives and the line still reads
      as a deliberate redaction rather than a broken formatter.
    * **args empty** -> the message may have been f-string-formatted at the call site, with
      the value already baked into ``record.msg`` (fastmcp does this at ``proxy.py:2381``).
      Clearing args would protect nothing, so the whole message goes, exactly as before.

    The one shape that would defeat the inference is an f-string template passed together
    with lazy args. ``test_fastmcp_oauth_loggers_never_mix_fstring_and_args`` asserts the
    framework never writes one, so a fastmcp upgrade that introduced it would fail CI rather
    than leak. ``exc_info``/``stack_info`` are still dropped unconditionally: a traceback is
    not a literal template and can carry anything.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.msg if isinstance(record.msg, str) else ""
        if not any(marker in message for marker in _OAUTH_SENSITIVE_MARKERS):
            return True
        # Interpolate FIRST, then clear args: the two together are what make the record both
        # value-free and still legible.
        record.msg = (
            redacted_message(record, fallback=_OAUTH_REDACTED_MESSAGE)
            if record.args
            else _OAUTH_REDACTED_MESSAGE
        )
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


_OAUTH_PRIVACY_FILTER = OAuthProxyPrivacyFilter()


def install_oauth_proxy_privacy_filter() -> None:
    """Install the narrow source-level OAuth privacy filter idempotently."""
    for name in OAUTH_PRIVACY_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(item, OAuthProxyPrivacyFilter) for item in logger.filters):
            logger.addFilter(_OAUTH_PRIVACY_FILTER)
        for handler in logger.handlers:
            if not any(isinstance(item, OAuthProxyPrivacyFilter) for item in handler.filters):
                handler.addFilter(_OAUTH_PRIVACY_FILTER)


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

    Logs the tool, namespace, outcome and elapsed time; the router's correlation id; the
    backend a ``call_tool`` dispatch targeted; and, on failure, a BOUNDED error code, the
    upstream HTTP status when the transport itself failed, and the backend's own request id.
    It still NEVER logs tool arguments, results, or exception messages, which can carry
    patient-derived data (variant coordinates, phenotype text).

    Redact the input, not the error (issue #159). The previous record kept only
    ``error_type``, and because the router is a proxy every backend fault is the same
    ``ToolError`` — 168 of 172 errors read "ToolError" and said nothing else, while no other
    sink could close the gap (MCP errors are in-band, so every proxied call logged 200 OK).
    An audit trail that cannot establish WHAT went wrong is weak accountability, not strong
    data minimisation. Every field added here is bounded by construction: see
    :mod:`genefoundry_router.tool_error_taxonomy`.
    """

    @staticmethod
    def _correlation_fields() -> dict[str, str]:
        """The router's own request id, when one is in scope.

        The class docstring used to claim this was "merged from contextvars". It never was:
        ``asgi-correlation-id`` binds its OWN ``ContextVar``, which structlog's
        ``merge_contextvars`` does not read, so no emitted record ever carried the id that
        ``X-Request-ID`` advertises to the caller. Omitted rather than null when there is no
        HTTP request in scope (stdio, the startup harvest).
        """
        current = correlation_id.get()
        return {"request_id": current} if current else {}

    async def _identity_fields(self, context: MiddlewareContext) -> dict[str, str]:
        # Resolve AFTER dispatch (warm catalog cache): only a verified registered tool is
        # logged verbatim; any unresolved/hostile name buckets to _unknown.
        tool, namespace = await resolve_log_identity(context)
        fields = {"tool": tool, "namespace": namespace, **self._correlation_fields()}
        dispatch_namespace = await resolve_dispatch_namespace(context)
        if dispatch_namespace is not None:
            fields["upstream_namespace"] = dispatch_namespace
        return fields

    @staticmethod
    def _failure_fields(exc: BaseException) -> dict[str, Any]:
        fields: dict[str, Any] = {
            # class only — never the message (may hold PII)
            "error_type": type(exc).__name__,
            # closed vocabulary; anything unrecognised buckets to "unclassified"
            "error_code": error_code(exc),
        }
        status = upstream_status(exc)
        if status is not None:
            fields["upstream_status"] = status
        request_id = upstream_request_id(exc)
        if request_id is not None:
            fields["upstream_request_id"] = request_id
        return fields

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:
            audit_log.info(
                "tool_call",
                **await self._identity_fields(context),
                outcome="error",
                **self._failure_fields(exc),
                elapsed_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            raise
        audit_log.info(
            "tool_call",
            **await self._identity_fields(context),
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
