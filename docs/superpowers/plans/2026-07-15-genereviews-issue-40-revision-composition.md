# GeneReviews-Link Issue #40 Revision and Composition Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining #40 ergonomics work with durable, bounded chapter revision history and documented composable workflows, while explicitly retaining the deliberate removal of duplicated `markdown_table` output.

**Architecture:** Persist a content-addressed ledger outside swappable corpus schemas. The ingest pipeline computes canonical per-section fingerprints from staging data and writes baseline/change records only in the same transaction that atomically activates a corpus; unchanged and failed ingests create no revision event. `get_chapter_metadata` exposes a bounded, opt-in history projection. Variant context remains a composed `search_passages_batch` recipe unless an evidence-backed workflow probe proves that composition cannot meet its contract.

**Tech Stack:** Python 3.12, FastAPI/FastMCP 3.x, Pydantic 2, asyncpg/PostgreSQL 18, pytest, Ruff, mypy, GitHub Actions.

---

**Target checkout:** `/home/bernt-popp/development/genereviews-link`. Token estimates are already shipped (`total_tokens_estimate = total_char_count // 4`) and require regression tests only. The former `markdown_table` field and `format=markdown_table` parameter are deliberately superseded by fenced structured `caption`, `header`, and `rows`; do not re-add them.

## File map

- Create: `genereview_link/db/migrations/control/0005_chapter_revision_ledger.sql` — durable ledger tables and immutable-row policy.
- Create: `genereview_link/corpus/revisions.py` — canonical section fingerprints and delta calculation.
- Modify: `genereview_link/corpus/pipeline.py` — derive staging fingerprints and write them only within successful `atomic_swap`.
- Modify: `genereview_link/cli.py` — explicit maintainer-only baseline command for legacy corpus promotion.
- Modify: `genereview_link/db/restore.py`, `.github/workflows/corpus-data-release.yml`, and `container-release.json` handling tests — include approved ledger data in the immutable artifact contract.
- Modify: `genereview_link/retrieval/repository.py` — bounded historical revision query.
- Modify: `genereview_link/models/genereview_models.py` and `genereview_link/api/routes/chapters.py` — opt-in `revision_history` response shape.
- Modify: `README.md`, `genereview_link/api/resources/usage.py`, and `docs/CHANGELOG.md` — composed variant recipe, normalized live-NCBI helper guidance, and markdown-table supersession.
- Test: `tests/unit/test_corpus_revisions.py`, `tests/integration/test_revision_ledger.py`, `tests/integration/test_repository_metadata.py`, `tests/test_routes_chapter_metadata.py`, `tests/test_routes_table.py`, `tests/test_tool_schema_descriptions.py`, `tests/unit/test_corpus_restore_policy.py`, and `tests/unit/test_readme_tools.py`.

### Task 1: Define canonical revision fingerprints and ledger schema

**Files:**

- Create: `genereview_link/corpus/revisions.py`
- Create: `genereview_link/db/migrations/control/0005_chapter_revision_ledger.sql`
- Create: `tests/unit/test_corpus_revisions.py`

- [ ] **Step 1: Write pure-function red tests for determinism and deltas**

```python
from genereview_link.corpus.revisions import SectionFingerprint, diff_revisions, fingerprint_sections


def test_fingerprint_is_stable_across_input_order_and_tracks_table_content() -> None:
    rows = [
        ("NBK1", "management", 1, "NBK1:0002", "B", None),
        ("NBK1", "management", 0, "NBK1:0001", "A", {"header": ["drug"], "rows": [["ivacaftor"]]}),
    ]
    assert fingerprint_sections(rows) == fingerprint_sections(list(reversed(rows)))
    assert fingerprint_sections(rows)[("NBK1", "management")].content_hash != fingerprint_sections(
        [(*rows[0],), ("NBK1", "management", 0, "NBK1:0001", "A", {"header": ["drug"], "rows": [["tezacaftor"]]})]
    )[("NBK1", "management")].content_hash


def test_diff_has_baseline_then_precise_added_changed_removed_without_unchanged() -> None:
    previous = {("NBK1", "summary"): SectionFingerprint("NBK1", "summary", "old", 1, 10)}
    current = {
        ("NBK1", "summary"): SectionFingerprint("NBK1", "summary", "new", 1, 12),
        ("NBK1", "management"): SectionFingerprint("NBK1", "management", "added", 1, 8),
    }
    assert [(item.section, item.change_kind) for item in diff_revisions(previous, current)] == [
        ("management", "added"), ("summary", "changed")
    ]
    assert diff_revisions(current, current) == []
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/unit/test_corpus_revisions.py -q`

