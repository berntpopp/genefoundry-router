# Data Pipeline and Release Remediation Implementation Plan

> Historical record — this plan records the approved execution sequence as of 2026-08-30.
> Current behavior is defined by merged code, immutable release evidence, GitHub state, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair every known broken/stale data path, publish only rights-cleared immutable data,
and prove current or intentionally historical provenance for every routed backend.

**Architecture:** Data code changes join each backend's consolidated Plan 2 PR before merge.
Credential-free builders create sealed artifacts; privileged publishers consume only verified
handoffs and affirmative rights records. Existing immutable identities produce verified no-ops;
same-tag/different-identity collisions fail closed.

**Tech Stack:** Python 3.12/3.14, uv, pytest, httpx, SQLite, PostgreSQL 18/pgvector, zstd, NCBI
FTP/HTTPS, GitHub Releases/attestations, Zenodo, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-30-fleet-security-pr-data-remediation-design.md`

## Global Constraints

- Every repository's `AGENTS.md` and linked instructions are read before work in its clean dedicated
  task worktree.
- Source identity records raw response metadata, canonical URL, capture time, compressed digest,
  expanded identity/invariants, and whether integrity is upstream-authenticated, prior-approved, or
  capture-only.
- Downloads are allowlisted, redirect-checked, streamed, bounded by compressed/expanded/time/count
  ceilings, staged outside the serving path, and promoted atomically.
- Published releases/tags/assets are immutable. An absent tag may be created; an identical identity
  is a verified no-op; a different identity under the same tag fails closed unless a tested `-rN`
  scheme exists.
- A mismatching draft also fails closed. Workflows never delete drafts automatically. The sole
  deletion in this program is the exact audited malformed ClinVar draft, performed as a separate
  ledgered operator action by release database ID after recording its repository/tag/assets.
- A failed build/publish/verification leaves the selected production artifact unchanged.
- GeneReviews builds and verification proceed locally, but no draft or release is published without
  a dated affirmative redistribution determination containing the exact required fields.
- Human prose is not tested by grep; scripts/workflows are exercised through pure helpers or
  controlled workflow-contract tests that assert effects and security boundaries.
- Data code fixes follow RED/GREEN TDD and each repository passes `make ci-local` before its PR.

---

### Task 1: Establish the source, rights, and release mutation ledger

**Files:**
- Create: no tracked product files
- Read: data workflows/configs/manifests/releases and repository licensing documents

**Interfaces:**
- Consumes: live upstream metadata and existing immutable releases.
- Produces: one record per data operation containing source tuple, integrity class, target tag,
  existing-tag identity, rights authority, responsible operator, reversible steps, and exact
  rollback/no-op outcome.

- [ ] **Step 1: Refresh upstream and published identities without downloading bulk artifacts**

Use HEAD/metadata APIs for ClinVar, GeneReviews, HPO, MaveDB, ClinGen, GenCC, HGNC, MGI, Mondo,
Orphanet, GTEx, and live-service sources. When HEAD is rejected or omits useful validators, use a
bounded conditional GET or `Range: bytes=0-0` only when the canonical host supports it; distinguish
`unsupported` from a verified identity. Record UTC time, method, URL, status, ETag, Last-Modified,
Content-Length/Content-Range, and redirect chain; query GitHub/Zenodo release assets and digests.

- [ ] **Step 2: Preflight local build capacity and safe staging paths**

```bash
df -h /home/bernt-popp/development
df -i /home/bernt-popp/development
docker system df
```

Record required free disk/RAM/runtime per repository. Use repository-local ignored staging or
`mktemp -d`; never target `/`, `$HOME`, a repository root, or a serving volume for cleanup.

- [ ] **Step 3: Record GeneReviews rights state before any publishing command**

The record must identify responsible reviewer/rights authority, terms/version, permitted
asset/use, required attribution, evidence URI/path, decision, and decision time. Until every field
is affirmative, the allowed action is `build-and-verify-local`; `draft`, `upload`, `release`, and
`deploy-new-data` are prohibited.

### Task 2: Restore HPO's bounded weekly resolver

**Files:**
- Modify: `hpo_link/ingest/downloader.py`
- Modify: `hpo_link/config.py`
- Modify: `hpo_link/ingest/release.py`
- Create: `hpo_link/ingest/release_identity.py`
- Modify: `.github/workflows/build-data.yml`
- Modify: `tests/unit/test_downloader.py`
- Modify: `tests/unit/test_release.py`
- Modify: `tests/unit/test_build_data_workflow.py`
- Create: `tests/unit/test_release_identity.py`
- Create: `tests/fixtures/releases/hpo_db_v2026_06_23.json`

**Interfaces:**
- Consumes: GitHub latest-release/manifest JSON through the resolver and immutable-release loader.
- Produces: one 131,072-byte metadata ceiling across downloader, config, and release defaults, with
  exact preflight/stream enforcement, plus identity-verified no-op behavior for an existing HPO
  release rather than tag-existence skipping. The already-published legacy `db-v2026-06-23`
  release is verified against its actual historical asset contract; every future release must use
  the complete checksum/attestation contract.

- [ ] **Step 1: Add three literal boundary tests**

Use controlled `httpx.MockTransport` responses whose valid JSON body is padded to exactly 131,071,
131,072, and 131,073 bytes in both resolver and manifest paths. The first two return/parse the
fixture; the last raises `DownloadError`. Add separate `Content-Length: 131073` tests proving
rejection before body consumption. Assert `HPODataConfig.max_manifest_bytes == 131072`. These tests
catch `>=`/`>` boundary mistakes, default drift, and removal of the bound.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
uv run pytest tests/unit/test_downloader.py tests/unit/test_release.py -q
```

