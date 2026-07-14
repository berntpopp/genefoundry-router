"""The RFC 9728 protected-resource URI must be the MCP endpoint — exactly once.

FastMCP appends the MCP mount path itself: ``set_mcp_path("/mcp")`` sets
``_resource_url = _get_resource_url("/mcp")`` = ``resource_base_url + "/mcp"``. So
``resource_base_url`` MUST be the ROOT origin (``GF_PUBLIC_BASE_URL``), never the
already-suffixed ``GF_JWT_AUDIENCE``.

Passing the audience produced ``https://genefoundry.org/mcp/mcp`` in production: the PRM
advertised a resource that is not the endpoint, OAuthProxy minted tokens whose audience
the router's own JWTVerifier rejects, and spec-compliant clients dutifully echoed the
doubled URI back in their RFC 8707 ``resource`` param.

The resource URI is the token audience. Nothing else in the suite pins it, which is how
the doubling shipped.
"""

from __future__ import annotations

import pytest

from genefoundry_router.auth import build_auth
from genefoundry_router.config import RouterSettings

ISSUER = "https://auth.genefoundry.example/realms/genefoundry"
PUBLIC_BASE = "https://genefoundry.example"  # ROOT origin — no path
AUDIENCE = "https://genefoundry.example/mcp"  # the MCP endpoint = the resource URI
MCP_PATH = "/mcp"


def _settings(mode: str) -> RouterSettings:
    return RouterSettings(
        _env_file=None,
        GF_AUTH_MODE=mode,
        GF_JWT_ISSUER=ISSUER,
        GF_JWT_JWKS_URL=f"{ISSUER}/protocol/openid-connect/certs",
        GF_JWT_AUDIENCE=AUDIENCE,
        GF_PUBLIC_BASE_URL=PUBLIC_BASE,
        GF_OAUTH_CLIENT_ID="genefoundry-router",
        GF_OAUTH_CLIENT_SECRET="secret",  # noqa: S106 - test fixture, not a real secret
        GF_OAUTH_AUTHORIZE_URL=f"{ISSUER}/protocol/openid-connect/auth",
        GF_OAUTH_TOKEN_URL=f"{ISSUER}/protocol/openid-connect/token",
    )


def _resource_url_of(provider: object) -> str:
    """Resolve the provider's protected-resource URI the way FastMCP's routes do."""
    target = provider
    # MultiAuth (oauth mode) delegates the resource surface to its `server`.
    if not hasattr(target, "set_mcp_path"):
        target = target.server  # type: ignore[attr-defined]
    target.set_mcp_path(MCP_PATH)  # type: ignore[attr-defined]
    return str(target._resource_url)  # type: ignore[attr-defined]


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_resource_uri_is_the_mcp_endpoint(mode: str) -> None:
    """Both modes must advertise the endpoint itself — not a doubled path.

    oauth mode is the deployed one and is what regressed.
    """
    assert _resource_url_of(build_auth(_settings(mode))) == AUDIENCE


@pytest.mark.parametrize("mode", ["jwt", "oauth"])
def test_resource_uri_never_doubles_the_mcp_segment(mode: str) -> None:
    """The specific production defect: https://host/mcp/mcp."""
    assert not _resource_url_of(build_auth(_settings(mode))).endswith("/mcp/mcp")


def test_minted_audience_matches_the_verifiers_expected_audience() -> None:
    """OAuthProxy mints tokens with audience = str(self._resource_url).

    If that diverges from GF_JWT_AUDIENCE — which the JWTVerifier enforces — the router
    issues tokens it will itself reject.
    """
    provider = build_auth(_settings("oauth"))
    assert _resource_url_of(provider) == AUDIENCE


def test_both_modes_agree_on_the_resource_uri() -> None:
    """The modes disagreed: jwt passed the root, oauth passed the audience. One had to
    be wrong, and the wrong one was deployed."""
    assert _resource_url_of(build_auth(_settings("jwt"))) == _resource_url_of(
        build_auth(_settings("oauth"))
    )
