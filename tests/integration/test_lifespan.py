import asyncio
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.observability import (
    BACKEND_STATUS,
    BACKEND_TOOL_COUNT,
    OAUTH_REFRESH_ATTEMPTS,
)
from genefoundry_router.refresh_observability import (
    RefreshEvent,
    RefreshLedger,
    RefreshLedgerUnavailable,
)
from genefoundry_router.registry import BackendDef, TransformConfig
from genefoundry_router.runtime_drift import (
    RuntimeDriftGuard,
    definitions_from_tools,
    fingerprint_definitions,
)
from genefoundry_router.server import build_app


def _server_with_tool(name: str = "gnomad_get_gene") -> FastMCP:
    server = FastMCP("genefoundry")

    @server.tool(name=name)
    async def tool(value: str = "") -> dict[str, str]:
        return {"value": value}

    return server


def _pinned(server: FastMCP) -> dict[str, str]:
    tools = asyncio.run(server._list_tools())  # type: ignore[attr-defined]
    return fingerprint_definitions(definitions_from_tools(tools))


def test_lifespan_runs_normalization_then_search(pubtator_fake):
    # isolate the module-level reachability cache from other tests' set_backend_up calls
    BACKEND_STATUS.clear()
    # poll disabled; normalization must still run at startup
    settings = RouterSettings(_env_file=None, GF_POLL_INTERVAL=0, GF_DRIFT_MODE="off")
    registry = [
        BackendDef(
            name="pubtator",
            url_env="X",
            namespace="pubtator",
            tags=["literature"],
            transform=TransformConfig(strip_prefix="pubtator_"),
        )
    ]
    app = build_app(settings, registry, proxy_targets={"pubtator": pubtator_fake})
    with TestClient(app) as client:  # triggers lifespan startup + shutdown
        body = client.get("/health").json()
        assert "pubtator" in body["backends"]["namespaces"]
        # the composed lifespan seeds reachability via set_backend_up at startup
        # (the bare mcp_app.lifespan does not), proving the composed lifespan ran.
        assert body["backends"]["reachable"]["pubtator"] is True


def test_lifespan_marks_zero_tool_backend_degraded(pubtator_fake):
    """Reachability is derived from the LIVE tool harvest, not from 'a URL is configured'.
    A mounted backend that yields no tools (down / 307 / transport-broken) must read as
    down in /health, and the aggregate status must flip to 'degraded'."""
    BACKEND_STATUS.clear()
    BACKEND_TOOL_COUNT.clear()
    settings = RouterSettings(_env_file=None, GF_POLL_INTERVAL=0, GF_DRIFT_MODE="off")
    registry = [
        BackendDef(name="pubtator", url_env="X", namespace="pubtator", tags=["literature"]),
        BackendDef(name="genereviews", url_env="Y", namespace="genereviews"),
    ]
    empty_fake = make_fake_backend("genereviews-link", [])  # reachable, but zero tools
    app = build_app(
        settings,
        registry,
        proxy_targets={"pubtator": pubtator_fake, "genereviews": empty_fake},
    )
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["backends"]["reachable"]["pubtator"] is True
        assert body["backends"]["reachable"]["genereviews"] is False
        assert body["backends"]["tools"]["genereviews"] == 0
        assert body["backends"]["tools"]["pubtator"] >= 1
        assert body["backends"]["degraded"] == ["genereviews"]
        assert body["status"] == "degraded"


def test_lifespan_reports_matching_startup_catalog(monkeypatch) -> None:
    server = _server_with_tool()
    guard = RuntimeDriftGuard(_pinned(server), "enforce")
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce")
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry)

    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body["drift"] == {"status": "ok", "changed": [], "added": [], "removed": []}


def test_lifespan_fails_on_changed_startup_definition(monkeypatch) -> None:
    server = _server_with_tool()
    guard = RuntimeDriftGuard({"gnomad_get_gene": "reviewed-digest"}, "enforce")
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce")
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry)

    with pytest.raises(BaseExceptionGroup) as exc_info:
        with TestClient(app):
            pass

    assert "changed tool definition: gnomad_get_gene" in str(exc_info.value.exceptions)


def test_startup_addition_is_degraded_and_quarantined(monkeypatch) -> None:
    server = _server_with_tool()
    pinned = _pinned(server)

    @server.tool(name="gnomad_unreviewed")
    async def unreviewed() -> dict[str, bool]:
        return {"ok": True}

    guard = RuntimeDriftGuard(pinned, "enforce")
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce")
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry)

    with TestClient(app) as client:
        body = client.get("/health").json()
        names = {tool.name for tool in client.portal.call(server.list_tools)}
        with pytest.raises(NotFoundError, match="gnomad_unreviewed"):
            client.portal.call(server.call_tool, "gnomad_unreviewed", {})

    assert body["drift"]["added"] == ["gnomad_unreviewed"]
    assert "gnomad_unreviewed" not in names


def test_startup_removal_degrades_without_failure(monkeypatch) -> None:
    server = _server_with_tool()
    pinned = {**_pinned(server), "gnomad_removed": "reviewed-digest"}
    guard = RuntimeDriftGuard(pinned, "enforce")
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce")
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]

    with TestClient(build_app(settings, registry)) as client:
        body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["drift"]["removed"] == ["gnomad_removed"]


