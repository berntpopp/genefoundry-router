# Fleet Release, Deployment, and Closure Implementation Plan

> Historical record — this plan records the approved execution sequence as of 2026-08-30.
> Current behavior is defined by merged code, immutable release evidence, GitHub state, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release and deploy every changed application/data tuple, refresh router truth from live
immutable evidence, prove strict 21/21 transport and behavior, and close with a reproducible
machine-readable audit and final security assessment.

**Architecture:** Data releases precede application pins; backend applications release and deploy
one at a time with exact rollback tuples; the router baseline/final release follows stable backend
deployment; strict closure probes follow the final router deployment; the security-profile gitlink
and reassessment are the last ordinary mutations, followed only by the modeled detached evidence
seal. The deployment controller remains
`/home/bernt-popp/development/strato_v6_docker_npm`.

**Tech Stack:** Python 3.12+, uv, pytest, FastMCP, Docker Compose/Nginx Proxy Manager, GitHub
Actions/Releases/CLI, OCI digests, Trivy, SPDX SBOM, attestations, JSON closure manifests.

**Spec:** `docs/superpowers/specs/2026-08-30-fleet-security-pr-data-remediation-design.md`

**Execution order:** Complete Tasks 1–2, then Task 4 Steps 1–4 (merge the generic controller
record/preflight safety schema), then Task 3, then Task 4 Steps 5–8 and the remaining tasks. This
intentional split ensures release-produced deployment records are validated by already-merged
controller code before any pin or production mutation.

## Global Constraints

- External mutations in this plan are covered by the owner's explicit 2026-08-30 end-to-end
  authorization, but each exact target is resolved read-only before mutation.
- Never overwrite a tag/release asset, force-push protected history, deploy a mutable tag, or expose
  a backend host port.
- The release workflow builds one production OCI artifact and scans/SBOMs/smokes/attests/publishes
  that exact digest.
- Every deployment record includes owner/host/stack path, current and target image/data tuple,
  volume/schema compatibility, backup requirement, exact deploy/observation/rollback commands, and
  ordered verification.
- A backend rollback restores the prior image and prior data pin together when data/schema
  compatibility requires it; the router rollback preserves its OAuth/refresh-observability volume.
- The router baseline is generated only from released, deployed, verified application manifests.
- Scheduled warning-tolerant monitoring is not closure evidence; closure fails on any nonzero
  transport or behavior probe.
- Closure evidence is at most 24 hours old and invalidated by any relevant source, workflow, base
  image, scanner policy/database, upstream data, release, or deployment change.
- GeneReviews publication/deployment remains prohibited without its affirmative dated rights record.
- The security profile is updated only after every available final release/deployment/probe result.
- Router Tasks 1–2 and 6 use one clean `codex/fleet-closure-20260830` worktree created from fresh
  `origin/main`; its exact lease/head is recorded until the final router PR merges.

---

### Task 1: Add a typed, fail-closed closure evidence contract

**Files:**
- Create: `genefoundry_router/release/closure.py`
- Create: `genefoundry_router/release/dependabot_cutover.py`
- Create: `genefoundry_router/release/fleet_workflow_refs.py`
- Create: `scripts/validate_fleet_closure.py`
- Create: `scripts/validate_dependabot_cutover.py`
- Create: `scripts/validate_fleet_workflow_refs.py`
- Create: `tests/release/test_fleet_closure_manifest.py`
- Create: `tests/release/test_dependabot_cutover.py`
- Create: `tests/release/test_fleet_workflow_refs.py`
- Create: `tests/fixtures/fleet_closure_valid.json`
- Create: `tests/fixtures/fleet_closure_blocked.json`
- Create: `tests/fixtures/dependabot_cutover_valid.json`
- Create: `tests/fixtures/fleet_workflow_refs_valid.json`
- Create after evidence exists outside source: `/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-remediation-closure.json`

**Interfaces:**
- Consumes: JSON with repository, PR, checks, action, image, data, deployment, probe, alert, and
  supporting-repository evidence.
- Produces: `load_closure_manifest(payload: object) -> FleetClosureManifest` and
  `validate_fresh_closure(manifest, *, now: datetime, max_age: timedelta) -> None` for either honest
  complete/blocked records, plus `require_complete_closure(manifest) -> None` for strict success.

- [ ] **Step 1: Write failing schema and freshness tests**

Before editing, fetch router `origin/main`, verify the canonical origin/instructions and primary
user state, and create or reuse only the clean ledger-owned
`.worktrees/fleet-closure-20260830` worktree/branch.

Create one minimal valid 22-application/3-supporting-repository fixture and mutations proving
rejection of:

- missing or extra repository;
- bool-as-int/type coercion and unknown fields;
- unmapped original PR/head or replacement without merge/check evidence;
- action refs that are not 40-hex SHAs, or a reusable router workflow ref that differs from the
  closure's exact Plan 1 trusted-builder SHA even when both values are valid 40-hex SHAs;
- image without platform/digest/scanner version/database timestamp/policy/verdict/SBOM/attestation;
- any selected or program-produced image with a nonzero fixable HIGH/CRITICAL count, a waiver,
  wrong policy ID, omitted intermediate digest, or evidence for a different digest;
- data-bound service without exact data identity or typed `not_applicable` reason;
- deployment without current/target/rollback tuple;
- probe summary other than transport 21/21 and behavior 21/21;
- alert evidence older than 24 hours or evidence predating a relevant release/deployment;
- website support row without full image/release/deployment evidence, benchmark row without exact
  required-check runs, security-profile row without final assessment merge/evidence, or any
  licensing/control blocker represented as success;
- absent deployment-controller source/PR/check/ledger/preflight/rollback evidence or controller
  evidence whose exact revision differs from the one used for rollout.
- absent or malformed `expected_detached_seal`: it must be the discriminated `planned` state, use
  exact `fleet-remediation-evidence-20260830-rN` syntax, target the same final router source SHA,
  name exactly the closure JSON, `SHA256SUMS`, and release-intent assets, and declare itself as the
  sole permitted post-assessment repository mutation. Reject extra assets, an already-used tag,
  target mismatch, arbitrary tag syntax, or a second future mutation.

