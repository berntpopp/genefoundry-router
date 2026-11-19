"""Inbound request limits: body-size cap + rate limits (DoS / abuse guard).

A read-only reference gateway still needs back-pressure: without it, an open or buggy
client can exhaust the router or use it to hammer upstream APIs (OWASP LLM10 - unbounded
consumption). Every limit is opt-in via settings; ``<= 0`` disables that limit.

Two rate limits, at two layers of the ASGI stack:

* ``RequestLimitMiddleware`` (outer app, BEFORE auth): per client IP. The pre-auth DoS
  floor - it also caps callers that present no token or a forged one.
* ``AccountRateLimitMiddleware`` (inside the MCP app, AFTER FastMCP's
  ``AuthenticationMiddleware``): per verified account. The key comes only from the
  ``AccessToken`` the verifier put in ``scope["user"]``, never from a request header.

Both advertise their quota with the IETF ``RateLimit`` / ``RateLimit-Policy`` fields
(draft-ietf-httpapi-ratelimit-headers-11, Structured Field Lists). Each layer adds its own
field line (``"ip"`` / ``"account"``); RFC 9651 lists may be split across lines.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

import structlog
from fastapi import FastAPI
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:
    from mcp.server.auth.provider import AccessToken

    from genefoundry_router.config import RouterSettings

log = structlog.get_logger(__name__)

_MAX_TRACKED = 100_000


def _scope_client_host(scope: Scope) -> str:
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        host = client[0]
        if isinstance(host, str) and host:
            return host
    return "unknown"


def _client_key(scope: Scope, trusted_proxy_hops: int) -> str:
    """Identify the caller from trusted X-Forwarded-For tail hops, else ASGI client."""
    client_host = _scope_client_host(scope)
    # Flatten ALL X-Forwarded-For values in order (duplicate header lines AND comma-joined
    # values): Headers.get returns only the FIRST line, so a caller could otherwise split a
    # spoofed hop into a second header and land it at the trusted tail. getlist folds them.
    values = Headers(scope=scope).getlist("x-forwarded-for")
    parts = [part.strip() for value in values for part in value.split(",") if part.strip()]
    if trusted_proxy_hops > 0 and len(parts) >= trusted_proxy_hops:
        return parts[-trusted_proxy_hops]
    return client_host


RateHeaders = list[tuple[bytes, bytes]]


def _rate_headers(policy: str, quota: int, remaining: int, reset: int, window: int) -> RateHeaders:
    """``RateLimit-Policy`` + ``RateLimit`` per the IETF draft. No ``pk``: the partition key
    would echo the caller's IP or account id into every response."""
    return [
        (b"ratelimit-policy", f'"{policy}";q={quota};w={window}'.encode()),
        (b"ratelimit", f'"{policy}";r={remaining};t={reset}'.encode()),
    ]


def _send_with_headers(send: Send, extra: RateHeaders) -> Send:
    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            message = {**message, "headers": [*message.get("headers", []), *extra]}
        await send(message)

    return wrapped


def _jsonrpc_error(
    status: int,
    message: str,
    retry_after: int | None = None,
    extra_headers: RateHeaders | None = None,
) -> JSONResponse:
    """An HTTP rejection whose body is a JSON-RPC error, so MCP clients surface ``message``
    instead of a generic "Server returned an error response" (#294)."""
    error: dict[str, object] = {"code": -32000, "message": message}
    headers: dict[str, str] = {k.decode(): v.decode() for k, v in extra_headers or []}
    if retry_after is not None:
        error["data"] = {"retry_after": retry_after}
        headers["Retry-After"] = str(retry_after)
    body = {"jsonrpc": "2.0", "id": None, "error": error}
    return JSONResponse(body, status_code=status, headers=headers or None)


class _ClientDisconnectedError(Exception):
    """Raised when the client disconnects before the request body completes."""


