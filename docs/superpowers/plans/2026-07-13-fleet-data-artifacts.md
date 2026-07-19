# Fleet Immutable Data Artifacts Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

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
| genereviews | `restored-database` | PostgreSQL volume | `data-bound` |
| pubtator | `restored-database` | PostgreSQL volume | `data-bound` |
| gencc, hgnc, mgi, mondo | `upstream-live` | authoritative SQLite volume | `data-bound` |
| metadome | `none` | derived SQLite cache volume | `data-independent` |

The four closed authoritative-data modes are `none`, `external-reference`,
`restored-database`, and `upstream-live`; runtime cache is a separate property.
`upstream-live` rows must record the resolved upstream URL/ETag/digest after every
materialization and explicitly report `reproducible_rollback: false`. They are
not relabelled `external-reference` until a reviewed immutable artifact exists.

This plan depends on the control-plane plan Tasks 1, 4, 6, and 7 being merged.
Those tasks create the authoritative schemas, OCI content policy, definition
artifact, and workflow CLI. Leaf repositories vendor the generated data schema
plus `CONTRACT_SHA256` from that protected router commit; `make vendor-check`
proves byte equality. They do not add a runtime dependency from backend to router.

### Task 1: Shared immutable data-release contract

**Files (router):**
- Create: `genefoundry_router/release/data.py`
- Create: `genefoundry_router/data/data-release-manifest.schema.json`
- Create: `tests/release/test_data_release.py`
- Modify: `genefoundry_router/release/cli.py`
- Modify: `scripts/container_release.py`

- [ ] **Step 1: Write failing manifest and materialization tests**

Require dataset/source identity, retrieval time, transformation repository/commit,
compatible schema range and actual schema version, record counts,
compressed/canonical-expanded-tree hashes and size/member ceilings,
license/redistribution decision, previous-known-good digest, application
compatibility, and disclaimer. `redistribution_allowed: false` must make public
publication invalid rather than merely recording the decision.
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

- [ ] **Step 2: Implement streaming verification and atomic version selection**

Download to the target volume with redirect/host/stall/throughput/byte limits,
hash while streaming, reject links/devices/FIFOs/set-id/path escapes, enforce
streamed decompression and expanded byte/member ceilings, and define expanded
identity as a sorted `path\0mode\0size\0sha256` listing. Materialize into
`data/<digest-prefix>/`, run the repository schema probe, fsync files and parent,
then atomically replace a temporary `data/current` symlink under an exclusive
lock. Expose `validate-data-manifest`, `materialize-data`, and `rollback-data` CLI
commands; retain current and previous-known-good directories.

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
- Create: `vendor/genefoundry/data-release-manifest.schema.json`
- Create: `vendor/genefoundry/CONTRACT_SHA256`
- Modify: `tests/unit/store/test_bundle_verification.py`
- Modify: `tests/unit/test_data_refresh_workflow.py`
- Create: `tests/unit/test_code_only_image.py`
- Delete: `clingen_link/data/clingen.sqlite.zst`
- Delete: `clingen_link/data/clingen.sqlite.sha256`

- [ ] **Step 1: Add failing external-snapshot tests**

Require `CLINGEN_LINK_DATA_BUNDLE_PATH`, exact compressed SHA-256, canonical
expanded-tree SHA-256, compatible schema range, maximum sizes, and expected versus
actual readiness identity. Assert production cannot fall back to a packaged
database and wheel/every OCI layer contain no SQLite or compressed snapshot.

- [ ] **Step 2: Implement external bundle loading**

Keep `clingen_link/data/svi_guidance.json` as the only exact-path code resource.
A hardened init service materializes the verified external snapshot into a
versioned writable volume directory; the application mounts the selected snapshot
read-only, opens SQLite as `mode=ro&immutable=1`, and exposes matching data identity
before readiness succeeds. Keep mutable cache outside the reference mount. Remove
database package artifacts and Docker COPY reachability; support reviewed
pre-seeded local artifacts for offline deployment.

- [ ] **Step 3: Make `data-refresh.yml` draft-first and immutable**

Split transformation from publication. The credential-free build job creates
`data-clingen-YYYY-MM-DD` assets/manifests and uploads a workflow artifact. A
non-executing publisher with scoped contents/OIDC/attestation permissions uses
only pinned runner tools, creates/reuses a matching draft, uploads without
`--clobber`, attests exact bytes with the pinned data-workflow SHA, verifies asset
digests, publishes once, and runs current `gh release verify`/`verify-asset`. It
hard-fails when redistribution review is not affirmative. A mismatched unpublished
draft is deleted/recreated; a published collision fails.