Also write cutover-contract tests that require exactly 22 repositories and an immutable 175-row
audit baseline (router 10 plus backends 165), a separately typed and counted `post_audit_deltas`
collection, strict fields/types, unique repository/PR/head tuples, valid supersession chains for
changed heads, a complete effective mapping for every refreshed live PR, and no unmapped or silently
dropped baseline/delta row. Each row must split immutable `identity` from mutable `resolution`;
the model re-hashes all 175 baseline identities and checks the recorded audit digest. Test zero
deltas, filling resolution without changing identity, a new PR, a changed-head supersession,
duplicate/cyclic supersession, count mismatch, baseline identity mutation, and unknown fields.

Write a fleet reusable-workflow validator contract that takes the exact trusted-builder router SHA
from Plan 1 plus a discovered occurrence inventory for all 21 backend repositories. Require every
`berntpopp/genefoundry-router/.github/workflows/...@SHA` CI/release occurrence to equal that one
40-hex SHA, require every discovered occurrence to have a file/line/repository row, require each
configured repository to be typed as `consumer` or reviewed `not_applicable`, and reject an older
but otherwise immutable SHA. Its CLI requires a strict repository-to-worktree map containing the
literal path, expected clean HEAD SHA, and phase for every backend; it refuses a dirty/mismatched
tree, primary-checkout inference, recursive directory discovery, or an unledgered nested worktree.
It emits canonical JSON bound to every scanned HEAD and fails if the supplied inventory omits a
discovered ref.

- [ ] **Step 2: Run tests and verify RED**

```bash
uv run pytest tests/release/test_fleet_closure_manifest.py -q
```

Expected: module/validator does not exist.

- [ ] **Step 3: Implement strict frozen Pydantic models and validator**

Use `extra="forbid"`, strict scalar fields, the router plus the exact backend set derived from
`servers.yaml` plus the three literal supporting repos, RFC3339 UTC timestamps, SHA/digest
patterns, and explicit discriminated states (`verified`, `not_applicable`, `blocked`). Keep the
module below 600 LOC by splitting only if the repository line-budget test requires it. Model the
three support roles separately; `genefoundry` is a containerized support application and carries
the same full image/release/deployment record as fleet applications. Model
`strato_v6_docker_npm` separately as the exact deployment-controller record rather than pretending
it is an application or one of the three supporting repositories.
Model `expected_detached_seal` in this initial contract (not as a late untyped Task 11 extension)
and cross-validate its target against the final router release/source identity.
Implement the cutover model and CLI separately in `dependabot_cutover.py`; the closure manifest
stores its SHA-256 and effective row count and re-validates it rather than duplicating the schema.
Implement the workflow-ref model/CLI separately in `fleet_workflow_refs.py`; closure stores the
trusted-builder SHA plus the validated occurrence-report digest and requires every consumer row to
equal it.

- [ ] **Step 4: Verify GREEN and CLI behavior**

```bash
uv run pytest tests/release/test_fleet_closure_manifest.py -q
uv run pytest tests/release/test_dependabot_cutover.py -q
uv run pytest tests/release/test_fleet_workflow_refs.py -q
uv run python scripts/validate_fleet_closure.py tests/fixtures/fleet_closure_valid.json
uv run python scripts/validate_dependabot_cutover.py \
  tests/fixtures/dependabot_cutover_valid.json
uv run python scripts/validate_fleet_workflow_refs.py --inventory \
  tests/fixtures/fleet_workflow_refs_valid.json --required-router-sha \
  0123456789abcdef0123456789abcdef01234567
uv run python scripts/validate_fleet_closure.py --allow-blocked \
  tests/fixtures/fleet_closure_blocked.json
make ci-local
```

- [ ] **Step 5: Commit the contract before authoring evidence**

```bash
git add genefoundry_router/release/closure.py genefoundry_router/release/dependabot_cutover.py \
  genefoundry_router/release/fleet_workflow_refs.py scripts/validate_fleet_closure.py \
  scripts/validate_dependabot_cutover.py scripts/validate_fleet_workflow_refs.py \
  tests/release/test_fleet_closure_manifest.py tests/release/test_dependabot_cutover.py \
  tests/release/test_fleet_workflow_refs.py \
  tests/fixtures/fleet_closure_valid.json tests/fixtures/fleet_closure_blocked.json \
  tests/fixtures/dependabot_cutover_valid.json tests/fixtures/fleet_workflow_refs_valid.json
git commit -m "feat: validate fleet closure evidence"
```

### Task 2: Add a strict transport-and-behavior closure sweep

**Files:**
- Create: `scripts/fleet_closure_probe.py`
- Create: `tests/unit/test_fleet_closure_probe.py`
- Create: `.github/workflows/fleet-closure.yml`
- Create: `tests/unit/test_fleet_closure_workflow.py`
- Modify: `genefoundry_router/conformance.py`, `docs/conformance/behaviour.py`
- Create: `tests/conformance/test_probe_auth.py`
- Create: `tests/conformance/test_behaviour_strict.py`
- Reuse unchanged: `servers.yaml`

**Interfaces:**
- Consumes: exact configured backend registry/environment URLs.
- Produces: JSON report with one transport and one behavior result per backend; exit 0 only for
  exact 21/21 in both categories.

- [ ] **Step 1: Write failing orchestrator tests**

Use injected subprocess runner fixtures for 21 literal backends. Test all-zero success, one
transport exit 2, one behavior exit 1, timeout, malformed/missing report, duplicate/missing backend,
and secret-redacted stderr. Add PubTator cases proving both probes receive its service token through
a child-only environment variable, never argv/URL/report/stderr, while uncredentialed backends do
not receive it. Reject URL userinfo/query/fragment before spawning. Every nonzero/malformed/timeout
case must make the overall command nonzero while retaining the other bounded results in the report.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/unit/test_fleet_closure_probe.py \
  tests/unit/test_fleet_closure_workflow.py -q
