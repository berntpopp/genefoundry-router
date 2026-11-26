"""SSRF-safe HTTP egress for community backends (Federation Standard v1 §6).

Every A/AAAA answer is resolved at connect time and must be a global address (after
unwrapping IPv4-mapped, 6to4 and NAT64 forms); the socket then dials the validated IP
literal while TLS keeps the hostname for SNI and certificate checks and the request keeps
its ``Host`` header. Only ``https`` on port 443; redirects are never followed and a 3xx is
``invalid_response``. No proxy support (``trust_env=False``): a proxy resolves DNS itself
and would bypass the connect-time check. Core backends never use this module.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

import anyio.to_thread
import httpcore2
import httpx2

from genefoundry_router.config import _is_conforming_community_url

Resolver = Callable[[str, int], list[str]]
IPCheck = Callable[[str], bool]

_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def is_public_ip(addr: str) -> bool:
    """True only for a global unicast address, also after unwrapping embedded IPv4."""
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped or ip.sixtofour:
            return is_public_ip(str(ip.ipv4_mapped or ip.sixtofour))
        if any(ip in net for net in _NAT64):
            return is_public_ip(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
    return ip.is_global and not (
        ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
    )


def resolve_host(host: str, port: int) -> list[str]:
    """Every A/AAAA address for ``host`` (blocking; run off the event loop)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _refusal(host: str, port: int, addrs: list[str], allow: IPCheck) -> str | None:
    if port != 443:
        return f"port {port} is not 443"
    if not addrs:
        return "no address"
    bad = [a for a in addrs if not allow(a)]
    return f"non-global address {bad[0]}" if bad else None


def check_community_url(url: str, resolve: Resolver = resolve_host) -> None:
    """Load-time check of a community URL; the connect-time check is the one relied on."""
    parts = urlsplit(url)
    if not _is_conforming_community_url(url) or parts.hostname is None:
        raise ValueError(f"community backend URL must be https://<host>/mcp: {url!r}")
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        pass
    else:
        raise ValueError(f"community backend URL must name a host, not an IP: {url!r}")
    try:
        addrs = resolve(parts.hostname, 443)
    except OSError as exc:
        raise ValueError(f"community backend URL does not resolve: {url!r}") from exc
    reason = _refusal(parts.hostname, 443, addrs, is_public_ip)
    if reason:
        raise ValueError(f"community backend URL refused ({reason}): {url!r}")


class _GuardedBackend(httpcore2.AsyncNetworkBackend):
    """Resolve, validate every answer, then dial the validated IP literal."""

    def __init__(self, resolve: Resolver, allow: IPCheck, inner: httpcore2.AsyncNetworkBackend):
        self._resolve, self._allow, self._inner = resolve, allow, inner

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        try:
            with anyio.fail_after(timeout):
                addrs = await anyio.to_thread.run_sync(self._resolve, host, port)
        except (OSError, TimeoutError) as exc:
            raise httpcore2.ConnectError(f"community egress: cannot resolve {host}") from exc
        reason = _refusal(host, port, addrs, self._allow)
        if reason:
            raise httpcore2.ConnectError(f"community egress refused {host}: {reason}")
        last: Exception | None = None
        for addr in addrs:
            try:
                return await self._inner.connect_tcp(
                    addr,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore2.ConnectError, httpcore2.ConnectTimeout) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        raise httpcore2.ConnectError("community egress: unix sockets refused")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def community_transport(
    resolve: Resolver = resolve_host,
    allow: IPCheck = is_public_ip,
    inner: httpcore2.AsyncNetworkBackend | None = None,
    verify: ssl.SSLContext | bool = True,
) -> httpx2.AsyncHTTPTransport:
    """An httpx transport whose every connection passes the connect-time IP guard."""
    transport = httpx2.AsyncHTTPTransport(verify=verify, trust_env=False)
    transport._pool = httpcore2.AsyncConnectionPool(
        ssl_context=httpx2.create_ssl_context(verify=verify, trust_env=False),
        network_backend=_GuardedBackend(resolve, allow, inner or httpcore2.AnyIOBackend()),
    )
    return transport


class InvalidResponseError(httpx2.HTTPError):
    """A community backend answered with a redirect, which v1 forbids."""

    error_code = "invalid_response"


class _RejectRedirects(httpx2.AsyncBaseTransport):
    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise InvalidResponseError(f"community backend answered {response.status_code}")
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def community_httpx_client_factory(transport: httpx2.AsyncBaseTransport | None = None) -> Any:
    """Client factory for community backends: own guarded pool, no redirects, no proxies."""

    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs["follow_redirects"] = False
        kwargs["trust_env"] = False
        # Forced, not a default: fastmcp passes its own 30 s connect timeout (§6: 3 s).
        kwargs["timeout"] = httpx2.Timeout(12.0, connect=3.0)
        return httpx2.AsyncClient(
            transport=_RejectRedirects(transport or community_transport()), **kwargs
        )

    factory.community = True  # type: ignore[attr-defined]
    return factory
