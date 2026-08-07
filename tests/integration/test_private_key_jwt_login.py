"""End-to-end RFC 7523 ``private_key_jwt`` login for a ChatGPT-shaped CIMD client.

WHY THIS FILE EXISTS
--------------------
ChatGPT authenticates to the router as a CIMD client (client_id is the HTTPS URL of a
Client-ID-Metadata-Document) whose ``token_endpoint_auth_method`` is ``private_key_jwt``:
at ``POST /token`` it sends an RSA-signed *client assertion* instead of a secret.  Per
RFC 7523 §3 the assertion's ``aud`` MUST be the token endpoint — and the only token
endpoint a client can know is the one advertised in
``/.well-known/oauth-authorization-server``.

FastMCP 3.4.5 computes the audience it *enforces* differently from the one it
*advertises*::

    advertised (mcp.server.auth.routes.build_metadata):
        AnyHttpUrl(str(base_url).rstrip("/") + "/token")   -> https://host/token
    enforced   (fastmcp/server/auth/oauth_proxy/proxy.py:2061):
        f"{self.base_url}/token"                           -> https://host//token

``base_url`` is a pydantic ``AnyHttpUrl``, which renders a bare origin with a trailing
slash, so with ``GF_PUBLIC_BASE_URL=https://genefoundry.org`` (the production value) the
f-string yields a **doubled slash**.  ChatGPT signs ``aud=https://genefoundry.org/token``,
``CIMDAssertionValidator`` verifies against ``https://genefoundry.org//token``, the
audience check fails -> ``Invalid JWT assertion`` -> HTTP 401 ``invalid_client``.

Claude is unaffected because its CIMD declares ``token_endpoint_auth_method: "none"``: it
never walks the assertion path at all.  ``test_public_client_cimd_login_and_mcp_call``
below is exactly that flow and is the CONTROL for this file — it exercises every stub in
the harness (CIMD fetch, IdP authorize + callback + token exchange, PKCE, router-minted
token, authenticated ``tools/list``) and must stay green.  If the control goes red the
xfails below are no longer attributable to the audience bug.

BRANCH STATE
------------
The fix lives on branch ``fix/private-key-jwt-assertion-audience`` and is expected to
install a shim named ``_install_client_assertion_audience_fix`` in
``genefoundry_router.auth`` (mirroring the existing ``_install_resource_tolerance``
shim, which patches the same FastMCP module for the same class of derivation mismatch).

The two tests that depend on that shim are marked ``xfail(strict=True)`` *conditionally*
on the shim being absent:

* on THIS branch the shim is missing -> the marker applies -> they report XFAIL (visible,
  never silently green) and an accidental pass is an error;
* once the fix lands the condition flips to False, the marker evaporates, and the tests
  must genuinely PASS — no edit to this file required at merge time.

A plain ``skip`` was rejected because it would not notice a regression that makes the
tests pass for the wrong reason, and an unconditional ``xfail(strict=True)`` was rejected
because it would turn the fix branch red on XPASS.

If the fix lands under a DIFFERENT name, the condition stays True, the tests pass anyway,
and ``strict=True`` turns the XPASS into a hard failure — loud, never silent.  Rename
``_AUDIENCE_FIX`` below to match and the file is correct again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import fastmcp
import pytest
import respx
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from httpx import Response
from joserfc import jwk, jwt
from pydantic import AnyHttpUrl

from genefoundry_router import auth as gf_auth
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

# --- the deployment shape that triggers the bug -------------------------------------
# GF_PUBLIC_BASE_URL is a BARE ORIGIN in production (the OAuth endpoints live at the
# apex; only the MCP endpoint is path-scoped).  That is what makes AnyHttpUrl append the
# trailing slash that the f-string then doubles.
PUBLIC_BASE = "https://genefoundry.example"
AUDIENCE = f"{PUBLIC_BASE}/mcp"  # GF_JWT_AUDIENCE == the resource URI
ISSUER = "https://auth.genefoundry.example/realms/genefoundry"
UPSTREAM_AUTHORIZE = f"{ISSUER}/protocol/openid-connect/auth"
UPSTREAM_TOKEN = f"{ISSUER}/protocol/openid-connect/token"
UPSTREAM_JWKS = f"{ISSUER}/protocol/openid-connect/certs"

# --- the ChatGPT client -------------------------------------------------------------
# Modelled on the live document served at https://chatgpt.com/oauth/<id>/client.json.
CHATGPT_CLIENT_ID = "https://chatgpt.example/oauth/abc/client.json"
CHATGPT_REDIRECT_URI = "https://chatgpt.example/connector/oauth/abc"
CHATGPT_JWKS_URI = "https://chatgpt.example/oauth/jwks.json"

# A Claude-shaped public client: same CIMD mechanism, no assertion.
PUBLIC_CLIENT_ID = "https://claude.example/oauth/xyz/client.json"
PUBLIC_CLIENT_REDIRECT_URI = "https://claude.example/api/mcp/auth_callback"

JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# The shim the fix branch is expected to add; see the module docstring.
_AUDIENCE_FIX = getattr(gf_auth, "_install_client_assertion_audience_fix", None)
_NEEDS_FIX = pytest.mark.xfail(
    _AUDIENCE_FIX is None,
    strict=True,
    reason=(
        "FastMCP 3.4.5 enforces a client-assertion audience of f'{base_url}/token', which "
        "doubles the slash for a bare-origin GF_PUBLIC_BASE_URL; the shim "
        "genefoundry_router.auth._install_client_assertion_audience_fix lives on branch "
        "fix/private-key-jwt-assertion-audience and is not on this branch"
    ),
)


# ------------------------------------------------------------------------------------
# JOSE helpers
# ------------------------------------------------------------------------------------
def _keypair(kid: str) -> jwk.RSAKey:
    return jwk.RSAKey.generate_key(2048, parameters={"kid": kid})


def _jwks(key: jwk.RSAKey) -> dict[str, Any]:
    """Public JWKS document for ``key`` (RS256, as both Keycloak and ChatGPT serve)."""
    public = key.as_dict(private=False)
    public["alg"] = "RS256"
    public["use"] = "sig"
    return {"keys": [public]}


def _sign(key: jwk.RSAKey, claims: dict[str, Any]) -> str:
    return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def _client_assertion(key: jwk.RSAKey, *, audience: str, client_id: str) -> str:
    """An RFC 7523 §2.2 client-authentication assertion, as ChatGPT mints it."""
    now = int(time.time())
    return _sign(
        key,
        {
            "iss": client_id,  # RFC 7523 §3: iss == sub == client_id
            "sub": client_id,
            "aud": audience,
            "jti": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + 120,
        },
    )


# ------------------------------------------------------------------------------------
# CIMD documents
# ------------------------------------------------------------------------------------
def _chatgpt_cimd() -> dict[str, Any]:
    return {
        "client_id": CHATGPT_CLIENT_ID,
        "client_uri": "https://chatgpt.example/",
        "redirect_uris": [CHATGPT_REDIRECT_URI],
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "ChatGPT",
        "token_endpoint_auth_signing_alg": "RS256",
        "jwks_uri": CHATGPT_JWKS_URI,
    }


def _public_client_cimd() -> dict[str, Any]:
    return {
        "client_id": PUBLIC_CLIENT_ID,
        "client_uri": "https://claude.example/",
        "redirect_uris": [PUBLIC_CLIENT_REDIRECT_URI],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "Claude",
    }


# ------------------------------------------------------------------------------------
# Harness
# ------------------------------------------------------------------------------------
@dataclass
class OAuthHarness:
    """The running app plus the key material the fake ChatGPT signs with."""

    client: TestClient
    chatgpt_key: jwk.RSAKey
    idp_key: jwk.RSAKey

    def authorization_server_metadata(self) -> dict[str, Any]:
        response = self.client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200, response.text
        return response.json()

    def advertised_token_endpoint(self) -> str:
        """The token endpoint a real client would sign its assertion for.

        Read from the LIVE app, never hardcoded — the whole bug is that the advertised
        value and the enforced value are derived differently.
        """
        return str(self.authorization_server_metadata()["token_endpoint"])

    def upstream_access_token(self) -> str:
        """A Keycloak-shaped access token the router's own JWTVerifier will accept."""
        now = int(time.time())
        return _sign(
            self.idp_key,
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "user-1234",
                "azp": "genefoundry-router",
                "scope": "openid profile email",
                "iat": now,
                "exp": now + 300,
            },
        )

    def authorize(self, *, client_id: str, redirect_uri: str) -> tuple[str, str]:
        """GET /authorize -> follow to the IdP.  Returns (txn_id, code_verifier)."""
        verifier, challenge = _pkce()
        response = self.client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "client-state-abc",
                "resource": AUDIENCE,  # RFC 8707
            },
        )
        assert response.status_code in (302, 307), (
            f"/authorize did not redirect: {response.status_code} {response.text[:400]}"
        )
        location = response.headers["location"]
        assert location.startswith(UPSTREAM_AUTHORIZE), (
            f"/authorize should hand off to the stubbed IdP, got {location!r}"
        )
        upstream = parse_qs(urlparse(location).query)
        assert upstream["code_challenge_method"] == ["S256"], upstream
        return upstream["state"][0], verifier

    def idp_callback(self, txn_id: str, *, redirect_uri: str) -> str:
        """GET /auth/callback with the IdP's code.  Returns the router's client code."""
        response = self.client.get("/auth/callback", params={"code": "idp-code-1", "state": txn_id})
        assert response.status_code == 302, (
            f"/auth/callback failed: {response.status_code} {response.text[:400]}"
        )
        location = response.headers["location"]
        assert location.startswith(redirect_uri), location
        returned = parse_qs(urlparse(location).query)
        assert returned["state"] == ["client-state-abc"]
        return returned["code"][0]

    def token(self, form: dict[str, str]):
        return self.client.post(
            "/token", data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )

    def mcp(self, payload: dict[str, Any], access_token: str | None = None):
        headers = dict(MCP_HEADERS)
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        return self.client.post("/mcp", json=payload, headers=headers)

    def list_tools(self, access_token: str) -> list[dict[str, Any]]:
        init = self.mcp(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "private-key-jwt-e2e", "version": "1.0.0"},
                },
            },
            access_token,
        )
        assert init.status_code == 200, f"initialize: {init.status_code} {init.text[:400]}"
        assert init.json()["result"]["serverInfo"]["name"] == "genefoundry"

        listing = self.mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, access_token)
        assert listing.status_code == 200, f"tools/list: {listing.status_code} {listing.text[:400]}"
        body = listing.json()
        assert "error" not in body, body
        return body["result"]["tools"]


