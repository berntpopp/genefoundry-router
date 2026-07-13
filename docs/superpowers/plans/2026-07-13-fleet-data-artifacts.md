# Fleet Immutable Data Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove authoritative datasets from public application images and give every data-bearing backend an explicit, verified, independently rollbackable data identity.

**Architecture:** The router publishes a strict data-release manifest contract and archive verifier. Bundle-producing repositories publish immutable draft-first GitHub Releases; applications materialize exact digest-pinned bundles atomically into read-only mounts, while live-upstream services are explicitly marked transitional and runtime caches remain separate writable state.

**Tech Stack:** Python 3.12, Pydantic 2, GitHub Releases, SHA-256, zstd/gzip/tar/pg_dump, Docker Compose named volumes, pytest, GitHub Actions.

---

## Repository mode matrix

| Repository | v1 data mode | Runtime state | Definition contract |
|---|---|---|---|
| router, autopvs1, gnomad, gtex, litvar, panelapp, spliceailookup, stringdb, uniprot, vep | `none` | in-memory cache only | `data-independent` |
| clingen, clinvar, hpo, mavedb, orphanet | `external-reference` | separate cache where present | `data-bound` |
| genereviews | `external-reference` | PostgreSQL volume | `data-bound` |
| pubtator | `external-reference` | PostgreSQL volume | `data-bound` |
| gencc, hgnc, mgi, mondo | `upstream-live` | authoritative SQLite volume | `data-bound` |
| metadome | `upstream-live` | derived SQLite cache volume | `data-bound` |

`upstream-live` rows must record the resolved upstream URL/ETag/digest after every
materialization and explicitly report `reproducible_rollback: false`. They are
not relabelled `external-reference` until a reviewed immutable artifact exists.

### Task 1: Shared immutable data-release contract

**Files (router):**
- Create: `genefoundry_router/release/data.py`
- Create: `genefoundry_router/data/data-release-manifest.schema.json`
- Create: `tests/release/test_data_release.py`
- Modify: `genefoundry_router/release/cli.py`
- Modify: `scripts/container_release.py`

- [ ] **Step 1: Write failing manifest and materialization tests**

Require dataset/source identity, retrieval time, transformation repository/commit,
schema version, record counts, compressed/expanded hashes and size ceilings,
license/redistribution decision, application compatibility, and disclaimer.
Test redirects outside the allowlist, oversize compressed files, expansion bombs,
checksum mismatch, symlink/path traversal, incompatible schema, and interrupted
replacement.

```python
def test_materialize_rejects_digest_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.zst"
    artifact.write_bytes(b"not the reviewed bundle")
    requirement = requirement_fixture(sha256="0" * 64)
    with pytest.raises(DataVerificationError, match="digest"):
        verify_compressed_artifact(artifact, requirement)


def test_upstream_live_cannot_claim_reproducible_rollback() -> None:
    with pytest.raises(ValidationError):
        DataRequirement(mode="upstream-live", reproducible_rollback=True)
```

- [ ] **Step 2: Implement streaming verification and atomic replacement**

Download to a same-filesystem temporary path with redirect/host/time/byte limits,
hash while streaming, validate archive members before expansion, enforce expanded
byte/member ceilings, run the repository schema probe, `fsync`, then rename.
Expose `validate-data-manifest` and `materialize-data` CLI commands.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_data_release.py -q && uv run mypy genefoundry_router`

Expected: all security and atomicity fixtures pass.

```bash
git add genefoundry_router/release/data.py genefoundry_router/data/data-release-manifest.schema.json tests/release/test_data_release.py genefoundry_router/release/cli.py scripts/container_release.py
git commit -m "feat(data): add immutable reference artifact contract"
```

### Task 2: ClinGen code-only image and immutable snapshots

**Files (`clingen-link`):**
- Modify: `clingen_link/config.py`
- Modify: `clingen_link/store/db.py`
- Modify: `pyproject.toml`
- Modify: `.dockerignore`
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.github/workflows/data-refresh.yml`
- Modify: `tests/unit/store/test_bundle_verification.py`
- Modify: `tests/unit/test_data_refresh_workflow.py`
- Create: `tests/unit/test_code_only_image.py`
- Delete: `clingen_link/data/clingen.sqlite.zst`
- Delete: `clingen_link/data/clingen.sqlite.sha256`

- [ ] **Step 1: Add failing external-snapshot tests**

Require `CLINGEN_LINK_DATA_BUNDLE_PATH`, exact compressed SHA-256, expanded
SHA-256, maximum sizes, and read-only materialization. Assert production cannot
fall back to a packaged database and wheel/image file lists contain no SQLite or
compressed snapshot.

- [ ] **Step 2: Implement external bundle loading**

Keep `clingen_link/data/svi_guidance.json` as the only exact-path code resource.
Load the verified external snapshot path, materialize into the named data volume,
and keep any mutable cache outside the reference mount. Remove database package
artifacts and Docker COPY reachability.

- [ ] **Step 3: Make `data-refresh.yml` draft-first and immutable**

