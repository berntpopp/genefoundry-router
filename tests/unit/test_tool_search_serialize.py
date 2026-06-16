"""Unit tests for compact discovery serialization (Finding 2: token cost)."""

from __future__ import annotations

from genefoundry_router.devtools.fakes import _EchoTool
from genefoundry_router.tool_search import serialize_tools_compact, summarize_returns

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "gene_symbol": {"type": "string", "description": "HGNC symbol"},
        "response_mode": {"type": "string", "enum": ["compact", "full"]},
    },
    "required": ["gene_symbol"],
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "records": {"type": "array", "items": {"type": "object"}},
        "_meta": {"type": "object"},
    },
}


def _tool() -> _EchoTool:
    return _EchoTool(
        name="clingen_get_gene_validity",
        description="Use this to fetch ClinGen gene-disease validity.",
        parameters=_INPUT_SCHEMA,
        output_schema=_OUTPUT_SCHEMA,
        tags={"gene-disease", "curation"},
        meta={"verbose": "x" * 500},  # the repeated MCPMeta block the report flagged
    )


def test_compact_keeps_input_schema_drops_output_and_meta():
    [entry] = serialize_tools_compact([_tool()])
    assert entry["name"] == "clingen_get_gene_validity"
    assert entry["description"].startswith("Use this")
    # full input schema is preserved — it is the agent's argument contract
    assert entry["inputSchema"] == _INPUT_SCHEMA
    # the nested output schema and the verbose meta block are gone
    assert "outputSchema" not in entry
    assert "meta" not in entry and "_meta" not in entry
    # tags survive (sorted)
    assert entry["tags"] == ["curation", "gene-disease"]


def test_compact_emits_one_line_returns_summary():
    [entry] = serialize_tools_compact([_tool()])
    returns = entry["returns"]
    assert isinstance(returns, str)
    assert "\n" not in returns  # genuinely one line
    # names the top-level output fields with terse types
    assert "success" in returns and "boolean" in returns
    assert "records" in returns and "object[]" in returns


def test_compact_omits_returns_when_no_output_schema():
    t = _EchoTool(name="ns_t", description="d", parameters={"type": "object", "properties": {}})
    [entry] = serialize_tools_compact([t])
    assert "returns" not in entry


def test_summarize_returns_handles_non_object_schema():
    assert summarize_returns({"type": "string"}) == "string"
    assert summarize_returns({"type": "array", "items": {"type": "string"}}) == "string[]"