- [ ] **Step 4: Verify and commit in `clingen-link`**

Run: `make ci-local && uv run pytest tests/unit/store/test_bundle_verification.py tests/unit/test_data_refresh_workflow.py tests/unit/test_code_only_image.py -q && docker build --target production -f docker/Dockerfile .`

Expected: tests pass and every final-image OCI layer contains no ClinGen
SQLite/bundle, including files hidden by later whiteouts.

```bash
git add clingen_link/config.py clingen_link/store/db.py clingen_link/data/svi_guidance.json pyproject.toml .dockerignore docker .github/workflows/data-refresh.yml vendor tests/unit/store/test_bundle_verification.py tests/unit/test_data_refresh_workflow.py tests/unit/test_code_only_image.py
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

Init services accept exact release URL/tag/digests or a reviewed pre-seeded local
artifact, use hardened streaming download, atomically select a versioned reference
directory under a lock, and expose expected/actual identity. Applications mount
the reference database read-only and place MaveDB's mapped-variant cache in a
separate writable volume/path. Production rejects `development_latest=true`.

- [ ] **Step 3: Harden both publishers**

Split both workflows into credential-free build and non-executing publish jobs,
pin every action, attest exact data assets, publish draft-first shared manifests,
and verify release immutability/assets. Remove MaveDB
`gh release upload --clobber`; ensure per-tag concurrency and digest-aware reruns
never replace a published asset.

- [ ] **Step 4: Verify and commit each repository independently**

Run in `clinvar-link`: `make ci-local && uv run pytest tests/test_bundle.py tests/test_config.py -q`

Run in `mavedb-link`: `make ci-local && uv run pytest tests/unit/test_config.py tests/unit/test_ingest_acquire.py tests/unit/test_npm_deploy_config.py -q`

Expected: all tests pass; `rg -n 'latest|--clobber' docker .github/workflows` finds no production mutable data resolution or overwrite.

Commit in each repository:

```bash
git add clinvar_link/config.py clinvar_link/ingest/bundle.py docker/entrypoint.sh docker/docker-compose.yml docker/docker-compose.prod.yml .github/workflows/data-bundle.yml tests/test_bundle.py tests/test_config.py
git commit -m "feat(data): require immutable digest-pinned data bundles"
```

Use the corresponding listed MaveDB paths for its separate commit; never use
`git add -A` after a local data build.

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
trust root. Split each publisher into credential-free build and non-executing,
SHA-pinned, attesting publish jobs with per-tag concurrency. Use a writable init
service to select a versioned directory under a lock; mount the resulting SQLite
database read-only/immutable in the application and confine metadata/temp files
to the data volume. Expected/actual data identity gates readiness.

- [ ] **Step 3: Verify and commit each repository**

Run in `hpo-link`: `make ci-local && uv run pytest tests/unit/test_build_data_workflow.py -q`

Run in `orphanet-link`: `make ci-local && uv run pytest tests/unit/test_config.py tests/unit/test_data_resolver.py -q`

Expected: all tests pass and both workflow manifests validate against the router schema.

```bash
git add hpo_link/config.py hpo_link/ingest/release.py docker/entrypoint.sh docker/docker-compose.yml docker/docker-compose.prod.yml .github/workflows/build-data.yml tests/unit/test_build_data_workflow.py
git commit -m "feat(data): normalize immutable data release evidence"
```

Use the corresponding listed Orphanet paths for its separate commit.

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

Require exact corpus release/digest, manifest/schema-range/embedding-model
compatibility, data-only custom-format archive TOC, compressed/expanded ceilings,
and a restored database provenance row that equals the deployment tuple. Reject
an asset URL whose tag, repository, digest, redistribution authorization, or
manifest differs from configuration; test current-image/prior-compatible-data
rollback.

- [ ] **Step 2: Implement an immutable corpus release line**

Keep the heavy corpus build manual/offline where licensing and compute require it.
Public publication is permitted only after a dated affirmative redistribution
review; otherwise use an operator-supplied pre-seeded private artifact. When
permitted, a credential-free build/validation job transfers the bundle to a
non-executing attesting publisher. Schema comes only from in-repo migrations; the
artifact is `pg_dump -Fc --data-only --no-owner --no-privileges`. A no-egress init
container validates the archive TOC, rejects schema/code entries and downloaded
plain SQL, then restores as a non-superuser with
`pg_restore --no-owner --no-privileges --single-transaction --exit-on-error`.

- [ ] **Step 3: Verify and commit**

Run: `make ci-local && uv run pytest tests/unit/test_bundle_download_authenticity.py tests/unit/test_corpus_bundle_validation.py tests/integration/test_bundle_round_trip.py -q`

Expected: miniature corpus round-trip passes with exact provenance.

```bash
git add genereview_link/ingest/github_release.py genereview_link/corpus/bundle.py genereview_link/corpus/bundle_validation.py genereview_link/config.py docker/docker-compose.yml docker/docker-compose.prod.yml .github/workflows/build-corpus.yml .github/workflows/verify-corpus-bundle.yml tests/unit/test_bundle_download_authenticity.py tests/unit/test_corpus_bundle_validation.py tests/integration/test_bundle_round_trip.py
git commit -m "feat(data): make GeneReviews corpus releases immutable"
```

### Task 6: Transitional live-upstream provenance

**Files (`gencc-link`, `hgnc-link`, `mgi-link`, `mondo-link`):**
- Modify: each package `config.py`
- Modify: each package `ingest/downloader.py`
- Modify: each `docker/entrypoint.sh`
- Modify: each `docker/docker-compose.yml`
- Modify: each `docker/docker-compose.prod.yml` (create it for MGI if absent)
- Modify/create: configuration and lifecycle tests in each repository

**Files (`metadome-link`):**
- Modify: `metadome_link/config.py`
- Modify: `metadome_link/cache/store.py`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add a common behavior test to each repository**

Assert a strict HTTPS egress host allowlist, redirect/size/stall/throughput limits, observed
upstream URL/ETag/last-modified/content SHA-256, transformation revision/schema,
atomic materialization, and a persisted provenance JSON next to the database.
Assert the four authoritative-data repositories say `upstream-live` and
`reproducible_rollback: false`.

- [ ] **Step 2: Implement observed provenance and state separation**

Write authoritative data and provenance into a versioned directory in the named
data volume under a materialization lock; do not place multi-gigabyte downloads in
tmpfs. A write-only init service materializes, while the app mounts the selection
read-only and readiness reports observed identity. Do not claim a reviewed
immutable data digest or reproducible data rollback in the release manifest.
Classify Metadome separately as `none` plus a deletable runtime cache; record its
cache path/eviction semantics and prove MCP definitions are data-independent.

- [ ] **Step 3: Verify and commit each repository**

Run `make ci-local` plus the repository's focused config/ingest/lifecycle tests in
`gencc-link`, `hgnc-link`, `mgi-link`, `mondo-link`, and `metadome-link`.

Expected: the four authoritative-data repositories record resolved upstream
identity and fail closed on unapproved egress/incomplete materialization;
Metadome truthfully reports only cache state.

Commit once per repository:

```bash
git add gencc_link docker tests
git commit -m "feat(data): attest live-upstream materialization"
```

Use the corresponding package directory plus `docker` and focused tests for each
separate HGNC, MGI, MONDO, and Metadome commit.

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
database identity as `restored-database`/`data-bound`. If a downloaded snapshot is
supported, apply the same data-only custom-format TOC and non-superuser/no-egress
restore policy as GeneReviews; reject downloaded plain SQL.

- [ ] **Step 2: Implement readiness and provenance checks**

Store source release/digest, migration head, restore time, and schema hash in a
control table. Health remains not-ready until the configured expected tuple and
actual control row match.

- [ ] **Step 3: Verify and commit**

Run: `make ci-local && uv run pytest tests/unit/test_data_identity.py tests/unit/test_db_migrations.py -q`

Expected: mismatched or absent database identity blocks readiness; exact identity passes.

```bash
git add pubtator_link/config.py pubtator_link/db/migrate.py docker/docker-compose.yml docker/docker-compose.prod.yml tests/unit/test_data_identity.py tests/unit/test_db_migrations.py
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
verification evidence. Restored databases are distinct from read-only external
references; runtime cache is independent. Test exact equality with the router
registry plus router and the `spliceai` namespace/server-name alias.

- [ ] **Step 2: Run every modified repository gate**

Run `make ci-local` in the router and every modified data repository. Build all
22 application images, apply the control-plane plan's
`image-content-policy-v1.json` to every OCI layer/config, and verify zero denied
database/archive/secret paths. Exercise exact and previous-known-good data
materialization, current-image/prior-data compatibility, readiness identity,
concurrent-init locking, offline pre-seeding, retention, and bounded disk cleanup.

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
