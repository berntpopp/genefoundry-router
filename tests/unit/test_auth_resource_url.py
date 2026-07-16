"""The OAuthProxy resource URI must be GF_JWT_AUDIENCE and survive the LIVE mount.

This reverts #71 and guards against its return. #71 set ``resource_base_url`` to the ROOT
origin on the theory that FastMCP forms ``_resource_url = resource_base_url + set_mcp_path``
and that the mount path is ``/mcp``. That holds only when you call ``set_mcp_path("/mcp")``
by hand — the trap the previous test fell into. It is NOT how the app runs.

In production the server is mounted with ``server.http_app(path="/mcp")`` inside FastAPI, so
the OAuthProxy's ``set_mcp_path`` receives the sub-app's OWN root — ``""`` — and
``_resource_url == resource_base_url`` verbatim. With the origin, the live ``_resource_url``
was the bare origin: every client sending ``…/mcp`` (per the RFC 9728 PRM) was rejected with
``server_error``, and minted tokens carried ``audience == origin``, which the router's own
``JWTVerifier`` (``GF_JWT_AUDIENCE`` = ``…/mcp``) rejects. That is INCIDENT 2026-07-15 — every
Claude/ChatGPT login to genefoundry.org/mcp broke.

So ``resource_base_url`` MUST be the full audience (``…/mcp``). The one cost is that FastMCP's
PRM derivation, which DOES append the path, can advertise ``…/mcp/mcp``; a doubled segment
that ``_install_resource_tolerance()`` collapses so clients echoing it back still validate.
These tests model the LIVE mount (``set_mcp_path("")``), not the hand-call, so origin-base
fails them.
"""

from __future__ import annotations

from genefoundry_router.auth import build_auth
from genefoundry_router.config import RouterSettings

ISSUER = "https://auth.genefoundry.example/realms/genefoundry"
PUBLIC_BASE = "https://genefoundry.example"  # ROOT origin — no path
AUDIENCE = "https://genefoundry.example/mcp"  # the MCP endpoint = the resource URI

# What server.http_app(path="/mcp") actually hands the mounted sub-app's auth provider:
# its own root, NOT the FastAPI mount prefix. The whole incident lives in this "" vs "/mcp".
LIVE_MOUNT_PATH = ""


def _settings(mode: str, **overrides: object) -> RouterSettings:
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
        **overrides,
    )


def _oauth_proxy(provider: object) -> object:
    """The OAuthProxy inside the oauth-mode MultiAuth (it owns the resource surface)."""
    return getattr(provider, "server", provider)


def _live_resource_url(provider: object) -> str:
    proxy = _oauth_proxy(provider)
    proxy.set_mcp_path(LIVE_MOUNT_PATH)  # type: ignore[attr-defined]
    return str(proxy._resource_url).rstrip("/")  # type: ignore[attr-defined]


def test_live_resource_url_is_the_audience() -> None:
    """The deployed oauth mode: under the real mount, the resource URI is the endpoint.

    Fails on #71's origin-base, which yields the bare origin here.
    """
    assert _live_resource_url(build_auth(_settings("oauth"))) == AUDIENCE


def test_minted_token_audience_matches_the_verifier() -> None:
    """OAuthProxy mints tokens with audience = str(self._resource_url). If that diverges
    from GF_JWT_AUDIENCE — which the JWTVerifier enforces — the router issues tokens it will
    itself reject. Under the live mount they must agree."""
    assert _live_resource_url(build_auth(_settings("oauth"))) == AUDIENCE


def test_router_reference_token_lifetime_is_bounded_and_configurable() -> None:
    """The MCP client receives a router token, not the short-lived Keycloak bearer token.

    FastMCP still validates/refreshes the upstream token, but this bounded reference-token
    lifetime prevents ChatGPT and Claude from needlessly restarting OAuth during normal use.
    """
    default_proxy = _oauth_proxy(build_auth(_settings("oauth")))
    configured_proxy = _oauth_proxy(
        build_auth(_settings("oauth", GF_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS=7_200))
    )

    assert default_proxy._fastmcp_access_token_expiry_seconds == 43_200  # type: ignore[attr-defined]
    assert configured_proxy._fastmcp_access_token_expiry_seconds == 7_200  # type: ignore[attr-defined]


def test_resource_tolerance_patch_is_installed_and_collapses_the_doubled_segment() -> None:
    """Because resource_base_url is the audience, FastMCP's path-appending PRM derivation can
    advertise …/mcp/mcp; the tolerance patch collapses it so spec-compliant clients that echo
    the doubled URI back still validate. #71 deleted this and broke exactly those clients."""
    build_auth(_settings("oauth"))  # installs the patch at oauth-provider build time
    from fastmcp.server.auth.oauth_proxy import proxy as p

    assert getattr(p, "_gf_resource_tolerant", False)
    assert p._normalize_resource_url("https://genefoundry.example/mcp/mcp") == (
        "https://genefoundry.example/mcp"
    )
    # a single, correct segment is left untouched
    assert p._normalize_resource_url("https://genefoundry.example/mcp") == (
        "https://genefoundry.example/mcp"
    )


def test_hand_calling_set_mcp_path_slash_mcp_doubles_which_is_the_trap_71_hit() -> None:
    """Documents WHY the naive test is wrong: calling set_mcp_path('/mcp') by hand appends a
    second segment, which the previous suite read as 'origin-base is correct'. The live mount
    passes '', not '/mcp'. This test exists so the distinction is not lost again."""
    proxy = _oauth_proxy(build_auth(_settings("oauth")))
    proxy.set_mcp_path("/mcp")  # type: ignore[attr-defined]
    assert str(proxy._resource_url).rstrip("/").endswith("/mcp/mcp")  # type: ignore[attr-defined]
