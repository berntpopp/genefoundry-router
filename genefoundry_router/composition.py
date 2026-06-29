"""Build per-backend proxies and mount them with a namespace."""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import FastMCPProxy, ProxyClient, ProxyProvider

from genefoundry_router.registry import BackendDef

log = structlog.get_logger(__name__)

DEFAULT_CACHE_TTL = 300
# Generous default so legitimately slow backends (e.g. spliceai cold ~60s) are not cut
# off, while still bounding an indefinite hang. Overridable via GF_BACKEND_TIMEOUT.
DEFAULT_BACKEND_TIMEOUT = 120.0


def make_proxy_client(target: Any, timeout: float | None = None) -> ProxyClient:
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
    client = ProxyClient(target) if timeout is None else ProxyClient(target, timeout=timeout)
    transport = getattr(client, "transport", None)
    if transport is not None and hasattr(transport, "forward_incoming_headers"):
        transport.forward_incoming_headers = False
    return client


def build_proxy(
    backend: BackendDef, target: Any | None = None, timeout: float | None = None
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
            client_factory=lambda: make_proxy_client(proxy_target, timeout),
            name=f"{backend.name}-proxy",
        )
    # In-process target (tests inject a FastMCP server): no HTTP transport, no forwarding.
    return create_proxy(proxy_target, name=f"{backend.name}-proxy")


def _register_via_mount(
    server: FastMCP, backend: BackendDef, target: Any | None, timeout: float | None
) -> None:
    """Default path: mount(create_proxy(...)) caches metadata at the default 300s TTL."""
    proxy = build_proxy(backend, target=target, timeout=timeout)
    server.mount(proxy, namespace=backend.namespace)


def _register_via_provider(
    server: FastMCP, backend: BackendDef, target: Any | None, timeout: float | None
) -> None:
    """Non-default TTL path: register a ProxyProvider honoring backend.cache_ttl.

    Convention notes §2: create_proxy cannot take cache_ttl. add_provider with a
    ProxyProvider(client_factory, cache_ttl=...) honors a per-backend TTL while still
    surfacing tools as ``<namespace>_<tool>``. The client factory routes through
    ``make_proxy_client`` so caller-token forwarding stays disabled (R1.6).
    """
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    provider = ProxyProvider(
        client_factory=lambda: make_proxy_client(proxy_target, timeout),
        cache_ttl=float(backend.cache_ttl),
    )
    server.add_provider(provider, namespace=backend.namespace)


def register_backend(
    server: FastMCP,
    backend: BackendDef,
    proxy_target: Any | None = None,
    timeout: float | None = None,
) -> None:
    """Mount a backend under its namespace, honoring a non-default cache_ttl.

    Tools surface as ``<namespace>_<tool>``. ``timeout`` bounds upstream calls (R1.6 the
    proxy client also disables caller-token forwarding). Normalization transforms extend
    this; the async normalization pass runs from the lifespan.
    """
    if backend.cache_ttl == DEFAULT_CACHE_TTL:
        _register_via_mount(server, backend, proxy_target, timeout)
    else:
        _register_via_provider(server, backend, proxy_target, timeout)
    log.info(
        "backend_mounted",
        backend=backend.name,
        namespace=backend.namespace,
        cache_ttl=backend.cache_ttl,
    )
