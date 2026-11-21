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


def _build(rewrite: bool = True, entrypoints: tuple[str, ...] = ("search_genes",)) -> FastMCP:
    settings = RouterSettings(_env_file=None, GF_REWRITE_HINTS=rewrite)
    registry = [
        BackendDef(name="clingen", url_env="X", namespace="clingen", entrypoints=list(entrypoints))
    ]
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


async def test_prose_hint_names_call_tool_for_unpinned_and_bare_for_pinned():
    # Issue #299: "Try search_genes." in an error message led a model to call an unlisted
    # name directly. clingen_search_genes is pinned here, clingen_get_gene_info is not.
    backend = FastMCP("clingen-link")

    @backend.tool(name="get_gene_validity")
    async def get_gene_validity() -> dict[str, Any]:
        """Return an error whose prose names a sibling tool."""
        return {"success": False, "message": "No gene. Try search_genes or get_gene_info."}

    settings = RouterSettings(_env_file=None)
    registry = [
        BackendDef(name="clingen", url_env="X", namespace="clingen", entrypoints=["search_genes"])
    ]
    server = build_server(settings, registry, proxy_targets={"clingen": backend})
    async with Client(server) as client:
        result = await client.call_tool("clingen_get_gene_validity", {})
    assert result.structured_content["message"] == (
        'No gene. Try clingen_search_genes or call_tool(name="clingen_get_gene_info").'
    )
    assert json.loads(result.content[0].text)["message"] == result.structured_content["message"]


async def test_next_command_to_unpinned_tool_is_the_call_tool_form_and_direct_name_routes():
    # #354: a model calls an unlisted next_commands target by name and its client rejects it.
    async with Client(_build(entrypoints=())) as client:
        listed = {t.name for t in await client.list_tools()}
        result = await client.call_tool("clingen_get_gene_validity", {"gene_symbol": "BRCA1"})
        step = result.structured_content["next_commands"][0]
        assert step == {
            "tool": "call_tool",
            "args": {"name": "clingen_search_genes", "arguments": {"q": "BRCA1"}},
        }
        assert json.loads(result.content[0].text)["next_commands"][0] == step
        # a client that does send the unlisted full name to the router is routed to the backend
        assert "clingen_get_gene_validity" not in listed
        assert result.is_error is False