```

- [ ] **Step 3: Implement bounded strict orchestration**

For each backend, invoke the installed transport probe and pass the resolved registry URL and
server name as separate subprocess arguments to `docs/conformance/behaviour.py`, after `--name`,
with an explicit per-probe timeout and bounded captured output. Read URLs only from existing
environment variables; never emit credentials/query strings. Validate exact registry cardinality
and write the report atomically. Add `GENEFOUNDRY_PROBE_SERVICE_TOKEN` support to both probe CLIs;
the orchestrator copies only the current backend's `service_token_env` value into that child
variable, the probes turn it into an Authorization header, and neither exposes a token CLI option.

Add an opt-in `docs/conformance/behaviour.py --strict --json-out PATH` contract without changing
the warning-tolerant scheduled mode. Under strict mode, any failed, skipped, or schema-ungated probe,
zero positive behavior coverage, duplicate/missing result, or malformed report exits nonzero and
records the reason. `tests/conformance/test_behaviour_strict.py` must prove that the current ordinary
report's permissive `conformant` field cannot turn skipped/ungated coverage into closure success.
The fleet orchestrator must invoke this strict mode and accept a backend only when its strict JSON
has positive schema-derived coverage with zero failed, skipped, and ungated probes.

- [ ] **Step 4: Implement manual-only workflow evidence**

The workflow uses least-privilege `contents: read`, pinned checkout/setup-python/setup-uv,
`production-fleet` environment secrets, no warning conversion (`|| true`/exit-code-2 special case),
and uploads the JSON summary. It is distinct from scheduled warning-tolerant monitoring.

- [ ] **Step 5: Verify GREEN and full router gate**

```bash
uv run pytest tests/unit/test_fleet_closure_probe.py \
  tests/unit/test_fleet_closure_workflow.py -q
make ci-local
git add scripts/fleet_closure_probe.py tests/unit/test_fleet_closure_probe.py \
  .github/workflows/fleet-closure.yml tests/unit/test_fleet_closure_workflow.py \
  genefoundry_router/conformance.py docs/conformance/behaviour.py \
  tests/conformance/test_probe_auth.py tests/conformance/test_behaviour_strict.py
git commit -m "feat: add strict fleet closure probes"
```

### Task 3: Prepare exact application versions and release records

**Files:**
- Modify in every changed application repo: `pyproject.toml`, `uv.lock`, changelog/release notes
- Modify only when exact data pins changed: `container-release.json`, production/NPM Compose/config
  and their tests
- External: immutable release tags, manifests, OCI digests, SBOMs, attestations

**Interfaces:**
- Consumes: merged Plan 2/3 changes and accepted data tuples.
- Produces: next SemVer patch per backward-compatible repo, or justified minor/major where its
  public contract changed, with one exact release record.

- [ ] **Step 1: Create the ledgered version/release worktree from fresh main**

For each owning repository, refresh `origin/main`, verify canonical origin/instructions/user state,
and create a clean `codex/fleet-release-20260830` worktree. Its required handoff is the exact Plan 2
replacement merge SHA by default. For a repository with a later Plan 3 source-repair or accepted
data-pin merge, that exact later SHA supersedes the Plan 2 handoff and must have the earlier merge as
an ancestor. No Plan 2/3 worktree is reused after its branch has merged.

- [ ] **Step 2: Re-query the current version/tag before each bump**

```bash
git fetch origin main --tags
evidence_root=/home/bernt-popp/development/fleet-remediation-evidence-20260830
gh release list --repo berntpopp/clinvar-link --limit 100 \
  --json tagName,isDraft,isPrerelease,publishedAt \
  >"$evidence_root/clinvar-link-semver-releases.json"
current_tag=$(jq -r '.[] | select(.isDraft == false and .isPrerelease == false) | .tagName' \
  "$evidence_root/clinvar-link-semver-releases.json" \
  | uv run python -c 'import re,sys; from packaging.version import Version; tags=[x.strip() for x in sys.stdin if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", x.strip())]; print(max(tags, key=lambda x: Version(x[1:])))')
test -n "$current_tag"
current_release_sha=$(git rev-parse "$current_tag^{}")
[[ "$current_release_sha" =~ ^[0-9a-f]{40}$ ]] || exit 65
uv version --bump patch
uv lock
```

Do not use GitHub's generic latest-release pointer because a data release may be newer than the
application release. Require the enumerated tag to match strict application SemVer, resolve its
peeled commit, download its application manifest, and prove manifest source/version equals that
tag/commit before selecting the next version. Do not assume audit-time versions; ensure
`pyproject.toml`, installed `__version__`, MCP serverInfo, health/buildinfo, lockfile root package,
changelog, and tag agree.

- [ ] **Step 3: Freeze pre-release compatibility and rollback metadata**

Prerequisite: Task 4 Steps 1–4 have merged the exact controller record schema and the immutable
executor worktree is detached at that safety merge SHA.

From `/home/bernt-popp/development/strato_v6_docker_npm/config/fleet.lock.yaml` and live health,
record the exact current/prior tuple, target source revision/version/data tuple, schema/volume
compatibility, backup or no-backup rationale, exact intended pin/deploy/health/rollback commands,
and observation threshold. Mark release-produced fields—application manifest hash and target OCI/
platform/SBOM/attestation digests—as strict typed `pending_release`; do not guess them or call this a
deployable record before the tag workflow produces them.

- [ ] **Step 4: Run fresh repository and image gates**

```bash
make ci-local
git status --short
```

Require the PR's exact production-image container gate green on the final version commit.

- [ ] **Step 5: Commit, push, review, merge, and tag the exact merge SHA**

```bash
git add pyproject.toml uv.lock CHANGELOG.md container-release.json docker tests
git diff --cached --check
git commit -m "chore: prepare verified application release"
git push -u origin codex/fleet-release-20260830
gh pr create --repo berntpopp/clinvar-link --base main --head codex/fleet-release-20260830 \
  --title 'chore: prepare verified application release' \
  --body-file /tmp/clinvar-link-release-pr.md
release_pr_number=$(gh pr view --repo berntpopp/clinvar-link codex/fleet-release-20260830 \
  --json number --jq .number)
gh pr checks "$release_pr_number" --repo berntpopp/clinvar-link --watch
gh pr merge "$release_pr_number" --repo berntpopp/clinvar-link --merge --delete-branch
merge_sha=$(gh pr view "$release_pr_number" --repo berntpopp/clinvar-link \
  --json mergeCommit --jq .mergeCommit.oid)