Expected: the 131,071/131,072 valid responses fail under the current 65,536-byte default.

- [ ] **Step 3: Raise only the release-metadata default**

Change `_METADATA_MAX_BYTES`, `HPODataConfig.max_manifest_bytes`, its validation ceiling, and
`release.py`'s manifest default to `128 * 1024`. Preserve `read_bounded`, URL/tag validation,
redirect policy, timeouts, and source/data artifact ceilings.

- [ ] **Step 4: Add current-legacy and future-release identity tests and observe RED**

Model explicit states `legacy_verified_noop`, `published_noop`, `create`, and `collision`. For the
one literal pre-standard tag `db-v2026-06-23`, require the actual asset names
`hpo-2026-06-23.sqlite.zst`, `hpo-2026-06-23.sqlite.zst.sha256`, and `manifest.json`; verify the
one-line checksum file, downloaded bundle bytes/digest/size, and the exact bounded manifest shape:
HPO/HPOA versions `2026-06-23`, schema `1`, bundle filename, compressed digest
`d677a96efd8c274045241934c33b25dfb6fc9a6414c27bed7ae3334d05d4c9f6`, compressed size
`19083660`, SQLite size `136249344`, and counts `term=20413`, `obsolete=577`, `closure=222576`,
`xref=18063`, `disease_phenotype=285598`, `gene_phenotype=332599`, and `gene_disease=15944`. Treat `built_utc` as recorded
provenance, not stable source identity. Freeze that audited contract in the named fixture and bind
it to an independently reviewed release/tag/asset API snapshot in the remediation evidence ledger.
Do not claim that this historical release has a nonexistent `SHA256SUMS` or attestation. Any other
tag is ineligible for legacy handling.

For every future release, require the full exact asset set, safe `SHA256SUMS`, downloaded
bundle/manifest digest and size, expanded identity/schema/count invariants, and attestation. Test
absent tag, exact legacy verification, legacy digest/asset/manifest mismatch, identical modern
published identity no-op, missing/corrupt/extra modern assets, published collision, and any draft
mismatch; every mismatch fails without release deletion or mutation.

```bash
uv run pytest tests/unit/test_release_identity.py tests/unit/test_build_data_workflow.py -q
```

Expected: RED because `build-data.yml` currently treats `gh release view` success as identity.

- [ ] **Step 5: Implement the bounded verifier and fail-closed workflow**

Implement a pure 1 MiB-bounded metadata/checksum identity helper and change the workflow to
download/verify the exact existing assets before selecting one of the typed states. The literal
current legacy tuple may no-op only when every historical invariant and the independent prior
digest match; an identical standard release may no-op only under the new full contract. Every
mismatch exits nonzero before any publishing step. No draft is deleted. New releases are never
emitted in legacy form.

- [ ] **Step 6: Verify GREEN and native gates**

```bash
uv run pytest tests/unit/test_downloader.py tests/unit/test_release.py \
  tests/unit/test_release_identity.py tests/unit/test_build_data_workflow.py -q
make ci-local
```

- [ ] **Step 7: Commit and add to the HPO replacement PR**

```bash
git add hpo_link/ingest/downloader.py hpo_link/ingest/release.py hpo_link/config.py \
  hpo_link/ingest/release_identity.py .github/workflows/build-data.yml \
  tests/unit/test_downloader.py tests/unit/test_release.py tests/unit/test_release_identity.py \
  tests/unit/test_build_data_workflow.py tests/fixtures/releases/hpo_db_v2026_06_23.json
git commit -m "fix: raise bounded HPO release metadata limit"
```

After merge, manually dispatch `build-data.yml`. Expected: it resolves v2026-06-23, reaches
`legacy_verified_noop` only after byte/manifest/source/prior-digest verification of
`db-v2026-06-23`, exits successfully, and publishes nothing. Any failed legacy invariant records a
blocker instead of pretending the current release is verified.

### Task 3: Make MaveDB published-tag reruns identity-aware

**Files:**
- Create: `mavedb_link/ingest/release_identity.py`
- Create: `tests/unit/test_data_workflow.py`
- Modify: `.github/workflows/data.yml`

