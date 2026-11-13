"""search_tools ``limit``: caller-bounded result count + a default-payload budget guard.

The 2026-11-12 fleet acceptance test found discovery calls costing tens of kB with no
way for the caller to ask for fewer hits. Per-hit cost is ~2 kB, dominated by the
inputSchema the agent needs to call the tool, so the result COUNT is the lever.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastmcp.tools.base import Tool

from genefoundry_router.config import RouterSettings
from genefoundry_router.search_expansion import expansion_texts
from genefoundry_router.tool_search import CompactBM25SearchTransform

_BASELINE = Path(__file__).resolve().parents[2] / "genefoundry_router/data/fleet-baseline.json"
# Representative discovery queries from the acceptance test (genes, variants, PGx, lit).
_QUERIES = (
    "splicing prediction",
    "ClinVar significance for a variant",
    "gene disease validity",
    "pharmacogenomic clinical annotations for clopidogrel",
    "search literature for a gene",
    "variant allele frequency",
    "drug gene interaction",
    "protein interaction partners",
)
# Measured max over _QUERIES on the 2026-10-21 baseline: 12.4 kB (5 hits). ~30% headroom;
# a backend whose schemas grow past this makes every discovery call pay for it — look first.
_DEFAULT_BUDGET_BYTES = 16_000


def _tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"variant lookup tool {name}",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )


def _fn(tool: Tool) -> Any:
    return getattr(tool, "fn")  # noqa: B009


def _ctx(tools: list[Tool]) -> MagicMock:
    ctx = MagicMock()
    ctx.fastmcp.list_tools = AsyncMock(return_value=tools)
    return ctx


def _fleet() -> list[Tool]:
    base = json.loads(_BASELINE.read_text(encoding="utf-8"))
    return [
        Tool(
            name=f"{ns}_{t['name']}",
            description=t.get("description") or "",
            parameters=t.get("inputSchema") or {},
            output_schema=t.get("outputSchema"),
            tags=set(t.get("tags") or []),
        )
        for ns, b in base["backends"].items()
        for t in b["tools"]
    ]


async def test_limit_bounds_the_hit_count_and_keeps_the_ranking_prefix() -> None:
    tools = [_tool(f"ns_tool{i}") for i in range(12)]
    search = CompactBM25SearchTransform(max_results=5, rerank_pool=20)._make_search_tool()
    ctx = _ctx(tools)

    default = await _fn(search)(query="variant lookup", ctx=ctx)
    assert len(default) == 5  # the configured default is unchanged
    two = await _fn(search)(query="variant lookup", limit=2, ctx=ctx)
    assert [h["name"] for h in two] == [h["name"] for h in default[:2]]
    ten = await _fn(search)(query="variant lookup", limit=10, detail="full", ctx=ctx)
    assert len(ten) == 10  # above the default, up to the advertised maximum


def test_limit_is_advertised_with_its_enforced_bounds() -> None:
    search = CompactBM25SearchTransform(max_results=5, rerank_pool=20)._make_search_tool()
    prop = search.parameters["properties"]["limit"]
    assert (prop["type"], prop["default"], prop["minimum"], prop["maximum"]) == (
        "integer",
        5,
        1,
        20,
    )
    assert "limit" not in search.parameters.get("required", [])


# Out-of-range rejection is proven end to end in tests/integration/test_tool_search_detail.py.


async def test_default_search_payload_stays_within_budget() -> None:
    tools = _fleet()
    expansions = expansion_texts(RouterSettings(_env_file=None).GF_SEARCH_EXPANSIONS)
    search = CompactBM25SearchTransform(max_results=5, expansions=expansions)._make_search_tool()
    ctx = _ctx(tools)
    for query in _QUERIES:
        hits = await _fn(search)(query=query, ctx=ctx)
        size = len(json.dumps(hits, separators=(",", ":")))
        assert len(hits) == 5 and size <= _DEFAULT_BUDGET_BYTES, (query, size)
        one = await _fn(search)(query=query, limit=1, ctx=ctx)
        assert one == hits[:1]  # limit=1 is the top hit, not a different ranking
