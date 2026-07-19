# GeneReviews-Link Issue #49 Offline Annotation Probe Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Run a reproducible, offline-only experiment that measures hybrid GLiNER/tmVar candidate annotations on fixed GeneReviews evidence, without changing the service, MCP schema, database, retrieval/ranking, image, or production dependencies.

**Architecture:** An independently hash-locked probe consumes only a checksum-validated passages JSONL plus immutable manifest (or a checked-in test fixture), runs revision-pinned models on CPU with networking disabled, and records raw spans, deterministic resolved spans, required-anchor recall, conjunction behavior, review candidates, and latency as JSONL. The benchmark parser reports physical raw-line count and valid semantic-query count from its validated input; it never assumes a magic cardinality.

**Tech Stack:** Python 3.12, uv, GLiNER, Flair tmVar, JSONL, SHA-256, pytest, Ruff, mypy.

---

**Target checkout:** /home/bernt-popp/development/genereviews-link on a new branch from current main. This supersedes the cardinality/runtime portions of docs/superpowers/plans/2026-06-12-issue-49-hybrid-annotation-probe.md: do not reuse its fixed-query-count assertion, optional application extra, or serving-time annotation proposal.

## Immutable boundary

- Do not change pyproject.toml, uv.lock, any genereview_link module, MCP tools/schemas, corpus ingest, database migrations, or retrieval/ranking.
- A real run accepts passages JSONL only with a manifest declaring exact release_tag, lower-case SHA-256, and format passages-jsonl-v1. Validate tag and bytes before importing a model. The fixture takes exactly the same path and is clearly non-production.
- Commit scripts/annotation_probe/requirements.in and a generated scripts/annotation_probe/requirements.lock. Direct dependencies are gliner==0.2.27 and flair==0.15.1; the lock pins and hashes every transitive package. It is never added to the application dependency graph.
- GLiNER is anthonyyazdaniml/gliner-biomed-large-v1.0-disease-chemical-gene-variant-species-cellline-ner revision 6d7bee431896cd5403e156b348fbf343808bb720. tmVar is Brizape/tmvar-PubMedBert-finetuned-24-02 revision 2663f3c90f24f8d1d95a50fbf92d758d507301ba.
- Models load with exact revision, local_files_only=True, CPU, and disabled network. A separately reviewed cache-preparation process may fetch those fixed revisions; an evidence run must not connect to a network.
- raw_line_count counts every newline-delimited benchmark record, including blank/comment/malformed records. semantic_query_count counts schema-valid query objects actually executed. Both are derived from the checksum-pinned input; no fixed cardinality belongs in tests or documentation.
- Results are research-use candidate annotations, never diagnoses, variant interpretations, or clinical recommendations.

## File map

- Create: scripts/annotation_probe/requirements.in and scripts/annotation_probe/requirements.lock — separate hash-locked environment.
- Create: scripts/annotation_probe_contract.py — pure parsing, validation, spans, scoring, serialization; no application imports.
- Create: scripts/annotation_probe.py — offline CLI and injected model adapters.
- Create: tests/fixtures/annotation_probe/benchmark_input.jsonl, corpus_fixture.jsonl, corpus_fixture.manifest.json, gold_passages.jsonl, gold_manifest.json.
- Create: tests/unit/test_annotation_probe_contract.py and tests/unit/test_annotation_probe_cli.py.
- Create: docs/experiments/issue-49-offline-annotation-probe.md.
- Modify: README.md only to link to the experiment; do not advertise a new MCP tool.

### Task 1: Lock inputs and derive benchmark cardinality

**Files:**

- Create: scripts/annotation_probe/requirements.in
- Create: scripts/annotation_probe/requirements.lock
- Create: scripts/annotation_probe_contract.py
- Create: tests/fixtures/annotation_probe/benchmark_input.jsonl
- Create: tests/fixtures/annotation_probe/corpus_fixture.jsonl
- Create: tests/fixtures/annotation_probe/corpus_fixture.manifest.json
- Create: tests/unit/test_annotation_probe_contract.py

