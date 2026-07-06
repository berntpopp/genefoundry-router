"""Inbound request limits: body-size cap + per-client rate limit (DoS / abuse guard).

A read-only reference gateway still needs back-pressure: without it, an open or buggy
client can exhaust the router or use it to hammer upstream APIs (OWASP LLM10 - unbounded
consumption). Both limits are opt-in via settings; ``<= 0`` disables that limit.
"""

from __future__ import annotations

import time

import structlog
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
    xff = Headers(scope=scope).get("x-forwarded-for")
    parts = [part.strip() for part in (xff or "").split(",") if part.strip()]
    if trusted_proxy_hops > 0 and len(parts) >= trusted_proxy_hops:
        return parts[-trusted_proxy_hops]
    return client_host


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
    """Reject oversized bodies (413) and rate-limit per client (429, fixed window)."""

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
            await JSONResponse({"error": "request entity too large"}, status_code=413)(
                scope, receive, send
            )
            return

        if self._rpm > 0 and not self._rate_allowed(scope):
            await JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(self._window)},
            )(scope, receive, send)
            return

        if self._max_body <= 0:
            await self.app(scope, receive, send)
            return

        try:
            buffered = await _read_body_until_limit(receive, self._max_body)
        except _ClientDisconnectedError:
            return

        if buffered is None:
            log.warning("request_too_large", limit=self._max_body)
            await JSONResponse({"error": "request entity too large"}, status_code=413)(
                scope, receive, send
            )
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

    def _rate_allowed(self, scope: Scope) -> bool:
        key = _client_key(scope, self._trusted_proxy_hops)
        allowed = self._increment(key, time.monotonic())
        if not allowed:
            log.warning("rate_limited", limit=self._rpm)
        return allowed

    def _increment(self, key: str, now: float) -> bool:
        window = int(now // self._window)
        if self._window_index != window:
            self._hits.clear()
            self._window_index = window
            self._ceiling_warned = False

        if key not in self._hits and len(self._hits) >= _MAX_TRACKED:
            if not self._ceiling_warned:
                log.warning("rate_limit_tracking_ceiling", max_tracked=_MAX_TRACKED)
                self._ceiling_warned = True
            return True

        count = self._hits.get(key, 0) + 1
        self._hits[key] = count
        return count <= self._rpm


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
