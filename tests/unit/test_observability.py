from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import configure_logging, register_health
from genefoundry_router.registry import BackendDef


def test_configure_logging_is_idempotent():
    configure_logging("INFO")
    configure_logging("DEBUG")  # must not raise on re-config


def test_health_reports_enabled_backends():
    app = FastAPI()
    backends = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp"),
        BackendDef(name="hgnc", url_env="Y", namespace="hgnc", enabled=False),
    ]
    register_health(app, backends)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["backends"]["enabled"] == 1
    assert "gnomad" in body["backends"]["namespaces"]
    assert "hgnc" not in body["backends"]["namespaces"]
