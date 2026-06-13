import pytest

from genefoundry_router.auth import build_auth
from genefoundry_router.config import RouterSettings
from genefoundry_router.exceptions import ConfigurationError


def test_none_mode_returns_none():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    assert build_auth(s) is None


def test_jwt_mode_requires_issuer_jwks_audience():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="jwt")  # no issuer/jwks/audience
    with pytest.raises(ConfigurationError):
        build_auth(s)


def test_oauth_mode_requires_config():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="oauth")
    with pytest.raises(ConfigurationError):
        build_auth(s)


def test_oauth_without_jwt_verifier_is_rejected():
    # R1.5: OAuthProxy.token_verifier is REQUIRED — never construct it with None.
    s = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_OAUTH_CLIENT_ID="id",
        GF_OAUTH_CLIENT_SECRET="secret",
        GF_OAUTH_AUTHORIZE_URL="https://idp/authorize",
        GF_OAUTH_TOKEN_URL="https://idp/token",
        GF_PUBLIC_BASE_URL="https://genefoundry.example.org/mcp",
        # deliberately omit GF_JWT_JWKS_URL/ISSUER/AUDIENCE
    )
    with pytest.raises(ConfigurationError):
        build_auth(s)
