"""A ``private_key_jwt`` client assertion must be validated against the token endpoint
we ADVERTISE, not against a doubled-slash variant of it.

INCIDENT 2026-08-07. Every ChatGPT connector login to https://genefoundry.org/mcp failed
with "Beim Einrichten der Verbindung ist etwas schiefgegangen". Claude kept working.

ChatGPT's Client ID Metadata Document declares ``token_endpoint_auth_method:
private_key_jwt``, so it authenticates at ``/token`` with an RFC 7523 assertion whose
``aud`` is the token endpoint published in ``/.well-known/oauth-authorization-server``:
``https://genefoundry.org/token``. FastMCP derives the audience it checks against as
``f"{self.base_url}/token"``. ``base_url`` is a pydantic ``AnyHttpUrl``, which normalises a
bare origin to a TRAILING SLASH — so at the root origin (required: the OAuth endpoints live
at root) that yields ``https://genefoundry.org//token``. Every correctly signed assertion
was then rejected as an audience mismatch -> ``Invalid JWT assertion`` -> 401
``invalid_client``.

Claude declares ``token_endpoint_auth_method: none``, never walks the assertion path, and so
was untouched — which is exactly why the outage looked client-specific rather than like the
server-side URL-join bug it is.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.routes import build_metadata
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl

from genefoundry_router.auth import _collapse_duplicate_slashes, build_auth
from genefoundry_router.config import RouterSettings
from genefoundry_router.oauth_proxy import CanonicalOAuthMetadata

ISSUER = "https://auth.genefoundry.example/realms/genefoundry"
PUBLIC_BASE = "https://genefoundry.example"  # ROOT origin — no path. This is the trigger.
AUDIENCE = "https://genefoundry.example/mcp"
ADVERTISED_TOKEN_ENDPOINT = "https://genefoundry.example/token"  # noqa: S105 - a URL
CLIENT_ID = "https://chatgpt.example/oauth/abc/client.json"


def _settings(**overrides: object) -> RouterSettings:
    return RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
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


def _authenticator(token_endpoint_url: str) -> Any:
    from fastmcp.server.auth.auth import PrivateKeyJWTClientAuthenticator

    return PrivateKeyJWTClientAuthenticator(
        provider=object(),  # type: ignore[arg-type]  # only stored, never called here
        cimd_manager=object(),  # type: ignore[arg-type]
        token_endpoint_url=token_endpoint_url,
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # the actual bug, and the no-op case
        ("https://genefoundry.org//token", "https://genefoundry.org/token"),
        ("https://genefoundry.org/token", "https://genefoundry.org/token"),
        ("https://host///token", "https://host/token"),
        # authority forms must survive byte-for-byte
        ("https://genefoundry.org:8443//token", "https://genefoundry.org:8443/token"),
        ("https://user:pw@host//token", "https://user:pw@host/token"),
        ("https://[2001:db8::1]:8443//token", "https://[2001:db8::1]:8443/token"),
        ("http://localhost:8000/token", "http://localhost:8000/token"),
        # a base_url WITH a path (unaffected deployments) still normalises correctly
        ("https://host/api//token", "https://host/api/token"),
        # query/fragment are meaningful — never squeeze them
        (
            "https://host//token?next=https://x.com/a",
            "https://host/token?next=https://x.com/a",
        ),
        ("https://host//token#a//b", "https://host/token#a//b"),
        # percent-encoded slashes are data, not separators
        ("https://host/p%2F%2Fq", "https://host/p%2F%2Fq"),
        # credentials are not ours to rewrite — the reason this parses instead of
        # regex-squeezing the whole string
        (
            "https://user%2Fname:pass@example.test//token",
            "https://user%2Fname:pass@example.test/token",
        ),
        ("ftp://user@example.test//x", "ftp://user@example.test/x"),
        # no scheme, or no path -> untouched
        ("/relative//x", "/relative//x"),
        ("https://host", "https://host"),
    ],
)
def test_collapse_duplicate_slashes(url: str, expected: str) -> None:
    assert _collapse_duplicate_slashes(url) == expected


def test_pydantic_base_url_doubles_the_slash_this_is_the_upstream_bug() -> None:
    """Documents the FastMCP defect the shim compensates for.

    If this test starts failing, pydantic/FastMCP changed the join and the shim in
    ``auth.py`` can probably be deleted — check before removing it.
    """
    base = AnyHttpUrl(PUBLIC_BASE)
    assert str(base) == "https://genefoundry.example/"  # trailing slash added
    assert f"{base}/token" == "https://genefoundry.example//token"  # the bug
    assert f"{base}/token" != ADVERTISED_TOKEN_ENDPOINT


def test_legacy_and_canonical_metadata_advertise_the_same_token_endpoint() -> None:
    """Cached pre-upgrade metadata and canonical metadata keep one assertion audience."""
    legacy = build_metadata(
        AnyHttpUrl(PUBLIC_BASE),
        None,
        ClientRegistrationOptions(),
        RevocationOptions(),
    )
    canonical_payload = legacy.model_dump(mode="json")
    canonical_payload["issuer"] = PUBLIC_BASE
    canonical = CanonicalOAuthMetadata.model_validate(canonical_payload)

    assert str(legacy.token_endpoint) == ADVERTISED_TOKEN_ENDPOINT
    assert canonical.model_dump(mode="json")["token_endpoint"] == ADVERTISED_TOKEN_ENDPOINT


def test_expected_assertion_audience_is_the_advertised_endpoint() -> None:
    """After build_auth(), the authenticator checks the URL clients actually sign."""
    build_auth(_settings())  # installs the shim at oauth-provider build time
    auth = _authenticator(f"{AnyHttpUrl(PUBLIC_BASE)}/token")
    assert auth._token_endpoint_url == ADVERTISED_TOKEN_ENDPOINT


def test_shim_is_idempotent_and_leaves_a_correct_url_untouched() -> None:
    build_auth(_settings())
    build_auth(_settings())  # second install must not double-wrap
    assert _authenticator(ADVERTISED_TOKEN_ENDPOINT)._token_endpoint_url == (
        ADVERTISED_TOKEN_ENDPOINT
    )


def test_correctly_signed_assertion_is_accepted_end_to_end() -> None:
    """The real check: FastMCP's JWTVerifier, wired exactly as cimd.py wires it.

    Signed with the advertised endpoint (what ChatGPT sends), it must verify. Signed with
    the doubled-slash URL it must NOT — that pins the direction of the fix, so a future
    "normalise both sides" change can't silently re-accept the broken form.
    """
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    build_auth(_settings())
    expected_aud = _authenticator(f"{AnyHttpUrl(PUBLIC_BASE)}/token")._token_endpoint_url

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    def assertion(aud: str) -> str:
        now = int(time.time())
        return pyjwt.encode(
            {
                "iss": CLIENT_ID,
                "sub": CLIENT_ID,
                "aud": aud,
                "jti": f"jti-{aud}-{now}",
                "iat": now,
                "exp": now + 300,
            },
            private_pem,
            algorithm="RS256",
        )

    # Same construction as CIMDAssertionValidator.validate_assertion().
    verifier = JWTVerifier(public_key=public_pem, issuer=CLIENT_ID, audience=expected_aud)

    accepted = asyncio.run(verifier.load_access_token(assertion(ADVERTISED_TOKEN_ENDPOINT)))
    assert accepted is not None, (
        "an assertion signed with the advertised token endpoint must authenticate; "
        "rejecting it is the 2026-08-07 ChatGPT outage"
    )

    rejected = asyncio.run(
        verifier.load_access_token(assertion("https://genefoundry.example//token"))
    )
    assert rejected is None
