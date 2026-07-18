# GeneReviews-Link Issue #27 Data-Only Corpus Release Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GeneReviews corpus promotion, verification, deployment, and recovery conform to the existing immutable digest-pinned data-only release architecture, with no serving-process download, restore, or local-ingest fallback.

**Architecture:** The reviewed source corpus is transformed in CI into a data-only archive and published with `SHA256SUMS`; a human-approved promotion pins its tag and digest in `container-release.json`. A no-egress init sidecar applies reviewed migrations, validates/extracts the local asset, restores only approved table data as a limited role, builds the in-repo HNSW index, and then releases the app. Verification reproduces that sequence from a tag and independently supplied digest, never from `latest` or a mutable URL.

**Tech Stack:** Python 3.12, Typer, asyncpg/PostgreSQL 18 with pgvector, GitHub Actions, GitHub CLI, Docker Compose, pytest, Ruff, mypy.

---

**Target checkout:** `/home/bernt-popp/development/genereviews-link` on a fresh branch from `main`. This supersedes the obsolete issue text that proposed `BUNDLE_URL=latest` and first-start server bootstrap; it does not weaken the restored-database trust boundary.

## Invariants

- `container-release.json.data.release_tag` and `.digest` are the deployment authority. `latest`, release-asset URLs supplied at runtime, same-host checksum files, and archive manifests are not trust roots.
- `genereview-link` never downloads, extracts, restores, or builds corpus data. Explicit maintainer CLI commands may build a source corpus outside the serving container.
- Migrations run before artifact restore. The artifact contains only approved `TABLE DATA` entries; `pg_restore` runs with `--no-owner --no-privileges --single-transaction --exit-on-error` as `genereview_restore`.
- A failed artifact must leave no active corpus and must block app startup. A healthy fresh volume must return the BRCA1 RRF passage search result after the init sidecar completes.

## File map

- Delete: `.github/workflows/verify-corpus-bundle.yml` — unpinned URL-based, schema-bearing verification.
- Modify: `.github/workflows/corpus-data-release.yml` — preserve data-only build/release and make resulting provenance explicit.
- Create: `.github/workflows/verify-corpus-data-release.yml` — tag+digest verification of immutable release assets.
- Modify: `container-release.json` — only via the reviewed data-promotion commit after a verified artifact exists.
- Modify: `genereview_link/config.py` and `genereview_link/server_lifecycle.py` — remove obsolete app bootstrap/release-watcher settings and behavior.
- Delete: `genereview_link/ingest/github_release.py` and `genereview_link/ingest/scheduler.py` — unused in-process mutable release resolution/download path.
- Modify: `pyproject.toml` — remove `apscheduler` only after its only runtime consumer is removed; run `uv lock` rather than hand-editing `uv.lock`.
- Modify: `docs/data.md`, `docs/configuration.md`, `docs/deployment.md`, `docker/README.md`, `.env.docker.example`, and `README.md` — remove all `BUNDLE_URL`, `BUILD_LOCAL`, and `latest` operational claims; add the reviewed promotion procedure.
- Modify: `tests/test_bootstrap_live_migration.py`, `tests/unit/test_corpus_restore_policy.py`, `tests/unit/test_github_release.py`, `tests/unit/test_bundle_download_authenticity.py`, `tests/integration/test_scheduler_advisory_lock.py`, and `tests/unit/test_server_lifecycle.py` — remove tests for deleted server-side bootstrap and add no-bootstrap assertions.
- Create: `tests/unit/test_corpus_data_release_workflow.py` — inspect the workflow contract without requiring an actual multi-gigabyte artifact.
- Modify: `tests/unit/test_docker_compose_config.py` and `tests/integration/test_bundle_round_trip.py` — prove sidecar ordering and data-only round trip.

### Task 1: Pin the no-in-process-bootstrap contract with tests

**Files:**

- Modify: `tests/test_bootstrap_live_migration.py`
- Create: `tests/unit/test_corpus_data_release_workflow.py`
- Modify: `tests/unit/test_docker_compose_config.py`

- [ ] **Step 1: Write the failing contract tests**

