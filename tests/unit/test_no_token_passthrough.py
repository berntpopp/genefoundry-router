"""R1.6 — the router MUST NOT forward the caller's auth token to backends.

fastmcp's ``ProxyClient`` enables ``forward_incoming_headers`` by default, and its HTTP
transport explicitly re-includes ``authorization`` (which ``get_http_headers`` would
otherwise strip). Left unchecked, the router would forward the caller's bearer token to
every backend — a token-passthrough / confused-deputy violation (MCP spec MUST NOT).
The router disables forwarding on every proxy client it builds.
"""

from genefoundry_router.composition import build_proxy, make_proxy_client
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


def test_build_proxy_does_not_forward_caller_headers() -> None:
    backend = BackendDef(
        name="gnomad", namespace="gnomad", url_env="X", url="https://backend.example.org/mcp"
    )
    proxy = build_proxy(backend)
    # The per-request client the proxy hands out must not forward incoming headers.
    client = proxy.client_factory()
    assert client.transport.forward_incoming_headers is False
