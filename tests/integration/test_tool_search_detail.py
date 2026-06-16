"""Integration: search_tools is compact by default, full on opt-in (Finding 2)."""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server
from genefoundry_router.tool_search import apply_tool_search


def _backend() -> FastMCP:
    server = FastMCP("clingen-link")

    @server.tool(name="get_gene_validity")
    async def get_gene_validity(gene_symbol: str) -> dict[str, Any]:
        """Fetch ClinGen gene-disease validity classifications for a gene."""
        return {"success": True, "records": [], "gene_symbol": gene_symbol}

    return server


async def _search(client: Client, **kwargs: Any) -> list[dict[str, Any]]:
    result = await client.call_tool("search_tools", {"query": "gene validity", **kwargs})
    data = result.data
    return data if isinstance(data, list) else data["result"]


async def test_search_tools_compact_by_default_then_full_on_opt_in():
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="clingen", url_env="X", namespace="clingen")]
    server = build_server(
        settings, registry, proxy_targets={"clingen": _backend()}, enable_search=False
    )
    apply_tool_search(server, settings, always_visible=[])

    async with Client(server) as client:
        compact = await _search(client)
        hit = next(e for e in compact if e["name"] == "clingen_get_gene_validity")
        # full input schema kept (argument contract); output schema collapsed to one line
        assert "inputSchema" in hit and "gene_symbol" in hit["inputSchema"]["properties"]
        assert "outputSchema" not in hit
        assert isinstance(hit.get("returns"), str)

        full = await _search(client, detail="full")
        fhit = next(e for e in full if e["name"] == "clingen_get_gene_validity")
        # opt-in restores the complete output schema
        assert "outputSchema" in fhit