[[ "$merge_sha" =~ ^[0-9a-f]{40}$ ]] || exit 65
git fetch origin "$merge_sha" --tags
version=$(git show "$merge_sha":pyproject.toml | sed -n 's/^version = "\([^"]*\)"/\1/p')
test -n "$version"
git tag -s "v${version}" "$merge_sha" -m "clinvar-link v${version}"
git push origin "v${version}"
```

Run this literal sequence with each owning repository substituted. Never re-tag a failed release;
repair and publish a new version.

- [ ] **Step 6: Verify each release artifact, not merely workflow conclusion**

Check tag target, GitHub immutability, application manifest source/version/platform/OCI digest,
zero-fixable policy with scanner/version/database timestamp, SBOM digest, provenance/attestation,
MCP definition, data identity, anonymous pull, and runtime-hardening smoke. Select the release run by
the exact tag/head SHA and captured tag-push time, never the latest run; record its ID. Then replace
every `pending_release` field in the external deployment record with the verified manifest/image/
platform/SBOM/attestation tuple, validate the complete record under the controller schema, hash and
independently review it. Task 4 may not stage/pin/deploy a partial record.

### Task 4: Deploy each backend application/data tuple with rollback

**Files:**
- Create in `strato_v6_docker_npm`: `config/release-ledger.yaml`,
  `scripts/release_ledger.py`, `scripts/utils/deployment_preflight.py`,
  `tests/test_release_ledger.py`, `tests/test_deployment_preflight.py`
- Modify there: `scripts/manage.py`, `scripts/utils/docker.py`, `scripts/utils/lockfile.py`,
  `config/fleet.lock.yaml`, `tests/test_lockfile_deploy.py`, `tests/test_release_controls.py`,
  `docs/commands.md`
- Create there when data changes: `config/data-attestations.yaml`,
  `scripts/utils/data_attestation.py`, `scripts/utils/data_activation.py`,
  `tests/test_data_attestation.py`, `tests/test_data_activation.py`
- External: production NPM/Compose stacks

**Interfaces:**
- Consumes: a verified immutable backend release manifest file and a frozen deployment record.
- Produces: manifest-explicit pinning plus fail-closed checkout/Compose preflight, `up --no-build`
  deployment, live health revision/data identity matching the target tuple, or an automated verified
  rollback to the exact prior application/data/Compose tuple. One immutable executor revision runs
  commands against separately reviewed controller configuration revisions; rollout records bind both.

- [ ] **Step 1: Read deployment instructions and verify controller state**

```bash
git -C /home/bernt-popp/development/strato_v6_docker_npm status --short --branch
git -C /home/bernt-popp/development/strato_v6_docker_npm fetch --prune origin
sed -n '1,360p' /home/bernt-popp/development/strato_v6_docker_npm/CLAUDE.md
```

Create/reuse a clean dedicated deployment worktree; never modify its primary checkout.

- [ ] **Step 2: TDD a frozen release ledger and manifest-explicit pin**

First add tests requiring one exact row per selected service with repository, manifest SHA-256,
application image/digest/version/source revision, Compose revision/projection digest, data tuple or
typed `none`, prior tuple, alerts/PR disposition, operator, observation window, and rollback command.
Test that `pin PROJECT --manifest FILE --record FILE` rejects `latest`, repository/project mismatch,
invalid/unsigned provenance, manifest digest mismatch, absent data identity, and a target equal to
neither the reviewed ledger nor release manifest. Require the lock entry to retain manifest digest,
data tuple, target tuple, and prior tuple. Observe RED.

Implement `release_ledger.py`, strict lock loading, and manifest-explicit pinning. Add an explicit
`--config-root PATH` boundary to every ledger/pin/deploy/attest/health/rollback command so an
immutable code executor never silently reads its own checkout's config. A staged pin writes the
reviewed target/prior record but does not claim that the target is live; a separate `promote-live`
operation can update the selected lock/attestation only after live acceptance. The controller may
query GitHub only to verify the supplied tag/manifest/provenance; it never selects the newest `v*`
for this rollout. Run focused tests to GREEN and commit the ledger schema separately.

- [ ] **Step 3: TDD fail-closed remote checkout, Compose projection, and rollback**

Add tests for dirty remote checkout, failed fetch, wrong `HEAD`, unreviewed Compose projection,
failed image pull, any build command, failed health/data identity, and rollback failure. Each
preflight failure must return before `docker compose up`; deployment must use only the pinned
`name@sha256` with `pull` then `up --no-build`. Remove the current
`Git pull failed, continuing anyway` path.

Implement an ordered preflight that requires a clean remote tree, fetches and detaches at the exact
reviewed Compose/source revision, renders JSON Compose, compares critical image/network/volume/
health/read-only/capability/security fields to the frozen projection, and refuses drift. Implement
`rollback PROJECT --record FILE` to restore and verify the exact prior lock, Compose revision, image,
and data volume/identity; never delete either data volume. For data-bearing rows, implement
`activate-data PROJECT --record FILE`: prepare only a named candidate volume with the target image,
verify candidate manifest/digests/schema/counts, stop only the target service, switch the target
image+volume atomically, require live health identity, and automatically restore the prior
image+volume on failure. Tests prohibit active-volume writes, `docker volume rm`, host/Docker/NPM/
router restarts, or success without live identity.

Implement a literal adapter registry in `scripts/utils/data_activation.py`; an unknown
`data.mode=release` project fails before any Docker mutation. SQLite bundle services (including
ClinVar, HPO, MaveDB, ClinGen, and any other audited SQLite data row) must declare exact init/app
service names, candidate-volume mount, bundle verifier, schema/count/query probes, and the
prior/target Compose projections. GeneReviews uses a separate PostgreSQL/pgvector adapter with an
exact sidecar seed-transfer service, new candidate DB volume, migrations-before-data-only-restore,
DB/restore/app service set, socket/state handling, and representative search probes. Every adapter
test injects crashes after prepare, seed transfer, restore, stop, switch, and health; each must leave
or restore the prior volume/image/projection and never delete either volume. The deployment record
freezes the chosen adapter and complete affected-service set; no generic best-effort fallback exists.

Add pre-baseline attestation tests and CLI mode:
`attest PROJECT --record FILE --prebaseline`. It verifies live health revision/image/data against
the exact release manifest and deployment record while deliberately not requiring equality with the
router's still-old candidate inventory. Ordinary post-router `attest PROJECT` retains strict router
pin/baseline equality. Global `attest --backend-prebaseline --records-dir DIR --json` is only a
bounded aggregation of 21 individually valid pre-baseline attestations.

```bash
controller_worktree=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-security-20260830
uv run --directory "$controller_worktree" pytest \
  tests/test_release_ledger.py tests/test_deployment_preflight.py \
  tests/test_lockfile_deploy.py tests/test_release_controls.py tests/test_data_attestation.py \
  tests/test_data_activation.py -q
