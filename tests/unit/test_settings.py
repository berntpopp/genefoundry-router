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
    assert s.GF_PUBLIC_BASE_URL is None  # R1.5 - public URL for OAuth metadata
    assert s.GF_TRUSTED_PROXY_HOPS == 1
    assert s.GF_METRICS_TOKEN is None


def test_allowed_origins_parses_csv(monkeypatch):
    monkeypatch.setenv("GF_ALLOWED_ORIGINS", "https://claude.ai, https://cursor.sh")
    s = RouterSettings(_env_file=None)
    assert s.GF_ALLOWED_ORIGINS == ["https://claude.ai", "https://cursor.sh"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.setenv("GF_PORT", "9001")
    monkeypatch.setenv("GF_TRUSTED_PROXY_HOPS", "2")
    monkeypatch.setenv("GF_METRICS_TOKEN", "scrape-secret")
    s = RouterSettings(_env_file=None)
    assert s.GF_AUTH_MODE == "jwt"
    assert s.GF_PORT == 9001
    assert s.GF_TRUSTED_PROXY_HOPS == 2
    assert s.GF_METRICS_TOKEN == "scrape-secret"


def test_metrics_token_blank_normalizes_to_none(monkeypatch):
    monkeypatch.setenv("GF_METRICS_TOKEN", "   ")
    s = RouterSettings(_env_file=None)
    assert s.GF_METRICS_TOKEN is None


def test_invalid_auth_mode_rejected(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "bogus")
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RouterSettings(_env_file=None)
