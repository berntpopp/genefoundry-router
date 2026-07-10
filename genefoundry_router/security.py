"""Outer Host and Origin validation for every router HTTP route."""

from __future__ import annotations

import ipaddress

import structlog
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = structlog.get_logger(__name__)


def _normalize_host(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("host must not be empty")

    try:
        return ipaddress.ip_address(raw).compressed.lower()
    except ValueError:
        pass

    if raw.startswith("["):
        close = raw.find("]")
        if close < 0:
            raise ValueError("invalid bracketed IPv6 host")
        host = raw[1:close]
        suffix = raw[close + 1 :]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            raise ValueError("invalid host port")
    elif raw.count(":") == 1:
        candidate, port = raw.rsplit(":", 1)
        if not port.isdigit():
            raise ValueError("invalid host port")
        host = candidate
    else:
        host = raw

    try:
        return ipaddress.ip_address(host).compressed.lower()
    except ValueError:
        if ":" in host or not host:
            raise ValueError("invalid host") from None
        return host.lower().rstrip(".")


class HostOriginValidationMiddleware:
    """Validate the HTTP Host first and any present browser Origin second."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: list[str],
        allowed_origins: list[str],
    ) -> None:
        self.app = app
        self._allowed_hosts = {_normalize_host(host) for host in allowed_hosts}
        self._allowed_origins = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if self._allowed_hosts:
            try:
                host = _normalize_host(headers.get("host", ""))
            except ValueError:
                host = ""
            if host not in self._allowed_hosts:
                log.warning("host_rejected")
                response = JSONResponse({"error": "misdirected request"}, status_code=421)
                await response(scope, receive, send)
                return

        origin = headers.get("origin")
        if origin is not None and origin not in self._allowed_origins:
            log.warning("origin_rejected")
            response = JSONResponse({"error": "forbidden origin"}, status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def add_host_origin_validation(
    app: FastAPI,
    allowed_hosts: list[str],
    allowed_origins: list[str],
) -> None:
    """Attach the router's single outer transport guard."""
    app.add_middleware(
        HostOriginValidationMiddleware,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