@pytest.fixture
def oauth_harness(tmp_path, monkeypatch, gnomad_fake: FastMCP):
    """The REAL router app in oauth mode, with only the network boundary stubbed.

    Stubbed (and nothing else):

    * the upstream Keycloak ``/token`` exchange and its JWKS  -> respx (plain httpx);
    * the CIMD document fetch and the client's JWKS fetch     -> the two SSRF helpers
      FastMCP routes those two fetches through.

    The SSRF helpers are replaced rather than mocked at the transport layer because
    ``ssrf_safe_fetch*`` resolves the hostname with a real DNS lookup and then connects to
    the pinned IP; there is no way to reach it from a test without either faking DNS or
    replacing the helper.  Replacing the helper keeps every layer ABOVE it real: CIMD
    parsing/validation, JWKS-to-PEM conversion, JWT signature verification, and — the
    point of this file — the assertion's issuer/subject/jti/lifetime/AUDIENCE checks.
    """
    # Keep the OAuthProxy's encrypted client/transaction store out of the user's data dir.
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path)

    # On the fix branch _build_oauth installs the shim itself (as it does for
    # _install_resource_tolerance); calling it here too makes the test independent of WHERE
    # it is wired. Shims in this repo are idempotent by convention. No-op on this branch.
    if _AUDIENCE_FIX is not None:
        _AUDIENCE_FIX()

    idp_key = _keypair("keycloak-1")
    chatgpt_key = _keypair("chatgpt-1")

    cimd_docs = {
        CHATGPT_CLIENT_ID: _chatgpt_cimd(),
        PUBLIC_CLIENT_ID: _public_client_cimd(),
    }

    from fastmcp.server.auth import cimd as cimd_module
    from fastmcp.server.auth import ssrf as ssrf_module
    from fastmcp.server.auth.providers import jwt as jwt_provider

    async def fake_cimd_fetch(url: str, **_: Any) -> ssrf_module.SSRFFetchResponse:
        if url not in cimd_docs:
            raise ssrf_module.SSRFFetchError(f"HTTP 404 fetching {url}")
        return ssrf_module.SSRFFetchResponse(
            content=json.dumps(cimd_docs[url]).encode(),
            status_code=200,
            headers={"content-type": "application/json", "cache-control": "max-age=300"},
        )

    async def fake_validate_url(url: str, require_path: bool = False):
        # The real check would reject the fixture hosts (they do not resolve); the
        # HTTPS-only and path requirements are still asserted here so the fixture cannot
        # smuggle in a URL shape the production check would refuse.
        parsed = urlparse(url)
        assert parsed.scheme == "https", url
        if require_path:
            assert parsed.path not in ("", "/"), url
        return ssrf_module.ValidatedURL(
            original_url=url,
            hostname=parsed.hostname or "",
            port=parsed.port or 443,
            path=parsed.path,
            resolved_ips=["203.0.113.10"],
        )

    async def fake_jwks_fetch(url: str, **_: Any) -> bytes:
        if url != CHATGPT_JWKS_URI:
            raise ssrf_module.SSRFFetchError(f"HTTP 404 fetching {url}")
        return json.dumps(_jwks(chatgpt_key)).encode()

    monkeypatch.setattr(cimd_module, "ssrf_safe_fetch_response", fake_cimd_fetch)
    monkeypatch.setattr(cimd_module, "validate_url", fake_validate_url)
    monkeypatch.setattr(jwt_provider, "ssrf_safe_fetch", fake_jwks_fetch)

    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_PUBLIC_BASE_URL=PUBLIC_BASE,  # bare origin — the production shape
        GF_JWT_ISSUER=ISSUER,
        GF_JWT_JWKS_URL=UPSTREAM_JWKS,
        GF_JWT_AUDIENCE=AUDIENCE,
        GF_OAUTH_CLIENT_ID="genefoundry-router",
        GF_OAUTH_CLIENT_SECRET="upstream-secret",  # noqa: S106 - fixture, not a real secret
        GF_OAUTH_AUTHORIZE_URL=UPSTREAM_AUTHORIZE,
        GF_OAUTH_TOKEN_URL=UPSTREAM_TOKEN,
        GF_OAUTH_JWT_SIGNING_KEY="test-signing-key-not-a-secret",
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})

    harness_holder: dict[str, OAuthHarness] = {}

    def upstream_token_response(_request) -> Response:
        return Response(
            200,
            json={
                "access_token": harness_holder["h"].upstream_access_token(),
                "token_type": "Bearer",
                "expires_in": 300,
                "refresh_token": "upstream-refresh-1",
                "scope": "openid profile email",
            },
        )

    # respx (httpcore-level) does not intercept TestClient's ASGI transport, so the
    # in-process app traffic is untouched while every real outbound httpx call is mocked.
    with respx.mock(assert_all_called=False) as mock:
        mock.get(UPSTREAM_JWKS).mock(return_value=Response(200, json=_jwks(idp_key)))
        mock.post(UPSTREAM_TOKEN).mock(side_effect=upstream_token_response)
        # follow_redirects=False so every hop of the flow is asserted explicitly.
        with TestClient(app, follow_redirects=False) as client:
            harness = OAuthHarness(client=client, chatgpt_key=chatgpt_key, idp_key=idp_key)
            harness_holder["h"] = harness
            yield harness