async def _read_body_until_limit(receive: Receive, limit: int) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnectedError
        if message["type"] != "http.request":
            continue
        body = message.get("body", b"")
        if body:
            total += len(body)
            if total > limit:
                return None
            chunks.append(body)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay_receive(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class RequestLimitMiddleware:
    """Reject oversized bodies (413) and rate-limit per client IP (429, fixed window)."""

    _policy = "ip"

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = 0,
        rate_limit_rpm: int = 0,
        trusted_proxy_hops: int = 1,
        window_seconds: int = 60,
    ) -> None:
        self.app = app
        self._max_body = max_body_bytes
        self._rpm = rate_limit_rpm
        self._trusted_proxy_hops = trusted_proxy_hops
        self._window = window_seconds
        self._hits: dict[str, int] = {}
        self._window_index: int | None = None
        self._ceiling_warned = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._content_length_exceeds(scope):
            await _jsonrpc_error(413, "request entity too large")(scope, receive, send)
            return

        if self._rpm > 0:
            key = _client_key(scope, self._trusted_proxy_hops)
            allowed, headers, reset = self._check(key, self._rpm)
            if not allowed:
                await self._reject(headers, reset)(scope, receive, send)
                return
            send = _send_with_headers(send, headers)

        if self._max_body <= 0:
            await self.app(scope, receive, send)
            return

        try:
            buffered = await _read_body_until_limit(receive, self._max_body)
        except _ClientDisconnectedError:
            return

        if buffered is None:
            log.warning("request_too_large", limit=self._max_body)
            await _jsonrpc_error(413, "request entity too large")(scope, receive, send)
            return

        await self.app(scope, _replay_receive(buffered, receive), send)

    def _content_length_exceeds(self, scope: Scope) -> bool:
        if self._max_body <= 0:
            return False
        content_length = Headers(scope=scope).get("content-length")
        if content_length is None or not content_length.isdigit():
            return False
        length = int(content_length)
        if length <= self._max_body:
            return False
        log.warning("request_too_large", content_length=length, limit=self._max_body)
        return True

    def _check(self, key: str, limit: int) -> tuple[bool, RateHeaders, int]:
        """Count one hit for ``key``; return (allowed, RateLimit headers, reset seconds)."""
        now = time.monotonic()
        allowed = self._increment(key, now, limit)
        if not allowed:
            # Never the key: it is an IP or an account id (GDPR); the policy name suffices.
            log.warning("rate_limited", policy=self._policy, limit=limit)
        remaining = max(limit - self._hits.get(key, limit), 0)
        reset = max(1, math.ceil(self._window - now % self._window))
        return allowed, _rate_headers(self._policy, limit, remaining, reset, self._window), reset

    def _reject(self, headers: RateHeaders, reset: int) -> JSONResponse:
        return _jsonrpc_error(
            429, f"rate limit exceeded; retry after {reset} s", reset, extra_headers=headers
        )

    def _increment(self, key: str, now: float, limit: int | None = None) -> bool:
        window = int(now // self._window)
        if self._window_index != window:
            self._hits.clear()
            self._window_index = window
            self._ceiling_warned = False

        if key not in self._hits and len(self._hits) >= _MAX_TRACKED:
            if not self._ceiling_warned:
                log.warning("rate_limit_tracking_ceiling", max_tracked=_MAX_TRACKED)
                self._ceiling_warned = True
            return False

        count = self._hits.get(key, 0) + 1
        self._hits[key] = count
        return count <= (self._rpm if limit is None else limit)


RateOverride = int | Literal["unlimited"]


class AccountRateLimitMiddleware(RequestLimitMiddleware):
    """Per-account rate limit (429, fixed window), keyed on the VERIFIED access token.

    Must run inside FastMCP's ``AuthenticationMiddleware`` (pass it to ``http_app``'s
    ``middleware=``), which puts an ``AuthenticatedUser`` in ``scope["user"]`` only after
    the token verifier accepted the bearer token. A request without one (no token, forged
    or expired token) passes through untouched: the route answers 401 and the pre-auth
    per-IP floor has already counted it.

    The bucket is the token subject (``sub``), else its client id. An override is looked
    up by subject first, then by client id (``azp``/``client_id``), so a service account
    can be named by its client id (``gfbench``). ``"unlimited"`` skips counting.
    """

    _policy = "account"

    def __init__(
        self,
        app: ASGIApp,
        rate_limit_rpm: int = 0,
        overrides: Mapping[str, RateOverride] | None = None,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app, rate_limit_rpm=rate_limit_rpm, window_seconds=window_seconds)
        self._overrides = dict(overrides or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        user = scope.get("user") if scope["type"] == "http" else None
        if not isinstance(user, AuthenticatedUser):
            await self.app(scope, receive, send)
            return
        token = user.access_token
        limit = self._limit_for(token)
        if limit <= 0:
            await self.app(scope, receive, send)
            return
        key = f"sub:{token.subject}" if token.subject else f"client:{token.client_id}"
        allowed, headers, reset = self._check(key, limit)
        if not allowed:
            await self._reject(headers, reset)(scope, receive, send)
            return
        await self.app(scope, receive, _send_with_headers(send, headers))

    def _limit_for(self, token: AccessToken) -> int:
        """Effective limit for this token; ``0`` = not limited."""
        for name in (token.subject, token.client_id):
            if name and name in self._overrides:
                value = self._overrides[name]
                return 0 if value == "unlimited" else int(value)
        return self._rpm


def ip_rate_limit_rpm(settings: RouterSettings) -> int:
    """The pre-auth per-IP floor: ``GF_RATE_LIMIT_IP_RPM``, unset = ``GF_RATE_LIMIT_RPM``."""
    ip_rpm = settings.GF_RATE_LIMIT_IP_RPM
    return settings.GF_RATE_LIMIT_RPM if ip_rpm is None else ip_rpm


def account_rate_limit_middleware(settings: RouterSettings) -> list[Middleware]:
    """The post-auth per-account limiter for ``http_app(middleware=...)``; ``[]`` when
    auth is off (no verified account to key on) or no account limit is configured."""
    if settings.GF_AUTH_MODE == "none":
        return []
    if settings.GF_RATE_LIMIT_RPM <= 0 and not settings.GF_RATE_LIMIT_OVERRIDES:
        return []
    log.info(
        "account_rate_limit",
        default_rpm=settings.GF_RATE_LIMIT_RPM,
        overrides=len(settings.GF_RATE_LIMIT_OVERRIDES),  # count only: keys are account ids
    )
    return [
        Middleware(
            AccountRateLimitMiddleware,  # type: ignore[arg-type]
            rate_limit_rpm=settings.GF_RATE_LIMIT_RPM,
            overrides=settings.GF_RATE_LIMIT_OVERRIDES,
        )
    ]


def add_request_limits(
    app: FastAPI,
    max_body_bytes: int,
    rate_limit_rpm: int,
    trusted_proxy_hops: int = 1,
) -> None:
    """Attach the request-limit middleware (no-op for whichever limit is <= 0)."""
    app.add_middleware(
        RequestLimitMiddleware,
        max_body_bytes=max_body_bytes,
        rate_limit_rpm=rate_limit_rpm,
        trusted_proxy_hops=trusted_proxy_hops,
    )