def test_one_refresh_reuses_normalized_catalog(monkeypatch) -> None:
    server = _server_with_tool()
    guard = RuntimeDriftGuard(_pinned(server), "enforce")
    calls = 0
    original = server._list_tools  # type: ignore[attr-defined]

    async def counted_list_tools():
        nonlocal calls
        calls += 1
        return await original()

    monkeypatch.setattr(server, "_list_tools", counted_list_tools)
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce")
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]

    with TestClient(build_app(settings, registry)):
        pass

    assert calls == 1


def test_refresh_ledger_is_shared_restored_and_marks_clean_lifecycle(monkeypatch, tmp_path) -> None:
    path = tmp_path / "refresh.sqlite3"
    key = "test-router-signing-key"
    now = time.time()
    seed = RefreshLedger(path, hmac_key=key)
    seed.record_event(
        RefreshEvent(
            at=now,
            request_id="seed-request",
            client_class="chatgpt",
            client_hmac="a" * 64,
            event_type="refresh",
            outcome="success",
        )
    )
    seed.close()
    before = OAUTH_REFRESH_ATTEMPTS.labels(client_class="chatgpt")._value.get()
    captured = {}
    server = _server_with_tool()

    def fake_build_server(*_args, **kwargs):
        captured["ledger"] = kwargs.get("refresh_ledger")
        return server

    monkeypatch.setattr("genefoundry_router.server.build_server", fake_build_server)
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_DRIFT_MODE="off",
        GF_REFRESH_OBSERVABILITY_DB=str(path),
        GF_OAUTH_JWT_SIGNING_KEY=key,
    )
    app = build_app(settings, [])
    try:
        assert captured["ledger"] is app.state.refresh_ledger
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
        assert OAUTH_REFRESH_ATTEMPTS.labels(client_class="chatgpt")._value.get() == before + 1
        with sqlite3.connect(path) as connection:
            markers = connection.execute(
                "SELECT marker FROM router_lifecycle ORDER BY id"
            ).fetchall()
        assert markers == [("startup",), ("shutdown",)]
        with pytest.raises(RefreshLedgerUnavailable, match="closed"):
            app.state.refresh_ledger.counter_snapshot()
    finally:
        app.state.refresh_ledger.close()


def test_refresh_heartbeat_advances_without_refresh_traffic(monkeypatch, tmp_path) -> None:
    path = tmp_path / "refresh.sqlite3"
    server = _server_with_tool()
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr(
        "genefoundry_router.server.REFRESH_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
        raising=False,
    )
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_DRIFT_MODE="off",
        GF_REFRESH_OBSERVABILITY_DB=str(path),
        GF_OAUTH_JWT_SIGNING_KEY="test-router-signing-key",
    )
    app = build_app(settings, [])

    with TestClient(app):
        time.sleep(0.04)
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT value FROM refresh_meta WHERE key='observer_heartbeat_at'"
            ).fetchone()

    assert row is not None
    assert float(row[0]) > 0


def test_failed_shutdown_is_not_marked_clean_and_ledger_is_closed(monkeypatch, tmp_path) -> None:
    path = tmp_path / "refresh.sqlite3"
    server = _server_with_tool()
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)

    async def fail_stop(_self) -> None:
        raise RuntimeError("primary refresher teardown failed")

    monkeypatch.setattr("genefoundry_router.server.PollingRefresher.stop", fail_stop)
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_DRIFT_MODE="off",
        GF_REFRESH_OBSERVABILITY_DB=str(path),
        GF_OAUTH_JWT_SIGNING_KEY="test-router-signing-key",
    )
    app = build_app(settings, [])

    with pytest.raises(BaseExceptionGroup) as exc_info:
        with TestClient(app):
            pass
    assert "primary refresher teardown failed" in str(exc_info.value.exceptions)
    with sqlite3.connect(path) as connection:
        markers = connection.execute("SELECT marker FROM router_lifecycle ORDER BY id").fetchall()
    assert markers == [("startup",)]
    with pytest.raises(RefreshLedgerUnavailable, match="closed"):
        app.state.refresh_ledger.counter_snapshot()


def test_failed_startup_does_not_claim_a_clean_observation_interval(monkeypatch, tmp_path) -> None:
    path = tmp_path / "refresh.sqlite3"
    server = _server_with_tool()
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)

    async def fail_normalization(*_args, **_kwargs):
        raise RuntimeError("startup failed")

    monkeypatch.setattr("genefoundry_router.server.apply_normalizations", fail_normalization)
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_DRIFT_MODE="off",
        GF_REFRESH_OBSERVABILITY_DB=str(path),
        GF_OAUTH_JWT_SIGNING_KEY="test-router-signing-key",
    )
    app = build_app(settings, [])
    try:
        with pytest.raises(BaseExceptionGroup):
            with TestClient(app):
                pass
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM router_lifecycle").fetchone()[0] == 0
    finally:
        app.state.refresh_ledger.close()


def test_production_startup_fails_closed_on_unreadable_configured_ledger(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "refresh.sqlite3"
    seed = RefreshLedger(path, hmac_key="test-router-signing-key")
    seed.close()
    os.chmod(path, 0)
    monkeypatch.setattr(
        "genefoundry_router.server.build_server",
        lambda *_a, **_k: pytest.fail("server must not build after ledger failure"),
    )
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="oauth",
        GF_DEPLOYMENT_MODE="production",
        GF_REFRESH_OBSERVABILITY_DB=str(path),
        GF_OAUTH_JWT_SIGNING_KEY="test-router-signing-key",
    )
    try:
        with pytest.raises(RefreshLedgerUnavailable, match="readable and writable"):
            build_app(settings, [])
    finally:
        os.chmod(path, 0o600)
