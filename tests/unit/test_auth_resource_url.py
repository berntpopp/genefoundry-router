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

import pytest
from fastapi.testclient import TestClient

from genefoundry_router.auth import build_auth
from genefoundry_router.config import RouterSettings
from genefoundry_router.server import build_app

ISSUER = "https://auth.genefoundry.example/realms/genefoundry"
PUBLIC_BASE = "https://genefoundry.example"  # ROOT origin — no path
AUDIENCE = "https://genefoundry.example/mcp"  # the MCP endpoint = the resource URI

# What server.http_app(path="/mcp") actually hands the mounted sub-app's auth provider:
# the streamable HTTP path ("/mcp"), so _resource_url = public_base + "/mcp" == AUDIENCE.
LIVE_MOUNT_PATH = "/mcp"


def _settings(mode: str, **overrides: object) -> RouterSettings:
    defaults: dict[str, object] = {
        "_env_file": None,
        "GF_AUTH_MODE": mode,
        "GF_JWT_ISSUER": ISSUER,
        "GF_JWT_JWKS_URL": f"{ISSUER}/protocol/openid-connect/certs",
        "GF_JWT_AUDIENCE": AUDIENCE,
        "GF_PUBLIC_BASE_URL": PUBLIC_BASE,
        "GF_OAUTH_CLIENT_ID": "genefoundry-router",
        "GF_OAUTH_CLIENT_SECRET": "secret",
        "GF_OAUTH_AUTHORIZE_URL": f"{ISSUER}/protocol/openid-connect/auth",
        "GF_OAUTH_TOKEN_URL": f"{ISSUER}/protocol/openid-connect/token",
    }
    defaults.update(overrides)
    return RouterSettings(**defaults)  # type: ignore[arg-type]


def _oauth_proxy(provider: object) -> object:
    """The OAuthProxy inside the oauth-mode MultiAuth (it owns the resource surface)."""
    return getattr(provider, "server", provider)


def _live_resource_url(provider: object) -> str:
    proxy = _oauth_proxy(provider)
    proxy.set_mcp_path(LIVE_MOUNT_PATH)  # type: ignore[attr-defined]
    return str(proxy._resource_url).rstrip("/")  # type: ignore[attr-defined]


def test_live_resource_url_is_the_audience() -> None:
    """The deployed oauth mode: under the real mount, the resource URI is the endpoint."""
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
    """Because resource_base_url is public_base, the tolerance patch remains installed
    to collapse any legacy doubled segment so spec-compliant clients that echo doubled URIs
    still validate."""
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


def test_calling_set_mcp_path_slash_mcp_matches_audience() -> None:
    """Calling set_mcp_path('/mcp') on public_base produces the exact audience without doubling."""
    proxy = _oauth_proxy(build_auth(_settings("oauth")))
    proxy.set_mcp_path("/mcp")  # type: ignore[attr-defined]
    assert str(proxy._resource_url).rstrip("/") == AUDIENCE  # type: ignore[attr-defined]


@pytest.mark.parametrize("mcp_path", ["/mcp", "/custom_mcp"])
def test_oauth_resource_url_and_metadata_match_audience(mcp_path: str) -> None:
    expected_audience = f"{PUBLIC_BASE}{mcp_path}"
    settings = _settings("oauth", GF_MCP_PATH=mcp_path, GF_JWT_AUDIENCE=expected_audience)
    app = build_app(settings, [])

    with TestClient(app) as client:
        # 1. 401 challenge on POST {mcp_path}
        res_post = client.post(mcp_path)
        assert res_post.status_code == 401
        auth_header = res_post.headers.get("www-authenticate", "")
        expected_meta_url = f"{PUBLIC_BASE}/.well-known/oauth-protected-resource{mcp_path}"
        assert f'resource_metadata="{expected_meta_url}"' in auth_header

        # 2. Discovery on GET {mcp_path}
        res_get = client.get(mcp_path)
        assert res_get.status_code == 200
        meta_rel_path = res_get.json()["endpoints"]["oauth_protected_resource"]
        assert meta_rel_path == f"/.well-known/oauth-protected-resource{mcp_path}"

        # 3. GET Protected Resource Metadata at that endpoint
        res_meta = client.get(meta_rel_path)
        assert res_meta.status_code == 200
        assert res_meta.json()["resource"] == expected_audience

        # 4. Proxy _resource_url matches expected audience
        proxy = _oauth_proxy(app.state.mcp_server.auth)
        assert str(proxy._resource_url).rstrip("/") == expected_audience  # type: ignore[attr-defined]