- [ ] **Step 1: Write failing parser and artifact-validation tests.**

~~~python
def test_benchmark_derives_both_counts_from_its_file() -> None:
    path = Path("tests/fixtures/annotation_probe/benchmark_input.jsonl")
    raw, semantic = benchmark_counts(path)
    physical_lines = path.read_text(encoding="utf-8").splitlines()

    assert raw == len(physical_lines)
    assert semantic == sum(
        bool(line.strip()) and not line.lstrip().startswith("#") and '"query"' in line
        for line in physical_lines
    )
    assert raw >= semantic


def test_hash_or_release_mismatch_stops_before_model_import(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"passage_id":"NBK1:1","text":"HFE"}\n', encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"release_tag":"fixture-r1","sha256":"' + "0" * 64 + '","format":"passages-jsonl-v1"}',
        encoding="utf-8",
    )

    with pytest.raises(CorpusInputError, match="sha256"):
        validate_corpus_input(corpus, manifest, expected_release_tag="fixture-r1")
~~~

- [ ] **Step 2: Run red.**

Run: uv run pytest tests/unit/test_annotation_probe_contract.py -q

Expected: FAIL with missing-module/import errors.

- [ ] **Step 3: Add the isolated lock and checked-in test source.**

Create requirements.in with exactly:

~~~text
gliner==0.2.27
flair==0.15.1
~~~

Generate requirements.lock on supported Linux CPython 3.12:

~~~bash
uv pip compile --generate-hashes --python-version 3.12 \
  --output-file scripts/annotation_probe/requirements.lock \
  scripts/annotation_probe/requirements.in
~~~

The test inspects every non-comment lock requirement and rejects an unpinned entry or one without sha256 hashes. Do not update the project lock.

Create benchmark_input.jsonl with normal query objects, one blank record, and one comment. Each executable object is query_id/query strings. Create a small corpus_fixture JSONL with passage_id, nbk_id, section, and text; generate its actual digest into corpus_fixture.manifest.json along with release_tag fixture-issue-49-r1 and format passages-jsonl-v1. This is a test fixture, not a production release claim.

- [ ] **Step 4: Implement fail-closed pure validation.**

In annotation_probe_contract.py add frozen CorpusDescriptor(release_tag, sha256, format), CorpusInputError, and:

~~~python
def benchmark_counts(path: Path) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    records = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    for line in records:
        value = json.loads(line)
        if not isinstance(value.get("query_id"), str) or not isinstance(value.get("query"), str):
            raise ValueError("semantic benchmark records require string query_id and query")
    return len(lines), len(records)
~~~

validate_corpus_input parses the manifest, requires the expected release tag and format, calculates the corpus SHA-256 with a closed binary handle, and rejects mismatches. Reading a validated corpus then rejects invalid JSON, duplicate passage_id, and empty/missing passage_id or text. Its caller completes validation before importing GLiNER, Flair, Torch, or adapter modules.

- [ ] **Step 5: Run green and commit.**

Run: uv run pytest tests/unit/test_annotation_probe_contract.py -q && uv run ruff check scripts/annotation_probe_contract.py tests/unit/test_annotation_probe_contract.py && uv run mypy scripts/annotation_probe_contract.py

Expected: exit 0; both counts derive from the actual file and mutable/mismatched input halts immediately.

~~~bash
git add scripts/annotation_probe tests/fixtures/annotation_probe tests/unit/test_annotation_probe_contract.py
git commit -m "test: define reproducible offline annotation probe inputs"
~~~

### Task 2: Encode six gold passages and deterministic resolution

**Files:**

- Modify: scripts/annotation_probe_contract.py
- Create: tests/fixtures/annotation_probe/gold_passages.jsonl
- Create: tests/fixtures/annotation_probe/gold_manifest.json
- Modify: tests/unit/test_annotation_probe_contract.py

