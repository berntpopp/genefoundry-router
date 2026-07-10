# Dependencies, GeneReviews, and Release Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the four open Dependabot PRs, close the stale router discovery issue with evidence,
complete all three GeneReviews issues, and prepare every affected repository for the fleet release
wave.

**Architecture:** Dependency PRs are repaired at their shared formatting root cause and refreshed to
one version cutoff before merge. GeneReviews work is split into corpus automation, additive revision
and variant-context features, and an offline-only annotation spike. Release work remains separate
from behavior PRs.

**Tech Stack:** Python 3.12+, uv, FastMCP 3.4.4, Ruff 0.15.21, mypy 2.2.0, FastAPI,
asyncpg/PostgreSQL, GitHub Actions, Hugging Face offline models, pytest, gh CLI.

## Global Constraints

- Dependency cutoff: 2026-07-10 23:59 UTC.
- Accepted targets: setup-uv 8.3.2, FastMCP 3.4.4, Uvicorn 0.51.0, Ruff 0.15.21,
  mypy 2.2.0, FastAPI 0.139.0, mkdocstrings 1.0.4 where present.
- All third-party Actions remain pinned to full commit SHAs.
- No GeneReviews model inference or heavy probe dependency enters production dependencies or the
  request path.
- GeneReviews response additions preserve every existing field.
- Every repo runs `make ci-local`; workflow changes also run actionlint or the repository's workflow
  presence tests.
- One atomic commit per task and one focused PR per independently reviewable issue.

---

### Task 1: Repair and Merge GenCC Dependabot PRs #20 and #21

**Files:**
- Modify on `main` formatting-fix PR: `gencc_link/server_manager.py`
- Modify in PR #20: `.github/workflows/ci.yml`, `.github/workflows/conformance.yml`,
  `.github/workflows/release.yml`
- Modify in PR #21: `pyproject.toml`, `uv.lock`
- Test: `tests/unit/test_server_manager.py` and existing full suite

**Interfaces:**
- Consumes: current `origin/main` and Dependabot branch heads.
- Produces: formatted base branch; setup-uv 8.3.2 full-SHA pins; dependency lock containing
  FastMCP 3.4.4, Uvicorn 0.51.0, Ruff 0.15.21, and mypy 2.2.0.

- [ ] **Step 1: Prove the shared failure on each Dependabot head**

```bash
gh api repos/berntpopp/gencc-link/actions/jobs/86304791300/logs | \
  rg "Would reformat: gencc_link/server_manager.py|Process completed with exit code 2"
gh api repos/berntpopp/gencc-link/actions/jobs/86305088181/logs | \
  rg "Would reformat: gencc_link/server_manager.py|Process completed with exit code 2"
```

Expected: both PRs fail only at the base branch's Ruff format check.

- [ ] **Step 2: Create and verify the base formatting fix**

```bash
uv run ruff format gencc_link/server_manager.py
uv run ruff format --check gencc_link tests server.py mcp_server.py scripts
make ci-local
git add gencc_link/server_manager.py
git commit -m "style: format FastMCP compatibility guard"
```

Expected: one behavior-neutral formatting diff and a green local gate. Publish, wait for checks,
and merge this PR before rebasing #20/#21.

- [ ] **Step 3: Refresh PR #20 to setup-uv 8.3.2**

Checkout the PR, rebase current `origin/main`, and ensure all three files use the official 8.3.2
commit `11f9893b081a58869d3b5fccaea48c9e9e46f990`:

```yaml
uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
```

Run:

```bash
make ci-local
git diff origin/main...HEAD -- .github/workflows
```

Expected: only three full-SHA action-pin replacements. Force-push with lease to the existing PR
branch, wait for all checks, and squash-merge #20.

- [ ] **Step 4: Refresh PR #21 dependency group**

Set these minimums in `pyproject.toml` where the dependency already exists:

```toml
"uvicorn[standard]>=0.51.0,<1.0.0"
"fastmcp>=3.4.4,<4.0.0"
"ruff>=0.15.21,<1.0.0"
"mypy>=2.2.0,<3.0.0"
```

Then run:

```bash
uv lock --upgrade-package uvicorn --upgrade-package fastmcp --upgrade-package ruff \
  --upgrade-package mypy
uv sync --group dev
uv run python -c 'import fastmcp; print(fastmcp.__version__)'
make ci-local
```

Expected: FastMCP reports 3.4.4 and the full gate passes. Rebase the existing PR branch after #20,
review the lock diff for only intended transitive changes, wait for checks, and squash-merge #21.

### Task 2: Repair and Merge gnomAD Dependabot PRs #29 and #30

