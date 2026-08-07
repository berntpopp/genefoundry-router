import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import (
    BACKEND_UP,
    METRICS_REGISTRY,
    OAUTH_REFRESH_ATTEMPTS,
    OAUTH_REFRESH_FAILURES,
    OAUTH_REFRESH_SUCCESS,
    record_refresh_metrics,
    register_health,
    register_metrics,
    restore_refresh_metrics,
    set_backend_up,
)
from genefoundry_router.refresh_observability import CounterSnapshot
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


def test_refresh_metric_names_and_label_sets_are_exact_and_bounded() -> None:
    assert OAUTH_REFRESH_ATTEMPTS._labelnames == ("client_class",)
    assert OAUTH_REFRESH_SUCCESS._labelnames == ("client_class",)
    assert OAUTH_REFRESH_FAILURES._labelnames == ("client_class", "reason")

    before_attempts = OAUTH_REFRESH_ATTEMPTS.labels(client_class="chatgpt")._value.get()
    before_failure = OAUTH_REFRESH_FAILURES.labels(
        client_class="chatgpt", reason="reuse_after_rotation"
    )._value.get()
    record_refresh_metrics("chatgpt", "failure", "reuse_after_rotation")
    assert OAUTH_REFRESH_ATTEMPTS.labels(client_class="chatgpt")._value.get() == (
        before_attempts + 1
    )
    assert (
        OAUTH_REFRESH_FAILURES.labels(
            client_class="chatgpt", reason="reuse_after_rotation"
        )._value.get()
        == before_failure + 1
    )

    with pytest.raises(ValueError):
        record_refresh_metrics("https://raw-client.example", "failure", "internal_error")
    with pytest.raises(ValueError):
        record_refresh_metrics("other", "failure", "raw upstream exception")


def test_refresh_metric_restore_is_idempotent_per_durable_source() -> None:
    source = "test-refresh-ledger-idempotence"
    snapshot = CounterSnapshot(
        attempts={"claude": 7},
        successes={"claude": 5},
        failures={("claude", "mapping_missing"): 2},
    )
    before_attempts = OAUTH_REFRESH_ATTEMPTS.labels(client_class="claude")._value.get()
    before_success = OAUTH_REFRESH_SUCCESS.labels(client_class="claude")._value.get()
    before_failure = OAUTH_REFRESH_FAILURES.labels(
        client_class="claude", reason="mapping_missing"
    )._value.get()

    restore_refresh_metrics(snapshot, source_id=source)
    restore_refresh_metrics(snapshot, source_id=source)

    assert OAUTH_REFRESH_ATTEMPTS.labels(client_class="claude")._value.get() == (
        before_attempts + 7
    )
    assert OAUTH_REFRESH_SUCCESS.labels(client_class="claude")._value.get() == (before_success + 5)
    assert (
        OAUTH_REFRESH_FAILURES.labels(client_class="claude", reason="mapping_missing")._value.get()
        == before_failure + 2
    )


def test_protected_metrics_endpoint_exposes_refresh_series_without_raw_labels() -> None:
    app = FastAPI()
    register_metrics(app, token="scrape-secret")  # noqa: S106 - fixture only
    record_refresh_metrics("other", "success")
    response = TestClient(app).get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert response.status_code == 200
    assert 'genefoundry_oauth_refresh_attempts_total{client_class="other"}' in response.text
    assert 'genefoundry_oauth_refresh_success_total{client_class="other"}' in response.text
    assert "raw-client" not in response.text
    assert METRICS_REGISTRY is not None
