"""Inbound request limits: body-size cap + per-client rate limit (DoS / abuse guard).

A read-only reference gateway still needs back-pressure: without it, an open or buggy
client can exhaust the router or use it to hammer upstream APIs (OWASP LLM10 — unbounded
consumption). Both limits are opt-in via settings; ``<= 0`` disables that limit.
"""

from __future__ import annotations

import time

import structlog
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = structlog.get_logger(__name__)


def _client_key(request: Request) -> str:
    """Identify the caller, honoring the first X-Forwarded-For hop (router runs behind a proxy)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


class RequestLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies (413) and rate-limit per client (429, fixed window)."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = 0,
        rate_limit_rpm: int = 0,
        window_seconds: int = 60,
    ) -> None:
        super().__init__(app)
        self._max_body = max_body_bytes
        self._rpm = rate_limit_rpm
        self._window = window_seconds
        # client -> (window_index, count); single entry per client keeps this bounded.
        self._hits: dict[str, tuple[int, int]] = {}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._max_body > 0:
            cl = request.headers.get("content-length")
            if cl is not None and cl.isdigit() and int(cl) > self._max_body:
                log.warning("request_too_large", content_length=int(cl), limit=self._max_body)
                return JSONResponse({"error": "request entity too large"}, status_code=413)

        if self._rpm > 0:
            key = _client_key(request)
            window = int(time.monotonic() // self._window)
            prev_window, count = self._hits.get(key, (window, 0))
            count = count + 1 if prev_window == window else 1
            self._hits[key] = (window, count)
            if count > self._rpm:
                log.warning("rate_limited", client=key, limit=self._rpm)
                return JSONResponse(
                    {"error": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(self._window)},
                )

        return await call_next(request)


def add_request_limits(app: FastAPI, max_body_bytes: int, rate_limit_rpm: int) -> None:
    """Attach the request-limit middleware (no-op for whichever limit is <= 0)."""
    app.add_middleware(
        RequestLimitMiddleware,
        max_body_bytes=max_body_bytes,
        rate_limit_rpm=rate_limit_rpm,
    )