**Files:**
- Modify on formatting-fix PR: `gnomad_link/server_manager.py`
- Modify in PR #30: `.github/workflows/ci.yml`, `.github/workflows/conformance.yml`,
  `.github/workflows/docs.yml`, `.github/workflows/release.yml`
- Modify in PR #29: `pyproject.toml`, `uv.lock`
- Test: existing full suite plus MCP application transport tests

**Interfaces:**
- Consumes: current `origin/main`, PR #29/#30.
- Produces: formatted base; setup-uv 8.3.2; FastAPI 0.139.0, Uvicorn 0.51.0,
  FastMCP 3.4.4, and mkdocstrings 1.0.4.

- [ ] **Step 1: Prove and repair the shared formatting failure**

```bash
gh api repos/berntpopp/gnomad-link/actions/jobs/85756327181/logs | \
  rg "Would reformat: gnomad_link/server_manager.py|Process completed with exit code 2"
gh api repos/berntpopp/gnomad-link/actions/jobs/85756348694/logs | \
  rg "Would reformat: gnomad_link/server_manager.py|Process completed with exit code 2"
uv run ruff format gnomad_link/server_manager.py
make ci-local
git add gnomad_link/server_manager.py
git commit -m "style: format FastMCP compatibility guard"
```

Publish and merge the formatting PR first.

- [ ] **Step 2: Refresh and merge PR #30**

Use setup-uv 8.3.2 full SHA in all four workflows:

```yaml
uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
```

Run `make ci-local`, review workflow-only diff, rebase/push the existing PR branch, wait for all
required checks, and squash-merge #30.

- [ ] **Step 3: Refresh and merge PR #29**

Use these dependency floors:

```toml
"fastapi>=0.139.0,<1.0.0"
"uvicorn[standard]>=0.51.0,<1.0.0"
"fastmcp>=3.4.4,<4.0.0"
"mkdocstrings[python]>=1.0.4,<2"
```

Run:

```bash
uv lock --upgrade-package fastapi --upgrade-package uvicorn --upgrade-package fastmcp \
  --upgrade-package mkdocstrings
uv sync --group dev
make ci-local
```

Expected: full local gate passes. Review FastMCP 3.4.4 Host/Origin behavior explicitly, rebase the
existing PR branch after #30, wait for GitHub checks, and squash-merge #29.

### Task 3: Close Router Discoverability Issue #3 with Current Evidence

**Files:**
- Read: `tests/discoverability/test_discoverability.py`
- Read: `genefoundry_router/discovery.py`, `genefoundry_router/server.py`
- No code change unless the historical sequence reproduces on current main.

**Interfaces:**
- Consumes: current fake-fleet and live catalog commands.
- Produces: a closure comment or a new narrowly reproducible issue.

- [ ] **Step 1: Run discovery evidence**

```bash
uv run pytest tests/discoverability/test_discoverability.py -q
uv run genefoundry-router list-tools --config servers.yaml
```

Expected: the catalog contains the reported tools and search can return them.

- [ ] **Step 2: Reproduce deferred-tool sequencing**

Use the client sequence from issue #3: list/search tools, invoke `tool_search`, then attempt the
historical `call_tool` ordering. Record whether the client unloaded its deferred tool rather than the
router omitting backend tools.

- [ ] **Step 3: Update issue #3**

Post exact versions, commands, and results. Close as stale when current behavior passes. If a current
failure remains, open a new issue naming the exact client, protocol sequence, expected/actual JSON,
and keep #3 linked rather than broadening its original ambiguity.

### Task 4: Automate GeneReviews Corpus Release Bundles (#27)

**Files:**
- Modify: `.github/workflows/build-corpus.yml`
- Modify: `.github/workflows/verify-corpus-bundle.yml`
- Modify: `genereview_link/cli.py` (`_build_bundle`)
- Modify: `docs/corpus-bundles.md`
- Modify: `docker/README.md`
- Test: `tests/test_bundle_manifest.py`
- Test: `tests/test_docker_compose_config.py`
- Create: `tests/test_corpus_workflows.py`

**Interfaces:**
- Consumes: existing `bundle build`, `bundle validate`, `BUNDLE_URL=latest`, and release asset
  naming helpers.
- Produces: a monthly/manual producer workflow and complete reproducibility provenance.

- [ ] **Step 1: Add failing manifest provenance tests**

```python
def test_bundle_manifest_records_build_identity(bundle_manifest: dict[str, object]) -> None:
    assert bundle_manifest["app_git_sha"] == "a" * 40
    assert bundle_manifest["app_version"]
    assert bundle_manifest["schema_migrations"] == {
        "control": ["0001", "0002"],
        "data": ["genereview:0001"],
    }
    assert bundle_manifest["created_by"] == "ci"
```