**Interfaces:**
- Consumes: current bundle plus `dist/bundle-metadata.json` and an existing release's exact bundle,
  `bundle-metadata.json`, `SHA256SUMS`, and attestation.
- Produces: one typed state—`published_noop`, `draft_publish_existing`, `create`, or `collision`—
  for the stable identity tuple; collision is nonzero and names the differing field. Volatile
  `retrieved_at` is recorded but not part of unchanged-source identity.

- [ ] **Step 1: Write failing pure CLI tests**

Fixtures use the literal stable keys `tag`, `asset_sha256`, `asset_size`,
`expanded_tree_sha256`, `expanded_size`, `schema_version`, `source_sha256`, `source_url`,
`score_set_count`, and `mapped_variant_count`. Test:

1. identical stable fields with different `retrieved_at` returns 0;
2. any source/asset/tree/schema/count mismatch returns nonzero and names the key;
3. missing/extra stable keys or wrong JSON types fail closed;
4. missing/corrupt/extra release assets, unsafe checksum entries, actual bundle digest/size/tree
   disagreement, or absent/invalid attestation fail closed;
5. neither local file nor release is modified.
6. the four typed states gate disjoint mutation steps: an identical draft is published without an
   upload, a published release performs no upload/attest/edit, `create` uploads/attests/publishes,
   and `collision` performs none of them.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/unit/test_data_workflow.py -q
```

Expected: failure because the verifier does not exist.

- [ ] **Step 3: Implement the bounded exact verifier**

Implement `compare_release_identity(current: Path, existing: Path) -> IdentityComparison`. Read each
local JSON with an explicit 1 MiB limit, require exact stable fields/types, compare them, and expose
a CLI that prints a machine-readable result. Do not invoke `gh` or mutate releases from the helper.

- [ ] **Step 4: Change workflow collision behavior**

In the publish job, when any release exists:

- require and download its exact bundle, metadata, and checksum asset set to a fresh temp directory;
- validate safe checksum paths, actual downloaded bundle digest/size, bounded expansion/tree
  identity, schema/count identity, and attestation before comparison;
- for a published release, run the verifier; identical produces `published_noop` and exits all
  upload, attestation, publish, and edit steps successfully; mismatch produces `collision` and
  fails without deletion;
- for a draft, identical produces `draft_publish_existing`: skip upload and attestation for bytes
  already sealed in the draft, reverify immediately, then publish that exact draft; a mismatch
  produces `collision`, performs no mutation, and fails for separate operator classification;
- an absent tag produces `create`, which alone may upload, attest, verify, and publish;
- gate upload, attestation, publication, and edit with explicit equality checks on the typed state;
  do not collapse these semantics into one `skip` boolean.

- [ ] **Step 5: Verify GREEN, workflow contract, and full gate**

```bash
uv run pytest tests/unit/test_data_workflow.py -q
make ci-local
```

- [ ] **Step 6: Commit, merge with Plan 2, and rerun**

```bash
git add mavedb_link/ingest/release_identity.py tests/unit/test_data_workflow.py \
  .github/workflows/data.yml
git commit -m "fix: make data publication identity-idempotent"
```

After merge, dispatch `data.yml`; expected stable identity matches `data-2026-06-24-s0`, workflow
succeeds, and no release/draft is created or mutated.

### Task 4: Derive ClinVar tags through one canonical identity function

**Files:**
- Modify: `clinvar_link/ingest/bundle.py`
- Create: `clinvar_link/ingest/release_metadata.py`
- Modify: `tests/test_bundle.py`
- Modify: `.github/workflows/data-bundle.yml`
- Create: `tests/test_data_bundle_workflow.py`

**Interfaces:**
- Consumes: stored raw `clinvar_release_date`, source URL/ETag/digest, and packed asset identity.
- Produces: public `release_tag_for_date(value: str | None) -> str` that rejects absent or
  unparseable publication identity, and
  `build_release_metadata(db_path: Path, asset_path: Path, out_dir: Path, *, retrieved_at: datetime)
  -> ReleaseMetadata`; the workflow calls the module rather than duplicating inline derivation.

- [ ] **Step 1: Add the RFC-1123 regression and collision tests**

```python
assert release_tag_for_date("Sun, 23 Aug 2026 00:00:00 GMT") == "bundle-2026-08-23"
assert release_tag_for_date("2026-08-23") == "bundle-2026-08-23"
with pytest.raises(ReleaseIdentityError):
    release_tag_for_date(None)