Expected: FAIL with `ModuleNotFoundError: genereview_link.corpus.revisions`.

- [ ] **Step 3: Implement the pure canonical representation**

Create `genereview_link/corpus/revisions.py` with frozen `SectionFingerprint(nbk_id, section, content_hash, passage_count, total_char_count)` and `SectionDelta(nbk_id, section, change_kind, previous_content_hash, content_hash, passage_count, total_char_count)`. Use this exact canonicalization:

```python
def fingerprint_sections(
    rows: Iterable[tuple[str, str, int, str, str, dict[str, object] | None]]
) -> dict[tuple[str, str], SectionFingerprint]:
    grouped: dict[tuple[str, str], list[tuple[int, str, str, str]]] = defaultdict(list)
    for nbk_id, section, chunk_index, passage_id, text, table_data in rows:
        table = json.dumps(table_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        grouped[(nbk_id, section)].append((chunk_index, passage_id, text, table))
    result: dict[tuple[str, str], SectionFingerprint] = {}
    for key, passages in grouped.items():
        ordered = sorted(passages)
        payload = "\n".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) for value in ordered)
        result[key] = SectionFingerprint(
            nbk_id=key[0], section=key[1], content_hash=sha256(payload.encode("utf-8")).hexdigest(),
            passage_count=len(ordered), total_char_count=sum(len(text) for _, _, text, _ in ordered),
        )
    return result
```

`diff_revisions(previous, current)` must sort by `(nbk_id, section)`, create `added` where only current exists, `removed` where only previous exists (with `content_hash=None`, zero current counts), `changed` only when digest differs, and no row for equal digests.

- [ ] **Step 4: Add the durable migration**

Create `0005_chapter_revision_ledger.sql`:

```sql
create table if not exists public.genereview_chapter_revisions (
    revision_id uuid primary key default gen_random_uuid(),
    nbk_id text not null,
    corpus_version text not null,
    observed_at timestamptz not null default now(),
    baseline boolean not null,
    unique (nbk_id, corpus_version)
);

create table if not exists public.genereview_section_revisions (
    revision_id uuid not null references public.genereview_chapter_revisions(revision_id) on delete cascade,
    section text not null,
    change_kind text not null check (change_kind in ('baseline', 'added', 'changed', 'removed')),
    previous_content_hash text,
    content_hash text,
    passage_count integer not null check (passage_count >= 0),
    total_char_count integer not null check (total_char_count >= 0),
    primary key (revision_id, section),
    check ((change_kind = 'removed' and content_hash is null) or (change_kind <> 'removed' and content_hash is not null))
);

create index if not exists genereview_chapter_revisions_lookup_idx
    on public.genereview_chapter_revisions (nbk_id, observed_at desc, revision_id desc);
```

The ledger is append-only by application convention: no update/delete application path is added. It is in `public` so a swappable `genereview` schema cannot erase history.

- [ ] **Step 5: Run pure tests green and commit**

Run: `uv run pytest tests/unit/test_corpus_revisions.py -q && uv run ruff check genereview_link/corpus/revisions.py tests/unit/test_corpus_revisions.py && uv run mypy genereview_link/corpus/revisions.py`

Expected: PASS / exit 0.

```bash
git add genereview_link/corpus/revisions.py genereview_link/db/migrations/control/0005_chapter_revision_ledger.sql tests/unit/test_corpus_revisions.py
git commit -m "feat: add immutable chapter revision ledger"
```

### Task 2: Write ledger rows only after a successful corpus swap

**Files:**

- Modify: `genereview_link/corpus/pipeline.py`
- Modify: `genereview_link/cli.py`
- Create: `tests/integration/test_revision_ledger.py`

- [ ] **Step 1: Add integration red tests for baseline, change, unchanged, and failure**

```python
async def test_atomic_swap_writes_baseline_then_only_changed_sections(pool: asyncpg.Pool) -> None:
    await seed_active_corpus(pool, version="v1", summary="old", management="same")
    await seed_staging_corpus(pool, version="v2", summary="new", management="same")
    await atomic_swap(pool, new_version="v2", chapter_count=1, section_fingerprints=await staging_fingerprints(pool))

    rows = await pool.fetch("select section, change_kind from public.genereview_section_revisions order by section")
    assert [(row["section"], row["change_kind"]) for row in rows] == [("management", "baseline"), ("summary", "baseline"), ("summary", "changed")]


async def test_failed_swap_leaves_no_revision_event(pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    await seed_active_corpus(pool, version="v1", summary="old")
    monkeypatch.setattr(pipeline, "_activate_staging_schema", AsyncMock(side_effect=asyncpg.PostgresError("boom")))
    with pytest.raises(asyncpg.PostgresError):
        await atomic_swap(pool, new_version="v2", chapter_count=1, section_fingerprints={})
    assert await pool.fetchval("select count(*) from public.genereview_chapter_revisions") == 0
```

