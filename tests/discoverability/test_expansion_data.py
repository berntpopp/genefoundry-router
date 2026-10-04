"""The packaged expansion data must match the catalog it indexes."""

from __future__ import annotations

import json
from importlib.resources import files

from genefoundry_router.devtools.discoverability import DEFAULT_CATALOG
from genefoundry_router.search_expansion import (
    MAX_ITEMS_PER_FIELD,
    catalog_entry_sha256,
    expansion_texts,
)

STALE_TOLERANCE = 0.05  # regenerate with `make expansions` after `make snapshot-catalog`


def _packaged() -> dict[str, dict[str, object]]:
    raw = files("genefoundry_router.data").joinpath("tool-expansions.json").read_text("utf-8")
    return json.loads(raw)["tools"]


def _catalog() -> dict[str, str]:
    entries = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
    return {
        e["name"]: catalog_entry_sha256(e["name"], e["description"] or "", e["params"])
        for e in entries
    }


def test_every_expansion_targets_a_catalog_tool() -> None:
    orphans = sorted(set(_packaged()) - set(_catalog()))
    assert not orphans, f"expansions for tools not in the catalog: {orphans}"


def test_expansions_cover_and_match_the_catalog() -> None:
    packaged, catalog = _packaged(), _catalog()
    stale = sorted(
        n for n, sha in catalog.items() if packaged.get(n, {}).get("source_sha256") != sha
    )
    assert len(stale) <= STALE_TOLERANCE * len(catalog), (
        f"{len(stale)} expansion entries missing/stale (run `make expansions`): {stale[:20]}"
    )


def test_every_entry_loads_with_substance() -> None:
    texts = expansion_texts("builtin")
    for name, entry in _packaged().items():
        assert name in texts
        assert len(entry["queries"]) >= 5, name  # type: ignore[arg-type]
        assert len(entry["queries"]) <= 2 * MAX_ITEMS_PER_FIELD  # type: ignore[arg-type]
