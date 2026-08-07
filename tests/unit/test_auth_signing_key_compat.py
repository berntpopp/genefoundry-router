"""Upgrade compatibility for FastMCP's historical secret-derived signing key."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import settings as fastmcp_settings
from fastmcp.server.auth import OAuthProxy
from mcp.shared.auth import OAuthClientInformationFull

from genefoundry_router.auth import (
    build_auth,
    resolve_oauth_signing_key,
    resolve_refresh_observability_hmac_key,
)
from genefoundry_router.config import RouterSettings


class _StubVerifier:
    required_scopes: list[str] | None = None


def _settings() -> RouterSettings:
    return RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_OAUTH_CLIENT_ID="router",
        GF_OAUTH_CLIENT_SECRET="legacy-client-secret",  # noqa: S106 - fixture
        GF_OAUTH_AUTHORIZE_URL="https://idp.example/authorize",
        GF_OAUTH_TOKEN_URL="https://idp.example/token",  # noqa: S106 - endpoint
        GF_PUBLIC_BASE_URL="https://genefoundry.org",
        GF_JWT_ISSUER="https://idp.example",
        GF_JWT_JWKS_URL="https://idp.example/jwks",
        GF_JWT_AUDIENCE="https://genefoundry.org/mcp",
        GF_REFRESH_OBSERVABILITY_DB="/data/genefoundry/refresh.sqlite3",
    )


def test_ledger_upgrade_preserves_legacy_dcr_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fastmcp_settings, "home", tmp_path)
    legacy = OAuthProxy(
        upstream_authorization_endpoint="https://idp.example/authorize",
        upstream_token_endpoint="https://idp.example/token",  # noqa: S106 - endpoint
        upstream_client_id="router",
        upstream_client_secret="legacy-client-secret",  # noqa: S106 - fixture
        token_verifier=_StubVerifier(),
        base_url="https://genefoundry.org",
        resource_base_url="https://genefoundry.org/mcp",
    )
    client = OAuthClientInformationFull(
        client_id="legacy-dcr-client",
        redirect_uris=["https://connector.example/callback"],
        grant_types=["authorization_code"],
        token_endpoint_auth_method="none",  # noqa: S106 - OAuth method name
    )
    asyncio.run(legacy.register_client(client))

    upgraded = build_auth(_settings()).server
    restored = asyncio.run(upgraded.get_client("legacy-dcr-client"))

    assert upgraded._jwt_signing_key == legacy._jwt_signing_key
    assert restored is not None
    assert restored.client_id == "legacy-dcr-client"


def test_refresh_observability_key_is_stable_and_domain_separated() -> None:
    settings = _settings()

    jwt_key = resolve_oauth_signing_key(settings)
    first = resolve_refresh_observability_hmac_key(settings)
    second = resolve_refresh_observability_hmac_key(settings)

    assert first == second
    assert first != jwt_key
    assert len(first) >= 32
