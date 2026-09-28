"""search_tools ranking stages end-to-end: document expansion and the optional reranker."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools.base import Tool

from genefoundry_router.config import RouterSettings
from genefoundry_router.search_rerank import RerankConfig, SystemOneReranker
from genefoundry_router.tool_search import CompactBM25SearchTransform, apply_tool_search


@pytest.fixture(autouse=True)
def _no_ambient_search_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's exported GF_SEARCH_* must not change what these tests measure."""
    for key in [k for k in os.environ if k.startswith("GF_SEARCH_")]:
        monkeypatch.delenv(key)


def _tool(name: str, description: str) -> Tool:
    return Tool(name=name, description=description, parameters={"type": "object", "properties": {}})


CATALOG = [
    _tool("g_get_variant_frequencies", "Return allele frequencies for a variant ID."),
    _tool("g_get_coverage", "Sequencing coverage for a genomic region."),
    _tool("s_predict_splicing", "Predict splice-site disruption for a variant."),
    _tool("h_resolve_symbol", "Resolve an HGNC gene symbol."),
    _tool("u_find_proteins", "Search UniProt protein entries."),
    _tool("m_resolve_disease", "Resolve a disease name to MONDO."),
]
LAY = "is this DNA change seen in healthy people"
EXPANSIONS = {"g_get_variant_frequencies": "how common is my DNA change in healthy people"}


class FakeReranker:
    """Records what the router sends and returns a scripted ranking."""

    def __init__(self, result: str | None, scores: dict[str, float] | None = None):
        self.result, self.scores = result, scores or {}
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, tools: Sequence[Tool]) -> list[tuple[Tool, float]] | None:
        self.calls.append((query, [t.name for t in tools]))
        if self.result == "fail":
            return None
        if self.result == "abstain":
            return []
        ranked = sorted(tools, key=lambda t: -self.scores.get(t.name, 0.0))
        return [(t, self.scores.get(t.name, 0.0)) for t in ranked]


def _server(transform: CompactBM25SearchTransform) -> FastMCP:
    server = FastMCP("t")
    for tool in CATALOG:
        server.add_tool(tool)
    server.add_transform(transform)
    return server


async def _search(server: FastMCP, query: str) -> list[dict[str, Any]]:
    async with Client(server) as client:
        result = await client.call_tool("search_tools", {"query": query})
    return json.loads(result.content[0].text) if result.content else []


async def test_expansion_finds_lay_wording_the_description_lacks() -> None:
    plain = CompactBM25SearchTransform(max_results=3)
    expanded = CompactBM25SearchTransform(max_results=3, expansions=EXPANSIONS)
    assert "g_get_variant_frequencies" not in [t.name for t in await plain._search(CATALOG, LAY)]
    top = [t.name for t in await expanded._search(CATALOG, LAY)]
    assert top[0] == "g_get_variant_frequencies"


async def test_expansion_text_never_reaches_the_serialized_hit() -> None:
    hits = await _search(_server(CompactBM25SearchTransform(expansions=EXPANSIONS)), LAY)
    assert hits and hits[0]["name"] == "g_get_variant_frequencies"
    assert "healthy" not in json.dumps(hits)
    assert "relevance" not in hits[0]  # no reranker -> no score field


async def test_reranker_reorders_and_truncates_to_served_results() -> None:
    fake = FakeReranker("rank", {"s_predict_splicing": 0.9, "g_get_variant_frequencies": 0.8})
    transform = CompactBM25SearchTransform(
        max_results=2, expansions=EXPANSIONS, reranker=fake, rerank_pool=6
    )
    query = "variant splice frequencies coverage protein disease symbol"
    hits = await _search(_server(transform), query)
    assert [h["name"] for h in hits] == ["s_predict_splicing", "g_get_variant_frequencies"]
    assert [h["relevance"] for h in hits] == [0.9, 0.8]
    sent_query, sent = fake.calls[0]
    assert sent_query == query
    assert len(sent) > 2  # the reranker sees the pool, not just the served top-K