# ------------------------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------------------------
def test_bare_origin_base_url_is_why_the_two_derivations_disagree() -> None:
    """The mechanism, side by side and with no app involved.

    Green on both branches — this is a pydantic/RFC fact, not a router behaviour.  It is
    here so that when the two tests below xfail, the cause is unambiguous and a future fix
    is not tempted to "solve" it by reformatting ``GF_PUBLIC_BASE_URL``: ``AnyHttpUrl``
    renders a bare origin with a trailing slash no matter how it was written.
    """
    base = AnyHttpUrl(PUBLIC_BASE)

    assert str(base) == f"{PUBLIC_BASE}/"
    # fastmcp/server/auth/oauth_proxy/proxy.py — the audience actually ENFORCED
    assert f"{base}/token" == f"{PUBLIC_BASE}//token"
    # mcp/server/auth/routes.py build_metadata — the endpoint ADVERTISED
    assert str(base).rstrip("/") + "/token" == f"{PUBLIC_BASE}/token"


def test_metadata_advertises_cimd_and_private_key_jwt(oauth_harness: OAuthHarness) -> None:
    """The private_key_jwt path must be reachable at all — otherwise the rest is moot."""
    metadata = oauth_harness.authorization_server_metadata()

    assert metadata["client_id_metadata_document_supported"] is True
    assert "private_key_jwt" in metadata["token_endpoint_auth_methods_supported"]
    assert "S256" in metadata["code_challenge_methods_supported"]
    # The endpoint a client signs for. Single slash — this is what ChatGPT puts in `aud`.
    assert metadata["token_endpoint"] == f"{PUBLIC_BASE}/token"