```

Add workflow-helper tests: release metadata streams DB/asset hashing without `Path.read_bytes`,
records raw/normalized dates plus source URL/ETag/digest/retrieval time, absent tag publishes,
identical source+asset identity no-ops, published same tag/different identity fails and never invokes
deletion, any mismatching draft also fails without deletion, and no `bundle-unknown` tag can be
created for missing/malformed source metadata.

- [ ] **Step 2: Verify RED against the workflow bug**

```bash
uv run pytest tests/test_bundle.py tests/test_data_bundle_workflow.py -q
```

Expected: public function/workflow behavior is missing and RFC input is still sliced incorrectly.

- [ ] **Step 3: Implement the minimal single source of truth**

Rename/promote `_release_tag` to `release_tag_for_date`, retain the existing strict ISO/RFC parser,
remove its `bundle-unknown` fallback in favor of `ReleaseIdentityError`, and call it from
`pack_bundle` plus `release_metadata.py`. Hash large DB/asset files in bounded
chunks. Remove the workflow's inline metadata program and
`str(meta["clinvar_release_date"])[:10]` entirely. Add raw Last-Modified, normalized date, source
URL/ETag/digest, and actual source retrieval time as separate manifest fields; packaging time is not
renamed as retrieval time.

- [ ] **Step 4: Verify GREEN and complete repository gates**

```bash
uv run pytest tests/test_bundle.py tests/test_data_bundle_workflow.py -q
make ci-local
```

- [ ] **Step 5: Commit and merge with the consolidated ClinVar PR**

```bash
git add clinvar_link/ingest/bundle.py clinvar_link/ingest/release_metadata.py tests/test_bundle.py \
  tests/test_data_bundle_workflow.py .github/workflows/data-bundle.yml
git commit -m "fix: derive immutable ClinVar bundle tags safely"
```

### Task 5: Build, verify, and publish the current ClinVar bundle

**Files:**
- External/local data: ignored `data/`, `dist/`, workflow artifacts, exact GitHub release
- Modify after verified release: immutable bundle pins in `container-release.json`, production/NPM
  Compose environment, config tests, and release-note inputs; Plan 4 solely owns application version
  files and `uv.lock` root-version updates

**Interfaces:**
- Consumes: merged Task 4, exact current NCBI source, and existing release/draft inventory.
- Produces: expected `bundle-2026-08-23` at the audit snapshot, or the deterministic exact newer
  normalized source date; valid release manifest/checksums/attestation and runtime pin.

- [ ] **Step 1: Inspect the malformed draft by exact database ID**

```bash
gh api repos/berntpopp/clinvar-link/releases --paginate \
  --jq '.[] | select(.draft == true) | [.id,.tag_name,.name,.created_at,.assets[].name]'
```

Confirm it is the failed `bundle-Sun, 23 Au` build and contains no accepted immutable release.
Delete only that exact draft ID after recording its metadata; do not use a broad tag/glob.

- [ ] **Step 2: Dispatch the corrected workflow and watch both jobs**

```bash
clinvar_head=$(gh api repos/berntpopp/clinvar-link/commits/main --jq .sha)
dispatched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run data-bundle.yml --repo berntpopp/clinvar-link --ref main
bundle_run=
for attempt in $(seq 1 12); do
  bundle_run=$(gh run list --repo berntpopp/clinvar-link --workflow data-bundle.yml \
    --limit 30 --json databaseId,headSha,createdAt,event \
    --jq ".[] | select(.event == \"workflow_dispatch\" and .headSha == \"$clinvar_head\" and .createdAt >= \"$dispatched_at\") | .databaseId" \
    | head -n 1)
  test -n "$bundle_run" && break
  sleep 5
done
test -n "$bundle_run"
gh run watch --repo berntpopp/clinvar-link "$bundle_run"
```

- [ ] **Step 3: Verify release identity before pinning**

Download to a fresh temp directory; verify `SHA256SUMS`, release attestation, compressed digest,
bounded expansion, SQLite integrity/schema/meta counts, representative variant/gene queries, raw
and normalized source identity, and tag collision rule.

- [ ] **Step 4: Update immutable runtime pins under TDD**

First change config tests to the exact accepted tag/URL/digests and verify RED. Update
`container-release.json` plus Compose/env sources, verify GREEN, render production/NPM models, run
`make ci-local`, and commit the pin separately.

### Task 6: Make GeneReviews local building safe by default

**Files:**
- Modify: `genereview_link/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `Makefile`, `docs/data.md`, `docs/CHANGELOG.md`

**Interfaces:**
- Consumes: `bundle publish-local` CLI invocation.
- Produces: a build/package-only local command with no upload option or GitHub release side effect;
  it packages the already-ingested/embedded database exactly once, and privileged rights-gated
  publication exists only in Task 7's separate sealed publisher.

- [ ] **Step 1: Add a behavioral default test**

