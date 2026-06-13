import pytest
from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app


def _jwt_app(gnomad_fake):
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="jwt",
        GF_JWT_ISSUER="https://idp.example.org/",
        GF_JWT_JWKS_URL="https://idp.example.org/.well-known/jwks.json",
        GF_JWT_AUDIENCE="https://genefoundry.example.org/mcp",
        GF_PUBLIC_BASE_URL="https://genefoundry.example.org/mcp",
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    return TestClient(build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake}))


def test_unauthenticated_mcp_returns_401_with_www_authenticate(gnomad_fake):
    with _jwt_app(gnomad_fake) as c:
        r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "www-authenticate" in {k.lower() for k in r.headers}


def test_protected_resource_metadata_served(gnomad_fake):
    with _jwt_app(gnomad_fake) as c:
        # RFC 9728 well-known (root or path-suffixed form); accept either
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/mcp",
        ):
            r = c.get(path)
            if r.status_code == 200 and "authorization_servers" in r.json():
                return
    pytest.fail("no Protected Resource Metadata document served")