- [ ] **Step 1: Write red fixture, overlap, and conjunction tests.**

~~~python
def test_gold_fixture_has_six_category_specific_passages() -> None:
    gold = load_gold(Path("tests/fixtures/annotation_probe/gold_passages.jsonl"))
    assert len(gold) == 6
    assert {(row["gene"], row["category"]) for row in gold} == {
        ("HFE", "gene"), ("HFE", "variant"),
        ("CFTR", "gene"), ("CFTR", "chemical"),
        ("GRIN2B", "disease"), ("GRIN2B", "phenotype"),
    }


def test_resolution_keeps_two_supported_conjunction_members() -> None:
    text = "CFTR p.Phe508del and p.Gly551Asp respond to ivacaftor"
    resolved = resolve_spans(text, [
        RawSpan("gliner", "variant", 5, 29, "p.Phe508del and p.Gly551Asp", 0.80),
        RawSpan("tmvar", "variant", 5, 16, "p.Phe508del", 0.90),
        RawSpan("tmvar", "variant", 21, 30, "p.Gly551Asp", 0.88),
    ])
    assert [(item.start, item.end, item.text) for item in resolved] == [
        (5, 16, "p.Phe508del"), (21, 30, "p.Gly551Asp")
    ]
~~~

- [ ] **Step 2: Run red.**

Run: uv run pytest tests/unit/test_annotation_probe_contract.py -q -k 'gold or resolution'

Expected: FAIL because the gold loader, spans, resolver, and fixture do not exist.

- [ ] **Step 3: Add exactly six source-pinned gold records.**

Each record has passage_id, nbk_id, source release_tag, gene, category, text, and required_anchors. Each anchor has canonical_id, accepted lexical equivalents, and offsets validated against text. The required record set is:

| Passage | Gene/category | Required anchor(s) |
| --- | --- | --- |
| NBK1440:0051 | HFE / gene | HFE |
| NBK1440:0051 | HFE / variant | p.Cys282Tyr; C282Y, c.845G>A, rs1800562 where present |
| NBK1250:0032 | CFTR / gene | CFTR |
| NBK1250:0032 | CFTR / chemical | elexacaftor, tezacaftor, ivacaftor |
| NBK5016:0005 | GRIN2B / disease | GRIN2B-related neurodevelopmental disorder |
| NBK5016:0009 | GRIN2B / phenotype | developmental delay |

Use only reviewed/licensed source text. gold_manifest.json pins its source release tag, source SHA-256, and gold fixture SHA-256. Tests validate every offset so transcription cannot silently alter an expected anchor.

- [ ] **Step 4: Implement source-preserving deterministic resolution.**

Add immutable RawSpan(model, label, start, end, text, score), ResolvedSpan(category, canonical_id, start, end, text, source_models), and AnchorScore(passage_id, canonical_id, found, matched_span_ids). Reject out-of-bounds or text-mismatched raw spans.

Normalize only gold-record lexical/HGVS equivalents by case-folding and whitespace collapse; do not call a clinical synonym service. Resolve overlap with this fixed key: start ascending, end descending, tmVar before GLiNER for variant labels, score descending, canonical ID ascending. Split a phrase connected by and, or, comma, slash, or semicolon only if both pieces independently match a documented anchor. Retain broad unsplittable output as raw evidence and never fabricate resolved constituents. score_gold_anchors emits per-anchor results and per-category required/found/recall only.

- [ ] **Step 5: Run green and commit.**

Run: uv run pytest tests/unit/test_annotation_probe_contract.py -q && uv run ruff format --check scripts/annotation_probe_contract.py tests/unit/test_annotation_probe_contract.py && uv run mypy scripts/annotation_probe_contract.py

Expected: PASS; all six offsets are valid, model order does not change output, and both CFTR variants remain resolved.

~~~bash
git add scripts/annotation_probe_contract.py tests/fixtures/annotation_probe/gold_passages.jsonl \
  tests/fixtures/annotation_probe/gold_manifest.json tests/unit/test_annotation_probe_contract.py
