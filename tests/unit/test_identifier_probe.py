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
from conformance.identifiers import (  # noqa: E402
    base_args,
    classify,
    entity_params,
    failures,
    run_probe,
)

CONFIG = json.loads((ROOT / "docs/conformance/identifiers.json").read_text())
GENE = CONFIG["entities"]["gene"]
IDS = list(GENE["forms"].values())
ENSG = GENE["forms"]["ensembl"]
# What a by-gene tool really returns: rows that never repeat the gene.
CONTROL = [{"disease": "Noonan syndrome 3"}, {"disease": "Cardiofaciocutaneous syndrome 2"}]


def ok(**env: Any) -> dict[str, Any]:
    return {"structuredContent": {"success": True, **env}}


def err(code: str, steps: list[dict[str, Any]] | None = None, **env: Any) -> dict[str, Any]:
    meta = {"next_commands": steps} if steps is not None else {}
    body = {"success": False, "error_code": code, "_meta": meta, **env}
    return {"isError": True, "structuredContent": body}


def step(tool: str, value: str = ENSG) -> dict[str, Any]:
    return {"tool": tool, "arguments": {"gene": value}}


CONVERTER = {"tool": "hgnc_resolve_symbol", "arguments": {"query": ENSG}}
META = {"tool": "get_server_capabilities", "arguments": {}}
FIELDS = {"field": "gene", "allowed_values": ["<symbol>"]}
RESOLVED_FROM = {"input": ENSG, "form": "ensembl", "resolved_to": "KRAS"}

CASES = [
    # PASS — resolved: the control's rows, resolved_from, or rows that name the gene
    ("resolved", ok(gene=ENSG, diseases=CONTROL, total=2)),
    ("resolved", ok(diseases=[{"disease": "x"}], total=1, resolved_from=RESOLVED_FROM)),
    ("resolved", ok(query=ENSG, hgnc_id="HGNC:6407", approved_symbol="KRAS")),
    ("resolved", ok(genes=[{"gene": "KRAS", "input_id": ENSG}], total=1)),
    # PASS — rejected with a converter (a loop or a meta tool beside it does not spoil it)
    ("rejected", err("invalid_input", [CONVERTER], **FIELDS)),
    ("rejected", err("invalid_input", [step("get_x"), CONVERTER, META], **FIELDS)),
    ("rejected", err("invalid_input", [step("hgnc_get_x")], **FIELDS)),  # another backend's tool
    # PASS — flagged per item
    ("flagged", ok(results=[], count=0, unsupported_identifiers=[ENSG])),
    # FAIL — silent empty, including answers that only echo or talk about the gene
    ("silent_empty", ok(gene=ENSG, gene_value=ENSG, diseases=[], total=0)),
    ("silent_empty", ok(query=ENSG, hgnc_id=None, found=False)),
    ("silent_empty", ok(gene=ENSG, gene_value=ENSG, applied_filters={"gene": "KRAS"})),
    ("silent_empty", ok(message="KRAS has no rows", results=[], total=0)),
    ("silent_empty", ok(results=[], total=0, not_found=[ENSG], unresolved_queries=[ENSG])),
    # FAIL — a different entity; a mention outside the rows does not rescue it
    ("wrong_entity", ok(query=ENSG, hgnc_id="HGNC:3845", approved_symbol="FKBP1A")),
    ("wrong_entity", ok(note="results for KRAS", diseases=[{"disease": "x"}], total=1)),
    ("wrong_entity", ok(genes=[{"gene": "TP53"}], unranked_candidate_genes=[ENSG], total=1)),
    ("wrong_entity", ok(results=[{"query": ENSG, "unresolved": True}], unresolved_queries=[ENSG])),
    # FAIL — errors
    ("absence", err("not_found", [CONVERTER], message="has no curation")),
    ("internal", err("internal")),
    ("self_loop", err("invalid_input", [step("get_x"), step("hpo_get_x", ENSG.lower())], **FIELDS)),
    ("unactionable", err("invalid_input", [META], **FIELDS)),
    ("unactionable", err("invalid_input", [step("get_x"), META], **FIELDS)),
    ("unactionable", err("invalid_input", [], **FIELDS)),
    ("unactionable", err("invalid_input", [CONVERTER], field="gene")),
    ("unactionable", err("invalid_input", [CONVERTER], allowed_values=["<symbol>"])),
    # SKIP
    ("inconclusive", err("upstream_unavailable")),
    ("inconclusive", err("rate_limited")),
]


@pytest.mark.parametrize(
    ("kind", "result"), CASES, ids=[f"{i}-{c[0]}" for i, c in enumerate(CASES)]
)
def test_classify(kind: str, result: dict[str, Any]) -> None:
    assert classify("get_x", "gene", ENSG, result, IDS, CONTROL, "hpo").kind == kind


def tool(name: str, required: list[str] | None = None, **params: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "inputSchema": {"properties": params, "required": required or []}}


STR = {"type": "string"}


