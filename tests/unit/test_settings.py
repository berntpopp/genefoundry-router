import pytest
from pydantic import ValidationError

from genefoundry_router.config import RouterSettings


def test_defaults(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("GF_"):
            monkeypatch.delenv(k, raising=False)
    s = RouterSettings(_env_file=None)
    assert s.GF_AUTH_MODE == "none"
    assert s.GF_PORT == 8000
    assert s.GF_HOST == "127.0.0.1"
    assert s.GF_MCP_PATH == "/mcp"
    assert s.GF_SERVERS_FILE == "servers.yaml"
    assert s.GF_SEARCH_MAX_RESULTS == 5
    assert s.GF_POLL_INTERVAL == 0
    assert s.GF_LOG_LEVEL == "INFO"
    assert s.GF_ALLOWED_ORIGINS == []  # R1.4 - empty = reject any present Origin
    assert s.GF_ALLOWED_HOSTS == []
    assert s.GF_PUBLIC_BASE_URL is None  # R1.5 - public URL for OAuth metadata
    assert s.GF_TRUSTED_PROXY_HOPS == 1
    assert s.GF_METRICS_TOKEN is None


def test_allowed_origins_parses_csv(monkeypatch):
    monkeypatch.setenv("GF_ALLOWED_ORIGINS", "https://claude.ai, https://cursor.sh")
    s = RouterSettings(_env_file=None)
    assert s.GF_ALLOWED_ORIGINS == ["https://claude.ai", "https://cursor.sh"]


def test_allowed_hosts_csv_is_split() -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_HOSTS="genefoundry.org,localhost,127.0.0.1,::1",
    )
    assert settings.GF_ALLOWED_HOSTS == ["genefoundry.org", "localhost", "127.0.0.1", "::1"]


def test_allowed_hosts_rejects_wildcard() -> None:
    with pytest.raises(ValidationError, match="GF_ALLOWED_HOSTS must not contain wildcard"):
        RouterSettings(_env_file=None, GF_ALLOWED_HOSTS="*")


def test_env_override(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.setenv("GF_PORT", "9001")
    monkeypatch.setenv("GF_TRUSTED_PROXY_HOPS", "2")
    monkeypatch.setenv("GF_METRICS_TOKEN", "scrape-secret")
    s = RouterSettings(_env_file=None)
    assert s.GF_AUTH_MODE == "jwt"
    assert s.GF_PORT == 9001
    assert s.GF_TRUSTED_PROXY_HOPS == 2
    assert s.GF_METRICS_TOKEN == "scrape-secret"  # noqa: S105 - test fixture data


def test_metrics_token_blank_normalizes_to_none(monkeypatch):
    monkeypatch.setenv("GF_METRICS_TOKEN", "   ")
    s = RouterSettings(_env_file=None)
    assert s.GF_METRICS_TOKEN is None


def test_production_rejects_development_unsafe_observability_acknowledgement(monkeypatch):
    monkeypatch.setenv("GF_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY", "true")

    with pytest.raises(ValidationError, match="development mode"):
        RouterSettings(_env_file=None)


def test_blank_drift_baseline_uses_packaged_default(monkeypatch) -> None:
    monkeypatch.setenv("GF_DRIFT_BASELINE", "   ")
    assert RouterSettings(_env_file=None).GF_DRIFT_BASELINE is None


def test_invalid_auth_mode_rejected(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "bogus")
    with pytest.raises(ValidationError):
        RouterSettings(_env_file=None)


def test_negative_trusted_proxy_hops_rejected(monkeypatch):
    monkeypatch.setenv("GF_TRUSTED_PROXY_HOPS", "-1")
    with pytest.raises(ValidationError):
        RouterSettings(_env_file=None)
