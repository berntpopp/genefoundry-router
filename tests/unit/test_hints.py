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
    n = rewrite_tool_refs(payload, "clingen", _NS, pinned={"clingen_search_genes"})
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
    count = rewrite_tool_refs(payload, "clingen", {"clingen"}, pinned={"clingen_search_genes"})
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


def test_prose_call_with_argument_list_becomes_call_tool_with_arguments():
    # #354: "call get_gene_score_sets(symbol)" must not become call_tool(name=...)(symbol)
    payload = {
        "message": "No match; call get_gene_score_sets(symbol) or list_score_sets(gene, page=2).",
        "hint": "Then search_genes() and resolve_symbol(symbol).",
    }
    rewrite_tool_refs(payload, "mavedb", {"mavedb"}, pinned={"mavedb_resolve_symbol"})
    assert payload["message"] == (
        'No match; call call_tool(name="mavedb_get_gene_score_sets", arguments={"symbol": ...})'
        ' or call_tool(name="mavedb_list_score_sets", arguments={"gene": ..., "page": 2}).'
    )
    # a pinned tool keeps its direct name and its own argument list
    assert payload["hint"] == (
        'Then call_tool(name="mavedb_search_genes", arguments={}) and '
        "mavedb_resolve_symbol(symbol)."
    )


def test_prose_parenthetical_that_is_not_an_argument_list_is_left_alone():
    payload = {
        "note": "Use get_variant (the HGVS form) or get_variant(e.g. NM_000.1:c.1A>G).",
    }
    rewrite_tool_refs(payload, "clinvar", {"clinvar"})
    assert payload["note"] == (
        'Use call_tool(name="clinvar_get_variant") (the HGVS form) or '
        'call_tool(name="clinvar_get_variant")(e.g. NM_000.1:c.1A>G).'
    )


def test_prose_call_keeps_the_argument_values_the_backend_gave():
    payload = {"hint": 'Retry get_variant(assembly="GRCh37", page=2, symbol).'}
    rewrite_tool_refs(payload, "clinvar", {"clinvar"})
    assert payload["hint"] == (
        'Retry call_tool(name="clinvar_get_variant", '
        'arguments={"assembly": "GRCh37", "page": 2, "symbol": ...}).'
    )


def test_next_command_to_an_unpinned_tool_is_wrapped_in_call_tool():
    # #354: a model follows next_commands[].tool by calling the name directly; a client that
    # only exposes the listed tools rejects an unpinned name before it reaches the router.
    payload = {
        "_meta": {
            "next_commands": [
                {"tool": "get_genes_for_disease", "arguments": {"disease_id": "OMIM:1"}},
                {"tool": "mondo_resolve_disease", "arguments": {"query": "x"}},
                {"tool": "hpo_search_terms", "args": {"q": "y"}},
                {"tool": "search_tools", "arguments": {"query": "z"}},
                {"tool": "hpo_get_term"},
            ]
        }
    }
    pinned = {"mondo_resolve_disease"}
    assert rewrite_tool_refs(payload, "hpo", {"hpo", "mondo"}, pinned) == 4
    assert payload["_meta"]["next_commands"] == [
        {
            "tool": "call_tool",
            "arguments": {
                "name": "hpo_get_genes_for_disease",
                "arguments": {"disease_id": "OMIM:1"},
            },
        },
        {"tool": "mondo_resolve_disease", "arguments": {"query": "x"}},  # pinned: direct
        {"tool": "call_tool", "args": {"name": "hpo_search_terms", "arguments": {"q": "y"}}},
        {"tool": "search_tools", "arguments": {"query": "z"}},  # synthetic: direct
        {"tool": "call_tool", "arguments": {"name": "hpo_get_term", "arguments": {}}},
    ]
    # idempotent: a second pass leaves the wrapped steps alone
    assert rewrite_tool_refs(payload, "hpo", {"hpo", "mondo"}, pinned) == 0


def test_prose_argument_parsing_is_linear_on_hostile_input():
    # CodeQL py/redos: "(" + " ,A=" * n once backtracked exponentially.
    import time

    payload = {"hint": "get_x(" + " ,A=" * 5000}
    start = time.perf_counter()
    rewrite_tool_refs(payload, "clinvar", {"clinvar"})
    assert time.perf_counter() - start < 1.0
    assert payload["hint"].startswith('call_tool(name="clinvar_get_x")(')