Use `data-clingen-YYYY-MM-DD` tags, generate the shared manifest, create/reuse a
matching draft, upload without `--clobber`, verify asset digests, publish once,
and run `gh release verify`. A mismatched unpublished draft is deleted and
recreated; a published release collision fails.

- [ ] **Step 4: Verify and commit in `clingen-link`**

Run: `make ci-local && uv run pytest tests/unit/store/test_bundle_verification.py tests/unit/test_data_refresh_workflow.py tests/unit/test_code_only_image.py -q && docker build --target production -f docker/Dockerfile .`

Expected: tests pass and exported rootfs contains no ClinGen SQLite/bundle.

```bash
git add -A
git commit -m "feat(data): externalize immutable ClinGen snapshots"
```

### Task 3: ClinVar and MaveDB exact bundle selection

**Files (`clinvar-link`):**
- Modify: `clinvar_link/config.py`
- Modify: `clinvar_link/ingest/bundle.py`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.github/workflows/data-bundle.yml`
- Modify: `tests/test_bundle.py`
- Modify: `tests/test_config.py`

**Files (`mavedb-link`):**
- Modify: `mavedb_link/config.py`
- Modify: `mavedb_link/ingest/bundle.py`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.github/workflows/data.yml`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_ingest_acquire.py`
- Modify: `tests/unit/test_npm_deploy_config.py`

- [ ] **Step 1: Write failing production-default tests in both repositories**

Production must reject `latest`, missing release tag, missing expected compressed
digest, missing expanded digest, and a shared mirror/cache path. Development may
opt into an explicit `development_latest=true`; it is never the default.

- [ ] **Step 2: Implement exact immutable requirements**

Entrypoints accept exact release URL/tag/digests, use hardened streaming download,
materialize atomically, mount the reference database read-only, and place MaveDB's
mapped-variant cache in a separate writable volume/path.

- [ ] **Step 3: Harden both publishers**

Publish draft-first shared manifests and verify release immutability. Remove
MaveDB `gh release upload --clobber`; ensure reruns compare assets by digest and
never replace a published asset.

- [ ] **Step 4: Verify and commit each repository independently**

Run in `clinvar-link`: `make ci-local && uv run pytest tests/test_bundle.py tests/test_config.py -q`

Run in `mavedb-link`: `make ci-local && uv run pytest tests/unit/test_config.py tests/unit/test_ingest_acquire.py tests/unit/test_npm_deploy_config.py -q`

Expected: all tests pass; `rg -n 'latest|--clobber' docker .github/workflows` finds no production mutable data resolution or overwrite.

Commit in each repository:

```bash
git add -A
git commit -m "feat(data): require immutable digest-pinned data bundles"
```

### Task 4: HPO and Orphanet publisher normalization

**Files (`hpo-link`):**
- Modify: `hpo_link/config.py`
- Modify: `hpo_link/ingest/release.py`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.github/workflows/build-data.yml`
- Modify: `tests/unit/test_build_data_workflow.py`

**Files (`orphanet-link`):**
- Modify: `orphanet_link/config.py`
- Modify: `orphanet_link/services/data_resolver.py`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.github/workflows/build-data.yml`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/unit/test_data_resolver.py`

- [ ] **Step 1: Add exact-identity and workflow tests**

Require HPO `db-vYYYY-MM-DD` and Orphanet `data-orphanet-YYYY-MM-DD` release
identities, compressed/expanded digests, schema probes, size ceilings, asset
digest recording, immutable draft-first publication, and no checksum-sidecar-only
trust decision.

- [ ] **Step 2: Implement normalized manifests/materializers**

Keep existing secure download logic but make the reviewed deployment digest the
trust root. Mount verified databases read-only and confine lock/temp files to an
explicit tmpfs or writable state volume.

- [ ] **Step 3: Verify and commit each repository**

Run in `hpo-link`: `make ci-local && uv run pytest tests/unit/test_build_data_workflow.py -q`

Run in `orphanet-link`: `make ci-local && uv run pytest tests/unit/test_config.py tests/unit/test_data_resolver.py -q`

Expected: all tests pass and both workflow manifests validate against the router schema.

```bash
git add -A
git commit -m "feat(data): normalize immutable data release evidence"
```

### Task 5: GeneReviews PostgreSQL corpus artifact