async def test_reranker_failure_falls_back_to_bm25_order() -> None:
    bm25 = await _search(_server(CompactBM25SearchTransform(max_results=2)), "variant splice")
    fake = FakeReranker("fail")
    rr = CompactBM25SearchTransform(max_results=2, reranker=fake, rerank_pool=6)
    hits = await _search(_server(rr), "variant splice")
    assert [h["name"] for h in hits] == [h["name"] for h in bm25]
    assert all("relevance" not in h for h in hits)
    assert fake.calls


async def test_reranker_abstention_returns_no_hits() -> None:
    transform = CompactBM25SearchTransform(max_results=3, reranker=FakeReranker("abstain"))
    assert await _search(_server(transform), "book a flight to Berlin variant") == []


async def test_full_detail_still_works_with_a_reranker() -> None:
    fake = FakeReranker("rank", {"u_find_proteins": 1.0})
    server = _server(CompactBM25SearchTransform(max_results=1, reranker=fake, rerank_pool=6))
    async with Client(server) as client:
        result = await client.call_tool("search_tools", {"query": "protein", "detail": "full"})
    hits = json.loads(result.content[0].text)
    assert hits[0]["name"] == "u_find_proteins"


async def test_real_systemone_client_through_search_tools() -> None:
    """The production reranker class over a mocked System One endpoint."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        answers = {
            qid: {"noul": 0.95 if q["instructions"]["tool"]["name"] == "m_resolve_disease" else 0.1}
            for qid, q in body["questions"].items()
        }
        return httpx.Response(200, json={"answers": answers})

    cfg = RerankConfig(url="https://jev.example/v1/systemone", model="jev-latest", min_score=0.3)
    reranker = SystemOneReranker(cfg, transport=httpx.MockTransport(handler))
    transform = CompactBM25SearchTransform(max_results=5, reranker=reranker, rerank_pool=6)
    hits = await _search(_server(transform), "resolve a disease or gene symbol")
    assert [h["name"] for h in hits] == ["m_resolve_disease"]  # others filtered at 0.3
    assert hits[0]["relevance"] == 0.95
    assert seen[0]["state"] == {"query": "resolve a disease or gene symbol"}


async def test_apply_tool_search_uses_builtin_expansions_and_rerank_off() -> None:
    server = FastMCP("t")
    apply_tool_search(server, RouterSettings(_env_file=None), always_visible=[])
    transform = next(t for t in server.transforms if isinstance(t, CompactBM25SearchTransform))
    assert len(transform.expansions) >= 250
    assert transform.reranker is None


async def test_apply_tool_search_can_disable_expansion_and_enable_rerank() -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_SEARCH_EXPANSIONS="off",
        GF_SEARCH_RERANK="systemone",
        GF_SEARCH_RERANK_URL="http://onejev:8000/v1/systemone",
    )
    server = FastMCP("t")
    apply_tool_search(server, settings, always_visible=[])
    transform = next(t for t in server.transforms if isinstance(t, CompactBM25SearchTransform))
    assert transform.expansions == {}
    assert isinstance(transform.reranker, SystemOneReranker)


def test_lifespan_closes_the_reranker_client(pubtator_fake) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from genefoundry_router.registry import BackendDef
    from genefoundry_router.server import build_app

    settings = RouterSettings(
        _env_file=None,
        GF_POLL_INTERVAL=0,
        GF_DRIFT_MODE="off",
        GF_SEARCH_RERANK="systemone",
        GF_SEARCH_RERANK_URL="http://127.0.0.1:9/v1/systemone",
    )
    registry = [BackendDef(name="pubtator", url_env="X", namespace="pubtator")]
    app = build_app(settings, registry, proxy_targets={"pubtator": pubtator_fake})
    with TestClient(app):
        server = app.state.mcp_server
        transform = next(t for t in server.transforms if isinstance(t, CompactBM25SearchTransform))
        assert isinstance(transform.reranker, SystemOneReranker)
        assert not transform.reranker._client.is_closed
    assert transform.reranker._client.is_closed
