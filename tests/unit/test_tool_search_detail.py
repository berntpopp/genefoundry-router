"""Unit tests for tool search detail and synthetic tool caching (#221, #232)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.tools.base import Tool

from genefoundry_router.tool_search import CompactBM25SearchTransform


def _sample_tool(
    name: str,
    description: str = "",
    properties: dict[str, dict[str, str]] | None = None,
    output_schema: dict[str, object] | None = None,
) -> Tool:
    props = properties or {}
    tool = Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": props},
    )
    if output_schema is not None:
        tool.output_schema = output_schema
    return tool


@pytest.mark.asyncio
async def test_pinned_entrypoints_included_in_search_results() -> None:
    """#221: Pinned entrypoint tools (always_visible) must be included in search results."""
    pinned = _sample_tool(
        "mondo_resolve_disease",
        description="Resolve disease names or terms to canonical MONDO IDs",
        properties={"query": {"type": "string", "description": "Disease name"}},
    )
    other = _sample_tool(
        "omim_search_phenotypes",
        description="Search OMIM morbid phenotypes",
        properties={"term": {"type": "string", "description": "Search term"}},
    )

    transform = CompactBM25SearchTransform(
        always_visible=["mondo_resolve_disease"],
        max_results=5,
    )
    search_tool = transform._make_search_tool()

    ctx = MagicMock()
    ctx.fastmcp.list_tools = AsyncMock(return_value=[pinned, other])

    for detail in ("compact", "full"):
        result = await search_tool.fn(query="resolve disease", detail=detail, ctx=ctx)
        assert isinstance(result, list)
        names = [entry["name"] for entry in result]
        assert "mondo_resolve_disease" in names
        assert "search_tools" not in names
        assert "call_tool" not in names


@pytest.mark.asyncio
async def test_search_results_compact_and_full_detail_structures() -> None:
    """Verify response structure for compact vs full detail modes."""
    output_schema: dict[str, object] = {
        "type": "object",
        "properties": {"disease_id": {"type": "string"}, "label": {"type": "string"}},
    }
    tool = _sample_tool(
        "mondo_resolve_disease",
        description="Resolve disease names",
        properties={"query": {"type": "string"}},
        output_schema=output_schema,
    )

    transform = CompactBM25SearchTransform(always_visible=["mondo_resolve_disease"])
    search_tool = transform._make_search_tool()

    ctx = MagicMock()
    ctx.fastmcp.list_tools = AsyncMock(return_value=[tool])

    compact_res = await search_tool.fn(query="resolve disease", detail="compact", ctx=ctx)
    assert isinstance(compact_res, list)
    assert len(compact_res) == 1
    hit = compact_res[0]
    assert hit["name"] == "mondo_resolve_disease"
    assert "inputSchema" in hit
    assert "returns" in hit
    assert hit["returns"] == "{disease_id: string, label: string}"

    full_res = await search_tool.fn(query="resolve disease", detail="full", ctx=ctx)
    assert isinstance(full_res, list)
    assert len(full_res) == 1
    full_hit = full_res[0]
    assert full_hit["name"] == "mondo_resolve_disease"
    assert full_hit["outputSchema"] == output_schema


@pytest.mark.asyncio
async def test_synthetic_tools_are_cached_per_transform() -> None:
    """#232: search_tools and call_tool are cached per CompactBM25SearchTransform instance."""
    t1 = CompactBM25SearchTransform(always_visible=["pinned_tool"])

    s1 = t1._make_search_tool()
    s2 = t1._make_search_tool()
    assert s1 is s2, "Expected _make_search_tool() to return cached Tool instance"

    c1 = t1._make_call_tool()
    c2 = t1._make_call_tool()
    assert c1 is c2, "Expected _make_call_tool() to return cached Tool instance"

    dummy_next = AsyncMock()
    assert await t1.get_tool("search_tools", dummy_next) is s1
    assert await t1.get_tool("call_tool", dummy_next) is c1
    dummy_next.assert_not_called()

    pinned = _sample_tool("pinned_tool", description="Pinned resolver")
    tools1 = await t1.transform_tools([pinned])
    tools2 = await t1.transform_tools([pinned])

    assert tools1[1] is s1
    assert tools1[2] is c1
    assert tools2[1] is s1
    assert tools2[2] is c1

    t2 = CompactBM25SearchTransform(always_visible=["pinned_tool"])
    s_other = t2._make_search_tool()
    c_other = t2._make_call_tool()
    assert s_other is not s1
    assert c_other is not c1
