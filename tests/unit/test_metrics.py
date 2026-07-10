from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import (
    BACKEND_UP,
    register_health,
    register_metrics,
    set_backend_up,
)
from genefoundry_router.registry import BackendDef
from genefoundry_router.runtime_drift import RuntimeDriftGuard


def test_metrics_endpoint_exposes_prometheus_text():
    app = FastAPI()
    register_metrics(app)
    BACKEND_UP.labels(backend="gnomad").set(1)
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_metrics_without_authorization_returns_401_when_token_set():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")  # noqa: S106 - test fixture data
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_metrics_wrong_bearer_token_returns_401():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")  # noqa: S106 - test fixture data
    resp = TestClient(app).get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_metrics_correct_bearer_token_returns_200():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")  # noqa: S106 - test fixture data
    resp = TestClient(app).get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_metrics_public_when_token_is_none():
    app = FastAPI()
    register_metrics(app, token=None)
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_metrics_tolerates_extra_whitespace_in_authorization():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")  # noqa: S106 - test fixture data
    resp = TestClient(app).get("/metrics", headers={"Authorization": "Bearer  scrape-secret"})
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_health_reports_cached_reachability():
    app = FastAPI()
    backends = [BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp")]
    set_backend_up(backends[0], up=True)
    register_health(app, backends)
    body = TestClient(app).get("/health").json()
    assert body["backends"]["reachable"]["gnomad"] is True


def test_health_remains_public_when_metrics_token_set():
    app = FastAPI()
    backends = [BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp")]
    register_metrics(app, token="scrape-secret")  # noqa: S106 - test fixture data
    register_health(app, backends)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "genefoundry"


def test_health_and_metrics_expose_runtime_drift() -> None:
    guard = RuntimeDriftGuard({}, "warn")
    guard.evaluate(
        {"gnomad_new_tool": "digest"},
        phase="startup",
        unreachable=set(),
    )
    app = FastAPI()
    register_health(app, [], drift_guard=guard)
    register_metrics(app)
    client = TestClient(app)

    body = client.get("/health").json()
    metrics = client.get("/metrics").text

    assert body["status"] == "degraded"
    assert body["drift"]["added"] == ["gnomad_new_tool"]
    assert "genefoundry_drift_added 1.0" in metrics
    assert "genefoundry_drift_last_check_timestamp_seconds" in metrics
