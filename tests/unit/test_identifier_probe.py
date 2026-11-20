"""Foreign-identifier conformance (Envelope §2b): classifier, derivation, expected-fail list."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "docs") not in sys.path:
    sys.path.insert(0, str(ROOT / "docs"))

from conformance import identifiers  # noqa: E402
from conformance.identifiers import classify, entity_params, failures, run_probe  # noqa: E402

CONFIG = json.loads((ROOT / "docs/conformance/identifiers.json").read_text())
GENE = CONFIG["entities"]["gene"]
IDS = list(GENE["forms"].values())
ENSG = GENE["forms"]["ensembl"]


def ok(**env: Any) -> dict[str, Any]:
    return {"structuredContent": {"success": True, **env}}


def err(code: str, next_commands: list[dict[str, Any]] | None = None, **env: Any) -> dict[str, Any]:
    meta = {"next_commands": next_commands} if next_commands is not None else {}
    return {
        "isError": True,
        "structuredContent": {"success": False, "error_code": code, "_meta": meta, **env},
    }


CONVERTER = [{"tool": "hgnc_resolve_symbol", "arguments": {"query": ENSG}}]
REJECTION = {"field": "gene", "allowed_values": ["<symbol>"]}

CASES = [
    # PASS
    ("resolved", ok(gene=ENSG, diseases=[{"gene_symbol": "KRAS"}], total=1, resolved_from={})),
    ("resolved", ok(query=ENSG, hgnc_id="HGNC:6407", approved_symbol="KRAS")),
    ("rejected", err("invalid_input", CONVERTER, **REJECTION)),
    ("flagged", ok(results=[{"query": ENSG, "unresolved": True}], unresolved_queries=[ENSG])),
    # FAIL
    ("silent_empty", ok(gene=ENSG, gene_value=ENSG, diseases=[], total=0)),
    ("silent_empty", ok(results=[{"query": ENSG, "hgnc_id": None}], count=0)),
    ("wrong_entity", ok(query=ENSG, hgnc_id="HGNC:3845", approved_symbol="FKBP1A")),
    ("wrong_entity", ok(genes=[{"gene": "TP53"}], unranked_candidate_genes=[ENSG], total=1)),
    ("absence", err("not_found", CONVERTER, message="has no curation")),
    (
        "self_loop",
        err("invalid_input", [{"tool": "hpo_get_x", "arguments": {"gene": ENSG}}], **REJECTION),
    ),
    ("unactionable", err("invalid_input", CONVERTER, field="gene")),
    ("unactionable", err("invalid_input", [], **REJECTION)),
    # SKIP
    ("inconclusive", err("upstream_unavailable")),
]


@pytest.mark.parametrize(
    ("kind", "result"), CASES, ids=[f"{i}-{c[0]}" for i, c in enumerate(CASES)]
)
def test_classify(kind: str, result: dict[str, Any]) -> None:
    assert classify("get_x", "gene", ENSG, result, IDS).kind == kind


def test_resolved_notes_a_missing_resolved_from_and_meta_only_next_step() -> None:
    assert classify("get_x", "gene", ENSG, ok(rows=[{"symbol": "KRAS"}]), IDS).detail
    meta_only = err("invalid_input", [{"tool": "get_server_capabilities"}], **REJECTION)
    assert "meta tool" in classify("get_x", "gene", ENSG, meta_only, IDS).detail


def tool(name: str, **params: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "inputSchema": {"properties": params, "required": []}}


def test_gene_parameters_are_derived_and_corrected_by_declaration() -> None:
    s = {"type": "string"}
    t = tool(
        "get_x",
        gene_symbol=s,
        candidate_genes={"type": "array", "items": s},
        query={**s, "description": "A gene symbol (current/previous/alias) or HGNC id."},
        include_genes={"type": "boolean"},
        gene_set=s,
        text={**s, "description": "Free text to resolve: a gene symbol or disease."},
    )
    entities = CONFIG["entities"]
    assert set(entity_params(t, entities, {})) == {"gene_symbol", "candidate_genes", "query"}
    declared = {"get_x.text": "gene", "get_x.query": None}
    assert set(entity_params(t, entities, declared)) == {"gene_symbol", "candidate_genes", "text"}


class FakeProbe:
    """Resolves the symbol and HGNC id; answers Ensembl with a silent empty."""

    server_info = {"name": "fake-link"}

    def __init__(self, *_: Any) -> None: ...

    def initialize(self) -> None: ...

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            tool("get_x", gene={"type": "string"}),
            tool("get_diagnostics", gene={"type": "string"}),
        ]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if args["gene"] == ENSG:
            return ok(rows=[], total=0)
        if args["gene"].startswith("NCBIGene"):
            return err("upstream_unavailable")
        return ok(rows=[{"hgnc_id": "HGNC:6407", "symbol": "KRAS"}], total=1)


@pytest.mark.parametrize(
    ("known", "expected"),
    [
        ([], {"get_x.gene:ensembl": "FAIL"}),
        (["get_x.gene:ensembl"], {"get_x.gene:ensembl": "XFAIL"}),
        (
            ["get_x.gene:ensembl", "get_x.gene:hgnc"],
            {"get_x.gene:ensembl": "XFAIL", "get_x.gene:hgnc": "XPASS"},
        ),
        (
            ["get_x.gene:ensembl", "gone.gene:hgnc"],
            {"get_x.gene:ensembl": "XFAIL", "gone.gene:hgnc": "STALE"},
        ),
    ],
)
def test_known_failures_are_strict_both_ways(
    known: list[str], expected: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "identifiers.json"
    config.write_text(
        json.dumps(
            {"entities": CONFIG["entities"], "backends": {"fake-link": {"known_failures": known}}}
        )
    )
    monkeypatch.setattr(identifiers, "Probe", FakeProbe)
    table = run_probe("http://fake", expected_name="fake-link", config=config)
    status = {row.key: row.status for row in table}
    assert {k: v for k, v in status.items() if v not in ("PASS", "SKIP")} == expected
    assert status["get_x.gene:symbol"] == "PASS" and status["get_x.gene:ncbigene"] == "SKIP"
    assert len(failures(table)) == sum(v != "XFAIL" for v in expected.values())


def test_seeded_known_failures_name_real_parameters() -> None:
    backends = json.loads((ROOT / "genefoundry_router/data/fleet-baseline.json").read_text())[
        "backends"
    ]
    for name, block in CONFIG["backends"].items():
        tools = {t["name"]: t for t in backends[name.removesuffix("-link")]["tools"]}
        for key in [*block.get("known_failures", []), *block.get("params", {})]:
            tool_name, _, param = key.partition(":")[0].partition(".")
            assert param in tools[tool_name]["inputSchema"]["properties"], f"{name}: {key}"
            assert key.partition(":")[2] in ("", *GENE["forms"]), f"{name}: {key}"