make -C "$controller_worktree" ci
```

- [ ] **Step 4: Review and merge the controller safety PR before any production mutation**

Commit the ledger/preflight/rollback changes, push the controller branch, create a PR, require its
current CI/security checks and adversarial review, merge normally, and record the exact merge SHA.
Create two explicit worktree classes:

1. `fleet-safety-executor-<sha>` is clean and detached at the exact safety merge SHA. It remains
   immutable and supplies every subsequent `scripts/manage.py` implementation.
2. For each rollout phase, `fleet-config-<project>-<phase>` is a fresh branch from current
   `origin/main`. The executor receives its literal path with `--config-root`; it never edits its
   own detached tree. Each pre-activation config PR freezes the pending target/prior ledger row and
   projection, and each post-verification config PR promotes only the verified live lock and
   attestation. Record executor SHA, config base SHA, pre-activation merge SHA, and post-verification
   merge SHA. A failed rollout creates a reviewed rollback-outcome config PR instead of promoting
   the failed target.

The controller has no application release contract, so do not invent a container release.

- [ ] **Step 5: Freeze one backend record and pin it from the exact manifest**

```bash
controller_executor=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-safety-executor
controller_config=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-config-clinvar-pre
evidence_root=/home/bernt-popp/development/fleet-remediation-evidence-20260830
manifest="$evidence_root/application-manifests/clinvar-link.json"
record="$evidence_root/deployment-records/clinvar-link.json"
test -f "$manifest" && test -f "$record"
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  pin clinvar-link --stage --manifest "$manifest" --record "$record"
git -C "$controller_config" diff -- config/fleet.lock.yaml config/release-ledger.yaml \
  config/data-attestations.yaml
```

Verify pinned application OCI digest, manifest digest, source/Compose SHA, data tag/digests/volume,
and rollback tuple. Commit and merge the frozen target/prior release-ledger row and projection before
activation, then pass its exact merge SHA/config path to the executor; the selected live lock change
is authored from a new post-verification config worktree and merged only after live verification.

- [ ] **Step 6: Run controller gates and deploy exactly one project**

```bash
make -C "$controller_executor" ci
if jq -e '.data.mode == "release"' "$record" >/dev/null; then
  python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
    activate-data clinvar-link --record "$record"
else
  python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
    deploy clinvar-link --record "$record" --no-build
fi
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  attest clinvar-link --record "$record" --prebaseline
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  health clinvar-link --record "$record"
```

Observe for the deployment record's bounded interval; verify health version/revision/data, MCP
definition, transport, representative behavior, internal-only port, read-only/cap-drop/no-new-
privileges, and restart policy.

- [ ] **Step 7: Roll back on any failed acceptance check**

Run `python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" rollback
clinvar-link --record "$record"`,
then rerun attest/health/MCP/behavior and record both failure and rollback. The rollback must restore
the prior image/data/Compose tuple and verify it; never mutate the new release or delete volumes.

- [ ] **Step 8: Commit verified controller pins in deployment order**

From a new config branch based on the pre-activation merge, run `promote-live` for exactly one
verified project, rerun controller CI, and merge its live pin/attestation result. Continue serially
through all changed backends; unchanged services retain exact existing tuples and still receive
closure probes. A failed rollout records the verified prior tuple/rollback outcome and does not
promote the failed target.

### Task 5: Release and deploy the hardened `genefoundry` website

**Files:**
- Modify in website source: supply-chain/action/base pins and version/release metadata from Plan 1
- Modify in deployment controller: website tuple through `scripts/manage.py pin genefoundry`

**Interfaces:**
- Consumes: website green PR, protected controls, immutable exact release.
- Produces: live website digest/revision with health/attestation and rollback record.

- [ ] **Step 1: Verify source release evidence**

Require exact source SHA, OCI digest, base digests, zero-fixable policy, SBOM/provenance, non-root and
read-only runtime, expose-only proxy network, immutable release, and successful explicit repository
target. Do not treat the prior v0.1.2 failed publish as evidence.

- [ ] **Step 2: Freeze, merge, deploy, and promote through the controller lifecycle**

```bash
controller_executor=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-safety-executor
controller_config=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-config-genefoundry-pre
website_manifest=/home/bernt-popp/development/fleet-remediation-evidence-20260830/application-manifests/genefoundry.json
website_record=/home/bernt-popp/development/fleet-remediation-evidence-20260830/deployment-records/genefoundry.json
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  pin genefoundry --stage --manifest "$website_manifest" --record "$website_record"
# Run controller CI and merge the pre-activation config PR; reset controller_config to that merge.
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  deploy genefoundry --record "$website_record" --no-build
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  attest genefoundry --record "$website_record" --prebaseline
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  health genefoundry --record "$website_record"
```

Before execution, require the website record to carry the exact prior/target image, Compose
projection, proxy route, health, and rollback command. After acceptance, create a fresh config
branch from the pre-activation merge, run `promote-live genefoundry --record "$website_record"`,
run controller CI, and merge the post-verification config PR. Roll back through the literal recorded
prior tuple on any revision, health, proxy-routing, or hardening failure and merge only the verified
rollback outcome. Record executor plus pre/post config merge SHAs.

### Task 6: Build the router candidate inventory and final baseline

**Files:**
- Modify: `ci/release-candidate-inventory.json`
- Create from the exact Plan 2 handoff: `ci/dependabot-cutover.json`
- Create from the final default-SHA Plan 2 handoff: `ci/fleet-workflow-refs.json`
- Modify: `genefoundry_router/data/fleet-baseline.json`
- Modify: router version/changelog/`uv.lock`
- Test: existing baseline/candidate/drift/release tests

**Interfaces:**
- Consumes: all 21 deployed backend application release manifests and live verified identities.
- Produces: normalized baseline bound to immutable release manifests and a final router patch release.

- [ ] **Step 1: Assemble verified release manifests and identity document**

Every manifest must match live health SHA/digest/data. Do not include unpublished branches or a
manifest whose target is not currently deployed. Copy the exact reviewed Plan 2 cutover handoff to
`ci/dependabot-cutover.json`, verify its detached SHA-256 before staging, and add router tests that
invoke the versioned Task 1 validator and require 22 repositories, the immutable 175-row audit
baseline, the separately counted post-audit deltas, valid supersession chains, and complete
effective replacement mappings for the refreshed live PR set.
Copy only Plan 2 Task 9's post-merge `fleet-workflow-refs-final.json` to
`ci/fleet-workflow-refs.json`, verify its detached hash, and rerun the validator against clean
detached worktrees at the exact recorded default SHAs. Reject the earlier candidate-worktree report.

- [ ] **Step 2: Generate candidate inventory through the native command**

```bash
evidence_root=/home/bernt-popp/development/fleet-remediation-evidence-20260830
controller_executor=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-safety-executor
controller_config=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-config-backends-live
prebaseline="$evidence_root/backend-prebaseline.json"
verified_manifests="$evidence_root/fleet-application-releases.json"
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  attest --backend-prebaseline \
  --records-dir "$evidence_root/deployment-records" --json \
  >"$prebaseline"
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  export-release-manifests \
  --attestation "$prebaseline" --out "$verified_manifests"