**Files (`genereviews-link`):**
- Modify: `genereview_link/ingest/github_release.py`
- Modify: `genereview_link/corpus/bundle.py`
- Modify: `genereview_link/corpus/bundle_validation.py`
- Modify: `genereview_link/config.py`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.github/workflows/build-corpus.yml`
- Modify: `.github/workflows/verify-corpus-bundle.yml`
- Modify: `tests/unit/test_bundle_download_authenticity.py`
- Modify: `tests/unit/test_corpus_bundle_validation.py`
- Modify: `tests/integration/test_bundle_round_trip.py`

- [ ] **Step 1: Add failing exact-corpus and restore tests**

Require exact corpus release/digest, manifest/schema/embedding-model compatibility,
compressed/expanded ceilings, and a restored database provenance row that equals
the deployment tuple. Reject an asset URL whose tag, repository, digest, or
manifest differs from configuration.

- [ ] **Step 2: Implement an immutable corpus release line**

Keep the heavy corpus build manual/offline where licensing and compute require it,
but make verification and publication automated: validate locally produced bundle,
create draft, attach shared manifest/evidence, verify asset hashes, publish/lock,
then restore only exact reviewed bundles into the PostgreSQL volume.

- [ ] **Step 3: Verify and commit**

Run: `make ci-local && uv run pytest tests/unit/test_bundle_download_authenticity.py tests/unit/test_corpus_bundle_validation.py tests/integration/test_bundle_round_trip.py -q`

Expected: miniature corpus round-trip passes with exact provenance.

```bash
git add -A
git commit -m "feat(data): make GeneReviews corpus releases immutable"
```

### Task 6: Transitional live-upstream provenance

**Files (`gencc-link`, `hgnc-link`, `mgi-link`, `mondo-link`):**
- Modify: each package `config.py`
- Modify: each package `ingest/downloader.py`
- Modify: each `docker/entrypoint.sh`
- Modify: each `docker/docker-compose.yml`
- Modify/create: configuration and lifecycle tests in each repository

**Files (`metadome-link`):**
- Modify: `metadome_link/config.py`
- Modify: `metadome_link/cache/store.py`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add a common behavior test to each repository**

Assert a strict HTTPS egress host allowlist, redirect/size/time limits, observed
upstream URL/ETag/last-modified/content SHA-256, transformation revision/schema,
atomic materialization, and a persisted provenance JSON next to the database.
Assert the release configuration says `upstream-live` and
`reproducible_rollback: false`.

- [ ] **Step 2: Implement observed provenance and state separation**

Write authoritative data and provenance to the named data volume; write transient
download files to tmpfs; keep Metadome's derived cache explicitly separate. Do
not claim a reviewed immutable data digest in the application release manifest.

- [ ] **Step 3: Verify and commit each repository**

Run `make ci-local` plus the repository's focused config/ingest/lifecycle tests in
`gencc-link`, `hgnc-link`, `mgi-link`, `mondo-link`, and `metadome-link`.

Expected: every repository records the resolved upstream identity and fails closed
on unapproved egress or incomplete materialization.

Commit once per repository:

```bash
git add -A
git commit -m "feat(data): attest live-upstream materialization"
```

### Task 7: PubTator database identity

**Files (`pubtator-link`):**
- Modify: `pubtator_link/config.py`
- Modify: `pubtator_link/db/migrate.py`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Create: `tests/unit/test_data_identity.py`
- Modify: `tests/unit/test_db_migrations.py`

- [ ] **Step 1: Write failing PostgreSQL identity tests**

Require an exact database snapshot/migration-set identity before the MCP service
becomes ready. Assert the application image contains only migrations/schema code,
the PostgreSQL volume is external state, and definitions capture records the
database identity as `data-bound`.

- [ ] **Step 2: Implement readiness and provenance checks**

Store source release/digest, migration head, restore time, and schema hash in a
control table. Health remains not-ready until the configured expected tuple and
actual control row match.

- [ ] **Step 3: Verify and commit**

Run: `make ci-local && uv run pytest tests/unit/test_data_identity.py tests/unit/test_db_migrations.py -q`

Expected: mismatched or absent database identity blocks readiness; exact identity passes.

```bash
git add -A
git commit -m "feat(data): bind PubTator readiness to database provenance"
```

### Task 8: Fleet data verification and adversarial review

**Files (router):**
- Create: `ci/fleet-data-controls.json`
- Create: `tests/release/test_fleet_data_controls.py`
- Create: `docs/superpowers/reviews/2026-07-13-fleet-data-artifacts-review.md`

- [ ] **Step 1: Generate and validate the fleet data ledger**

Each of 22 rows records mode, application path exclusions, reference release and
digests when applicable, live-upstream egress/provenance when applicable, cache
path, schema compatibility, redistribution decision, rollback property, and last
verification evidence. Test exact equality with the router registry plus router.

- [ ] **Step 2: Run every modified repository gate**

Run `make ci-local` in the router and every modified data repository. Build and
export every data-bearing application image, apply image-content-policy-v1, and
verify zero authoritative database/archive paths.

Expected: all local gates pass and content reports have zero denied paths.

- [ ] **Step 3: Obtain Claude Code Opus 4.8 xhigh PR reviews**

For each repository PR, submit the approved design, this plan, complete diff,
focused data tests, image content report, and `make ci-local` output to
`claude-opus-4-8` with effort `xhigh`. Record accepted fixes and evidence-backed
rejections; no blocking/high finding may remain open.

- [ ] **Step 4: Commit the router ledger/review record**

```bash
git add ci/fleet-data-controls.json tests/release/test_fleet_data_controls.py docs/superpowers/reviews/2026-07-13-fleet-data-artifacts-review.md
git commit -m "docs(data): record fleet artifact verification and review"
```
