"""Build per-backend proxies and mount them with a namespace."""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.server import create_proxy

from genefoundry_router.registry import BackendDef

log = structlog.get_logger(__name__)


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


def register_backend(
    server: FastMCP,
    backend: BackendDef,
    proxy_target: Any | None = None,
) -> None:
    """Mount a backend's proxy onto ``server`` under ``backend.namespace``.

    Tools surface as ``<namespace>_<tool>``. Normalization transforms (Task 15) and
    cache_ttl handling (Task 14) extend this; the async normalization pass runs from
    the lifespan (Task 23).
    """
    proxy = build_proxy(backend, target=proxy_target)
    server.mount(proxy, namespace=backend.namespace)
    log.info("backend_mounted", backend=backend.name, namespace=backend.namespace)