prebaseline_sha=$(sha256sum "$prebaseline" | cut -d' ' -f1)
[[ "$prebaseline_sha" =~ ^[0-9a-f]{64}$ ]] || exit 65
reviewed_identity="fleet-2026-08-30-${prebaseline_sha:0:12}"
make release-candidate RELEASE_MANIFESTS="$verified_manifests" IDENTITY="$reviewed_identity"
```

Create `controller_config` as a clean detached worktree at the exact final backend/website
post-verification config merge SHA and assert that SHA against the rollout ledger before either
command. The executor must not read config from its own detached safety-code checkout.

- [ ] **Step 3: Snapshot the live normalized baseline**

```bash
make snapshot-baseline RELEASE_CANDIDATE_INVENTORY=ci/release-candidate-inventory.json
make ci-local
make ci-full
```

Expected: definition matches are derived from live released backends and no unreviewed drift is
normalized away.

- [ ] **Step 4: Prepare, review, merge, and release the final router patch**

Bump from the control-plane release to the next patch, include candidate/baseline plus closure-probe
and closure-manifest code, run `make ci-local` and `make ci-full`, commit on the ledgered closure
branch, push, create the final router PR, and require fresh source/container/release checks plus
whole-branch adversarial review. Immediately before the final gates, fetch `origin/main`, integrate
any material advance normally (never rewrite a shared reviewed branch), review the resulting diff,
and rerun both gates. Capture that PR's merge commit SHA, assert the reviewed head is its
ancestor, read the version from that exact SHA, sign/tag that SHA, and select the tag-triggered run
by matching head SHA and tag-push time. Verify the exact immutable release artifact; never tag moving
`origin/main` or select the latest workflow run.

### Task 7: Deploy the final router without losing OAuth state

**Files:**
- Modify in deployment controller: router tuple through `scripts/manage.py pin genefoundry-router`
- External: router production stack and persistent `fastmcp_data` volume

**Interfaces:**
- Consumes: final router application manifest, prior router tuple, stable backend fleet.
- Produces: live final router revision/baseline with preserved OAuth and refresh-observability state.

- [ ] **Step 1: Verify persistent-state backup/compatibility and render config**

```bash
router_manifest=/home/bernt-popp/development/fleet-remediation-evidence-20260830/router-application-release-manifest.json
test -f "$router_manifest"
router_digest=$(jq -er '.image.digest' "$router_manifest")
[[ "$router_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 65
make container-deploy-verify MANIFEST="$router_manifest"
GENEFOUNDRY_IMAGE="ghcr.io/berntpopp/genefoundry-router@${router_digest}" \
  make docker-prod-config
```

Record the actual digest in the command before execution. Preserve the prior full tuple and
`fastmcp_data` volume; do not rotate OAuth signing/client secrets during deployment.

- [ ] **Step 2: Freeze, merge, deploy, and promote through the controller**

```bash
controller_executor=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-safety-executor
controller_config=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-config-router-pre
router_record=/home/bernt-popp/development/fleet-remediation-evidence-20260830/deployment-records/genefoundry-router.json
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  pin genefoundry-router --stage --manifest "$router_manifest" --record "$router_record"
# Run controller CI and merge the pre-activation config PR; reset controller_config to that merge.
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  deploy genefoundry-router --record "$router_record" --no-build
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  attest genefoundry-router --record "$router_record"
python3 "$controller_executor/scripts/manage.py" --config-root "$controller_config" \
  health genefoundry-router --record "$router_record"
```

The router record must freeze prior/target image, baseline, Compose projection, OAuth/state volume,
health, and exact rollback. After acceptance, create a new config branch from the pre-activation
merge, run `promote-live genefoundry-router --record "$router_record"`, run controller CI, and merge
the post-verification config PR. Record executor plus pre/post config merge SHAs.

- [ ] **Step 3: Verify router security boundaries**

Run router health/revision/baseline, no-drift, list-tools, OAuth/auth contracts, and the proxy
integration that proves caller `Authorization` is stripped while only an exact backend-specific
credential is injected. Verify Streamable HTTP `/mcp` and no public backend ports.

- [ ] **Step 4: Roll back the entire router tuple on failure**

Run the executor's manifest-bound `--config-root "$controller_config" rollback
genefoundry-router --record "$router_record"` to
restore the prior digest/Compose tuple while preserving the state volume, then rerun health, auth,
drift, and fleet probes.

### Task 8: Run strict closure, monitoring, and control audits

**Files:**
- External: workflow runs/artifacts and repository variables/secrets names
- Modify: closure JSON only after current results exist

**Interfaces:**
- Consumes: final live fleet and configured monitoring/App credentials.
- Produces: current drift, monitoring, strict 21/21+21/21, and 22/22 control evidence.

- [ ] **Step 1: Confirm monitoring variables and successful current runs**

```bash
router_manifest=/home/bernt-popp/development/fleet-remediation-evidence-20260830/router-application-release-manifest.json
controller_executor=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-safety-executor
controller_config=/home/bernt-popp/development/strato_v6_docker_npm/.worktrees/fleet-config-router-live
expected_router_sha=$(jq -er '.source.revision' "$router_manifest")
current_main_sha=$(gh api repos/berntpopp/genefoundry-router/commits/main --jq .sha)
live_router_sha=$(python3 "$controller_executor/scripts/manage.py" \
  --config-root "$controller_config" health genefoundry-router --json | jq -er '.revision')
test "$current_main_sha" = "$expected_router_sha"
test "$live_router_sha" = "$expected_router_sha"
gh variable list --repo berntpopp/genefoundry-router
gh workflow run drift.yml --repo berntpopp/genefoundry-router --ref main
gh workflow run fleet-probe.yml --repo berntpopp/genefoundry-router --ref main
```

Select and watch each monitoring run with Plan 1 Task 6's exact workflow/event/head-SHA/dispatch-time
matcher; never use `--limit 1` as identity. If current `main`, the release-manifest source, or live
health revision differ, stop closure and repeat the final router release/deployment flow from the
new reviewed main rather than dispatching unreleased code.

- [ ] **Step 2: Dispatch strict closure and require exact counts**

```bash
final_router_sha=$(gh api repos/berntpopp/genefoundry-router/commits/main --jq .sha)
test "$final_router_sha" = "$(jq -er '.source.revision' "$router_manifest")"
dispatched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run fleet-closure.yml --repo berntpopp/genefoundry-router --ref main
for attempt in $(seq 1 12); do
  closure_run=$(gh run list --repo berntpopp/genefoundry-router --workflow fleet-closure.yml \
    --limit 30 --json databaseId,headSha,createdAt,event \
    --jq ".[] | select(.event == \"workflow_dispatch\" and .headSha == \"$final_router_sha\" and .createdAt >= \"$dispatched_at\") | .databaseId" \
    | head -n 1)
  test -n "$closure_run" && break
  sleep 5
done
test -n "$closure_run"
gh run watch --repo berntpopp/genefoundry-router "$closure_run"
```

Download and validate the JSON report; a green conclusion without exact 21/21 transport and 21/21
behavior rows is rejected.
Reassert `current main == release-manifest source == live health revision` after the run and again
immediately before evidence sealing; any mismatch restarts final router release/deployment.

- [ ] **Step 3: Run the GitHub App control audit when its two secrets exist**

Dispatch `control-audit.yml`, require 22/22, verify App token is short-lived/revoked/not logged, and
download/validate/hash the current live-ledger artifact into the external evidence directory. Do not
merge a post-release router ledger PR; Plan 1's checked ledger is already in the final router source,
while this fresh artifact supplies closure-time evidence. If the App prerequisite remains absent,
record `blocked` and continue all non-control closure evidence; overall completion remains false.

- [ ] **Step 4: Refresh every change-sensitive evidence class at the end of rollout**

Within one 24-hour closure window, re-scan every currently selected router/backend/website digest
and every distinct image produced by the program under the current scanner/database and exact
`fixable-high-critical-v1` policy; re-query each data upstream's stable identity/rights state; rerun
controller attest/health and strict transport/behavior; and re-query GitHub PR/check/alert/control
state. If any upstream, scanner policy/database, selected digest, release, deployment, or default
source changed since its earlier record, invalidate and regenerate the affected evidence before
closure rather than copying the older result.

### Task 9: Close superseded bot PRs and tracked issues only from accepted evidence

**Files:**
- External: immutable 175-row audited bot-PR baseline, post-audit delta rows, and router/backend
  issues

**Interfaces:**
- Consumes: merged commits, the final router release's cutover inventory, immutable application/data
  releases, and deployment/probe evidence.
- Produces: PR/issue comments and closures that link exact proof and retain unresolved blockers.

- [ ] **Step 1: Comment on and close only mapped superseded bot PRs**

Download `ci/dependabot-cutover.json` from the exact final router tag and verify it against the local
reviewed SHA-256. For each row marked superseded, re-read the original PR and require its current
head SHA equals the effective validated baseline/delta head (following any supersession chain), the
replacement merge exists, and final checks are green.
Comment with replacement PR/merge SHA plus the immutable final-tag permalink to the inventory, read
the comment back, then close. Changed/new/still-relevant rows remain open and make completion false.
Afterward, re-query router plus 21 backends and require no live bot PR absent from the effective
baseline-plus-delta inventory and no closure without a merged equivalent.

- [ ] **Step 2: Comment on each affected issue with exact evidence**

Cover router #124/#79 and the corresponding AutoPVS1, ClinGen, GeneReviews, gnomAD, LitVar,
PubTator, GeneReviews #27, and any data-workflow trackers. Include commit/PR/release/digest/deployment
and run IDs.

- [ ] **Step 3: Close only satisfied issues**

Keep GeneReviews #27 or control-audit tracking open when rights/App prerequisites are blocked. A
source-only fix does not close a deployment or data-currentness issue.

### Task 10: Refresh the security profile as the last source/control/deployment mutation

**Files:**
- Modify in `/home/bernt-popp/development/genefoundry-mcp-security-profile`: gitlink
  `genefoundry-router`, `README.md`, dated reassessment document, generated one-pager sources/assets

**Interfaces:**
- Consumes: final router/fleet releases, deployed tuples, strict probes, alert/control evidence,
  controller revision, and issue dispositions.
- Produces: the final source/control/deployment mutation: an assessment that distinguishes
  verified, not applicable, and blocked controls and points at exact immutable identities. The
  modeled detached evidence tag/release in Task 11 is the sole later repository-state exception.

- [ ] **Step 1: Resolve origin/instructions and create an explicit clean worktree**

Discover and read every applicable instruction file first. Then run:

```bash
profile_root=/home/bernt-popp/development/genefoundry-mcp-security-profile
profile_worktree="$profile_root/.worktrees/fleet-security-20260830"
git -C "$profile_root" remote get-url origin
git -C "$profile_root" status --short --branch
git -C "$profile_root" fetch --prune origin
git -C "$profile_root" worktree add "$profile_worktree" \
  -b codex/fleet-security-20260830 origin/main
git -C "$profile_worktree" submodule update --init
```

Reuse an existing path only if its ledger owner/base/branch/clean state match; never run
unqualified git commands from the router.

- [ ] **Step 2: Advance the gitlink to the exact final router release SHA**

```bash
router_manifest=/home/bernt-popp/development/fleet-remediation-evidence-20260830/router-application-release-manifest.json
final_router_release_sha=$(jq -er '.source.revision' "$router_manifest")
[[ "$final_router_release_sha" =~ ^[0-9a-f]{40}$ ]] || exit 65
git -C "$profile_worktree/genefoundry-router" fetch --tags origin
git -C "$profile_worktree/genefoundry-router" checkout "$final_router_release_sha"
git -C "$profile_worktree" submodule status
```

- [ ] **Step 3: Rerun, update, and verify the assessment**

Record exact app/data/deployment/run/alert/control/controller/issue evidence and the 24-hour cutoff.
Do not turn absent GeneReviews rights, control App, deployment access, or failed probes into passing
prose.

```bash
git -C "$profile_worktree" diff --submodule=log --check
git -C "$profile_worktree/genefoundry-router" rev-parse HEAD
git -C "$profile_worktree/genefoundry-router" describe --tags --always
make -C "$profile_worktree/genefoundry-router" ci-local
```

- [ ] **Step 4: Merge the profile PR after every ordinary mutation**

Commit, push, create the profile PR, require current checks/adversarial review, and merge normally.
Record the exact merge SHA. This repository has no deployment/release automation, so do not invent
one. After this merge, no repository source/lock, issue/PR, setting, selected application/data
release, or deployment is mutated. Only closure confirmation workflow/query runs and the one
predeclared detached closure-evidence tag/release may follow; neither may change application source,
controls, selected data, or deployment.

### Task 11: Author, validate, and externally seal the final closure manifest

**Files:**
- Create/modify outside source:
  `/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-remediation-closure.json`
- Create outside source:
  `/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-remediation-seal-receipt.json`
- External: final read-only repository/PR/check/alert queries and immutable evidence release

**Interfaces:**
- Consumes: all final evidence from Tasks 3–10.
- Produces: a valid fresh closure manifest and an honest complete/blocked result without changing
  router `main` or invalidating the final router release/deployment.

- [ ] **Step 1: Rerun change-sensitive evidence after the profile merge**

Repeat every Task 8 Step 4 scan, upstream/rights query, controller attest/health, strict transport/
behavior probe, monitoring/control audit, and GitHub state query after the profile merge. Bind each
new run by exact workflow/event/head-SHA/dispatch-time and download its exact artifact. Compare the
substantive tuples/results to those assessed in Task 10; newer run IDs/query timestamps with
identical results are superseding confirmation evidence. Any changed digest, policy/database,
upstream/rights identity, release, deployment, probe result, alert/control result, or default source
invalidates closure: return to the affected task, refresh the assessment/profile, and repeat this
step. Do not create a source PR merely to store these confirmations.

Recreate the 21-entry default-worktree map from freshly fetched exact `origin/main` SHAs and clean
detached worktrees, then rerun `validate_fleet_workflow_refs.py` with the final trusted-builder SHA.
Require its canonical content/hash to match `ci/fleet-workflow-refs.json`; a default advance or ref
change invalidates the final router inventory/release and returns to Task 6.

- [ ] **Step 2: Query final repository/PR/check/alert state read-only**

For router + 21 backends + the three supporting repos + deployment controller, record default
SHA/time, open PRs/issues, required checks/run IDs, action/reusable refs,
Dependabot/code/secret alert query time/results, and explicit `not_applicable` API responses. Include
the final security-profile merge SHA. Re-query immediately before sealing.

- [ ] **Step 3: Record exact image/data/deployment/probe evidence**

Include current selected image tuple for all 22 fleet applications plus the containerized website,
every image produced by the program, scanner/version/db timestamp/policy/verdict, SBOM/attestation
digests, data/licensing identities, live target/rollback tuples, strict probe rows,
monitoring/control runs, three supporting-role records, and the exact deployment-controller record.
Record the preselected unused evidence tag, exact final router target SHA, and a typed
`expected_detached_seal` exception. No other future mutation may be listed.

- [ ] **Step 4: Validate at one fixed UTC time**

```bash
router_source=/home/bernt-popp/development/genefoundry-router/.worktrees/fleet-closure-20260830
closure_file=/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-remediation-closure.json
closure_now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
test "$(gh api repos/berntpopp/genefoundry-router/commits/main --jq .sha)" = \
  "$(jq -er '.source.revision' \
    /home/bernt-popp/development/fleet-remediation-evidence-20260830/router-application-release-manifest.json)"
status=$(jq -er .status "$closure_file")
if test "$status" = complete; then
  uv run --directory "$router_source" python scripts/validate_fleet_closure.py \
    --now "$closure_now" "$closure_file"
else
  uv run --directory "$router_source" python scripts/validate_fleet_closure.py \
    --allow-blocked --now "$closure_now" "$closure_file"
  ! uv run --directory "$router_source" python scripts/validate_fleet_closure.py \
    --now "$closure_now" "$closure_file"
fi
make -C "$router_source" ci-local
```

- [ ] **Step 5: Seal once, then write a final detached receipt**

Before authoring the closure JSON, select the first unused literal
`fleet-remediation-evidence-20260830-rN` tag and prove both tag and release are absent. Put that tag
and exact final router SHA in `expected_detached_seal`, so tag choice cannot change after validation.
Write `SHA256SUMS` plus a detached release-intent record containing tag, target, closure hash, status,
and asset names; independently review all three files. Create a signed non-SemVer tag targeting the
exact final router release SHA, create the release as a draft, upload only the closure JSON,
checksum, and release-intent record, redownload and verify every byte, then publish it under the
repository's immutable-release policy. A `blocked` record/tag is labeled blocked and is never
described as completion.

Immediately afterward, re-read the release/tag target, immutable flag, release ID/time, asset IDs/
sizes/digests and every application/default-source/control/deployment identity. Require the
evidence tag/release to be the only state delta from the validated manifest. Write those facts to
`fleet-remediation-seal-receipt.json`, SHA-256 it, detach-sign it with the same operator identity used
for release tags, make the local receipt/signature read-only, and independently verify the
signature. The receipt is deliberately outside the sealed core because it can only exist after the
release ID exists. Any other delta invalidates the seal and requires a new assessed `rN`; never
overwrite/delete the immutable failed attempt. Do not add evidence to router source, merge a
post-release router PR, or re-tag the final application release.
