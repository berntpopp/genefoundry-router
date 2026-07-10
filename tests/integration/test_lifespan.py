import asyncio

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.observability import BACKEND_STATUS, BACKEND_TOOL_COUNT
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
