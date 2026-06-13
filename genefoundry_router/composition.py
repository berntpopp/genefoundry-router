"""Build per-backend proxies and mount them with a namespace."""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.server import create_proxy
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider

from genefoundry_router.registry import BackendDef

log = structlog.get_logger(__name__)

DEFAULT_CACHE_TTL = 300


def build_proxy(backend: BackendDef, target: Any | None = None) -> FastMCP:
    """Create a FastMCP proxy for a backend.

    ``target`` overrides the proxy target (used by tests to inject an in-process
    FastMCP). In production it defaults to ``backend.url``.

    R1.6 — confused-deputy invariant: a bare URL target is auto-wrapped in a plain
    ``ProxyClient`` that uses the router's OWN connection to the backend. The
    router MUST NOT forward the caller's auth token to upstreams. Do NOT pass the
    request's Authorization header into the proxy client. Backends are public/no-auth
    today; if a backend ever needs auth, give the proxy its OWN service credential.
    """
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    return create_proxy(proxy_target, name=f"{backend.name}-proxy")


def _register_via_mount(server: FastMCP, backend: BackendDef, target: Any | None) -> None:
    """Default path: mount(create_proxy(...)) caches metadata at the default 300s TTL."""
    proxy = build_proxy(backend, target=target)
    server.mount(proxy, namespace=backend.namespace)


def _register_via_provider(server: FastMCP, backend: BackendDef, target: Any | None) -> None:
    """Non-default TTL path: register a ProxyProvider honoring backend.cache_ttl.

    Convention notes §2: create_proxy cannot take cache_ttl. add_provider with a
    ProxyProvider(client_factory, cache_ttl=...) honors a per-backend TTL while still
    surfacing tools as ``<namespace>_<tool>``.
    """
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    provider = ProxyProvider(
        client_factory=lambda: ProxyClient(proxy_target),
        cache_ttl=float(backend.cache_ttl),
    )
    server.add_provider(provider, namespace=backend.namespace)


def register_backend(
    server: FastMCP,
    backend: BackendDef,
    proxy_target: Any | None = None,
) -> None:
    """Mount a backend under its namespace, honoring a non-default cache_ttl.

    Tools surface as ``<namespace>_<tool>``. Normalization transforms (Task 15) extend
    this; the async normalization pass runs from the lifespan (Task 23).
    """
    if backend.cache_ttl == DEFAULT_CACHE_TTL:
        _register_via_mount(server, backend, proxy_target)
    else:
        _register_via_provider(server, backend, proxy_target)
    log.info(
        "backend_mounted",
        backend=backend.name,
        namespace=backend.namespace,
        cache_ttl=backend.cache_ttl,
    )