Invoke the command with build internals replaced by local test fakes; it must never call `_run_gh`.
Assert `--upload` is rejected as an unknown option, and that no local builder code path imports or
calls a GitHub release mutator. Also assert the command does not call ingest or embed and fails if
the already-built database/manifest is absent. The assertion is on the real CLI branch/side effect,
not only help text.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/test_cli.py::TestBundleCommands -q
```

Expected: current explicit upload path remains reachable.

- [ ] **Step 3: Remove local upload capability and make the Makefile build-only explicit**

Remove the Typer upload option and `_run_gh` branch entirely; make `bundle-publish-local` invoke only
the sealed packager over the already-built database; document that ingest/embed are separate prior
steps and publication is a separate rights-document-gated operation.

- [ ] **Step 4: Verify GREEN and full gate**

```bash
uv run pytest tests/test_cli.py::TestBundleCommands -q
make ci-local
git add genereview_link/cli.py tests/test_cli.py Makefile docs/data.md docs/CHANGELOG.md
git commit -m "fix: make local corpus builds non-publishing"
```

### Task 7: Repair GeneReviews data-only packaging and verification

**Files:**
- Modify: `.github/workflows/corpus-data-release.yml`
- Modify: `.github/workflows/verify-corpus-bundle.yml`
- Create: `tests/unit/test_corpus_data_release_workflow.py`
- Create: `tests/unit/test_verify_corpus_bundle_workflow.py`
- Modify: `genereview_link/corpus/bundle.py`, `genereview_link/corpus/bundle_validation.py`,
  `genereview_link/corpus/bundle_metadata.py`, `genereview_link/download_guard.py`,
  `genereview_link/db/restore.py`
- Create: `genereview_link/corpus/handoff.py`
- Create: `tests/unit/test_corpus_handoff.py`
- Modify corresponding unit/integration tests

**Interfaces:**
- Consumes: a sealed locally built full corpus asset, its manifest/digest, and an affirmative rights
  record only in the separate privileged publication step.
- Produces: data-only `corpus.dump`, `manifest.json`, `SHA256SUMS`, one immutable tag selected by
  upstream identity and `rN`, plus a verifier compatible with those exact assets.

- [ ] **Step 1: Add failing workflow/security contract tests**

Tests require:

- PostgreSQL/pgvector service at full digest in both workflows;
- migrations before restoring data-only dump;
- actual `SHA256SUMS`/manifest asset names rather than `${url}.sha256`;
- bounded allowlisted downloader rather than unbounded `curl -fL`;
- internal manifest checks, attestation verification, chapter/passage/embedding equality, HNSW
  index, and representative search fixtures;
- build job without contents-write/release credentials; privileged publisher without source
  checkout and gated by the complete rights record plus one exact immutable handoff object ID;
- no `pg_restore ... || true`; restore uses data-only, single-transaction, exit-on-error semantics;
- existing identical published tag no-op and different identity fail-closed; every draft mismatch
  fails without deletion.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/test_corpus_data_release_workflow.py \
  tests/unit/test_verify_corpus_bundle_workflow.py -q
```

- [ ] **Step 3: Implement a sealed build/publish boundary**

The local build writes artifact, checksum set, provenance manifest, and a seal document into a
fresh directory without invoking `gh`. `bundle seal-handoff --source DIR --root
"$GENEREVIEWS_HANDOFF_ROOT"` copies those files into a new content-addressed object directory named
by the seal SHA-256, rejects symlinks/special files, fsyncs files and directories, writes an exact
size/digest/file-mode manifest, atomically renames the object, and makes its files/directories
read-only. The owner-only handoff root is outside every source checkout and serving volume; object
IDs are never overwritten or reused and objects remain retained through closure. A second seal
attempt with different bytes at an existing ID fails rather than replacing anything. Unit tests
cover source substitution, symlink traversal, post-seal mutation, size/digest mismatch, partial
objects, wrong ownership/mode, and object-ID confusion.

The privileged local `bundle publish-handoff --root ROOT --object-id SHA256 --rights-file FILE`
process receives only the literal object ID and complete affirmative rights JSON record—never a
mutable staging path or source checkout. It re-opens files with no-follow semantics, re-verifies
the object manifest and every digest/invariant immediately before minting release credentials, and
binds the rights decision to that exact object/source/artifact tuple, naming authority/reviewer,
decision time, applicable terms/version, permitted asset/use, attribution, and durable evidence
URI. It selects the exact
`corpus-data-{normalized-upstream-date}-rN` collision-free tag, and then performs draft-first
publication.
The verifier consumes the actual release assets, applies migrations to an empty database before
data-only restore, and runs real representative queries.

- [ ] **Step 4: Verify GREEN with local round trip**

```bash
uv run pytest tests/unit/test_corpus_data_release_workflow.py \
  tests/unit/test_verify_corpus_bundle_workflow.py \
  tests/integration/test_bundle_round_trip.py \
  tests/unit/test_corpus_bundle_validation.py \
  tests/unit/test_corpus_restore_policy.py tests/unit/test_corpus_handoff.py -q
make ci-local
```

- [ ] **Step 5: Commit separately from the bulk corpus**

```bash
git add .github/workflows/corpus-data-release.yml \
  .github/workflows/verify-corpus-bundle.yml genereview_link/corpus \
  genereview_link/download_guard.py genereview_link/db/restore.py tests/unit/test_corpus_handoff.py \
  tests
git commit -m "fix: seal and verify data-only corpus releases"
```

### Task 8: Rebuild and locally verify the current GeneReviews corpus