```python
def test_serving_lifecycle_has_no_download_restore_or_local_ingest_path() -> None:
    text = Path("genereview_link/server_lifecycle.py").read_text(encoding="utf-8")
    for forbidden in ("BUNDLE_URL", "BUILD_LOCAL", "run_full_ingest", "resolve_latest", "corpus restore"):
        assert forbidden not in text


def test_data_release_verification_requires_tag_and_independent_digest() -> None:
    workflow = Path(".github/workflows/verify-corpus-data-release.yml").read_text(encoding="utf-8")
    assert "release_tag:" in workflow
    assert "expected_sha256:" in workflow
    assert "gh release download \"$RELEASE_TAG\"" in workflow
    assert "sha256sum --check SHA256SUMS" in workflow
    assert "pg_restore --list" in workflow
    assert "--single-transaction" in workflow
    assert "BRCA1 risk-reducing mastectomy salpingo-oophorectomy" in workflow
    assert "releases/latest" not in workflow


def test_base_compose_waits_for_no_egress_restore_before_app() -> None:
    compose = _compose_config("docker/docker-compose.yml")
    restore = compose["services"]["genereview-corpus-restore"]
    app = compose["services"]["genereview-link"]
    assert restore["networks"] == ["genereview_internal"]
    assert app["depends_on"]["genereview-corpus-restore"]["condition"] == "service_completed_successfully"
```

- [ ] **Step 2: Run the tests to prove the current contradiction**

Run: `uv run pytest tests/test_bootstrap_live_migration.py tests/unit/test_corpus_data_release_workflow.py tests/unit/test_docker_compose_config.py -q`

Expected: FAIL because the current lifecycle still has `BUILD_LOCAL`, old bootstrap settings/files remain, and the replacement pinned verification workflow does not exist.

- [ ] **Step 3: Replace stale lifecycle tests with the sidecar-only contract**

Delete the two `monkeypatch.setattr(settings, "BUNDLE_URL", ...)` assertions. Keep the active-schema migration regression: it must still prove a serving app applies reviewed data migrations to an already-restored active schema. Add a second test that an empty database does not invoke ingestion, does not create a scheduler, and logs the fixed 503/degraded behavior.

- [ ] **Step 4: Implement only the minimal lifecycle code to satisfy the boundary**

In `_bootstrap`, retain control migrations, the active-corpus data migration path, and the empty-schema warning. Delete the entire `if settings.BUILD_LOCAL:` block. In `_initialize_state`, delete the release-watcher scheduler setup; keep `app.state.scheduler = None` only if teardown compatibility needs it, otherwise remove the state and matching teardown logic. The explicit `genereview-link ingest`, `embed`, and `bundle publish-local` maintainer commands remain outside `server_lifecycle.py`.

- [ ] **Step 5: Run green and commit the trust-boundary removal**

Run: `uv run pytest tests/test_bootstrap_live_migration.py tests/unit/test_corpus_data_release_workflow.py tests/unit/test_docker_compose_config.py tests/unit/test_server_lifecycle.py -q`

Expected: PASS.

```bash
git add genereview_link/server_lifecycle.py tests/test_bootstrap_live_migration.py \
  tests/unit/test_corpus_data_release_workflow.py tests/unit/test_docker_compose_config.py \
  tests/unit/test_server_lifecycle.py
git commit -m "fix: make corpus loading sidecar-only"
```

### Task 2: Remove stale mutable bundle code and configuration

**Files:**

- Delete: `genereview_link/ingest/github_release.py`
- Delete: `genereview_link/ingest/scheduler.py`
- Modify: `genereview_link/config.py`
- Modify: `pyproject.toml`
- Modify: `tests/unit/test_github_release.py`
- Modify: `tests/unit/test_bundle_download_authenticity.py`
- Modify: `tests/integration/test_scheduler_advisory_lock.py`

- [ ] **Step 1: Turn dead-code absence into red tests**