def test_public_client_cimd_login_and_mcp_call(oauth_harness: OAuthHarness) -> None:
    """CONTROL: the same flow for a Claude-shaped CIMD client (auth method "none").

    Proves the harness — IdP stubs, CIMD fetch, PKCE, the router-minted token and the
    authenticated MCP call — is sound, so the private_key_jwt xfails below can only be
    attributed to the client-assertion audience.
    """
    txn_id, verifier = oauth_harness.authorize(
        client_id=PUBLIC_CLIENT_ID, redirect_uri=PUBLIC_CLIENT_REDIRECT_URI
    )
    code = oauth_harness.idp_callback(txn_id, redirect_uri=PUBLIC_CLIENT_REDIRECT_URI)

    response = oauth_harness.token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": PUBLIC_CLIENT_REDIRECT_URI,
            "client_id": PUBLIC_CLIENT_ID,
            "code_verifier": verifier,
        }
    )
    assert response.status_code == 200, f"{response.status_code} {response.text[:400]}"
    access_token = response.json()["access_token"]

    assert oauth_harness.mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    tools = oauth_harness.list_tools(access_token)
    assert any(tool["name"] == "search_tools" for tool in tools), sorted(
        tool["name"] for tool in tools
    )


@_NEEDS_FIX
def test_chatgpt_private_key_jwt_login_and_mcp_call(oauth_harness: OAuthHarness) -> None:
    """THE test: a ChatGPT-shaped client completes OAuth and then USES the MCP endpoint.

    Every step is asserted: /authorize -> stubbed IdP -> /auth/callback -> /token with a
    real RS256 client assertion whose ``aud`` is read from the live authorization-server
    metadata -> Bearer /mcp initialize + tools/list.
    """
    token_endpoint = oauth_harness.advertised_token_endpoint()

    txn_id, verifier = oauth_harness.authorize(
        client_id=CHATGPT_CLIENT_ID, redirect_uri=CHATGPT_REDIRECT_URI
    )
    code = oauth_harness.idp_callback(txn_id, redirect_uri=CHATGPT_REDIRECT_URI)

    response = oauth_harness.token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CHATGPT_REDIRECT_URI,
            "client_id": CHATGPT_CLIENT_ID,
            "code_verifier": verifier,
            "client_assertion_type": JWT_BEARER,
            "client_assertion": _client_assertion(
                oauth_harness.chatgpt_key,
                audience=token_endpoint,
                client_id=CHATGPT_CLIENT_ID,
            ),
        }
    )
    assert response.status_code == 200, (
        f"private_key_jwt token exchange failed: {response.status_code} "
        f"{response.text[:400]} (assertion aud={token_endpoint!r})"
    )
    body = response.json()
    assert body["token_type"].lower() == "bearer"
    access_token = body["access_token"]
    assert access_token

    # Login is not the deliverable — a usable MCP session is.
    tools = oauth_harness.list_tools(access_token)
    names = {tool["name"] for tool in tools}
    assert "search_tools" in names, sorted(names)
    assert "call_tool" in names, sorted(names)