**Files:**
- Local ignored staging/data/PostgreSQL volume only
- External publication only if the Task 1 rights record is affirmative
- Modify after accepted publication: `container-release.json`, production/NPM Compose pins,
  config tests, `docs/data.md`, and `docs/CHANGELOG.md`; Plan 4 owns version/lock propagation

**Interfaces:**
- Consumes: exact NBK1116 listing/archive and side-data snapshot.
- Produces: corpus identity `(listing relpath, upstream last_updated, archive digest, side-data digest
  set)`, sealed full/data-only artifacts, counts, representative query results, and optional
  rights-cleared immutable release.

- [ ] **Step 1: Capture environment and upstream identities**

Record code SHA, `uv.lock` digest, installed distributions, model/revision/file digests,
Torch/CUDA/runtime/GPU identity, build parameters, canonical URLs, raw metadata, and available disk.
Capture-only archive/side-data digests are labeled as such.

- [ ] **Step 2: Run the documented build without upload**

```bash
export DATABASE_URL='postgresql://genereview:genereview@127.0.0.1:5436/genereview'
CORPUS_RELEASE_ID=$(jq -er '.release_id' \
  /home/bernt-popp/development/fleet-remediation-evidence-20260830/genereviews-source-identity.json)
uv sync --group dev --frozen
uv run genereview-link db migrate
uv run genereview-link ingest
uv run genereview-link embed --schema genereview
uv run genereview-link bundle validate
uv run genereview-link bundle publish-local --release-id "$CORPUS_RELEASE_ID"
```

Set `CORPUS_RELEASE_ID` from the captured normalized upstream `last_updated` plus the collision-free
`rN`; execution date is never a fallback. Never silently fall back to a fake embedding provider for
a production corpus.

- [ ] **Step 3: Verify the sealed artifact locally**

Restore into a new empty PostgreSQL instance from reviewed migrations plus data-only dump. Prove
chapter identity/count equals the captured upstream listing and manifest, record and review every
delta from the prior 882-chapter artifact, prove passage/embedding counts match, HNSW exists,
database integrity/migrations match, and representative GeneReviews search/section fixtures pass.

```bash
make eval
```

The evaluation must run against the restored candidate corpus and record its exact artifact/model
identity; a mock or fallback embedding provider is not acceptance evidence.

- [ ] **Step 4: Apply the rights decision**

Seal the verified artifact into the restricted content-addressed handoff root and record its literal
object ID, byte size, digest manifest, owner/mode, and seal time. If affirmative, run the privileged
publisher on that exact object ID and digest-bound rights record, verify release/attestation/assets,
dispatch `verify-corpus-bundle.yml`, then update exact runtime pins under RED/GREEN config tests. If
denied/ambiguous, publish nothing—including drafts—retain both object and local evidence through
closure, record the blocker, and do not update production pins or close issue #27.

### Task 9: Refresh ClinGen only after manifest and redistribution review

**Files:**
- Create: `clingen_link/etl/release_identity.py`
- Create: `clingen_link/etl/rights_record.py`
- Modify: `.github/workflows/data-refresh.yml`
- Modify: `tests/unit/test_data_refresh_workflow.py`
- Create: `tests/unit/test_release_identity.py`
- Create: `tests/unit/test_rights_record.py`
- External: fresh build artifact, manifest comparison, optional immutable release/pin

**Interfaces:**
- Consumes: `data-clingen-2026-07-16`, fresh source snapshot, and the repository's redistribution
  review.
- Produces: a source-derived stable release identity and tag; verified no-op when identical, or a
  new rights-cleared immutable data release when identity differs. UTC workflow date is never used
  as the upstream source identity, and boolean/free-text workflow inputs never substitute for a
  complete rights record bound to the exact source/artifact digests.

- [ ] **Step 1: Add source-identity and collision tests, then verify RED**

In `tests/unit/test_release_identity.py`, require exact stable identity fields from the built
manifest/source inputs, deterministic tag derivation from the upstream identity, exclusion of
volatile capture time, and strict types. In `tests/unit/test_data_refresh_workflow.py`, require:

1. absent tag creates a draft;
2. published identical tag/identity is a complete no-op;
3. published same tag/different identity fails without deletion;
4. every mismatching draft fails without deletion or recreation;
5. the current `datetime.now(UTC)` tag derivation is absent;
6. `publish=true` without a strict rights record fails before any `gh` command; arbitrary
   `redistribution_review` input is absent.
7. a protected-environment validation job has `contents: read`, checks the exact sealed
   source/artifact/tag tuple, and emits a digest-bound approval artifact; only a separate dependent
   publisher job has `contents: write`, performs no source checkout/build, and refuses an approval
   or artifact digest produced by any other run/job/tuple.

In `tests/unit/test_rights_record.py`, reject missing/extra/wrong-type fields, non-affirmative
decisions, stale terms, digest mismatch, absent attribution/evidence URI, and a record whose source,
artifact, reviewer/authority, decision time, permitted use, or tag differs from the sealed handoff.

