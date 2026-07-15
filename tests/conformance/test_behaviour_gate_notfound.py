"""Pin the behaviour gate's treatment of an honest `not_found` on a tool's OWN documented example.

A tool keyed on a runtime-issued handle (a session/job id, an opaque cursor) can never ship a
static `examples` value that resolves against a fresh deployment; its only honest answer to the
gate's example-built control call is `not_found`. The earlier gate counted that as "the documented
example was rejected" and failed the whole server — it red-flagged pubtator-link over
`get_research_session_status`, and would have pressured such tools back toward a vaguer, less
actionable error just to pass.

The fix: `not_found` on the example control call is INCONCLUSIVE (the call FORM was valid; the
entity is merely absent — nothing to verify a filter/page against), while a MALFORMED example
(`invalid_input` / `ambiguous_query`) STILL fails. Both directions are asserted here so the branch
cannot silently over-broaden into hiding a real documentation defect.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_GATE_PATH = Path(__file__).resolve().parents[2] / "docs" / "conformance" / "behaviour.py"
_spec = importlib.util.spec_from_file_location("gf_behaviour_gate_under_test", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
behaviour = importlib.util.module_from_spec(_spec)
# Register before exec so the module's @dataclass (Report) can resolve its own __module__.
sys.modules[_spec.name] = behaviour
_spec.loader.exec_module(behaviour)

EXPECTED_NAME = "demo-link"

# One required parameter whose example is a runtime handle that cannot resolve statically.
HANDLE_TOOL: dict[str, Any] = {
    "name": "get_session_status",
    "inputSchema": {
        "type": "object",
        "required": ["session_id"],
        "properties": {
            "session_id": {
                "type": "string",
                "description": "An opaque session handle issued at runtime.",
                "examples": ["sess-abc123"],
            }
        },
    },
}


def _error_result(code: str) -> dict[str, Any]:
    """A well-formed fleet error tool-result: isError:true + a flat structured envelope."""
    return {
        "isError": True,
        "structuredContent": {
            "success": False,
            "error_code": code,
            "message": f"answered with {code}",
            "field": "session_id",
        },
    }


class _FakeProbe:
    """Feeds run_probe canned wire responses; the example control call returns `control_code`."""

    control_code = "not_found"

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.base_url = base_url
        self.server_info: dict[str, Any] = {}

    def initialize(self) -> dict[str, Any]:
        self.server_info = {"name": EXPECTED_NAME}
        return {"serverInfo": self.server_info}

    def list_tools(self) -> list[dict[str, Any]]:
        return [HANDLE_TOOL]

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # The unknown-argument probe always gets a proper, actionable invalid_input.
        if behaviour.BOGUS_ARG in args:
            return _error_result("invalid_input")
        # The example-built control call returns the code under test.
        return _error_result(type(self).control_code)


def _run(monkeypatch: pytest.MonkeyPatch, control_code: str) -> Any:
    fake = type("FakeProbe", (_FakeProbe,), {"control_code": control_code})
    monkeypatch.setattr(behaviour, "Probe", fake)
    return behaviour.run_probe("http://fake", expected_name=EXPECTED_NAME)


def test_not_found_on_example_is_inconclusive_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rep = _run(monkeypatch, "not_found")
    assert rep.conformant, (
        "an honest not_found on a tool's own example must not fail the gate; "
        f"failed={rep.failed} ungated={rep.ungated}"
    )
    assert any("get_session_status" in s and "not_found" in s for s in rep.skipped), (
        f"expected a not_found skip for the handle tool; skipped={rep.skipped}"
    )
    assert not any("documented example is accepted" in f for f in rep.failed), (
        f"not_found must not produce an example-acceptance failure; failed={rep.failed}"
    )


def test_invalid_input_on_example_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # The other direction: a MALFORMED example is a real documentation defect and must STILL fail,
    # so the not_found carve-out has not swallowed the check whole.
    rep = _run(monkeypatch, "invalid_input")
    assert not rep.conformant, "a malformed (invalid_input) example must still fail the gate"
    assert any("documented example is accepted" in f for f in rep.failed), (
        f"expected an example-acceptance failure for invalid_input; failed={rep.failed}"
    )