Run `uv run pytest tests/test_bundle_manifest.py -q`; expect failure because `_build_bundle` leaves
these fields unset.

- [ ] **Step 2: Populate build identity in `_build_bundle`**

Add helpers with these interfaces:

```python
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _build_git_sha() -> str:
    candidate = os.getenv("GITHUB_SHA", "").lower()
    if _GIT_SHA_RE.fullmatch(candidate):
        return candidate
    value = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, timeout=5
    ).strip().lower()
    if not _GIT_SHA_RE.fullmatch(value):
        raise RuntimeError("git did not return a full commit SHA")
    return value


async def _schema_migration_versions(pool: asyncpg.Pool) -> dict[str, list[str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select namespace, version from public.schema_migrations "
            "order by namespace, version"
        )
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(str(row["namespace"]), []).append(str(row["version"]))
    return result


def _bundle_created_by() -> str:
    return "ci" if os.getenv("GITHUB_ACTIONS") == "true" else "operator"
```

`_build_git_sha` uses validated 40-hex `GITHUB_SHA` or `git rev-parse HEAD`; version comes from
installed package metadata; migrations are ordered by namespace/version; `created_by` is `ci` only
when `GITHUB_ACTIONS=true`. Re-run the test and expect pass.

- [ ] **Step 3: Replace the disabled workflow with a real producer**

The workflow has manual `release_id`/`dry_run` inputs, monthly cron `0 3 1 * *`,
`contents: write`, non-cancelling concurrency, Postgres `pgvector/pgvector:0.8.2-pg18`, full-SHA
checkout/setup-uv pins, frozen sync, migrate, ingest, embed, validate, bundle build, SHA verification,
dry-run artifact upload or release creation, and verify-workflow dispatch. Add a disk-space precheck
and 120-minute job timeout.

`tests/test_corpus_workflows.py` parses YAML and asserts every trigger, permission, SHA pin,
validation command, checksum, dry-run branch, and concurrency key. Run it expecting failure before
the workflow edit and pass afterward.

- [ ] **Step 4: Harden the verification workflow and docs**

Replace tag pins such as `actions/checkout@v7` with full SHAs. Document local build, monthly/manual
publication, latest versus pinned restore, `BUILD_LOCAL=true`, required secrets, provenance fields,
and verification. Run `make ci-local` and commit.

- [ ] **Step 5: Run real acceptance after merge**

Trigger dry-run, then publication. Trigger verification against the immutable asset, restore into a
fresh volume, and assert the BRCA1 RRF query returns at least one result. Attach workflow/release URLs
and manifest to #27, then close it.

### Task 5: Add GeneReviews Revision History (#40 item 1)

**Files:**
- Create: `genereview_link/db/migrations/control/0004_chapter_revision_history.sql`
- Create: `genereview_link/corpus/revisions.py`
- Modify: `genereview_link/corpus/pipeline.py`
- Modify: `genereview_link/retrieval/repository.py`
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `genereview_link/api/routes/chapters.py`
- Test: `tests/integration/test_revision_history.py`
- Test: `tests/test_chapter_metadata_revision_history.py`

**Interfaces:**
- Produces: `record_revision_history(pool, new_version) -> None`,
  `Repository.get_revision_history(nbk_id, limit) -> tuple[ChapterRevisionRow, ...]`, and additive
  `ChapterMetadataResponse.revision_history`.

- [ ] **Step 1: Write failing integration cases**

Test first ingest (`added` sections), unchanged reingest (no duplicate change), changed text,
removed section, stable ordering, and `limit<=20`. Run:

```bash
uv run pytest tests/integration/test_revision_history.py -q
```

Expected: failure because the migration and recorder do not exist.

- [ ] **Step 2: Add control-schema history tables**

Create immutable rows keyed by `(nbk_id, corpus_version, section_path)` with `content_sha256`,
`change_type` constrained to `added|changed|removed`, and `recorded_at`. Store hashes and bounded
metadata, not full chapter text.

- [ ] **Step 3: Record history before atomic swap**

`record_revision_history` compares active `genereview.genereview_passages` with
`genereview_staging.genereview_passages`, groups deterministic section paths, hashes ordered passage
text hashes, and inserts only differences. Call it immediately before `atomic_swap` so both schemas
exist. Re-run integration tests.

- [ ] **Step 4: Expose additive metadata**

Add models:

```python
class ChapterRevision(BaseModel):
    corpus_version: str
    recorded_at: datetime
    added_sections: list[str] = Field(default_factory=list)
    changed_sections: list[str] = Field(default_factory=list)
    removed_sections: list[str] = Field(default_factory=list)
```

