"""R1.6 — the router MUST NOT forward the caller's auth token to backends.

fastmcp's ``ProxyClient`` enables ``forward_incoming_headers`` by default, and its HTTP
transport explicitly re-includes ``authorization`` (which ``get_http_headers`` would
otherwise strip). Left unchecked, the router would forward the caller's bearer token to
every backend — a token-passthrough / confused-deputy violation (MCP spec MUST NOT).
The router disables forwarding on every proxy client it builds.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import FastMCP

from genefoundry_router.composition import (
    CachedProxyProvider,
    _BorrowedTransport,
    close_shared_upstream_transport,
    default_httpx_client_factory,
    get_shared_upstream_transport,
    make_proxy_client,
    register_backend,
)
from genefoundry_router.registry import BackendDef


def test_make_proxy_client_disables_header_forwarding() -> None:
    client = make_proxy_client("https://backend.example.org/mcp")
    assert client.transport.forward_incoming_headers is False


def test_backend_service_token_is_injected_without_caller_forwarding() -> None:
    client = make_proxy_client(
        "https://backend.example.org/mcp",
        service_token="router-owned-secret",  # noqa: S106 - inert test value
    )
    assert client.transport.headers["Authorization"] == "Bearer router-owned-secret"
    assert client.transport.forward_incoming_headers is False


def test_blank_backend_service_token_is_not_injected() -> None:
    client = make_proxy_client("https://backend.example.org/mcp", service_token="")
    assert "Authorization" not in client.transport.headers
    assert client.transport.forward_incoming_headers is False


def test_register_backend_provider_does_not_forward_caller_headers() -> None:
    server = FastMCP("test-server")
    backend = BackendDef(
        name="gnomad", namespace="gnomad", url_env="X", url="https://backend.example.org/mcp"
    )
    register_backend(server, backend)
    providers = [getattr(p, "_inner", p) for p in server.providers]
    provider = next(p for p in providers if isinstance(p, CachedProxyProvider))
    client = provider.client_factory()
    assert client.transport.forward_incoming_headers is False


@pytest.mark.asyncio
async def test_borrowed_transport_aclose_does_not_close_pool() -> None:
    mock_pool = MagicMock(spec=httpx.AsyncHTTPTransport)
    mock_pool.aclose = AsyncMock()

    borrowed = _BorrowedTransport(mock_pool)
    await borrowed.aclose()

    # aclose on borrowed transport is a no-op; pool is not closed
    mock_pool.aclose.assert_not_called()


@pytest.mark.asyncio
async def test_close_shared_upstream_transport_closes_pool() -> None:
    pool = get_shared_upstream_transport()
    assert pool is not None

    await close_shared_upstream_transport()
    # Next get creates a fresh pool
    pool2 = get_shared_upstream_transport()
    assert pool2 is not pool

    await close_shared_upstream_transport()


def test_default_httpx_client_factory_sets_redirects_false() -> None:
    factory = default_httpx_client_factory()
    # FastMCP supplies follow_redirects=True; factory must override to False without TypeError
    client = factory(headers={}, follow_redirects=True)
    assert client.follow_redirects is False
    assert client.trust_env is False
