from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app


def _client(gnomad_fake, origins):
    settings = RouterSettings(_env_file=None, GF_ALLOWED_ORIGINS=origins)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    return TestClient(build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake}))


def test_disallowed_origin_blocked_on_mcp(gnomad_fake):
    c = _client(gnomad_fake, ["https://claude.ai"])
    r = c.post("/mcp/", headers={"origin": "https://evil.example"}, json={})
    assert r.status_code == 403


def test_absent_origin_allowed(gnomad_fake):
    c = _client(gnomad_fake, [])
    assert c.get("/health").status_code == 200  # health check sends no Origin
