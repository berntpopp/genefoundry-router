import asyncio

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError
from fastmcp.tools import Tool

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.runtime_drift import (
    RuntimeDriftGuard,
    definitions_from_tools,
    fingerprint_definitions,
)
from genefoundry_router.server import build_app


def _add_tool(server: FastMCP, name: str, description: str) -> None:
    async def dynamic(value: str = "") -> dict[str, str]:
        return {"tool": name, "value": value}

    server.add_tool(Tool.from_function(dynamic, name=name, description=description))


async def _pinned(server: FastMCP) -> dict[str, str]:
    tools = await server._list_tools()  # type: ignore[attr-defined]
    return fingerprint_definitions(definitions_from_tools(tools))


class _SignalingGuard(RuntimeDriftGuard):
    def __init__(self, pinned: dict[str, str], event: asyncio.Event) -> None:
        super().__init__(pinned, "enforce")
        self.event = event

    def evaluate(self, current, *, phase, unreachable):  # type: ignore[no-untyped-def]
        report = super().evaluate(current, phase=phase, unreachable=unreachable)
        if phase == "poll":
            self.event.set()
        return report


@pytest.mark.asyncio
async def test_poll_addition_is_hidden_from_list_search_and_call(monkeypatch) -> None:
    server = FastMCP("genefoundry")
    _add_tool(server, "gnomad_get_gene", "stable reviewed gene lookup")
    event = asyncio.Event()
    guard = _SignalingGuard(await _pinned(server), event)
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce", GF_POLL_INTERVAL=0.01)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry)

    async with app.router.lifespan_context(app):
        _add_tool(server, "gnomad_new_tool", "unreviewed added capability")
        await asyncio.wait_for(event.wait(), timeout=1)

        names = {tool.name for tool in await server.list_tools()}
        search = await server.call_tool("search_tools", {"query": "unreviewed capability"})
        stable = await server.call_tool("gnomad_get_gene", {"value": "TP53"})

        assert "gnomad_new_tool" not in names
        assert "gnomad_new_tool" not in str(search)
        assert "TP53" in str(stable)
        with pytest.raises(NotFoundError, match="gnomad_new_tool"):
            await server.call_tool("gnomad_new_tool", {})


@pytest.mark.asyncio
async def test_poll_changed_tool_is_quarantined_and_task_survives(monkeypatch) -> None:
    server = FastMCP("genefoundry")
    _add_tool(server, "gnomad_get_gene", "reviewed definition")
    _add_tool(server, "gnomad_get_region", "stable unaffected definition")
    event = asyncio.Event()
    guard = _SignalingGuard(await _pinned(server), event)
    monkeypatch.setattr("genefoundry_router.server.build_server", lambda *_a, **_k: server)
    monkeypatch.setattr("genefoundry_router.server.load_runtime_guard", lambda _settings: guard)
    settings = RouterSettings(_env_file=None, GF_DRIFT_MODE="enforce", GF_POLL_INTERVAL=0.01)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry)

    async with app.router.lifespan_context(app):
        server.local_provider.remove_tool("gnomad_get_gene")
        _add_tool(server, "gnomad_get_gene", "changed after review")
        await asyncio.wait_for(event.wait(), timeout=1)

        names = {tool.name for tool in await server.list_tools()}
        unaffected = await server.call_tool("gnomad_get_region", {"value": "1:1-10"})
        assert "gnomad_get_gene" not in names
        assert "1:1-10" in str(unaffected)
        assert guard.last_report.changed == ["gnomad_get_gene"]

        event.clear()
        await asyncio.wait_for(event.wait(), timeout=1)