git commit -m "test: add fixed gold anchors for annotation probe"
~~~

### Task 3: Run revision-pinned adapters offline and write audit-grade JSONL

**Files:**

- Create: scripts/annotation_probe.py
- Modify: scripts/annotation_probe_contract.py
- Create: tests/unit/test_annotation_probe_cli.py

- [ ] **Step 1: Write the red CLI test using fake adapters.**

~~~python
def test_probe_writes_complete_auditable_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_gliner_and_tmvar(monkeypatch)
    output = tmp_path / "evidence.jsonl"

    assert main([
        "--fixture", "tests/fixtures/annotation_probe/corpus_fixture.jsonl",
        "--fixture-manifest", "tests/fixtures/annotation_probe/corpus_fixture.manifest.json",
        "--corpus-release-tag", "fixture-issue-49-r1",
        "--benchmark", "tests/fixtures/annotation_probe/benchmark_input.jsonl",
        "--gold", "tests/fixtures/annotation_probe/gold_passages.jsonl",
        "--output", str(output), "--seed", "20260715",
        "--false-positive-sample-size", "3",
    ]) == 0

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["record_type"] == "run_metadata"
    assert {"raw_span", "resolved_span", "gold_anchor", "false_positive_sample", "summary"} <= {
        row["record_type"] for row in rows
    }
    assert rows[0]["models"]["gliner"]["revision"] == "6d7bee431896cd5403e156b348fbf343808bb720"
~~~

- [ ] **Step 2: Run red.**

Run: uv run pytest tests/unit/test_annotation_probe_cli.py -q

Expected: FAIL because CLI and evidence contracts are absent.

- [ ] **Step 3: Implement the network-disabled CPU CLI.**

Implement mutually exclusive source pairs: --fixture/--fixture-manifest and --corpus-passages/--corpus-manifest. Both require --corpus-release-tag; neither allows URLs. Also require --benchmark, --gold, --output, and --seed; default --false-positive-sample-size to 20 and reject non-positive values.

Before adapter initialization set HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, CUDA_VISIBLE_DEVICES="", TOKENIZERS_PARALLELISM=false and seed Python, NumPy, and Torch. Pass CPU, local_files_only=True, and exact revisions to constructors. Missing cache files produce an actionable cache-preparation error. GlinerAdapter and TmvarAdapter are injected interfaces so test fakes require no model weights.

The hermetic fixture execution is:

~~~bash
HF_HOME="$PWD/.annotation-probe-hf-cache" \
uv run --no-project --with-requirements scripts/annotation_probe/requirements.lock \
  python scripts/annotation_probe.py \
  --fixture tests/fixtures/annotation_probe/corpus_fixture.jsonl \
  --fixture-manifest tests/fixtures/annotation_probe/corpus_fixture.manifest.json \
  --corpus-release-tag fixture-issue-49-r1 \
  --benchmark tests/fixtures/annotation_probe/benchmark_input.jsonl \
  --gold tests/fixtures/annotation_probe/gold_passages.jsonl \
  --output /tmp/genereviews-issue-49-fixture-evidence.jsonl \
  --seed 20260715
~~~

It runs only after a separately reviewed cache-population step for those revisions; evidence execution itself has no network path.

- [ ] **Step 4: Write complete honest evidence.**

Write, in order: run_metadata; benchmark_query rows; raw_span rows retaining model label/score/offset/text; resolved_span rows with source_models; gold_anchor rows; zero-to-N false_positive_sample rows; final summary.

Metadata includes source descriptor, lock SHA-256, models/revisions, Python/platform/CPU, offline flags, seed, derived raw_line_count/semantic_query_count, and timestamps. Per-passage records contain adapter and total monotonic milliseconds. Summary contains CPU-local p50/p95/max/count latency, category recall, raw spans by model, resolved count, conjunction discovered/split/retained, and derived benchmark counts.