```bash
uv run pytest tests/unit/test_release_identity.py tests/unit/test_rights_record.py \
  tests/unit/test_data_refresh_workflow.py -q
```

Expected: RED because the workflow currently derives `data-clingen-YYYY-MM-DD` from execution time
and performs collision logic inline.

- [ ] **Step 2: Implement the bounded identity helper and fail-closed publisher**

Implement a pure helper that parses the existing data-release manifest with a 1 MiB ceiling,
derives its stable source/data tuple and canonical tag, and compares an existing release manifest.
Change the workflow to call it, to download exact existing checksum/manifest assets, and to apply
the no-op/collision rules above before any release mutation. Preserve draft-first publication,
attestation, and rollback-manifest verification. Replace the boolean plus free-text review
authorization with a protected `data-release` environment secret
`CLINGEN_RIGHTS_RECORD_JSON`.

Split publication into credential-isolated jobs. A protected-environment `validate-publication`
job has only `contents: read`; it downloads the sealed build artifact, validates the rights record
against the exact source/artifact/tag tuple, and uploads a small approval JSON bound to workflow run
ID, build artifact ID/digest, source SHA, target tag, rights-record digest, and validation time. A
dependent `publish-release` job is the only job with `contents: write`; GitHub mints its token only
after validation succeeds. It performs no checkout or build, downloads only the exact artifact and
approval IDs handed through `needs`, revalidates both digests/tuple, then invokes `gh`. No write
credential exists in the validation job, and no source tree exists in the writer job.

- [ ] **Step 3: Verify GREEN and the complete repository gate**

```bash
uv run pytest tests/unit/test_release_identity.py tests/unit/test_rights_record.py \
  tests/unit/test_data_refresh_workflow.py -q
make ci-local
git add clingen_link/etl/release_identity.py clingen_link/etl/rights_record.py \
  .github/workflows/data-refresh.yml tests/unit/test_release_identity.py \
  tests/unit/test_rights_record.py tests/unit/test_data_refresh_workflow.py
git commit -m "fix: bind ClinGen releases to source identity"
```

- [ ] **Step 4: Dispatch a non-publishing fresh build**

```bash
gh workflow run data-refresh.yml --repo berntpopp/clingen-link --ref main -f publish=false
```

Watch the run and download the artifact to a fresh bounded staging directory.

- [ ] **Step 5: Compare stable manifest/source/data identities**

Verify checksums, schema/counts, representative queries, source metadata, and byte/tree identity
against the existing release. Record exact differing fields.

- [ ] **Step 6: Complete the dated redistribution review**

Publish only if identity differs and the review explicitly permits the exact asset/use/attribution.
Store the complete digest-bound record only as `CLINGEN_RIGHTS_RECORD_JSON` in the protected
`data-release` environment, dispatch with `publish=true`, and verify the published immutable
identity. Otherwise record identical no-op or rights blocker and leave the current release selected.

### Task 10: Correct gnomAD v4.1.1 provenance through the live contract

**Files:**
- Modify: `gnomad_link/mcp/resources.py`
- Modify: `tests/unit/mcp/test_freshness_meta.py`
- Modify: README/resource/docs locations that literally advertise 4.1.0
- Modify: changelog inputs according to repository convention; Plan 4 owns application version files

**Interfaces:**
- Consumes: representative live GraphQL responses at supported operations.
- Produces: `GNOMAD_DATA_RELEASE = "4.1.1"` in every response provenance field after compatibility
  is proven.

- [ ] **Step 1: Run representative live GraphQL contract queries**

Exercise supported gene, variant, constraint, and coverage operations against upstream and record
schema/results/errors. No source change is made if upstream has not actually switched the queried
contract.

- [ ] **Step 2: Change literal behavior tests first**

Update freshness tests to require `"4.1.1"` and verify RED against production's 4.1.0 constant.

```bash
uv run pytest tests/unit/mcp/test_freshness_meta.py -q
```

- [ ] **Step 3: Update the single source and docs**

Change `GNOMAD_DATA_RELEASE`, resource/docs references, and changelog. Do not create a data bundle.

- [ ] **Step 4: Verify GREEN and full gate**

```bash
uv run pytest tests/unit/mcp/test_freshness_meta.py -q
make ci-local
git add gnomad_link/mcp/resources.py tests/unit/mcp/test_freshness_meta.py README.md docs \
  CHANGELOG.md
git commit -m "fix: report gnomAD v4.1.1 provenance"
```

### Task 11: Refresh GenCC local cache and verify deployed conditional refresh

**Files:**
- Local ignored: `data/gencc.sqlite` and conditional metadata cache
- Modify product files only if a reproduced defect is found under TDD

**Interfaces:**
- Consumes: normal `gencc-link-data refresh` conditional ETag/Last-Modified flow.
- Produces: current local identity and live deployment diagnostics; no GitHub data release.

- [ ] **Step 1: Run the repository's normal conditional refresh**