- [ ] **Step 2: Run red**

Run: `GENEREVIEW_TEST_DATABASE_URL=postgresql://... uv run pytest tests/integration/test_revision_ledger.py -q`

Expected: FAIL because `atomic_swap` has no fingerprint input or ledger writer.

- [ ] **Step 3: Compute staging fingerprints before, but persist them within, `atomic_swap`**

Add `async def staging_section_fingerprints(pool)` in `pipeline.py`, reading only `nbk_id, chapter_section, chunk_index, passage_id, text, table_data` from `genereview_staging.genereview_passages`, then calling `fingerprint_sections`. At the end of `run_full_ingest`, compute it after the final `_flush` and pass it to `atomic_swap`.

Inside the existing `async with conn.transaction()` in `atomic_swap`, read the last known fingerprints by joining `genereview_chapter_revisions` and `genereview_section_revisions` before activating the staging schema. Call `diff_revisions`; for an NBK with no previous ledger, insert one chapter row with `baseline=true` and all its current sections as `baseline`. For a known NBK, insert a chapter revision with `baseline=false` only if it has at least one delta; insert only its changed/added/removed section rows. Execute these inserts after schema activation and the active corpus flag update but before the transaction exits. Thus an exception anywhere rolls back both activation and ledger rows.

- [ ] **Step 4: Add explicit legacy baseline command**

Add `genereview-link corpus baseline-revisions` in `cli.py`. It must call the same `staging_section_fingerprints`-equivalent reader on the active `genereview` schema and insert baseline rows only for NBK IDs with no existing ledger row. It prints `created <n> chapter baselines; existing <m> unchanged` and does not fetch NCBI, restore data, or alter the active corpus. This command is only for the trusted maintainer/data-release transformation job; never call it from app startup.

- [ ] **Step 5: Run integration green**

Run: `GENEREVIEW_TEST_DATABASE_URL=postgresql://... uv run pytest tests/integration/test_revision_ledger.py tests/integration/test_ingest_end_to_end.py -q`

Expected: PASS; unchanged hash writes no revision, changed summary writes only a `changed` delta, first known data writes baseline, and a forced failure leaves the ledger untouched.

- [ ] **Step 6: Commit transactional ingest behavior**

```bash
git add genereview_link/corpus/pipeline.py genereview_link/cli.py tests/integration/test_revision_ledger.py
git commit -m "feat: record corpus section deltas after atomic swap"
```

### Task 3: Ship bounded historical retrieval through chapter metadata

**Files:**

- Modify: `genereview_link/retrieval/repository.py`
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Modify: `tests/integration/test_repository_metadata.py`
- Modify: `tests/test_routes_chapter_metadata.py`

- [ ] **Step 1: Add failing repository and route tests**

```python
async def test_metadata_history_returns_newest_two_revisions_with_ordered_deltas(pool: asyncpg.Pool) -> None:
    await seed_revision_history(pool, "NBKMETA")
    history = await GeneReviewRepository(pool).get_chapter_revisions("NBKMETA", limit=2)
    assert [item.corpus_version for item in history] == ["v3", "v2"]
    assert [(delta.section, delta.change_kind) for delta in history[0].section_deltas] == [
        ("management", "added"), ("summary", "changed")
    ]


@pytest.mark.asyncio
async def test_metadata_requires_opt_in_and_caps_history() -> None:
    app = _build_app(metadata=_make_metadata_row(), revisions=_make_revisions(25))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        compact = await client.get("/chapters/NBK1247/metadata")
        detailed = await client.get("/chapters/NBK1247/metadata", params={"include": "revision_history", "revision_limit": 2})
    assert compact.json()["revision_history"] is None
    assert len(detailed.json()["revision_history"]) == 2
    assert detailed.json()["revision_history"][0]["corpus_version"] == "v25"
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/integration/test_repository_metadata.py tests/test_routes_chapter_metadata.py -q -k 'revision'`

Expected: FAIL because neither repository query nor response field exists.

- [ ] **Step 3: Add typed bounded revision models**

Add these models in `genereview_models.py`:

