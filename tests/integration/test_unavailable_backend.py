"""Issue #256: a configured backend that serves 0 tools must not be advertised, and
calling its tool must say the backend is unavailable instead of a generic not_found."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

import genefoundry_router.server as server_module
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app


async def test_dead_backend_is_not_advertised_and_reports_unavailable(
    gnomad_fake: FastMCP,
) -> None:
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", entrypoints=["search_genes"]),
        # enabled but no URL resolved: mounted nowhere, harvests 0 tools
        BackendDef(name="mondo", url_env="Y", namespace="mondo", entrypoints=["resolve_disease"]),
    ]
    app = build_app(RouterSettings(), registry, proxy_targets={"gnomad": gnomad_fake})
    async with app.router.lifespan_context(app):
        server = app.state.mcp_server
        assert "mondo" not in server.instructions
        assert "gnomad_search_genes" in server.instructions
        async with Client(server) as client:
            for res in (
                await client.call_tool("mondo_resolve_disease", {}, raise_on_error=False),
                await client.call_tool(
                    "call_tool",
                    {"name": "mondo_resolve_disease", "arguments": {}},
                    raise_on_error=False,
                ),
            ):
                assert res.is_error
                assert res.structured_content["error_code"] == "upstream_unavailable"
                assert res.structured_content["retryable"] is True
                assert "mondo" in res.structured_content["message"]
            unknown = await client.call_tool("nosuch_tool", {}, raise_on_error=False)
            assert unknown.structured_content["error_code"] == "not_found"


async def test_backend_recovery_restores_pins_and_clears_unavailable(
    gnomad_fake: FastMCP, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", entrypoints=["search_genes"])
    ]
    app = build_app(RouterSettings(), registry, proxy_targets={"gnomad": gnomad_fake})
    real = server_module.apply_normalizations
    async with app.router.lifespan_context(app):
        server = app.state.mcp_server

        async def _empty(*_a: object) -> list[object]:
            return []

        monkeypatch.setattr(server_module, "apply_normalizations", _empty)
        await app.state.refresh_catalog("poll")
        assert "gnomad_search_genes" not in server.instructions
        assert server.notfound_guard.unavailable == {"gnomad"}

        async def _boom(*_a: object) -> list[object]:
            raise RuntimeError("relist failed")

        before = server.instructions
        monkeypatch.setattr(server_module, "apply_normalizations", _boom)
        with pytest.raises(RuntimeError):
            await app.state.refresh_catalog("poll")
        assert server.instructions == before

        monkeypatch.setattr(server_module, "apply_normalizations", real)
        await app.state.refresh_catalog("poll")
        assert "gnomad_search_genes" in server.instructions
        assert server.notfound_guard.unavailable == frozenset()
        async with Client(server) as client:
            res = await client.call_tool("gnomad_nosuch", {}, raise_on_error=False)
            assert res.structured_content["error_code"] == "not_found"
