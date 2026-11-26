"""Pin the WARN-level resolver-miss probe (Response-Envelope Standard v1 §2a, router #301).

A `resolve_*` tool fed a value nothing can match should answer `not_found` with
`_meta.next_commands`. The probe only WARNS: it must never change the conformance verdict, so the
vendored gate stays backwards-compatible across the fleet.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_GATE_PATH = Path(__file__).resolve().parents[2] / "docs" / "conformance" / "behaviour.py"
_spec = importlib.util.spec_from_file_location("gf_behaviour_gate_resolver_miss", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
behaviour = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = behaviour
_spec.loader.exec_module(behaviour)

NAME = "demo-link"
RESOLVER: dict[str, Any] = {
    "name": "resolve_disease",
    "inputSchema": {
        "type": "object",
        "required": ["query"],
        "properties": {"query": {"type": "string", "examples": ["cystic fibrosis"]}},
    },
}
PROVENANCE = {
    "source": "mondo",
    "data_version": "2026-11-03",
    "retrieved_at": "2026-11-20T08:00:00Z",
}
HIT: dict[str, Any] = {
    "structuredContent": {"success": True, "result": {"id": "MONDO:0009061"}, "_meta": PROVENANCE}
}


def _not_found(next_commands: list[Any]) -> dict[str, Any]:
    env = {"success": False, "error_code": "not_found", "message": "no match"}
    env["_meta"] = {"next_commands": next_commands}
    return {"isError": True, "structuredContent": env}


def _run(monkeypatch: pytest.MonkeyPatch, miss: dict[str, Any], hit: dict[str, Any] = HIT) -> Any:
    class Fake:
        def __init__(self, base_url: str, timeout: float = 60.0) -> None:
            self.server_info = {"name": NAME}

        def initialize(self) -> dict[str, Any]:
            return {}

        def list_tools(self) -> list[dict[str, Any]]:
            return [RESOLVER]

        def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
            if behaviour.BOGUS_ARG in args:
                return {
                    "isError": True,
                    "structuredContent": {
                        "success": False,
                        "error_code": "invalid_input",
                        "field": behaviour.BOGUS_ARG,
                    },
                }
            return miss if args.get("query") == behaviour.SENTINEL else hit

    monkeypatch.setattr(behaviour, "Probe", Fake)
    return behaviour.run_probe("http://fake", expected_name=NAME)


@pytest.mark.parametrize(
    ("meta", "named"),
    [
        ({}, "source, data_version, retrieved_at"),
        ({**PROVENANCE, "retrieved_at": ""}, "retrieved_at"),
        ({**PROVENANCE, "data_version": None}, "data_version"),
    ],
)
def test_success_without_provenance_fails(
    monkeypatch: pytest.MonkeyPatch, meta: dict[str, Any], named: str
) -> None:
    """Response-Envelope Standard v1.2: a missing or empty provenance field fails the gate."""
    hit = {"structuredContent": {"success": True, "result": {"id": "X"}, "_meta": meta}}
    rep = _run(monkeypatch, _not_found([{"tool": "search_diseases"}]), hit)
    assert not rep.conformant
    assert any(
        f.endswith(f"missing or empty: {named} (Response-Envelope Standard v1.2)")
        for f in rep.failed
    )


def test_success_with_provenance_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    rep = _run(monkeypatch, _not_found([{"tool": "search_diseases"}]))
    assert rep.conformant, rep.failed
    assert any("success carries _meta" in p for p in rep.passed)


def test_not_found_with_next_commands_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    rep = _run(monkeypatch, _not_found([{"tool": "search_diseases"}]))
    assert not rep.warned
    assert any("resolver miss" in p for p in rep.passed)


@pytest.mark.parametrize(
    ("miss", "why"),
    [
        (_not_found([]), "no _meta.next_commands"),
        ({"structuredContent": {"success": True, "result": None}}, "answered success"),
    ],
)
def test_unhelpful_miss_warns_without_failing(
    monkeypatch: pytest.MonkeyPatch, miss: dict[str, Any], why: str
) -> None:
    rep = _run(monkeypatch, miss)
    assert any(why in w for w in rep.warned), rep.warned
    assert rep.conformant, rep.failed


def test_openai_compat_tools_are_exempt_from_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    """`search`/`fetch` return OpenAI's fixed document shape (v1.2 exemption), not the envelope."""
    monkeypatch.setitem(RESOLVER, "name", "fetch")
    hit = {"structuredContent": {"id": "X", "title": "t", "text": "…", "url": "u", "metadata": {}}}
    rep = _run(monkeypatch, _not_found([{"tool": "search"}]), hit)
    assert rep.conformant, rep.failed
    assert not any("success carries _meta" in p for p in rep.passed)