```python
class SectionRevisionDelta(BaseModel):
    section: SectionName
    change_kind: Literal["baseline", "added", "changed", "removed"]
    previous_content_hash: str | None = None
    content_hash: str | None = None
    passage_count: int = Field(ge=0)
    total_char_count: int = Field(ge=0)


class ChapterRevision(BaseModel):
    corpus_version: str
    observed_at: datetime
    baseline: bool
    section_deltas: list[SectionRevisionDelta]


class ChapterMetadataResponse(BaseModel):
    # existing fields unchanged
    revision_history: list[ChapterRevision] | None = None
```

Use Pydantic `Literal` values exactly as the migration constraint; do not expose text diffs or full content from historical revisions.

- [ ] **Step 4: Implement one bounded repository query and opt-in route**

Add `GeneReviewRepository.get_chapter_revisions(nbk_id: str, *, limit: int) -> tuple[ChapterRevisionRow, ...]`. Validate `1 <= limit <= 20` in the route. Query the latest `limit` ledger rows ordered `observed_at DESC, revision_id DESC`, then fetch all their deltas ordered `section ASC`, group in Python, and return no rows for unknown/baseline-free chapters. In `get_chapter_metadata`, add `include: Literal["revision_history"] | None = None` and `revision_limit: int = Query(5, ge=1, le=20)`. Call the history query only when `include == "revision_history"`; all existing default response fields remain unchanged.

- [ ] **Step 5: Run green and MCP smoke**

Run: `uv run pytest tests/integration/test_repository_metadata.py tests/test_routes_chapter_metadata.py tests/unit/test_mcp_tool_surface.py -q`

Expected: PASS; FastMCP auto-exposes the documented optional inputs without a new leaf tool, keeping the surface budget stable.

- [ ] **Step 6: Commit retrieval API**

```bash
git add genereview_link/retrieval/repository.py genereview_link/models/genereview_models.py \
  genereview_link/api/routes/chapters.py tests/integration/test_repository_metadata.py \
  tests/test_routes_chapter_metadata.py
git commit -m "feat: expose bounded GeneReviews revision history"
```

### Task 4: Preserve structured tables and document composable workflows

**Files:**

- Modify: `README.md`
- Modify: `genereview_link/api/resources/usage.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `tests/test_routes_table.py`
- Modify: `tests/test_tool_schema_descriptions.py`
- Modify: `tests/unit/test_readme_tools.py`

- [ ] **Step 1: Lock markdown-table supersession with a red documentation test**

```python
def test_docs_record_markdown_table_as_deliberately_superseded() -> None:
    changelog = Path("docs/CHANGELOG.md").read_text(encoding="utf-8")
    usage = Path("genereview_link/api/resources/usage.py").read_text(encoding="utf-8")
    assert "markdown_table is deliberately superseded" in changelog
    assert "Render markdown client-side from structured fenced cells" in usage


def test_readme_contains_batch_variant_context_recipe_and_live_helper_value() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "search_passages_batch" in readme
    assert "get_abstract" in readme and "normalized" in readme
    assert "get_links" in readme and "categorized" in readme
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/test_routes_table.py tests/test_tool_schema_descriptions.py tests/unit/test_readme_tools.py -q`

Expected: FAIL only for the new documentation assertions; the existing negative tests must continue to prove no `markdown_table` field or `format` parameter exists.

- [ ] **Step 3: Add compact README/usage guidance without a new endpoint**

Under the README Tools table, add one short paragraph: use `get_abstract` for normalized/cached PubMed abstract metadata and `get_links` for categorized Bookshelf/PMC/external references; use direct E-utilities only when raw upstream payload is specifically required. Add a `search_passages_batch` recipe with exactly these five independent searches:

```json
{"queries":[
  {"q":"BRCA1 c.5266dupC founder variant", "nbk_id":"NBK1247", "sections":["molecular_genetics"], "limit":5},
  {"q":"BRCA1 pathogenic variant hotspot founder", "nbk_id":"NBK1247", "sections":["molecular_genetics"], "limit":5},
  {"q":"BRCA1 risk reducing surgery variant management", "nbk_id":"NBK1247", "sections":["management"], "limit":5},
  {"q":"CFTR F508del modulator indication", "nbk_id":"NBK1250", "sections":["management"], "limit":5},
  {"q":"GRIN2B phenotype spectrum", "nbk_id":"NBK501979", "sections":["clinical_features"], "limit":5}
]}
```

In the usage resource and changelog, state exactly: `markdown_table is deliberately superseded because it duplicated the structured untrusted-text payload; render markdown client-side from caption/header/rows.` Do not create `get_variant_context` in this change. A wrapper is permitted only in a follow-up issue if the release probe below shows the recipe cannot return its specified chapter-constrained candidates.

- [ ] **Step 4: Prove the composition recipe and table boundary**

Run: `uv run pytest tests/test_routes_table.py tests/test_tool_schema_descriptions.py tests/unit/test_readme_tools.py -q && make lint-readme`

Expected: PASS. Then, against a seeded local corpus or deployed release, invoke `search_passages_batch` with the exact JSON above and record that every subquery returns only its specified `nbk_id`/section. If a constrained query has no candidate because the corpus lacks it, record that observed absence and do not add a wrapper that hides it.

- [ ] **Step 5: Commit composition/docs**

```bash
git add README.md genereview_link/api/resources/usage.py docs/CHANGELOG.md \
  tests/test_routes_table.py tests/test_tool_schema_descriptions.py tests/unit/test_readme_tools.py
