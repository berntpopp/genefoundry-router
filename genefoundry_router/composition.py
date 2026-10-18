"""Build per-backend proxies and mount them with a namespace."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any

import httpx
import structlog
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import (
    ClientFactoryT,
    FastMCPProxy,
    ProxyClient,
    ProxyProvider,
    _CacheEntry,
)
from fastmcp.tools import Tool

from genefoundry_router.registry import BackendDef

log = structlog.get_logger(__name__)

DEFAULT_CACHE_TTL = 300
# Generous default so legitimately slow backends (e.g. spliceai cold ~60s) are not cut
# off, while still bounding an indefinite hang. Overridable via GF_BACKEND_TIMEOUT.
DEFAULT_BACKEND_TIMEOUT = 120.0

force_tool_refresh: ContextVar[bool] = ContextVar("force_tool_refresh", default=False)

_shared_upstream_transport: httpx.AsyncHTTPTransport | None = None


def get_shared_upstream_transport() -> httpx.AsyncHTTPTransport:
    """Return or initialize the singleton upstream HTTP connection pool."""
    global _shared_upstream_transport
    if _shared_upstream_transport is None:
        _shared_upstream_transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=25),
        )
    return _shared_upstream_transport


async def close_shared_upstream_transport() -> None:
    """Async helper for server shutdown to close the shared upstream transport pool."""
    global _shared_upstream_transport
    if _shared_upstream_transport is not None:
        await _shared_upstream_transport.aclose()
        _shared_upstream_transport = None


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Non-closing wrapper around a shared httpx.AsyncHTTPTransport pool.

    Prevents per-call AsyncClient.aclose() from terminating the connection pool.
    """

    def __init__(self, pool: httpx.AsyncHTTPTransport) -> None:
        self.pool = pool

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.pool.handle_async_request(request)

    async def aclose(self) -> None:
        # No-op: the server lifespan owns and closes the pool via close_shared_upstream_transport.
        pass


def default_httpx_client_factory(
    pool: httpx.AsyncHTTPTransport | None = None,
) -> Any:
    """Build an httpx client factory borrowing from a shared transport pool."""
    transport_pool = pool if pool is not None else get_shared_upstream_transport()

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["follow_redirects"] = False
        kwargs.setdefault("trust_env", False)
        kwargs.setdefault("timeout", httpx.Timeout(30.0, read=300.0))
        return httpx.AsyncClient(
            transport=_BorrowedTransport(transport_pool),
            **kwargs,
        )

    return factory


# Alias for compatibility with codex review references
pooled_http_factory = default_httpx_client_factory


class CachedProxyProvider(ProxyProvider):
    """ProxyProvider that caches tools/list results and protects against thundering herds."""

    def __init__(
        self,
        client_factory: ClientFactoryT,
        cache_ttl: float | None = None,
    ) -> None:
        super().__init__(client_factory, cache_ttl=cache_ttl)
        self._refresh_lock = asyncio.Lock()
        self._tools_cache_entry: _CacheEntry | None = None

    class _ToolsCacheProxy:
        def __init__(self, provider: CachedProxyProvider) -> None:
            self._provider = provider

        def get(self) -> Sequence[Tool] | None:
            entry = self._provider._tools_cache_entry
            if entry is not None and entry.is_fresh(self._provider._cache_ttl):
                return entry.items
            return None

        @property
        def items(self) -> Sequence[Tool]:
            return (
                self._provider._tools_cache_entry.items
                if self._provider._tools_cache_entry is not None
                else []
            )

        @property
        def timestamp(self) -> float:
            return (
                self._provider._tools_cache_entry.timestamp
                if self._provider._tools_cache_entry is not None
                else 0.0
            )

        def is_fresh(self, ttl: float) -> bool:
            if self._provider._tools_cache_entry is None:
                return False
            return self._provider._tools_cache_entry.is_fresh(ttl)

        def __bool__(self) -> bool:
            return self._provider._tools_cache_entry is not None

    @property
    def _tools_cache(self) -> Any:
        return self._ToolsCacheProxy(self)

    @_tools_cache.setter
    def _tools_cache(self, val: Any) -> None:
        if isinstance(val, CachedProxyProvider._ToolsCacheProxy):
            return
        self._tools_cache_entry = val

    async def _list_tools(self) -> Sequence[Tool]:
        if not force_tool_refresh.get() and (cached := self._tools_cache.get()) is not None:
            return [t.model_copy(deep=True) for t in cached]
        async with self._refresh_lock:
            if not force_tool_refresh.get() and (cached := self._tools_cache.get()) is not None:
                return [t.model_copy(deep=True) for t in cached]
            self._tools_cache = None
            tools = await super()._list_tools()
            return [t.model_copy(deep=True) for t in tools]


