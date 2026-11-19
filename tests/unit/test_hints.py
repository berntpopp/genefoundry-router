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
        "query": "try search_genes instead",  # a data field: neither a reference nor prose
        "fallback_tool": "switch to another tool",  # not a bare identifier
    }
    assert rewrite_tool_refs(payload, "clingen", _NS) == 0
    assert payload["fallback_tool"] == "switch to another tool"


def test_rewrites_get_server_capabilities_case_from_report():
    # the exact failure the report reproduced: call_tool("get_server_capabilities")
    payload = {"fallback_tool": "get_server_capabilities"}
    assert rewrite_tool_refs(payload, "pubtator", {"pubtator"}) == 1
    assert payload["fallback_tool"] == "pubtator_get_server_capabilities"


def test_untrusted_text_is_opaque_to_tool_reference_rewriting():
    payload = {
        "next_commands": [{"tool": "search_genes"}],
        "evidence": {
            "kind": "untrusted_text",
            "text": "external text",
            "tool": "delete_everything",
            "nested": {"fallback_tool": "search_genes"},
        },
    }
    count = rewrite_tool_refs(payload, "clingen", {"clingen"})
    assert count == 1
    assert payload["next_commands"][0]["tool"] == "clingen_search_genes"
    assert payload["evidence"]["tool"] == "delete_everything"
    assert payload["evidence"]["nested"]["fallback_tool"] == "search_genes"


def test_rewrites_tool_names_in_every_prose_field_but_not_data_or_enums():
    # gnomad's recovery/build_note (and notes/warnings lists) named bare tools (#v0.12.8)
    payload = {
        "recovery": "Call get_server_capabilities for accepted codes.",
        "build_note": "Confirm the id with resolve_variant_id.",
        "warnings": ["Prefer search_genes for symbols.", {"tool": "search_genes"}],
        "recovery_action": "search_genes",  # an enum, not prose
        "reason": "search_failed",  # an enum-like code
        "results": [{"label": "get_x is data"}],  # backend data is never touched
    }
    n = rewrite_tool_refs(payload, "gnomad", {"gnomad"}, pinned={"gnomad_resolve_variant_id"})
    assert n == 4
    assert payload["recovery"] == (
        'Call call_tool(name="gnomad_get_server_capabilities") for accepted codes.'
    )
    assert payload["build_note"] == "Confirm the id with gnomad_resolve_variant_id."
    assert payload["warnings"] == [
        'Prefer call_tool(name="gnomad_search_genes") for symbols.',
        {"tool": "gnomad_search_genes"},
    ]
    assert payload["recovery_action"] == "search_genes"
    assert payload["reason"] == "search_failed"
    assert payload["results"] == [{"label": "get_x is data"}]