def test_gene_parameters_are_derived_and_corrected_by_declaration() -> None:
    t = tool(
        "get_x",
        gene_symbol=STR,
        candidate_genes={"type": "array", "items": STR},
        query={**STR, "description": "A gene symbol (current/previous/alias) or HGNC id."},
        include_genes={"type": "boolean"},
        gene_set=STR,
        text={**STR, "description": "Free text to resolve: a gene symbol or disease."},
    )
    entities = CONFIG["entities"]
    assert set(entity_params(t, entities, {})) == {"gene_symbol", "candidate_genes", "query"}
    declared = {"get_x.text": "gene", "get_x.query": None}
    found = entity_params(t, entities, declared)
    assert found == {
        "gene_symbol": "gene",
        "candidate_genes": "gene",
        "text": "gene",
        "query": None,
    }


def test_base_args_needs_no_example_for_the_probed_or_a_same_entity_parameter() -> None:
    t = tool("get_x", ["gene", "hgnc_id", "tissue"], gene=STR, hgnc_id=STR, tissue=STR)
    assert base_args(t, "hgnc_id", {"gene": "KRAS"}) is None  # tissue has no example
    t["inputSchema"]["properties"]["tissue"] = {**STR, "examples": ["Lung"]}
    assert base_args(t, "hgnc_id", {"gene": "KRAS"}) == {"gene": "KRAS", "tissue": "Lung"}


class FakeProbe:
    """One by-gene tool whose rows never name the gene; `answers` maps a sent value to a result."""

    server_info = {"name": "fake-link"}
    answers: dict[str, dict[str, Any]] = {}
    default: dict[str, Any] = ok(rows=CONTROL, total=2)

    def __init__(self, *_: Any) -> None: ...

    def initialize(self) -> None: ...

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool("get_x", gene=STR), tool("get_diagnostics", gene=STR)]

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self.answers.get(args["gene"], self.default)


def run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any], **block: Any
) -> tuple[dict[str, str], list[str]]:
    config = tmp_path / "identifiers.json"
    config.write_text(
        json.dumps({"entities": CONFIG["entities"], "backends": {"fake-link": block}})
    )
    monkeypatch.setattr(identifiers, "Probe", type("Fake", (FakeProbe,), {"answers": answers}))
    table = run_probe("http://fake", expected_name="fake-link", config=config)
    return {row.key: row.status for row in table}, failures(table)


SILENT = {ENSG: ok(rows=[], total=0), "NCBIGene:3845": err("upstream_unavailable")}
E, H = "get_x.gene:ensembl", "get_x.gene:hgnc"


@pytest.mark.parametrize(
    ("known", "expected"),
    [
        ([], {E: "FAIL"}),
        ([E], {E: "XFAIL"}),
        ([E, H], {E: "XFAIL", H: "XPASS"}),
        ([E, "gone.gene:hgnc"], {E: "XFAIL", "gone.gene:hgnc": "STALE"}),
    ],
)
def test_known_failures_are_strict_both_ways(
    known: list[str], expected: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, bad = run(tmp_path, monkeypatch, SILENT, known_failures=known)
    assert {k: v for k, v in status.items() if v not in ("PASS", "SKIP")} == expected
    assert status["get_x.gene:symbol"] == "PASS" and status["get_x.gene:ncbigene"] == "SKIP"
    assert len(bad) == sum(v != "XFAIL" for v in expected.values())


def test_rows_that_never_name_the_gene_still_resolve_against_the_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert set(run(tmp_path, monkeypatch, {})[0].values()) == {"PASS"}


def test_a_tool_that_answers_every_form_with_not_found_is_ungated_not_conformant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, bad = run(tmp_path, monkeypatch, dict.fromkeys(IDS, err("not_found")))
    assert set(status.values()) == {"UNGATED"}
    assert len(bad) == 4


def test_internal_on_a_foreign_form_fails_while_the_symbol_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run(tmp_path, monkeypatch, {ENSG: err("internal")})[0][E] == "FAIL"


def test_another_form_is_the_control_when_the_symbol_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = err("invalid_input", [CONVERTER], **FIELDS)
    status, _ = run(tmp_path, monkeypatch, {v: rejected for v in IDS if v != ENSG})
    assert set(status.values()) == {"PASS"}


def test_a_null_declaration_excludes_the_parameter_and_shows_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, bad = run(
        tmp_path,
        monkeypatch,
        dict.fromkeys(IDS, err("not_found")),
        params={"get_x.gene": None},
        reasons={"get_x.gene": "free-text search"},
    )
    assert status == {"get_x.gene:-": "EXCL"} and not bad


def test_the_backends_local_block_replaces_the_vendored_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / identifiers.LOCAL).write_text(json.dumps({"known_failures": []}))
    assert run(tmp_path, monkeypatch, SILENT, known_failures=[E])[0][E] == "FAIL"


def test_seeded_blocks_name_real_parameters_and_explain_every_exclusion() -> None:
    baseline = json.loads((ROOT / "genefoundry_router/data/fleet-baseline.json").read_text())
    for name, block in CONFIG["backends"].items():
        tools = {t["name"]: t for t in baseline["backends"][name.removesuffix("-link")]["tools"]}
        params = block.get("params", {})
        for key in [*block.get("known_failures", []), *params]:
            tool_name, _, param = key.partition(":")[0].partition(".")
            assert param in tools[tool_name]["inputSchema"]["properties"], f"{name}: {key}"
            assert key.partition(":")[2] in ("", *GENE["forms"]), f"{name}: {key}"
        assert {k for k, v in params.items() if v is None} == set(block.get("reasons", {})), name
        for forms in block.get("forms", {}).values():
            assert list(forms) == list(GENE["forms"]), name
