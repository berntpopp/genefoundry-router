"""Canonical OAuth issuer and bounded legacy-token compatibility."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp.server.auth.jwt_issuer import JWTIssuer
from joserfc.errors import JoseError
from starlette.applications import Starlette

from genefoundry_router.auth import build_auth
from genefoundry_router.config import RouterSettings
from genefoundry_router.oauth_proxy import (
    CanonicalOAuthMetadata,
    CanonicalProtectedResourceMetadata,
    GeneFoundryOAuthProxy,
    TransitionalJWTIssuer,
    canonical_issuer,
)

BASE_URL = "https://genefoundry.org"
LEGACY_ISSUER = f"{BASE_URL}/"
AUDIENCE = f"{BASE_URL}/mcp"
DEADLINE = datetime(2026, 9, 6, tzinfo=UTC)
BEFORE_DEADLINE = DEADLINE - timedelta(seconds=1)
AFTER_DEADLINE = DEADLINE + timedelta(seconds=1)
SIGNING_KEY = b"test-key-with-at-least-32-bytes!!"

INVALID_LEGACY_ISSUER_LISTS = [
    pytest.param([BASE_URL], id="canonical-without-slash"),
    pytest.param(["https://attacker.example/"], id="alternate-origin"),
    pytest.param([f"{BASE_URL}/legacy"], id="path"),
    pytest.param([f"{BASE_URL}/?source=legacy"], id="query"),
    pytest.param([f"{BASE_URL}/#legacy"], id="fragment"),
    pytest.param([f"{BASE_URL}//"], id="extra-slash"),
    pytest.param([LEGACY_ISSUER, "https://attacker.example/"], id="extra-value"),
    pytest.param([LEGACY_ISSUER, LEGACY_ISSUER], id="duplicate-alias"),
]


@pytest.mark.parametrize("base_url", [BASE_URL, LEGACY_ISSUER])
def test_canonical_issuer_is_slashless_for_bare_origin(base_url: str) -> None:
    assert canonical_issuer(base_url) == BASE_URL


def test_canonical_metadata_models_preserve_an_empty_url_path() -> None:
    authorization = CanonicalOAuthMetadata(
        issuer=BASE_URL,
        authorization_endpoint=f"{BASE_URL}/authorize",
        token_endpoint=f"{BASE_URL}/token",
    )
    protected = CanonicalProtectedResourceMetadata(
        resource=AUDIENCE,
        authorization_servers=[BASE_URL],
    )

    assert authorization.model_dump(mode="json")["issuer"] == BASE_URL
    assert protected.model_dump(mode="json")["authorization_servers"] == [BASE_URL]


def _issuer(
    *,
    now: datetime = BEFORE_DEADLINE,
    legacy_issuers: tuple[str, ...] = (LEGACY_ISSUER,),
) -> TransitionalJWTIssuer:
    return TransitionalJWTIssuer(
        issuer=BASE_URL,
        audience=AUDIENCE,
        signing_key=SIGNING_KEY,
        legacy_issuers=legacy_issuers,
        legacy_accept_until=DEADLINE,
        clock=lambda: now,
    )


def _access_token(
    *,
    issuer: str = LEGACY_ISSUER,
    audience: str = AUDIENCE,
    signing_key: bytes = SIGNING_KEY,
) -> str:
    return JWTIssuer(
        issuer=issuer,
        audience=audience,
        signing_key=signing_key,
    ).issue_access_token(client_id="client", scopes=["openid"], jti="access-jti")


def _refresh_token(
    *,
    issuer: str = LEGACY_ISSUER,
    audience: str = AUDIENCE,
    signing_key: bytes = SIGNING_KEY,
) -> str:
    return JWTIssuer(
        issuer=issuer,
        audience=audience,
        signing_key=signing_key,
    ).issue_refresh_token(
        client_id="client",
        scopes=["openid"],
        jti="refresh-jti",
        expires_in=3600,
    )


def test_new_access_and_refresh_tokens_issue_only_the_canonical_issuer() -> None:
    issuer = _issuer()

    access = issuer.issue_access_token(client_id="client", scopes=["openid"], jti="access")
    refresh = issuer.issue_refresh_token(
        client_id="client", scopes=["openid"], jti="refresh", expires_in=3600
    )

    assert issuer.verify_token(access)["iss"] == BASE_URL
    assert issuer.verify_token(refresh, expected_token_use="refresh")["iss"] == BASE_URL  # noqa: S106


def test_trailing_slash_access_and_refresh_tokens_validate_before_deadline() -> None:
    issuer = _issuer()

    assert issuer.verify_token(_access_token())["iss"] == LEGACY_ISSUER
    assert (
        issuer.verify_token(_refresh_token(), expected_token_use="refresh")[  # noqa: S106
            "iss"
        ]
        == LEGACY_ISSUER
    )


@pytest.mark.parametrize(
    ("token", "expected_token_use"),
    [(_access_token(), "access"), (_refresh_token(), "refresh")],
)
def test_trailing_slash_legacy_tokens_are_rejected_after_deadline(
    token: str, expected_token_use: str
) -> None:
    with pytest.raises(JoseError):
        _issuer(now=AFTER_DEADLINE).verify_token(token, expected_token_use=expected_token_use)


def test_only_explicitly_configured_legacy_issuer_is_accepted() -> None:
    with pytest.raises(JoseError):
        _issuer(legacy_issuers=()).verify_token(_access_token())


@pytest.mark.parametrize("legacy_issuers", INVALID_LEGACY_ISSUER_LISTS)
def test_transitional_issuer_rejects_non_alias_legacy_values(
    legacy_issuers: list[str],
) -> None:
    with pytest.raises(ValueError, match="legacy issuers must be empty or exactly"):
        _issuer(legacy_issuers=tuple(legacy_issuers))


@pytest.mark.parametrize("legacy_issuers", INVALID_LEGACY_ISSUER_LISTS)
def test_settings_reject_non_alias_legacy_values(legacy_issuers: list[str]) -> None:
    with pytest.raises(ValueError, match="legacy issuers must be empty or exactly"):
        RouterSettings(_env_file=None, GF_OAUTH_LEGACY_ISSUERS=legacy_issuers)


def test_settings_allow_an_empty_legacy_issuer_list() -> None:
    settings = RouterSettings(_env_file=None, GF_OAUTH_LEGACY_ISSUERS=[])

    assert settings.GF_OAUTH_LEGACY_ISSUERS == []


@pytest.mark.parametrize(
    ("token", "expected_token_use"),
    [
        (_access_token(audience=f"{BASE_URL}/other"), "access"),
        (_refresh_token(), "access"),
        (_access_token(signing_key=b"different-signing-key-32-bytes!!!"), "access"),
    ],
)
def test_transition_preserves_audience_token_use_and_signature_checks(
    token: str, expected_token_use: str
) -> None:
    with pytest.raises(JoseError):
        _issuer().verify_token(token, expected_token_use=expected_token_use)


def _proxy(**overrides: Any) -> GeneFoundryOAuthProxy:
    class StubVerifier:
        required_scopes: list[str] | None = None

    kwargs: dict[str, Any] = {
        "upstream_authorization_endpoint": "https://idp.example/authorize",
        "upstream_token_endpoint": "https://idp.example/token",
        "upstream_client_id": "router",
        "upstream_client_secret": "test-secret",
        "token_verifier": StubVerifier(),
        "base_url": BASE_URL,
        "resource_base_url": AUDIENCE,
        "jwt_signing_key": SIGNING_KEY,
        "canonical_issuer_url": BASE_URL,
        "legacy_issuer_urls": [LEGACY_ISSUER],
        "legacy_issuer_accept_until": DEADLINE,
    }
    kwargs.update(overrides)
    return GeneFoundryOAuthProxy(**kwargs)


def test_proxy_installs_transitional_issuer_with_same_audience_and_signing_key() -> None:
    proxy = _proxy()

    proxy.set_mcp_path("")

    assert isinstance(proxy._jwt_issuer, TransitionalJWTIssuer)
    assert proxy._jwt_issuer.issuer == BASE_URL
    assert proxy._jwt_issuer.audience == AUDIENCE
    token = proxy._jwt_issuer.issue_access_token(
        client_id="client", scopes=["openid"], jti="access"
    )
    assert proxy._jwt_issuer.verify_token(token)["iss"] == BASE_URL


def test_proxy_metadata_uses_byte_identical_canonical_issuer() -> None:
    client = TestClient(Starlette(routes=_proxy().get_routes("")))

    authorization = client.get("/.well-known/oauth-authorization-server")
    protected = client.get("/.well-known/oauth-protected-resource/mcp")

    assert authorization.status_code == 200
    assert protected.status_code == 200
    assert authorization.json()["issuer"] == BASE_URL
    assert protected.json()["authorization_servers"] == [BASE_URL]
    assert authorization.json()["issuer"] == protected.json()["authorization_servers"][0]
    assert authorization.json()["token_endpoint"] == f"{BASE_URL}/token"
    assert authorization.json()["registration_endpoint"] == f"{BASE_URL}/register"


def test_proxy_metadata_does_not_advertise_cimd_when_cimd_is_disabled() -> None:
    client = TestClient(Starlette(routes=_proxy(enable_cimd=False).get_routes("")))

    metadata = client.get("/.well-known/oauth-authorization-server").json()

    assert metadata.get("client_id_metadata_document_supported") is None
    assert metadata["token_endpoint_auth_methods_supported"] == [
        "client_secret_post",
        "client_secret_basic",
    ]


def test_build_auth_uses_the_router_owned_proxy() -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_PUBLIC_BASE_URL=BASE_URL,
        GF_JWT_ISSUER="https://idp.example/realms/genefoundry",
        GF_JWT_JWKS_URL="https://idp.example/realms/genefoundry/certs",
        GF_JWT_AUDIENCE=AUDIENCE,
        GF_OAUTH_CLIENT_ID="router",
        GF_OAUTH_CLIENT_SECRET="test-secret",  # noqa: S106 - fixture only
        GF_OAUTH_AUTHORIZE_URL="https://idp.example/authorize",
        GF_OAUTH_TOKEN_URL="https://idp.example/token",  # noqa: S106 - URL, not a secret
        GF_OAUTH_CANONICAL_ISSUER=BASE_URL,
        GF_OAUTH_LEGACY_ISSUERS=[LEGACY_ISSUER],
        GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL=DEADLINE,
    )

    provider = build_auth(settings)

    assert isinstance(provider.server, GeneFoundryOAuthProxy)


def test_settings_pin_the_release_transition_instead_of_sliding_on_restart() -> None:
    settings = RouterSettings(_env_file=None)

    assert settings.GF_OAUTH_CANONICAL_ISSUER == BASE_URL
    assert settings.GF_OAUTH_LEGACY_ISSUERS == [LEGACY_ISSUER]
    assert settings.GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL == DEADLINE
