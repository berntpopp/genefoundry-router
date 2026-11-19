"""Unit tests for tool-surface CI linter rules (including zero-arg get_diagnostics)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from check_tool_surface import check_backend, lint_tool_schema  # noqa: E402


def test_get_diagnostics_advertising_response_mode_is_flagged() -> None:
    tool_exact = {
        "name": "get_diagnostics",
        "description": "System health and diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "response_mode": {
                    "type": "string",
                    "description": "Output mode: compact or full.",
                    "enum": ["compact", "full"],
                }
            },
        },
    }
    violations = lint_tool_schema(tool_exact, server="test-link")
    s5_violations = [v for v in violations if v.rule == "S5"]
    assert len(s5_violations) == 1
    assert "get_diagnostics must not advertise response_mode" in s5_violations[0].detail
    assert "zero-argument" in s5_violations[0].detail


def test_diagnostics_name_variants_with_response_mode_are_flagged() -> None:
    for name in ("metadome_get_diagnostics", "service_diagnostics"):
        tool = {
            "name": name,
            "description": "Diagnostic health check.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "response_mode": {
                        "type": "string",
                        "description": "Output mode.",
                        "enum": ["compact", "full"],
                    }
                },
            },
        }
        violations = lint_tool_schema(tool, server="test-link")
        s5 = [v for v in violations if v.rule == "S5"]
        assert len(s5) == 1, f"Expected S5 violation for {name}"
        assert f"{name} must not advertise response_mode" in s5[0].detail


def test_get_diagnostics_zero_argument_passes() -> None:
    for name in ("get_diagnostics", "clinvar_get_diagnostics", "hpo_diagnostics"):
        tool = {
            "name": name,
            "description": "Returns backend health; takes no arguments.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        }
        violations = lint_tool_schema(tool, server="test-link")
        assert not any(v.rule == "S5" for v in violations)
        assert violations == []


def test_non_diagnostics_tool_with_response_mode_not_flagged_by_s5() -> None:
    tool = {
        "name": "search_genes",
        "description": "Search gene catalog.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "response_mode": {
                    "type": "string",
                    "description": "Output verbosity.",
                    "enum": ["compact", "full"],
                }
            },
        },
    }
    violations = lint_tool_schema(tool, server="test-link")
    assert not any(v.rule == "S5" for v in violations)


def test_check_backend_reports_s5_violations() -> None:
    backend_entry: dict[str, Any] = {
        "tools": [
            {
                "name": "get_diagnostics",
                "description": "Health check.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "response_mode": {
                            "type": "string",
                            "description": "Verbosity.",
                        }
                    },
                },
            },
            {
                "name": "get_data",
                "description": "Fetch data.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
    }
    violations = check_backend("demo", backend_entry)
    assert any(v.rule == "S5" for v in violations)


# D1 (Tool-Schema Documentation Standard v1.1): real fleet leads, verbatim first sentences.
D1_PASS = [
    "Return a Mondo disease term: definition, synonyms, grouped cross-references.",  # returns
    "Retrieve homology scores for proteins showing sequence similarity.",  # output verb
    "Use this to search ClinGen dosage (genes + regions) by text or ISCA region.",
    "Use this for ClinGen clinical actionability of a gene: adult/pediatric status, release.",
    "Use this when a caller needs allele counts; returns per-population frequencies.",
    "Batch lookup → one {gene, panels} row per symbol.",
]
D1_FAIL = [
    "Use this when a user needs PubMed literature search through PubTator3.",
    "Signature: get_tolerance_landscape(transcript_id, position_start=, limit=).",
    "Perform functional enrichment analysis.",
    "Convenience orchestration tool. Returns a GeneReview summary.",  # returns is sentence 2
    "Use this when a caller needs variant details. This tool does NOT return ClinVar.",
    "Use this when a client needs supported tools. Returns ~7kB.",
    "",
]


def test_d1_lead_states_return_on_real_fleet_leads() -> None:
    from surface import lead_states_return

    assert [d for d in D1_PASS if not lead_states_return(d)] == []
    assert [d for d in D1_FAIL if lead_states_return(d)] == []


def test_d1_negated_and_sized_returns_do_not_count() -> None:
    from surface import lead_states_return

    assert not lead_states_return("Use this for variant details; does not return ClinVar.")
    assert not lead_states_return("Use this for variant details; it doesn\u2019t return ClinVar.")
    assert not lead_states_return("Use this when a client needs capabilities; returns ~7kB.")


def test_d1_is_flagged_but_advisory() -> None:
    from check_tool_surface import ADVISORY

    tool = {"name": "search_literature", "description": D1_FAIL[0], "inputSchema": {}}
    assert [v.rule for v in lint_tool_schema(tool)] == ["D1"]
    assert "D1" in ADVISORY
