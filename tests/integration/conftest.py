"""In-process FastMCP fake backends for integration tests (no network)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP


def make_fake_backend(name: str, tool_names: list[str]) -> FastMCP:
    """Build a FastMCP server exposing trivial echo tools with the given names."""
    server = FastMCP(name)
    for tool_name in tool_names:

        def _make(tn: str):
            async def _tool(value: str = "") -> dict[str, str]:
                return {"tool": tn, "server": name, "value": value}

            _tool.__name__ = tn
            return _tool

        server.tool(name=tool_name)(_make(tool_name))
    return server


@pytest.fixture
def gnomad_fake() -> FastMCP:
    # clean, Standard-v1-compliant leaf names
    return make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])


@pytest.fixture
def gtex_fake() -> FastMCP:
    # deliberately collides with gnomad on search_genes
    return make_fake_backend("gtex-link", ["get_gene_information", "search_genes"])


@pytest.fixture
def pubtator_fake() -> FastMCP:
    # self-prefixed leaf names (non-compliant) -> exercises strip_prefix
    return make_fake_backend(
        "pubtator-link", ["pubtator_search_literature", "pubtator_get_passages"]
    )


@pytest.fixture
def pubtator_clean_fake() -> FastMCP:
    # post Tool-Naming Standard v1 (pubtator-link#57): clean, unprefixed leaf names
    return make_fake_backend("pubtator-link", ["search_literature", "get_passages"])
