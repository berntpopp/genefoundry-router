"""The OAuthProxy resource URI must be GF_JWT_AUDIENCE and survive the LIVE mount.

In production the server is mounted with ``server.http_app(path="/mcp")`` inside FastAPI,
which passes the mount path ("/mcp") to FastMCP's sub-app auth provider so
``_resource_url == public_base + "/mcp" == GF_JWT_AUDIENCE``.
Following #235a / #84, PRM metadata and the OAuthProxy resource check share this
exact single-source resource URI without route duplication and without legacy
tolerance monkeypatching.
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


def _alias_count() -> float:
    from genefoundry_router.observability import METRICS_REGISTRY

    value = METRICS_REGISTRY.get_sample_value(
        "genefoundry_oauth_resource_alias_total", {"client_class": "other"}
    )
    return value or 0.0


async def test_only_the_legacy_doubled_resource_is_aliased_to_the_canonical() -> None:
    """ChatGPT apps created while the PRM advertised …/mcp/mcp (<= v0.10.3) still send it.
    Exactly that doubled form is rewritten to the canonical resource before FastMCP's strict
    RFC 8707 check, so the canonical value goes upstream; every other path stays rejected."""
    from urllib.parse import parse_qs, urlsplit

    from fastmcp.server.auth.oauth_proxy import proxy as p
    from fastmcp.server.auth.oauth_proxy.proxy import AuthorizeError
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    assert not getattr(p, "_gf_resource_tolerant", False)  # no FastMCP monkeypatch
    proxy = _oauth_proxy(build_auth(_settings("oauth")))
    proxy.set_mcp_path(LIVE_MOUNT_PATH)  # type: ignore[attr-defined]
    client = OAuthClientInformationFull(
        client_id="test-client",
        client_name="Test Client",
        redirect_uris=[AnyUrl("https://client.example/cb")],
    )

    def params(resource: str) -> AuthorizationParams:
        return AuthorizationParams(
            state="state-123",
            scopes=["mcp"],
            code_challenge="challenge-123",
            redirect_uri_provided_explicitly=True,
            redirect_uri=AnyUrl("https://client.example/cb"),
            resource=resource,
        )

    for resource in (AUDIENCE, f"{AUDIENCE}/mcp", f"{AUDIENCE}/mcp/"):
        before = _alias_count()
        redirect_url = await proxy.authorize(client, params(resource))  # type: ignore[attr-defined]
        assert redirect_url.startswith(f"{ISSUER}/protocol/openid-connect/auth")
        assert parse_qs(urlsplit(redirect_url).query)["resource"] == [AUDIENCE]
        assert _alias_count() == before + (resource != AUDIENCE)

    for resource in (
        f"{PUBLIC_BASE}/other",
        f"{AUDIENCE}/other",
        f"{AUDIENCE}/mcp/mcp",
        "https://evil.example/mcp/mcp",
    ):
        with pytest.raises(AuthorizeError) as exc_info:
            await proxy.authorize(client, params(resource))  # type: ignore[attr-defined]
        assert str(exc_info.value.error) == "invalid_target"
    # The minted audience is the canonical resource regardless of what the client sent.
    assert proxy.jwt_issuer.audience == AUDIENCE  # type: ignore[attr-defined]


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


def test_doubled_resource_authorize_reaches_the_idp_with_the_canonical_resource(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """9 of 11 prod /authorize calls (2026-11-19) carry resource …/mcp/mcp from ChatGPT apps
    created before v0.10.4. Through the live app they must reach the IdP, with the canonical
    resource upstream, instead of an invalid_target ("We couldn't confirm the connection")."""
    from urllib.parse import parse_qs, urlsplit

    app = build_app(_settings("oauth"), [])
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == (
            AUDIENCE
        )
        callback = "https://chatgpt.com/connector_platform_oauth_redirect"
        registered = client.post(
            "/register", json={"redirect_uris": [callback], "token_endpoint_auth_method": "none"}
        )
        response = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": registered.json()["client_id"],
                "redirect_uri": callback,
                "code_challenge": "x" * 43,
                "code_challenge_method": "S256",
                "state": "s",
                "resource": f"{AUDIENCE}/mcp",
            },
            follow_redirects=False,
        )
    assert response.status_code == 302
    location = response.headers["location"]
    query = parse_qs(urlsplit(location).query)
    assert "error" not in query, location
    assert location.startswith(f"{ISSUER}/protocol/openid-connect/auth"), location
    assert query["resource"] == [AUDIENCE]
    assert "Unexpected error" not in caplog.text