```bash
make data-refresh
make data-info
```

Verify source ETag/Last-Modified, counts, atomic DB replacement, and representative queries.

- [ ] **Step 2: Verify live diagnostics and conditional behavior**

Run health/diagnostics, then a bounded conditional check proving a 304/no-op or an exact changed
identity. If production is stale, diagnose scheduler/volume state and fix only with a failing test.

### Task 12: Prove current, intentional, or live-source states for remaining data services

**Files:**
- Orphanet when needed: create `orphanet_link/ingest/release_identity.py`, create
  `tests/unit/test_build_data_workflow.py`, modify `.github/workflows/build-data.yml`
- GTEx when needed: modify `gtex_link/models/gtex.py`, `gtex_link/models/responses.py`,
  `tests/test_models/test_validation.py`, `tests/test_models/test_schema_literals.py`,
  `tests/unit/test_readme_datasets.py`, `README.md`, `docs/data.md`; create
  `tests/integration/test_live_dataset_catalog.py`
- Modify other docs/tests only when observed truth differs from advertised truth
- Local ignored refreshes: HGNC, HPO, MGI, Mondo developer databases

**Interfaces:**
- Consumes: source metadata, live diagnostics, representative queries.
- Produces: exact acceptance records rather than subjective “fresh” claims.

- [ ] **Step 1: Verify current immutable/in-process sources**

For HGNC, MGI, and Mondo, compare live reported version/ETag/build time/counts to canonical source;
refresh ignored local developer DBs through native commands. For Mondo, also run its repository
deployment verifier with the recorded service endpoint through `make verify-deploy URL="$MONDO_URL"`.

For Orphanet, first add workflow-contract tests proving that an existing
`data-1.3.42-4.1.8-2025-03-03` tag is not sufficient to skip: the workflow must download the exact
manifest/checksum assets, compare source/asset/schema/count identity, no-op only when identical,
fail on a published or draft collision without deletion. Add a bounded pure identity
helper if the workflow cannot be tested without shell text inspection. Observe RED, implement the
minimum, run `make ci-local`, merge it into the Plan 2 PR, then dispatch the weekly builder and
require the verified identical no-op.

- [ ] **Step 2: Correct and live-prove GTEx's exact supported set**

Add a captured live-catalog fixture test requiring exactly
`{gtex_v8, gtex_v10, gtex_v10_sn_rna_seq}` and proving every advertised ID succeeds in a
representative query. Observe RED because current models advertise `gtex_snrnaseq_pilot`. Update
the `DatasetId`/`DatasetLiteral` definitions and `DATASET_GENCODE_VERSION` only after confirming the
live identifier and its GENCODE release, then align schema tests, capabilities/provenance tests,
README, and data documentation. Record a compatibility reason and representative query result for
every upstream catalog ID that remains excluded. Run focused model/schema/live-contract tests and
`make ci-local` before committing.

- [ ] **Step 3: Verify intentional historical provenance**

For MetaDome, prove responses/README expose GRCh37, Gencode v19, gnomAD r2.0.2, ClinVar
2018-06-03, Pfam 30.0, and the unavailability of a newer upstream artifact. Do not relabel it current.

- [ ] **Step 4: Verify live-source services without fake releases**

For AutoPVS1, LitVar, PanelApp, PubTator, SpliceAI Lookup, STRINGdb, UniProt, and VEP, record
canonical URL, UTC result, source/version field or `not_available`, representative query, and
bounded transient classification. Rerun UniProt's exact 503 job once; do not weaken it.

### Task 13: Merge data commits and record release/pin outputs

**Files:**
- External: consolidated backend PRs, data releases, release attestations
- Modify: ignored SDD/closure ledger only

**Interfaces:**
- Consumes: reviewed Tasks 2–12.
- Produces: merged code SHAs, immutable data identities, no-op evidence, rights blockers, and exact
  runtime pins for the release/deployment closure plan.

- [ ] **Step 1: Add code fixes to the owning Plan 2 replacement PRs**

Accept the ledgered worktree lease only at the exact Plan 2 clean head, add source/workflow repair
commits, rerun native full gates, return the exact clean head to Plan 2, and require fresh GitHub
checks; previously green checks are stale. Merge the source-repair PR before any workflow dispatch
that requires corrected code on `main`.

- [ ] **Step 2: Verify every release/no-op externally**

For published assets, verify tag immutability, asset/checksum/manifest agreement, attestation, and
representative data queries. Then create a separate data-pin PR from fresh `origin/main`, change
pin/config tests RED then GREEN, run `make ci-local`, review, and merge. Plan 4 adds the sole
application version bump to that pin state and publishes the application release. For no-ops,
record stable identity comparison and successful workflow run. For blockers, record the exact
unmet precondition without publishing.

- [ ] **Step 3: Hand exact tuples to Plan 4**

Record application source SHA, data tag/digests/schema, prior tuple, target tuple, deployment
compatibility, and rollback tuple for every changed data-bound service.