def make_proxy_client(
    target: Any,
    timeout: float | None = None,
    service_token: str | None = None,
    httpx_client_factory: Any | None = None,
) -> ProxyClient:
    """Build the router's upstream proxy client with caller-token forwarding DISABLED.

    R1.6 — confused-deputy / token-passthrough invariant. fastmcp's ``ProxyClient``
    sets ``transport.forward_incoming_headers = True`` by default, and its HTTP transport
    explicitly re-includes ``authorization`` (which ``get_http_headers`` would otherwise
    strip) — i.e. the caller's bearer token would be forwarded to every backend. The
    router connects to backends on its OWN connection and forwards NOTHING from the
    caller; if a backend ever needs auth, give the proxy its own service credential.

    Also applies an outbound ``timeout`` so a hung/slow backend cannot stall the router.
    Safe for in-process targets too: non-HTTP transports lack the flag and are left as-is.
    """
    transport_target = target
    if isinstance(target, str):
        factory = (
            httpx_client_factory
            if httpx_client_factory is not None
            else default_httpx_client_factory()
        )
        transport_target = StreamableHttpTransport(
            target,
            headers={"Authorization": f"Bearer {service_token}"} if service_token else None,
            httpx_client_factory=factory,
        )
    client = (
        ProxyClient(transport_target)
        if timeout is None
        else ProxyClient(transport_target, timeout=timeout)
    )
    transport = getattr(client, "transport", None)
    if transport is not None and hasattr(transport, "forward_incoming_headers"):
        transport.forward_incoming_headers = False
    return client


def build_proxy(
    backend: BackendDef,
    target: Any | None = None,
    timeout: float | None = None,
    httpx_client_factory: Any | None = None,
) -> FastMCP:
    """Create a FastMCP proxy for a backend.

    ``target`` overrides the proxy target (used by tests to inject an in-process
    FastMCP). In production it defaults to ``backend.url``.

    R1.6 — confused-deputy invariant: a URL target is wrapped in a ``ProxyClient`` whose
    incoming-header forwarding is disabled (see ``make_proxy_client``) so the router uses
    its OWN connection and never forwards the caller's auth token upstream.
    """
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    if isinstance(proxy_target, str):
        # Production path (a backend URL): control the client so token forwarding is off.
        return FastMCPProxy(
            client_factory=lambda: make_proxy_client(
                proxy_target,
                timeout,
                service_token=backend.service_token,
                httpx_client_factory=httpx_client_factory,
            ),
            name=f"{backend.name}-proxy",
        )
    # In-process target (tests inject a FastMCP server): no HTTP transport, no forwarding.
    return create_proxy(proxy_target, name=f"{backend.name}-proxy")


def _register_via_mount(
    server: FastMCP,
    backend: BackendDef,
    target: Any | None,
    timeout: float | None,
    httpx_client_factory: Any | None = None,
) -> None:
    """Default path: route through CachedProxyProvider."""
    _register_via_provider(
        server,
        backend,
        target,
        timeout,
        httpx_client_factory=httpx_client_factory,
    )


def _register_via_provider(
    server: FastMCP,
    backend: BackendDef,
    target: Any | None,
    timeout: float | None,
    httpx_client_factory: Any | None = None,
) -> None:
    """Register a CachedProxyProvider honoring backend.cache_ttl / cache_ttl_s."""
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    ttl = getattr(backend, "cache_ttl_s", None)
    if ttl is None:
        ttl = getattr(backend, "cache_ttl", DEFAULT_CACHE_TTL)
    if ttl is None:
        ttl = DEFAULT_CACHE_TTL
    provider = CachedProxyProvider(
        client_factory=lambda: make_proxy_client(
            proxy_target,
            timeout,
            service_token=backend.service_token,
            httpx_client_factory=httpx_client_factory,
        ),
        cache_ttl=float(ttl),
    )
    server.add_provider(provider, namespace=backend.namespace)


def register_backend(
    server: FastMCP,
    backend: BackendDef,
    proxy_target: Any | None = None,
    timeout: float | None = None,
    httpx_client_factory: Any | None = None,
) -> None:
    """Mount a backend under its namespace using CachedProxyProvider.

    Tools surface as ``<namespace>_<tool>``. ``timeout`` bounds upstream calls (R1.6 the
    proxy client also disables caller-token forwarding). Normalization transforms extend
    this; the async normalization pass runs from the lifespan.
    """
    if httpx_client_factory is not None:
        _register_via_provider(
            server,
            backend,
            proxy_target,
            timeout,
            httpx_client_factory=httpx_client_factory,
        )
    else:
        _register_via_provider(server, backend, proxy_target, timeout)
    ttl = getattr(backend, "cache_ttl_s", None) or getattr(backend, "cache_ttl", DEFAULT_CACHE_TTL)
    log.info(
        "backend_mounted",
        backend=backend.name,
        namespace=backend.namespace,
        cache_ttl=ttl,
    )


def mount_backends(
    server: FastMCP,
    backends: list[BackendDef],
    proxy_targets: dict[str, Any] | None = None,
    timeout: float | None = None,
    httpx_client_factory: Any | None = None,
) -> None:
    """Mount all enabled backends in the registry."""
    targets = proxy_targets or {}
    for backend in backends:
        if not backend.enabled:
            continue
        target = targets.get(backend.name)
        if httpx_client_factory is not None:
            register_backend(
                server,
                backend,
                proxy_target=target,
                timeout=timeout,
                httpx_client_factory=httpx_client_factory,
            )
        else:
            register_backend(
                server,
                backend,
                proxy_target=target,
                timeout=timeout,
            )
