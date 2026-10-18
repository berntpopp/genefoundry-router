"""Unit tests for tool-surface CI linter rules (including zero-arg get_diagnostics)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from check_tool_surface import check_backend, lint_server, lint_tool_schema  # noqa: E402


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
            "description": "Zero-argument health check.",
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


def test_lint_server_reports_s5_violations() -> None:
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
    violations = lint_server("demo", backend_entry)
    assert any(v.rule == "S5" for v in violations)
    assert check_backend("demo", backend_entry) == violations
