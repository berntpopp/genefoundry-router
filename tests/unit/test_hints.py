"""Unit tests for namespace-aware hint rewriting (Finding 1: bare-name hints)."""

from __future__ import annotations

from mcp.types import TextContent

from genefoundry_router.hints import _rewrite_block, rewrite_tool_refs

_NS = {"clingen", "gnomad", "mgi"}


def test_rewrite_block_leaves_non_json_prose_untouched():
    # safety property: free-text content is never regex-rewritten
    block = TextContent(type="text", text="No gene found. Try search_genes instead.")
    assert _rewrite_block(block, "clingen", _NS) is block


def test_rewrite_block_rewrites_json_text_in_place():
    block = TextContent(type="text", text='{"fallback_tool": "search_genes"}')
    out = _rewrite_block(block, "clingen", _NS)
    assert out is not block  # a rewritten copy
    assert '"clingen_search_genes"' in out.text


def test_rewrite_block_unchanged_json_returns_same_block():
    block = TextContent(type="text", text='{"result": 1}')
    assert _rewrite_block(block, "clingen", _NS) is block


def test_rewrites_fallback_tool_and_next_commands():
    payload = {
        "success": False,
        "error_code": "not_found",
        "fallback_tool": "search_genes",
        "next_commands": [{"tool": "search_genes", "args": {"q": "BRCA1"}}],
    }
    n = rewrite_tool_refs(payload, "clingen", _NS)
    assert n == 2
    assert payload["fallback_tool"] == "clingen_search_genes"
    assert payload["next_commands"][0]["tool"] == "clingen_search_genes"
    # non-reference fields are untouched
    assert payload["next_commands"][0]["args"] == {"q": "BRCA1"}


def test_is_idempotent():
    payload = {"fallback_tool": "search_genes"}
    assert rewrite_tool_refs(payload, "clingen", _NS) == 1
    assert rewrite_tool_refs(payload, "clingen", _NS) == 0
    assert payload["fallback_tool"] == "clingen_search_genes"


def test_leaves_already_namespaced_values_alone():
    # a value already carrying a known namespace prefix is never re-prefixed
    payload = {"tool": "gnomad_get_gene_variants"}
    assert rewrite_tool_refs(payload, "clingen", _NS) == 0
    assert payload["tool"] == "gnomad_get_gene_variants"


def test_ignores_prose_and_non_identifier_values():
    payload = {
        "message": "try search_genes instead",  # not a reference key
        "fallback_tool": "switch to another tool",  # not a bare identifier
    }
    assert rewrite_tool_refs(payload, "clingen", _NS) == 0
    assert payload["fallback_tool"] == "switch to another tool"


def test_rewrites_get_server_capabilities_case_from_report():
    # the exact failure the report reproduced: call_tool("get_server_capabilities")
    payload = {"fallback_tool": "get_server_capabilities"}
    assert rewrite_tool_refs(payload, "pubtator", {"pubtator"}) == 1
    assert payload["fallback_tool"] == "pubtator_get_server_capabilities"