```python
def test_runtime_configuration_exposes_only_local_sidecar_restore_inputs() -> None:
    fields = settings.model_fields
    assert {"CORPUS_SEED_PATH", "CORPUS_BUNDLE_SHA256", "CORPUS_RESTORE_DIR", "RESTORE_DATABASE_URL", "RESTORE_ROLE"} <= set(fields)
    assert not {"BUNDLE_URL", "EXPECTED_BUNDLE_SHA256", "ALLOW_UNANCHORED_BUNDLE", "BUNDLE_BOOTSTRAP_DIR", "BUILD_LOCAL", "GITHUB_REPO", "AUTO_PULL_RELEASES"} & set(fields)


def test_no_runtime_module_imports_legacy_release_downloader() -> None:
    package = Path("genereview_link")
    assert not (package / "ingest" / "github_release.py").exists()
    assert not (package / "ingest" / "scheduler.py").exists()
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/unit/test_github_release.py tests/unit/test_bundle_download_authenticity.py tests/integration/test_scheduler_advisory_lock.py -q`

Expected: these currently test the legacy mutable path and therefore must be removed/replaced as part of the deliberate deletion, not made to pass against dead code.

- [ ] **Step 3: Delete the implementation and obsolete tests together**

Delete `github_release.py`, `scheduler.py`, their direct test modules, and only their cases in `tests/test_coverage_boost.py`. Remove the seven obsolete Settings fields named in Step 1 and remove `apscheduler` from `pyproject.toml`; then run `uv lock` to regenerate `uv.lock`. Do not delete `CORPUS_SEED_PATH`, `CORPUS_BUNDLE_SHA256`, `CORPUS_RESTORE_DIR`, `RESTORE_DATABASE_URL`, or `RESTORE_ROLE`.

- [ ] **Step 4: Add a compact replacement test at the right boundary**

Add to `tests/unit/test_corpus_restore_policy.py`:

```python
def test_restore_policy_never_contains_runtime_network_or_latest_resolution() -> None:
    source = Path("genereview_link/db/restore.py").read_text(encoding="utf-8")
    assert "httpx" not in source
    assert "github" not in source.casefold()
    assert "latest" not in source.casefold()
    assert "--single-transaction" in source
    assert "--exit-on-error" in source
```

- [ ] **Step 5: Verify deletion and lock correctness**

Run: `uv lock && uv run pytest tests/unit/test_corpus_restore_policy.py tests/test_bootstrap_live_migration.py -q && uv run python -c 'from genereview_link.config import settings; assert not hasattr(settings, "BUNDLE_URL")'`

Expected: exit 0 and no `apscheduler` or `github_release` import in `rg -n 'BUNDLE_URL|BUILD_LOCAL|AUTO_PULL_RELEASES|github_release|resolve_latest' genereview_link tests`.

- [ ] **Step 6: Commit the deletion atomically**

```bash
git add -u genereview_link tests pyproject.toml uv.lock
git add tests/unit/test_corpus_restore_policy.py
git commit -m "refactor: remove mutable corpus bootstrap path"
```

### Task 3: Replace URL verification with pinned data-release verification

**Files:**

- Delete: `.github/workflows/verify-corpus-bundle.yml`
- Create: `.github/workflows/verify-corpus-data-release.yml`
- Modify: `.github/workflows/corpus-data-release.yml`
- Modify: `tests/unit/test_corpus_data_release_workflow.py`

- [ ] **Step 1: Write structural red tests for every required verification phase**

```python
def test_workflow_migrates_before_data_only_restore_and_smokes_rrf() -> None:
    workflow = Path(".github/workflows/verify-corpus-data-release.yml").read_text()
    assert workflow.index("genereview-link db migrate") < workflow.index("pg_restore")
    assert "python -m genereview_link.db.restore" not in workflow  # use the reviewed CLI boundary
    assert "genereview-link corpus restore" in workflow
    assert "--no-owner" in workflow and "--no-privileges" in workflow
    assert "rerank=rrf" in workflow
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/unit/test_corpus_data_release_workflow.py -q`

Expected: FAIL because only `verify-corpus-bundle.yml` exists and it accepts an arbitrary URL, downloads a sibling checksum, restores before migrations, and accepts schema-bearing archives.