False-positive review candidates only come from resolved spans unmatched to required gold anchors. Select the stable first N by SHA-256 of seed:passage_id:start:end:canonical_id. Mark review_candidate=true; never call them confirmed false positives or derive a precision rate without human adjudication. Never claim ranking uplift, clinical performance, or variant validity.

- [ ] **Step 5: Run isolated verification and commit.**

Run: uv run pytest tests/unit/test_annotation_probe_contract.py tests/unit/test_annotation_probe_cli.py -q && uv run ruff check scripts/annotation_probe.py scripts/annotation_probe_contract.py tests/unit/test_annotation_probe_cli.py && uv run mypy scripts/annotation_probe.py scripts/annotation_probe_contract.py

Expected: all exit 0 with no app import, download, or project-lock alteration.

~~~bash
git add scripts/annotation_probe.py scripts/annotation_probe_contract.py tests/unit/test_annotation_probe_cli.py
git commit -m "feat: add offline hybrid annotation probe"
~~~

### Task 4: Document the result boundary, verify scope, and close from evidence

**Files:**

- Create: docs/experiments/issue-49-offline-annotation-probe.md
- Modify: README.md
- Modify: tests/unit/test_annotation_probe_cli.py

- [ ] **Step 1: Write the documentation boundary test.**

~~~python
def test_experiment_docs_forbid_runtime_integration_and_magic_counts() -> None:
    text = Path("docs/experiments/issue-49-offline-annotation-probe.md").read_text(encoding="utf-8")
    assert "No serving, schema, retrieval, database, or production dependency change" in text
    assert "raw_line_count" in text and "semantic_query_count" in text
    assert "fixed benchmark cardinality" in text
    assert "research-use candidate annotations" in text
~~~

- [ ] **Step 2: Run red.**

Run: uv run pytest tests/unit/test_annotation_probe_cli.py -q -k docs

Expected: FAIL because the experiment guide is absent.

- [ ] **Step 3: Write the reproducibility/decision guide.**

Document cache-preparation approval, manifest tag/hash validation, lock command, offline CPU command, JSONL schema, artifact retention, research-use wording, and the prohibition on a fixed benchmark cardinality. Its report template requires corpus tag/digest, lock digest, model revisions, dynamically derived counts, six-anchor category results, conjunction summary, review-candidate count/rule, and latency scope.

Allow exactly three outcomes: no follow-up; another offline experiment; or separately approved serving proposal. State that this experiment itself establishes no serving change. A later serving proposal needs a new design, clinical-safety/provenance review, performance budget, MCP contract tests, and implementation plan. README merely links to the guide.

- [ ] **Step 4: Verify repository scope and all gates.**

Run: uv run pytest tests/unit/test_annotation_probe_contract.py tests/unit/test_annotation_probe_cli.py -q && make ci-local && git diff --exit-code -- pyproject.toml uv.lock genereview_link

Expected: all commands exit 0. The final command proves the application/runtime dependency set remains untouched.

~~~bash
git add docs/experiments/issue-49-offline-annotation-probe.md README.md tests/unit/test_annotation_probe_cli.py
git commit -m "docs: record offline annotation experiment boundary"
~~~

- [ ] **Step 5: Review and close #49 with experimental evidence only.**

Open a focused PR containing only probe, fixtures, tests, lock, and documentation; include lock digest and six gold anchors and request data-steward plus application-maintainer review. CI runs fake-adapter fixtures only: no model/corpus download, MCP call, image build, or deployment.

After source release/license approval, execute the real run on a controlled CPU worker with exact revisions pre-cached and outbound network disabled. Store JSONL plus SHA-256 in the approved artifact store. Close #49 with merged commit, corpus tag/digest, lock digest, model revisions, JSONL digest, dynamically derived raw/semantic counts, six-anchor recall, conjunction/review-candidate result, and latency scope. Explicitly state that no production service was changed or deployed. If findings justify runtime work, keep #49 open or open a separately scoped issue; do not implement it under this plan.
