"""Transport security: Origin-header validation (MCP DNS-rebinding defense).

Per the MCP Streamable-HTTP transport spec (2025-11-25): servers MUST validate the
``Origin`` header and respond 403 when it is present and not allow-listed. Requests
with NO ``Origin`` header (non-browser MCP clients, curl health checks) pass through.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = structlog.get_logger(__name__)


class OriginValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self._allowed = set(allowed_origins)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and origin not in self._allowed:
            log.warning("origin_rejected", origin=origin)
            return JSONResponse({"error": "forbidden origin"}, status_code=403)
        return await call_next(request)


def add_origin_validation(app: FastAPI, allowed_origins: list[str]) -> None:
    """Attach Origin validation. Empty allowlist rejects ANY request that sends Origin."""
    app.add_middleware(OriginValidationMiddleware, allowed_origins=allowed_origins)