- [ ] **Step 3: Create the pinned workflow with exact shell phases**

Create `.github/workflows/verify-corpus-data-release.yml` with `workflow_dispatch` inputs `release_tag` and `expected_sha256`, both required. Its verification job must:

```bash
set -euo pipefail
test "$RELEASE_TAG" = "$(jq -r '.data.release_tag' container-release.json)"
test "$EXPECTED_SHA256" = "$(jq -r '.data.digest' container-release.json | sed 's/^sha256://')"
mkdir -p "$RUNNER_TEMP/data-release"
gh release download "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" \
  --pattern corpus-bundle.tar.gz --pattern SHA256SUMS --dir "$RUNNER_TEMP/data-release"
cd "$RUNNER_TEMP/data-release"
echo "$EXPECTED_SHA256  corpus-bundle.tar.gz" | sha256sum -c -
sha256sum --check SHA256SUMS
```

Use a pinned `pgvector/pgvector:0.8.2-pg18@sha256:42e7f6b4e1eceb02ff14e3e6bc6108bbe259abbe83879dc1845d0da1ddeb555d` service. Install PG18 client as in `corpus-data-release.yml`; run `uv sync --group dev --frozen`; run `DATABASE_URL=... uv run genereview-link db migrate`; copy the verified asset to a read-only seed directory; run `CORPUS_SEED_PATH=... CORPUS_BUNDLE_SHA256="$EXPECTED_SHA256" RESTORE_DATABASE_URL=... uv run genereview-link corpus restore`; and prove:

```bash
curl --fail --retry 20 --retry-connrefused --retry-delay 1 \
  'http://127.0.0.1:8000/passages/search?q=BRCA1%20risk-reducing%20mastectomy%20salpingo-oophorectomy&rerank=rrf&limit=5' \
  | jq -e '.results | any(.passage_id == "NBK1247:0024")'
```

Start the app only after restore and use a non-superuser `genereview_restore` connection for the archive. Add negative workflow test jobs/commands that reject an unpinned digest and a `pg_restore --list` output containing a schema entry; do not attempt to publish or mutate any release in this verification workflow.

- [ ] **Step 4: Make data-release provenance internally consistent**

In `corpus-data-release.yml`, retain its committed `SOURCE_TAG`/`SOURCE_SHA256` guard and ensure the generated manifest includes `source_release`, `source_sha256`, `corpus_release_id`, data-only `restore.entry_types`, and `checksums`. Keep draft-first publication, attestation, and `gh release verify-asset`. The workflow may never use `releases/latest` or `BUNDLE_URL`.

- [ ] **Step 5: Run static tests and YAML parse**

Run: `uv run pytest tests/unit/test_corpus_data_release_workflow.py -q && ruby -e 'require "yaml"; YAML.load_file(".github/workflows/verify-corpus-data-release.yml")'`

Expected: exit 0. If Ruby is absent, use `uv run python -c 'import yaml, pathlib; yaml.safe_load(pathlib.Path(".github/workflows/verify-corpus-data-release.yml").read_text())'` after confirming PyYAML is available in the dev environment.

- [ ] **Step 6: Commit verification before data promotion**

```bash
git add .github/workflows/corpus-data-release.yml .github/workflows/verify-corpus-data-release.yml \
  .github/workflows/verify-corpus-bundle.yml tests/unit/test_corpus_data_release_workflow.py
git commit -m "ci: verify pinned data-only corpus releases"
```

### Task 4: Make documentation and deployment declarations match runtime

**Files:**

- Modify: `README.md`
- Modify: `docs/data.md`
- Modify: `docs/configuration.md`
- Modify: `docs/deployment.md`
- Modify: `docker/README.md`
- Modify: `.env.docker.example`
- Modify: `tests/unit/test_docker_compose_config.py`

- [ ] **Step 1: Add failing documentation-consistency tests**

```python
@pytest.mark.parametrize("path", [
    "README.md", "docs/data.md", "docs/configuration.md", "docs/deployment.md", "docker/README.md", ".env.docker.example",
])
def test_production_docs_do_not_advertise_mutable_bootstrap(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    assert "BUNDLE_URL" not in text
    assert "BUILD_LOCAL" not in text
    assert "AUTO_PULL_RELEASES" not in text
    assert "release asset URL" not in text
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/unit/test_docker_compose_config.py -q`

