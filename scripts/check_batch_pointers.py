"""Batch-pointer lint (Tool-Schema Documentation Standard D2) — offline, against the baseline.

    python scripts/check_batch_pointers.py

A pinned (entrypoint) tool whose backend has a batch or set-level sibling MUST name that sibling
in its description, so a model holding 15 ids makes one call, not 15 (router #343).

Siblings are derived: same backend, same name once `_batch`/`_bulk` is dropped and every word is
singularised (`get_variant` -> `get_variants`, `resolve_symbol` -> `resolve_symbols_batch`).
SET_LEVEL adds the set-level tools naming cannot find. KNOWN_FAILURES is strict both ways: a
listed pair that now passes is an error, so a fix cannot regress unnoticed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "genefoundry_router/data/fleet-baseline.json"

# namespace -> pinned leaf -> set-level siblings that share no name with it.
SET_LEVEL: dict[str, dict[str, list[str]]] = {
    "hpo": {
        "get_phenotypes_for_gene": ["score_genes_for_phenotypes"],
        "get_genes_for_phenotype": ["score_genes_for_phenotypes"],
    },
    "stringdb": {"get_interaction_partners": ["compute_functional_enrichment"]},
}

# "<namespace>_<pinned> -> <sibling>" pairs failing on 2026-11-20. Delete a line when it is fixed.
KNOWN_FAILURES: frozenset[str] = frozenset(
    {
        "autopvs1_get_variant_pvs1_data -> get_variants_pvs1_data_bulk",
        "clinvar_get_variant -> get_variants",
        "genereviews_search_passages -> search_passages_batch",
        "hgnc_resolve_symbol -> resolve_symbols_batch",
        "hpo_get_genes_for_phenotype -> score_genes_for_phenotypes",
        "hpo_get_phenotypes_for_gene -> score_genes_for_phenotypes",
        "mondo_resolve_disease -> resolve_disease_batch",
        "spliceai_predict_splicing -> predict_splicing_batch",
        "stringdb_get_interaction_partners -> compute_functional_enrichment",
        "uniprot_find_proteins -> find_proteins_batch",
        "vep_annotate_variant -> annotate_variants_batch",
    }
)


def _stem(name: str) -> tuple[str, ...]:
    words = [w for w in name.split("_") if w not in ("batch", "bulk")]
    return tuple(w.removesuffix("s") for w in words)


def siblings(namespace: str, pinned: str, tool_names: list[str]) -> list[str]:
    derived = [t for t in tool_names if t != pinned and _stem(t) == _stem(pinned)]
    explicit = [t for t in SET_LEVEL.get(namespace, {}).get(pinned, []) if t in tool_names]
    return sorted({*derived, *explicit})


def missing_pointers(backends: dict[str, Any], entrypoints: dict[str, list[str]]) -> set[str]:
    """Every `"<ns>_<pinned> -> <sibling>"` whose pinned description does not name the sibling."""
    missing: set[str] = set()
    for namespace, pinned_tools in entrypoints.items():
        tools = {t["name"]: t for t in backends.get(namespace, {}).get("tools", [])}
        for pinned in pinned_tools:
            description = (tools.get(pinned) or {}).get("description") or ""
            for sibling in siblings(namespace, pinned, list(tools)):
                # A whole name, optionally namespaced: `get_variants` is not named by `get_variants_by_gene`.
                named = rf"(?<![A-Za-z0-9_])(?:{namespace}_)?{re.escape(sibling)}(?![A-Za-z0-9_])"
                if not re.search(named, description):
                    missing.add(f"{namespace}_{pinned} -> {sibling}")
    return missing


def load() -> tuple[dict[str, Any], dict[str, list[str]]]:
    backends = json.loads((ROOT / BASELINE).read_text())["backends"]
    servers = yaml.safe_load((ROOT / "servers.yaml").read_text())["servers"]
    return backends, {s["namespace"]: s.get("entrypoints") or [] for s in servers}


def main() -> int:
    missing = missing_pointers(*load())
    new, fixed = sorted(missing - KNOWN_FAILURES), sorted(KNOWN_FAILURES - missing)
    for pair in sorted(missing & KNOWN_FAILURES):
        print(f"  XFAIL  {pair}")
    for pair in new:
        print(f"  FAIL   {pair} — the pinned description does not name its batch sibling")
    for pair in fixed:
        print(f"  XPASS  {pair} — fixed; delete it from KNOWN_FAILURES")
    print(
        f"\nD2 batch pointers: {len(new)} new, {len(fixed)} fixed-but-listed, "
        f"{len(missing & KNOWN_FAILURES)} known"
    )
    return 1 if new or fixed else 0


if __name__ == "__main__":
    sys.exit(main())
