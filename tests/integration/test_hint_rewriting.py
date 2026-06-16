"""Integration: bare tool-reference hints come back namespaced (Finding 1)."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server


def _backend() -> FastMCP:
    server = FastMCP("clingen-link")

    @server.tool(name="get_gene_validity")
    async def get_gene_validity(gene_symbol: str = "") -> dict[str, Any]:
        """Return a not-found error carrying bare-name self-healing hints."""
        return {
            "success": False,
            "error_code": "not_found",
            "fallback_tool": "search_genes",
            "next_commands": [{"tool": "search_genes", "args": {"q": gene_symbol}}],
        }

    return server


def _build(rewrite: bool = True) -> FastMCP:
    settings = RouterSettings(_env_file=None, GF_REWRITE_HINTS=rewrite)
    registry = [BackendDef(name="clingen", url_env="X", namespace="clingen")]
    return build_server(settings, registry, proxy_targets={"clingen": _backend()})


async def test_direct_call_rewrites_structured_and_text():
    async with Client(_build()) as client:
        result = await client.call_tool("clingen_get_gene_validity", {"gene_symbol": "BRCA1"})
        sc = result.structured_content
        assert sc["fallback_tool"] == "clingen_search_genes"
        assert sc["next_commands"][0]["tool"] == "clingen_search_genes"
        # the JSON text block (what many clients display) is rewritten in lockstep
        text_payload = json.loads(result.content[0].text)
        assert text_payload["fallback_tool"] == "clingen_search_genes"


async def test_call_tool_proxy_path_rewrites_via_reentrant_middleware():
    async with Client(_build()) as client:
        result = await client.call_tool(
            "call_tool",
            {"name": "clingen_get_gene_validity", "arguments": {"gene_symbol": "BRCA1"}},
        )
        sc = result.structured_content
        assert sc["fallback_tool"] == "clingen_search_genes"
        assert sc["next_commands"][0]["tool"] == "clingen_search_genes"


async def test_toggle_off_leaves_bare_names():
    async with Client(_build(rewrite=False)) as client:
        result = await client.call_tool("clingen_get_gene_validity", {"gene_symbol": "BRCA1"})
        assert result.structured_content["fallback_tool"] == "search_genes"
