"""Batch-pointer lint (Tool-Schema Documentation Standard D2)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import check_batch_pointers as lint  # noqa: E402


def _backend(**descriptions: str) -> dict:
    return {"tools": [{"name": n, "description": d} for n, d in descriptions.items()]}


def test_siblings_are_derived_from_names() -> None:
    names = ["get_variant", "get_variants", "get_variant_details", "resolve_symbols_batch"]
    assert lint.siblings("x", "get_variant", names) == ["get_variants"]
    assert lint.siblings("x", "resolve_symbol", names) == ["resolve_symbols_batch"]
    assert lint.siblings("x", "get_variants_pvs1_data", ["get_variant_pvs1_data_bulk"]) == [
        "get_variant_pvs1_data_bulk"
    ]


def test_set_level_sibling_comes_from_the_explicit_map() -> None:
    names = ["get_phenotypes_for_gene", "score_genes_for_phenotypes"]
    assert lint.siblings("hpo", "get_phenotypes_for_gene", names) == ["score_genes_for_phenotypes"]
    assert lint.siblings("other", "get_phenotypes_for_gene", names) == []


def test_pinned_description_must_name_the_sibling() -> None:
    backends = {
        "a": _backend(get_gene="One gene.", get_genes="Many genes."),
        "b": _backend(get_gene="One gene. For 2 or more, call get_genes once.", get_genes="Many."),
        "c": _backend(get_gene="One gene.", get_genes="Many genes."),  # not pinned: not checked
    }
    pins = {"a": ["get_gene"], "b": ["get_gene"], "c": []}
    assert lint.missing_pointers(backends, pins) == {"a_get_gene -> get_genes"}


def test_sibling_is_matched_as_a_whole_name_optionally_namespaced() -> None:
    backends = {
        "a": _backend(get_gene="One gene; see get_genes_by_panel.", get_genes="Many."),
        "b": _backend(get_gene="One gene. For a list call b_get_genes.", get_genes="Many."),
    }
    pins = {"a": ["get_gene"], "b": ["get_gene"]}
    assert lint.missing_pointers(backends, pins) == {"a_get_gene -> get_genes"}


def test_known_failures_match_the_packaged_baseline_exactly() -> None:
    # Strict both ways: a new miss fails, and so does a fixed pair still listed.
    assert lint.missing_pointers(*lint.load()) == lint.KNOWN_FAILURES