Add `revision_history: list[ChapterRevision] = Field(default_factory=list)` to
`ChapterMetadataResponse`. Query at most 20 versions, group deterministically, and preserve all
existing fields. Run route/unit/integration tests and `make ci-local`.

### Task 6: Add `get_variant_context` (#40 item 4) and README Value Note

**Files:**
- Create: `genereview_link/api/routes/variant_context.py`
- Modify: `genereview_link/server_manager.py`
- Modify: `genereview_link/models/genereview_models.py`
- Modify: `README.md`
- Test: `tests/test_variant_context_route.py`
- Test: `tests/unit/test_mcp_tool_surface.py`

**Interfaces:**
- Produces: REST/MCP operation `get_variant_context(gene: str, variant: str, limit: int = 10)` using
  existing indexed retrieval with no new model or upstream call.

- [ ] **Step 1: Write failing route and tool-surface tests**

Assert validation, unknown gene, bounded `limit` 1..20, deterministic query expansion, forwarding
to existing `search_passages`, provenance, and MCP registration. Run targeted tests; expect 404 or
missing operation.

- [ ] **Step 2: Implement deterministic query construction**

```python
def build_variant_context_query(gene: str, variant: str) -> str:
    clean_gene = gene.strip().upper()
    clean_variant = variant.strip()
    return (
        f'"{clean_gene}" "{clean_variant}" '
        "founder variant hotspot modifier genotype phenotype management"
    )
```

Reject empty/control-bearing values and cap each at 128 characters. Resolve the chapter through
existing gene search, call the existing `search_passages` handler with the resolved NBK ID and
bounded limit, and return the ordinary response envelope with the expanded query recorded as
metadata. Include the router in `server_manager.py` and re-run tests.

- [ ] **Step 3: Add the literal README value note**

Update the tool list so `get_abstract`/`get_links` explicitly name caching, normalized structured
responses, structured errors, version/provenance stamping, and cross-reference enrichment over raw
E-utilities. Run `make ci-local` and commit.

### Task 7: Execute the Hybrid Annotation Probe (#49)

**Files:**
- Create: `scripts/probe_hybrid_annotation.py`
- Modify: `.gitignore`
- Test: `tests/test_probe_hybrid_annotation.py`
- Runtime output: `/tmp/probe_hybrid_annotation/`

**Interfaces:**
- Produces deterministic JSONL span records and a JSON summary; production imports remain unchanged.

- [ ] **Step 1: Implement and test dependency-free utilities**

Write tests for HGVS/rsID backfill, overlap merge priority, seeded sampling, model metadata,
category recall, anchor recovery, latency summary, and JSONL serialization. The test imports the
script without importing GLiNER/Flair/Transformers; model imports stay inside loader functions.

- [ ] **Step 2: Implement the exact CLI**

Support `--bench`, `--labeled`, `--out-dir`, `--seed`, `--device`, `--threshold`, and
`--no-hunflair`; default output is `/tmp/probe_hybrid_annotation`. Pin exact Hugging Face model
revisions in constants and write their IDs, revisions, licenses, runtime versions, device, and seed
to every run summary.

- [ ] **Step 3: Run the CPU probe outside production dependencies**

```bash
uv run --with 'gliner>=0.4,<1' --with 'flair>=0.15,<1' --with 'torch>=2.2' \
  python scripts/probe_hybrid_annotation.py --device cpu
```

Expected: deterministic JSONL for 299 benchmark queries, three marquee gold inputs, and 50 seeded
passages, plus per-category recall and HFE/CFTR/GRIN2B anchor outcomes.

- [ ] **Step 4: Verify production dependency isolation and close the spike**

```bash
git diff -- pyproject.toml uv.lock
make ci-local
```

Expected: no production dependency diff. Attach the summary to issue #49 and close regardless of
positive or negative outcome; open a new design issue before any schema/ranking adoption.

### Task 8: Version and Release Maintenance/Product Changes

**Files:**
- Modify in GenCC, gnomAD, and GeneReviews after behavior merges: `pyproject.toml`, `uv.lock`,
  changelog path
- Test: `tests/unit/test_version_single_source.py`

**Interfaces:**
- Consumes: merged tasks 1-7.
- Produces: separate version PRs and release/runtime evidence.

- [ ] **Step 1: Bump GenCC and gnomAD PATCH versions**

Update only `[project].version`, lock, changelog, run `make ci-local`, publish separate version PRs,
merge after checks, and record releases.

- [ ] **Step 2: Bump GeneReviews MINOR version**

Revision history and the new tool are backward-compatible additions, so bump MINOR. Add changelog
entries for #27/#40/#49, lock/sync, run `make ci-local`, merge the version PR, and verify the release
workflow and advertised MCP server version.
