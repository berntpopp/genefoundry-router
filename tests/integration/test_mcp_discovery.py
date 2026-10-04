from __future__ import annotations

from fastapi.testclient import TestClient
from fastmcp import FastMCP

from genefoundry_router import __version__
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

ALLOWED_ORIGIN = "https://claude.ai"


def _create_app(auth_mode: str = "none", origins: list[str] | None = None):
    extra = {}
    if auth_mode == "oauth":
        extra = {
            "GF_OAUTH_CLIENT_ID": "client-id",
            "GF_OAUTH_CLIENT_SECRET": "secret",
            "GF_OAUTH_AUTHORIZE_URL": "https://auth.example.com/auth",
            "GF_OAUTH_TOKEN_URL": "https://auth.example.com/token",
            "GF_PUBLIC_BASE_URL": "https://genefoundry.example",
            "GF_JWT_ISSUER": "https://auth.example.com/",
            "GF_JWT_JWKS_URL": "https://auth.example.com/jwks.json",
            "GF_JWT_AUDIENCE": "https://genefoundry.example/mcp",
        }
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_ORIGINS=origins or [ALLOWED_ORIGIN],
        GF_ALLOWED_HOSTS=[],
        GF_AUTH_MODE=auth_mode,
        GF_MCP_PATH="/mcp",
        **extra,
    )
    fake_backend = FastMCP("fake")

    @fake_backend.tool()
    def ping() -> str:
        return "pong"

    registry = [BackendDef(name="fake", url_env="X", namespace="fake")]
    return build_app(settings, registry, proxy_targets={"fake": fake_backend})


def test_mcp_discovery_returns_metadata_and_capabilities() -> None:
    app = _create_app()
    with TestClient(app) as client:
        response = client.get("/mcp")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "genefoundry"
    assert data["version"] == __version__
    assert data["protocol_version"] == "2024-11-05"
    assert "capabilities" in data
    assert "tools" in data["capabilities"]
    assert "endpoints" in data
    assert data["endpoints"]["mcp"] == "/mcp"
    assert data["endpoints"]["health"] == "/health"
    assert data["endpoints"]["metrics"] == "/metrics"
    assert data["endpoints"]["provenance"] == "/provenance"
    assert "docs" in data


def test_mcp_discovery_includes_oauth_resource_metadata_when_auth_enabled() -> None:
    app = _create_app(auth_mode="oauth")
    with TestClient(app) as client:
        response = client.get("/mcp")

    assert response.status_code == 200
    data = response.json()
    assert "oauth_protected_resource" in data["endpoints"]
    assert (
        data["endpoints"]["oauth_protected_resource"] == "/.well-known/oauth-protected-resource/mcp"
    )


def test_mcp_discovery_carries_cors_header_for_allowed_origin() -> None:
    app = _create_app(origins=[ALLOWED_ORIGIN])
    with TestClient(app) as client:
        response = client.get("/mcp", headers={"origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert "Origin" in response.headers.get("vary", "")


def test_mcp_discovery_rejects_disallowed_origin() -> None:
    app = _create_app(origins=[ALLOWED_ORIGIN])
    with TestClient(app) as client:
        response = client.get("/mcp", headers={"origin": "https://attacker.example"})

    assert response.status_code == 403