Expected: FAIL because documentation still instructs `BUNDLE_URL=latest` and `.env.docker.example` exposes inactive `BUILD_LOCAL` and `AUTO_PULL_RELEASES` variables.

- [ ] **Step 3: Replace text with a reviewed promotion procedure**

Document this exact sequence in `docs/data.md`:

```text
1. Build and validate the source corpus on the approved maintainer environment.
2. Dispatch corpus-data-release.yml with an affirmative redistribution review.
3. Verify its immutable corpus-data-* release by tag and SHA-256 through verify-corpus-data-release.yml.
4. In a reviewed commit, atomically update container-release.json.data.release_tag and .digest.
5. Run the container release workflow, deploy the resulting application image digest, and run the BRCA1 RRF smoke on a fresh volume.
```

State that `docker/ci-prepare-smoke.sh` is the authorized pre-deploy fetcher: it reads the release tag/digest from `container-release.json`, verifies bytes before the sidecar sees them, and places the asset in `CORPUS_SEED_DIR`. Delete the three old runtime loading modes, the in-process `latest` URL examples, and unanchored-bundle discussion. Keep research-use and redistribution-review language.

- [ ] **Step 4: Run documentation and Compose tests green**

Run: `uv run pytest tests/unit/test_docker_compose_config.py tests/unit/test_corpus_restore_policy.py -q && make lint-readme`

Expected: PASS; README remains within its enforced standard and all production docs agree that only the no-egress init sidecar restores data.

- [ ] **Step 5: Commit docs**

```bash
git add README.md docs/data.md docs/configuration.md docs/deployment.md docker/README.md \
  .env.docker.example tests/unit/test_docker_compose_config.py
git commit -m "docs: document immutable GeneReviews corpus promotion"
```

### Task 5: Verify, release, deploy, and close issue #27

- [ ] **Step 1: Run repository and container gates**

Run: `make ci-local && make docker-build && docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --env-file .env.docker config >/tmp/genereviews-prod-compose.yaml`

Expected: all commands exit 0. Inspect the rendered config: `genereview-link` has no corpus seed mount/restore command, `genereview-corpus-restore` has only the internal network plus read-only `/seed`, and app `depends_on` uses `service_completed_successfully`.

- [ ] **Step 2: Create and merge the implementation PR**

```bash
git push -u origin fix/issue-27-data-only-release
gh pr create --repo berntpopp/genereviews-link --base main --head fix/issue-27-data-only-release \
  --title "fix: make corpus release verification data-only" --body "Closes #27 after release and deployment evidence."
```

Merge only after required checks/review pass. Record the exact merge SHA and verify all its GitHub checks are `success` or `skipped`.

- [ ] **Step 3: Produce a real immutable data release and bind it**

Dispatch `corpus-data-release.yml` with `publish=true`, `redistribution_allowed=true`, and a dated review reference. Run `verify-corpus-data-release.yml` with its exact `corpus-data-*` tag and asset SHA-256. In a separate reviewed promotion commit update only `container-release.json.data.release_tag` and `data.digest` to those verified values; never edit a published data tag or reuse its digest for different bytes.

- [ ] **Step 4: Deploy to a fresh production-equivalent volume**

Use `docker/ci-prepare-smoke.sh` to fetch/verify the release asset, deploy the app image pinned to its image digest, and run `docker compose ... up --wait`. Verify the restore container exits 0, the serving container cannot reach the artifact network, `/health` reports the deployed revision, and the BRCA1 RRF request returns `NBK1247:0024` among results.

- [ ] **Step 5: Post issue closure evidence**

Post the merge SHA, data-release tag, asset SHA-256, promotion-commit SHA, application image digest, pinned verification workflow URL, fresh-volume command/output summary, and BRCA1 response summary to GitHub issue #27. Close it only after that evidence exists; do not close it based solely on the local test suite.