@_NEEDS_FIX
def test_doubled_slash_assertion_audience_is_rejected(oauth_harness: OAuthHarness) -> None:
    """Pins the DIRECTION of the fix.

    ``https://host//token`` is the audience today's buggy derivation produces.  No client
    can legitimately sign for it (nothing advertises it), so once the fix lands it MUST be
    rejected.  Before the fix this assertion is the only one that works — which is why
    this test fails on this branch.
    """
    txn_id, verifier = oauth_harness.authorize(
        client_id=CHATGPT_CLIENT_ID, redirect_uri=CHATGPT_REDIRECT_URI
    )
    code = oauth_harness.idp_callback(txn_id, redirect_uri=CHATGPT_REDIRECT_URI)

    doubled = f"{PUBLIC_BASE}//token"
    assert doubled != oauth_harness.advertised_token_endpoint()

    response = oauth_harness.token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CHATGPT_REDIRECT_URI,
            "client_id": CHATGPT_CLIENT_ID,
            "code_verifier": verifier,
            "client_assertion_type": JWT_BEARER,
            "client_assertion": _client_assertion(
                oauth_harness.chatgpt_key, audience=doubled, client_id=CHATGPT_CLIENT_ID
            ),
        }
    )
    assert response.status_code == 401, (
        f"an assertion for the non-advertised audience {doubled!r} was accepted "
        f"({response.status_code}) — the doubled-slash derivation is still live"
    )
    assert response.json()["error"] == "invalid_client"
