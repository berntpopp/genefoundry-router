"""Regression tests for behaviour-gate envelope row detection."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_GATE_PATH = Path(__file__).resolve().parents[2] / "docs" / "conformance" / "behaviour.py"
_spec = importlib.util.spec_from_file_location("gf_behaviour_gate_rows_under_test", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
behaviour = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = behaviour
_spec.loader.exec_module(behaviour)


def test_empty_auxiliary_dict_does_not_hide_counted_rows() -> None:
    env: dict[str, Any] = {
        "success": True,
        "count": 2,
        "results": [{"id": "a"}, {"id": "b"}],
        "facets": {},
    }

    assert behaviour.rows(env) == [{"id": "a"}, {"id": "b"}]


def test_empty_grouped_collection_still_counts_as_empty_rows() -> None:
    env: dict[str, Any] = {
        "success": True,
        "count": 2,
        "mappings": {},
    }

    assert behaviour.rows(env) == []


def test_rows_and_counts_are_read_inside_result() -> None:
    """Response-Envelope v1.2 backends carry the payload, and often its counts, under `result`."""
    env: dict[str, Any] = {
        "success": True,
        "result": {"variants": [{"id": "a"}, {"id": "b"}], "total": 9, "truncated": True},
        "_meta": {},
    }

    assert behaviour.rows(env) == [{"id": "a"}, {"id": "b"}]
    assert behaviour.count_of(env) == 9
    assert behaviour.total_of(env) == 9
    assert behaviour.more_flag(env) is True


def test_grouped_collection_inside_result() -> None:
    env: dict[str, Any] = {"success": True, "result": {"mappings": {"OMIM": [{"id": "x"}]}}}

    assert behaviour.rows(env) == [{"id": "x"}]


def test_record_inside_result_is_not_a_collection() -> None:
    env: dict[str, Any] = {"success": True, "result": {"id": "HP:1", "children": [{"id": "c"}]}}

    assert behaviour.rows(env) is None


def test_top_level_counts_win_over_result() -> None:
    env: dict[str, Any] = {"success": True, "total": 3, "result": {"total": 7}}

    assert behaviour.total_of(env) == 3