git commit -m "docs: document GeneReviews revision and variant workflows"
```

### Task 5: Preserve revision ledger through immutable artifacts

**Files:**

- Modify: `genereview_link/db/restore.py`
- Modify: `.github/workflows/corpus-data-release.yml`
- Modify: `tests/unit/test_corpus_restore_policy.py`
- Modify: `tests/integration/test_bundle_round_trip.py`

- [ ] **Step 1: Add red artifact-policy tests**

```python
def test_revision_ledger_tables_are_approved_data_only_targets() -> None:
    assert "public.genereview_chapter_revisions" in CORPUS_TABLES
    assert "public.genereview_section_revisions" in CORPUS_TABLES
    assert_data_only_archive([
        *DATA_ENTRIES,
        "3452; 0 17000 TABLE DATA public genereview_chapter_revisions genereview",
        "3453; 0 17001 TABLE DATA public genereview_section_revisions genereview",
    ])
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/unit/test_corpus_restore_policy.py tests/integration/test_bundle_round_trip.py -q`

Expected: FAIL because the current approved data table allowlist and workflow `pg_dump -t` list omit both ledger tables.

- [ ] **Step 3: Extend the verified artifact contract, not the trust model**

Add the two public ledger tables to `CORPUS_TABLES`, the `pg_dump --data-only` table list in `corpus-data-release.yml`, generated manifest `restore.tables`, and the release image allowlist only if the central data-bound workflow requires migration files to enumerate the new control migration. Before the workflow generates the data-only dump, run `uv run genereview-link corpus baseline-revisions` so transforming the legacy source creates first-seen baseline rows. Do not allow any new archive entry types.

- [ ] **Step 4: Run artifact tests green**

Run: `uv run pytest tests/unit/test_corpus_restore_policy.py tests/integration/test_bundle_round_trip.py -q`

Expected: PASS; a data-only archive with ledger rows restores, a schema-bearing archive remains rejected, and a future data release can return durable history.

- [ ] **Step 5: Commit the artifact extension**

```bash
git add genereview_link/db/restore.py .github/workflows/corpus-data-release.yml \
  tests/unit/test_corpus_restore_policy.py tests/integration/test_bundle_round_trip.py
git commit -m "feat: preserve revision ledger in corpus releases"
```

### Task 6: Verify, release, deploy, and close #40

- [ ] **Step 1: Run complete local verification**

Run: `make ci-local && GENEREVIEW_TEST_DATABASE_URL=postgresql://... make test-integration && make docker-build`

Expected: all pass. The integration database must be disposable and named with `test`, as enforced by `tests/integration/conftest.py`.

- [ ] **Step 2: Pull request and merge evidence**

```bash
git push -u origin feat/issue-40-revision-history
gh pr create --repo berntpopp/genereviews-link --base main --head feat/issue-40-revision-history \
  --title "feat: add durable GeneReviews revision history" --body "Implements remaining #40 revision and composition work."
```

Merge only after all checks/review pass and record the merge SHA.

- [ ] **Step 3: Promotion/deployment evidence**

Produce a new data-only corpus release containing the initial baselines, verify its exact tag/digest with the #27 workflow, update `container-release.json` in a reviewed promotion commit, build/deploy the image digest, and run three probes:

```text
get_chapter_metadata(nbk_id="NBK1247") -> revision_history is absent/null by default
get_chapter_metadata(nbk_id="NBK1247", include="revision_history", revision_limit=2) -> <=2 ordered revisions/deltas
search_passages_batch(<README recipe>) -> every result respects its requested NBK/section filter
```

- [ ] **Step 4: Close tracker with supersession record**

Post the merge SHA, data tag/digest, image digest, revision-history probe output, and recipe result to #40. State explicitly in the issue comment: `markdown_table` was deliberately superseded by structured fenced cells and will not be restored; token estimates shipped before this PR. Close #40 only after this statement and deployment evidence are present.
