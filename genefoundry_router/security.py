"""Outer Host and Origin validation for every router HTTP route."""

from __future__ import annotations

import ipaddress

import structlog
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
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


# --- CORS for the MCP transport (issue #162) --------------------------------------------
#
# ``HostOriginValidationMiddleware`` above ENFORCES the origin allowlist (403 on a
# mismatch) but never GRANTS anything: it emits no ``Access-Control-*`` header, so
# ``OPTIONS /mcp`` fell through to the transport's own route table and answered 405
# ``allow: DELETE, POST``. A browser stops at that preflight, so every entry in
# ``GF_ALLOWED_ORIGINS`` — all of them browser-hosted MCP clients — was unusable.
#
# Scope: the wrapper goes around the MOUNTED MCP app only, NOT the whole FastAPI app.
# The OAuth routes (``/token``, ``/.well-known/*``) already run the MCP SDK's own
# ``cors_middleware`` and answer with ``Access-Control-Allow-Origin: *``; a second,
# app-wide CORS layer would append a SECOND ``Access-Control-Allow-Origin`` to those
# responses, and a duplicated ACAO is rejected by every browser. Scoping the wrapper
# keeps the endpoints that already work exactly as they are.
#
# Layering: this sits INSIDE ``HostOriginValidationMiddleware``, so the Host check
# (DNS-rebinding guard, 421) and the non-preflight Origin check (403) both still run
# first and are unchanged. A preflight from an allowlisted origin passes the guard and is
# answered here; a preflight from any other origin is still refused upstream with no CORS
# grant, which is what a browser needs to see.

# Request headers a browser must be allowed to send on /mcp. ``Accept`` and
# ``Content-Type`` are CORS-safelisted (Starlette adds them regardless); the rest are not,
# and each one omitted here is a client feature that silently cannot work:
#   authorization        - the bearer token; not safelisted, so without it no
#                          authenticated browser client can call the endpoint at all.
#   mcp-session-id       - the session handle the Streamable HTTP transport requires the
#                          client to echo on every request after initialize.
#   mcp-protocol-version - the negotiated protocol version header.
#   last-event-id        - SSE stream resumption.
MCP_CORS_ALLOW_HEADERS: tuple[str, ...] = (
    "accept",
    "authorization",
    "content-type",
    "last-event-id",
    "mcp-protocol-version",
    "mcp-session-id",
)

# Response headers script must be able to READ. Only the CORS-safelisted response headers
# (Cache-Control, Content-Language, Content-Length, Content-Type, Expires, Last-Modified,
# Pragma) are readable by default, so anything else is invisible to a browser client
# unless it is named here:
#   mcp-session-id   - assigned by the server on initialize; unreadable => no session.
#   www-authenticate - carries ``resource_metadata=...``; unreadable => the client cannot
#                      discover the OAuth challenge that is supposed to start the flow.
#   x-request-id     - the correlation id, for user-reportable diagnostics.
MCP_CORS_EXPOSE_HEADERS: tuple[str, ...] = (
    "mcp-session-id",
    "mcp-protocol-version",
    "www-authenticate",
    "x-request-id",
)

# GET is advertised even though the router answers it 405: the MCP Streamable HTTP
# transport REQUIRES a server that does not offer an SSE stream on GET to return 405, and
# a browser can only observe that answer if the preflight for GET succeeds. Failing the
# preflight instead would leave a browser client unable to tell "this server has no SSE
# stream" (a normal, handled condition) from "CORS is broken".
MCP_CORS_ALLOW_METHODS: tuple[str, ...] = ("GET", "POST", "DELETE", "OPTIONS")

MCP_CORS_MAX_AGE = 600


class PathScopedCors:
    """Apply ``CORSMiddleware`` to the MCP transport path only, pass everything else through.

    The MCP app is mounted at ``/`` (so its baked ``GF_MCP_PATH`` route owns ``/mcp``), which
    means the mount is also the catch-all for every path no outer route claims. Wrapping the
    mount wholesale therefore made ``OPTIONS`` on ANY unrouted path answer a preflight — a
    grant for endpoints that do not exist. Scoping by path keeps the grant to the one endpoint
    that needs it, and keeps this middleware's blast radius equal to its justification.
    """

    def __init__(self, app: ASGIApp, cors: ASGIApp, mcp_path: str) -> None:
        self.app = app
        self.cors = cors
        self._path = "/" + mcp_path.strip("/")

    def _in_scope(self, path: str) -> bool:
        return path == self._path or path.startswith(f"{self._path}/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self._in_scope(scope.get("path", "")):
            await self.cors(scope, receive, send)
            return
        await self.app(scope, receive, send)


def wrap_mcp_cors(app: ASGIApp, allowed_origins: list[str], mcp_path: str) -> ASGIApp:
    """Wrap the mounted MCP ASGI app so ``GF_ALLOWED_ORIGINS`` is served, not just checked.

    ``allow_credentials`` stays False on purpose. MCP browser clients authenticate with an
    explicitly-set ``Authorization`` header, which needs only ``Access-Control-Allow-Headers``;
    turning credentials on would additionally admit cookie-bearing cross-origin requests to
    ``/mcp``, which nothing needs and which widens CSRF exposure. The allowlist is exact-match
    (never ``*``), so the validated origin is reflected and ``Vary: Origin`` is set.
    """
    cors = CORSMiddleware(
        app,
        allow_origins=list(allowed_origins),
        allow_methods=list(MCP_CORS_ALLOW_METHODS),
        allow_headers=list(MCP_CORS_ALLOW_HEADERS),
        allow_credentials=False,
        expose_headers=list(MCP_CORS_EXPOSE_HEADERS),
        max_age=MCP_CORS_MAX_AGE,
    )
    return PathScopedCors(app, cors, mcp_path)
