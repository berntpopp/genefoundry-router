"""Offline document expansion: loading, validation, caps, and the stale-entry hash."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from genefoundry_router.search_expansion import (
    MAX_ITEM_CHARS,
    MAX_ITEMS_PER_FIELD,
    catalog_entry_sha256,
    expansion_texts,
    load_expansion_file,
)


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "exp.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _doc(tools: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "generator": {"models": ["m"]}, "tools": tools}


def test_off_and_blank_disable_expansion() -> None:
    assert expansion_texts("off") == {}
    assert expansion_texts("") == {}
    assert expansion_texts("  OFF ") == {}


def test_builtin_expansions_are_packaged_and_cover_the_fleet() -> None:
    texts = expansion_texts("builtin")
    assert len(texts) >= 250  # ~285 fleet tools at generation time
    assert all(isinstance(v, str) and v for v in texts.values())
    assert "spliceai_predict_splicing" in texts


def test_file_expansions_join_queries_and_keywords(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        _doc({"x_tool": {"source_sha256": "a" * 64, "queries": ["how rare"], "keywords": ["AF"]}}),
    )
    assert expansion_texts(str(path)) == {"x_tool": "how rare AF"}


def test_items_and_lengths_are_capped(tmp_path: Path) -> None:
    many = [f"q{i} " + "w" * (MAX_ITEM_CHARS * 2) for i in range(MAX_ITEMS_PER_FIELD + 10)]
    path = _write(tmp_path, _doc({"x_tool": {"queries": many, "keywords": []}}))
    text = expansion_texts(str(path))["x_tool"]
    assert text.count("q") == MAX_ITEMS_PER_FIELD
    assert len(text) <= MAX_ITEMS_PER_FIELD * (MAX_ITEM_CHARS + 1)


def test_control_characters_are_stripped(tmp_path: Path) -> None:
    path = _write(tmp_path, _doc({"x_tool": {"queries": ["a\x00b‮c"], "keywords": []}}))
    assert expansion_texts(str(path))["x_tool"] == "a b c"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 2, "tools": {}},
        {"version": 1},
        {"version": 1, "tools": []},
        {"version": 1, "tools": {"x": {"queries": "not-a-list"}}},
        {"version": 1, "tools": {"x": {"queries": [1, 2]}}},
        {"version": 1, "tools": {"x": ["bad"]}},
    ],
)
def test_malformed_files_fail_loudly(tmp_path: Path, payload: object) -> None:
    with pytest.raises(ValueError):
        load_expansion_file(_write(tmp_path, payload))


def test_missing_file_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        expansion_texts(str(tmp_path / "missing.json"))


def test_invalid_json_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "exp.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        load_expansion_file(path)


def test_catalog_entry_hash_is_order_insensitive_and_content_sensitive() -> None:
    a = catalog_entry_sha256("x_t", "desc", {"p": "one", "q": "two"})
    assert a == catalog_entry_sha256("x_t", "desc", {"q": "two", "p": "one"})
    assert a != catalog_entry_sha256("x_t", "desc changed", {"p": "one", "q": "two"})
    assert a != catalog_entry_sha256("x_t", "desc", {"p": "one"})
    assert len(a) == 64


def test_manual_keywords_lead_the_keywords_and_survive_the_cap(tmp_path: Path) -> None:
    generated = [f"g{i}" for i in range(MAX_ITEMS_PER_FIELD)]
    entry = {"queries": [], "keywords": generated, "manual_keywords": ["PMC"]}
    text = expansion_texts(str(_write(tmp_path, _doc({"x_tool": entry}))), only=("keywords",))
    assert text["x_tool"].startswith("PMC g0 ") and f"g{MAX_ITEMS_PER_FIELD - 1}" not in text


def test_regeneration_keeps_the_manual_overlay(tmp_path: Path, monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "gen_exp", root / "scripts/gen_tool_expansions.py"
    )
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    monkeypatch.setattr(gen, "_generate", lambda *a: {"queries": ["new q"], "keywords": ["new"]})
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    catalog = _write(tmp_path, [{"name": "x_tool", "description": "changed", "params": {}}])
    out = tmp_path / "out.json"
    stale = {"source_sha256": "old", "queries": [], "keywords": ["k"], "manual_keywords": ["PMC"]}
    out.write_text(json.dumps(_doc({"x_tool": stale})), encoding="utf-8")
    argv = ["--catalog", str(catalog), "--out", str(out), "--cache-dir", str(tmp_path / "c")]
    assert gen.main(argv) == 0
    entry = json.loads(out.read_text(encoding="utf-8"))["tools"]["x_tool"]
    assert entry["keywords"] == ["new"] and entry["manual_keywords"] == ["PMC"]
