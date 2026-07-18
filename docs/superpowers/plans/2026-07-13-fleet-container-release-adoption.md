# Fleet Container Release Adoption Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt the router release control plane in all 21 backend repositories, configure GitHub/GHCR controls, publish reviewed code-only releases, and reconcile production to verified image/data/definition tuples.

**Architecture:** The router keeps a typed rollout matrix and generates only deterministic leaf configuration and thin SHA-pinned callers. Each repository retains its application-specific Docker/Compose fixtures, but central static checks prove one production build per trust transition, no competing publisher, digest-only production, complete control evidence, and atomic fleet candidate coverage.

**Tech Stack:** Python 3.12, JSON/Pydantic, GitHub Actions reusable workflows, GH CLI/API, GHCR, Docker Compose, pytest, Git worktrees, GitHub pull requests.

---

## Exact adoption matrix

| Repository | Service | Port | Data mode | Definitions | Smoke profile |
|---|---:|---:|---|---|---|
| genefoundry-router | genefoundry | 8000 | none | data-independent | compose-two-context |
| autopvs1-link | autopvs1-link | 8000 | none | data-independent | compose-two-context |
| clingen-link | clingen-link | 8000 | external-reference | data-bound | immutable-bundle |
| clinvar-link | clinvar-link | 8000 | external-reference | data-bound | immutable-bundle |
| gencc-link | gencc-link | 8000 | upstream-live | data-bound | prepared-live-fixture |
| genereviews-link | genereview-link | 8000 | restored-database | data-bound | postgres-bundle |
| gnomad-link | gnomad-link | 8000 | none | data-independent | compose-two-context |
| gtex-link | gtex-link | 8000 | none | data-independent | compose-two-context |
| hgnc-link | hgnc-link | 8000 | upstream-live | data-bound | prepared-live-fixture |
| hpo-link | hpo-link | 8000 | external-reference | data-bound | immutable-bundle |
| litvar-link | litvar-link | 8000 | none | data-independent | compose-two-context |
| mavedb-link | mavedb-link | 8000 | external-reference | data-bound | immutable-bundle |
| metadome-link | metadome-link | 8000 | none | data-independent | compose-two-context |
| mgi-link | mgi-link | 8000 | upstream-live | data-bound | prepared-live-fixture |
| mondo-link | mondo-link | 8000 | upstream-live | data-bound | prepared-live-fixture |
| orphanet-link | orphanet-link | 8000 | external-reference | data-bound | immutable-bundle |
| panelapp-link | panelapp-link | 8000 | none | data-independent | compose-two-context |
| pubtator-link | pubtator-link | 8000 | restored-database | data-bound | postgres-bundle |
| spliceailookup-link | spliceailookup-link | 8603 | none | data-independent | compose-two-context |
| stringdb-link | stringdb-link | 8000 | none | data-independent | compose-two-context |
| uniprot-link | uniprot-link | 8000 | none | data-independent | compose-two-context |
| vep-link | vep-link | 8000 | none | data-independent | compose-two-context |

## Normative execution gates

The control-plane plan is implemented, Opus-reviewed, merged to router `main`,
and protected before this plan renders any caller. The renderer pins the exact
`origin/main` merge commit and proves it is a 40-hex ancestor of protected
`origin/main`; feature-worktree or deletable-branch SHAs are rejected.

Execution order is mandatory:

1. Tasks 1–8 prepare the ledger, renderer, leaf branches, and account controls.
2. Task 9 opens/reviews all draft PRs; Task 10 merges the router self-adoption
   first and then leaf PRs, recording exact merge SHAs.
3. Task 11 publishes only new post-adoption exact tags.
4. Tasks 12–14 reconcile/deploy, enable continuous operations, and close evidence.

Pre-adoption tags are never backfilled. Every enabled backend must reach a
verified `release` decision before candidate reconciliation; a temporary `defer`
keeps the initiative incomplete rather than admitting an unverified candidate.

### Task 1: Release/defer ledger for current version drift

**Files (router):**
- Create: `ci/fleet-release-decisions.json`
- Create: `genefoundry_router/release/rollout.py`
- Create: `tests/release/test_release_decisions.py`

- [ ] **Step 1: Write failing decision-ledger tests**

Require router plus all `servers.yaml` repositories, current project version,
latest stable source tag, exact head SHA, decision enum `release`/`defer`, reason,
reviewer, and date. Reject a `release` decision when version metadata, lockfile,
changelog, or protected-main ancestry is incoherent. Reject a `defer` row without
an explicit reason.

- [ ] **Step 2: Generate current evidence, then review decisions**

For each absolute repository path in the adoption matrix, use `git -C`,
`uv version --short`, and `gh release list`/`git tag` to populate evidence.
Preserve the known fourteen version/tag mismatches and mark pre-adoption tags as
ineligible; plan a new post-adoption patch release rather than reusing them.
Select `release` only after the repository PR and control-plane prerequisites
pass; otherwise select `defer` with the concrete unmet prerequisite.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_release_decisions.py -q`

Expected: exactly 22 coherent reviewed rows pass.

```bash
git add ci/fleet-release-decisions.json genefoundry_router/release/rollout.py tests/release/test_release_decisions.py
git commit -m "chore(release): reconcile fleet release decisions"
```

### Task 2: Deterministic leaf-adoption generator

**Files (router):**
- Create: `ci/fleet-container-rollout.json`
- Create: `scripts/render_container_adoption.py`
- Create: `tests/release/test_adoption_renderer.py`
- Modify: `Makefile`

- [ ] **Step 1: Write golden-output tests**

For all 22 rows, render a strict `container-release.json`, `container-ci.yml`, and
`container-release.yml`. Derive the central reusable-workflow revision from
`git rev-parse origin/main^{commit}`, require exactly 40 lowercase hex characters,
prove it is an ancestor of protected `origin/main`, and refuse a dirty,
feature-branch, or unmerged revision. The caller has tag-push release triggers
only and no free-text backfill input. External data identities come
from validated immutable data manifests; live modes come from the observed
provenance configuration.

```python
def test_spliceai_override_is_exact(renderer: AdoptionRenderer) -> None:
    rendered = renderer.render_config("berntpopp/spliceailookup-link")
    assert rendered["service"]["container_port"] == 8603
    assert rendered["service"]["name"] == "spliceailookup-link"


def test_callers_pin_one_full_standard_sha(renderer: AdoptionRenderer) -> None:
    text = renderer.render_release_caller("berntpopp/gnomad-link")
    pins = re.findall(r"berntpopp/genefoundry-router/.github/workflows/_container-release.yml@([0-9a-f]+)", text)
    assert pins == [renderer.standard_revision]
    assert len(pins[0]) == 40
```

- [ ] **Step 2: Implement check/write modes**

`--check` compares canonical generated content without writes. `--write --repo`
writes only the three generated files for one explicitly named repository and
refuses paths outside the reviewed sibling repository root. Hand-maintained
Docker fixtures and Compose files are validation inputs, never generated.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_adoption_renderer.py -q && uv run python scripts/render_container_adoption.py --check`

Expected: golden output is stable and covers exactly the adoption matrix.

```bash
git add ci/fleet-container-rollout.json scripts/render_container_adoption.py tests/release/test_adoption_renderer.py Makefile
git commit -m "feat(release): generate typed fleet workflow adoption"
```

### Task 3: Four representative pilots

**Repositories:**
- `genefoundry-router` (control-plane owner)
- `gnomad-link` (stateless/live API)
- `clingen-link` (immutable external data)
- `pubtator-link` (dependent PostgreSQL service)

**Files in each repository:**
- Create: `container-release.json`
- Create: `.github/workflows/container-ci.yml`
- Create: `.github/workflows/container-release.yml`
- Create: `docker/ci-prepare-smoke.sh` only for ClinGen/PubTator
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.dockerignore`
- Modify: `Makefile`
- Delete: duplicate `.github/workflows/docker.yml` and/or `.github/workflows/container-security.yml` after equivalent central coverage exists

- [ ] **Step 1: Create isolated branches/worktrees and render leaf files**

Use one worktree/branch `feat/container-release-standard` per repository. Run the
renderer from the pinned router control-plane commit, then review the generated
diff before application-specific edits.

- [ ] **Step 2: Add two-context/stateless and data-bound smoke fixtures**

GnomAD points its upstream base URL first at fixture A and then fixture B with
different response bodies; MCP definitions must hash equally. ClinGen prepares a
tiny schema-compatible reviewed bundle and records its exact digest. PubTator
initializes a minimal PostgreSQL schema/control-row fixture and records its exact
database identity. Fixture preparation has read-only GitHub permissions and no
secrets/OIDC/package write.

- [ ] **Step 3: Make production Compose digest-only**

In every pilot application service set `build: !reset null`, set image from a
required digest-addressed environment value, use `pull_policy: missing`, and
retain no published application host port behind the proxy. Keep local builds in
development Compose.

- [ ] **Step 4: Verify one build and all gates**

Run each repository `make ci-local`, production/NPM Compose renders, central
configuration validation, single OCI-layout image build, health/MCP/per-layer-
content/config/hardening/Trivy checks, build-declared digest verification, and
definition contract capture. Run router fleet static checks across the four
worktrees.

Expected: four passing pilot reports, exactly one build per CI invocation, and no
duplicate application-image workflow.

- [ ] **Step 5: Commit each pilot independently**

```bash
git add container-release.json .github/workflows docker .dockerignore Makefile
git commit -m "feat(release): adopt verified container publication"
```

### Task 4: Stateless backend adoption

**Repositories:** `autopvs1-link`, `gtex-link`, `litvar-link`, `metadome-link`,
`panelapp-link`, `spliceailookup-link`, `stringdb-link`, `uniprot-link`, `vep-link`

**Files in each repository:**
- Create: `container-release.json`
- Create: `.github/workflows/container-ci.yml`
- Create: `.github/workflows/container-release.yml`
- Create: `docker/fixtures/upstream-a/response.json`
- Create: `docker/fixtures/upstream-b/response.json`
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.dockerignore`
- Modify: `Makefile`
- Delete: `.github/workflows/docker.yml` where present
- Delete: `.github/workflows/container-security.yml`

- [ ] **Step 1: Render and validate exact per-repository configuration**

Use the adoption matrix service/port values and `data-independent`. The two
fixture responses must differ in data values while exposing the same upstream
protocol shape; capture initializes/lists tools without invoking an upstream
operation and proves equal canonical definition hashes.

- [ ] **Step 2: Update Docker/Compose and remove duplicate builds**

Add standard OCI/code-only labels, ensure no reference/runtime data is copied,
clear production builds, require digest images, and retain current hardening.
Move non-container conformance checks into existing CI only when they do not
rebuild the production image.

- [ ] **Step 3: Verify and commit every repository separately**

For each repository run `make ci-local`, Compose renders, central config/content
checks, and both definition contexts. For SpliceAI assert port 8603 end-to-end;
all others assert port 8000.

Expected: all nine repositories pass and router static checks count one build;
Metadome records a separate deletable runtime cache but no authoritative data.

```bash
git add container-release.json .github/workflows docker .dockerignore Makefile
git commit -m "feat(release): adopt verified container publication"
```

### Task 5: Remaining data-bound adoption

**Repositories:** `clinvar-link`, `hpo-link`, `mavedb-link`, `orphanet-link`,
`genereviews-link`

**Files in each repository:**
- Create: `container-release.json`
- Create: `.github/workflows/container-ci.yml`
- Create: `.github/workflows/container-release.yml`
- Create: `docker/ci-prepare-smoke.sh`
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.dockerignore`
- Modify: `Makefile`
- Delete: duplicate application-image build/security workflows after coverage proof

- [ ] **Step 1: Bind configuration to actual immutable data manifests**

Resolve the newest reviewed compatible data release with `gh release view`,
validate its shared manifest, then write the exact tag/compressed/expanded digest
into generated configuration. The preparation script materializes only the tiny
checked-in CI fixture; release capture uses the exact production release/digest.

- [ ] **Step 2: Enforce code-only Docker contexts and digest-only production**

Run wheel file-list and every-layer/config OCI gates before deleting old workflow
coverage. Separate reference mounts from MaveDB cache and GeneReviews PostgreSQL
state. Require exact production image and data identities.

- [ ] **Step 3: Verify and commit every repository separately**

Run `make ci-local`, data-specific tests from the data-artifact plan, every-layer
OCI content/config inspection, production/NPM Compose render, local smoke, and data-bound
definition capture.

Expected: five code-only images and five exact data-bound definition records.

```bash
git add container-release.json .github/workflows docker .dockerignore Makefile
git commit -m "feat(release): adopt data-bound container publication"
```

### Task 6: Live-upstream/runtime-state adoption

**Repositories:** `gencc-link`, `hgnc-link`, `mgi-link`, `mondo-link`

**Files in each repository:**
- Create: `container-release.json`
- Create: `.github/workflows/container-ci.yml`
- Create: `.github/workflows/container-release.yml`
- Create: `docker/ci-prepare-smoke.sh`
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.dockerignore`
- Modify: `Makefile`
- Delete: `.github/workflows/container-security.yml`

- [ ] **Step 1: Configure transitional truthfully**

Set `mode: upstream-live`, the repository-specific HTTPS egress allowlist,
`reproducible_rollback: false`, runtime cache/state paths, `data-bound`, and a
prepared local upstream fixture whose observed provenance is captured.

- [ ] **Step 2: Adopt image/Compose/workflow standard**

Keep authoritative/live materialization and caches entirely in named volumes,
never the image. Clear production builds, require digest images, and remove
duplicate image builds after equivalent central gates pass.

- [ ] **Step 3: Verify and commit every repository separately**

Run `make ci-local`, live-provenance tests, every-layer OCI content/config checks, Compose renders,
prepared smoke, and data-bound definitions. Assert release manifests do not claim
immutable data rollback.

```bash
git add container-release.json .github/workflows docker .dockerignore Makefile
git commit -m "feat(release): adopt live-data container publication"
```

### Task 7: Fleet-wide static and compute controls

**Files (router):**
- Create: `genefoundry_router/release/fleet_workflows.py`
- Create: `genefoundry_router/release/fleet_compose.py`
- Create: `genefoundry_router/release/fleet_report.py`
- Create: `tests/release/test_fleet_workflows.py`
- Create: `tests/release/test_fleet_compose.py`
- Modify: `ci/fleet-container-rollout.json`

- [ ] **Step 1: Write whole-fleet assertions**

Clone/read the exact PR heads and assert 22 typed configs, callers pinned to one
protected router-main full SHA, no unknown action pins, workflow-level empty
permissions, the exact per-job permission graph, protected release environments,
tag-push-only publication, denial of `pull_request_target`/`workflow_run`/
`issue_comment`, non-cancelling release concurrency, one production-image build
per invocation, no release use of PR-writable cache, no competing publisher, no
`latest`, no production `build`, no exposed backend host port, complete
hardening, bounded artifact retention, CI-only versioned cache scopes, and
router/disabled-backend handling. Prove OCI-layout digest preservation and make
duplicate-security-workflow deletion conditional on a separate leaf-code-free
SARIF report job or an explicit reviewed alerting decision.

- [ ] **Step 2: Implement bounded reconciliation**

Process at most four repositories concurrently and prune images/build cache and
temporary artifacts after each row. Emit canonical JSON plus a Markdown summary;
distinguish source/config/policy failures from external registry/scanner outages.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_fleet_workflows.py tests/release/test_fleet_compose.py -q`

Expected: all 22 repositories pass with bounded concurrency and disk cleanup.

```bash
git add genefoundry_router/release/fleet_workflows.py genefoundry_router/release/fleet_compose.py genefoundry_router/release/fleet_report.py tests/release/test_fleet_workflows.py tests/release/test_fleet_compose.py ci/fleet-container-rollout.json
git commit -m "test(release): enforce fleet publication invariants"
```

### Task 8: GitHub rules, immutable releases, and GHCR bootstrap

**Files (router):**
- Create: `scripts/audit_container_controls.py`
- Modify: `ci/container-controls.json`
- Create: `tests/release/test_control_audit.py`

- [ ] **Step 1: Add API-response fixture tests**

Preflight which ruleset, environment, immutable-release, and package controls are
API-readable/enforceable for the personal namespace. Require active exact-version
tag update/delete/non-fast-forward protection with explicit bypass actors and
signed creation policy, read-only default token, protected release environment,
immutable releases, package linkage/public visibility, anonymous token+manifest
HEAD, no standing package token, and retention of released/deployed digests.
Unavailable hard controls require named manual evidence and remain a release
blocker; they are never auto-passed.

- [ ] **Step 2: Configure each repository/package**

Apply repository rules/environments using the authenticated maintainer session.
For every new GHCR package, prefer repository `GITHUB_TOKEN` to publish a
source-labelled disposable bootstrap image, link it, set public visibility,
verify an anonymous token+manifest HEAD from a temporary empty HOME, delete the
disposable tag, and update the signed control ledger. If an exceptional
short-lived package token is needed, revoke it in the same task and record the
revocation. Preflight version/source tags against a different existing digest.
Do not create an application release tag in this task.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_control_audit.py -q && uv run python scripts/audit_container_controls.py --check ci/container-controls.json`

Expected: 22 passing rows or an explicit external-state blocker naming the exact
repository/control; no row is silently omitted.

```bash
git add scripts/audit_container_controls.py ci/container-controls.json tests/release/test_control_audit.py
git commit -m "chore(release): attest fleet GitHub and GHCR controls"
```

### Task 9: Opus-reviewed draft PR publication

**Files (router):**
- Create: `ci/fleet-container-prs.json`
- Create: `docs/superpowers/reviews/2026-07-13-fleet-container-release-pr-review.md`

- [ ] **Step 1: Run pre-PR verification in every repository**

Run `make ci-local` plus standardized config/build/content/Compose/smoke checks in
router and all 21 backends. Run the router whole-fleet static suite across exact
branch heads. Capture commands, revisions, exit status, and evidence digests.

- [ ] **Step 2: Run Claude Code Opus 4.8 xhigh on every PR**

First run `claude --version` and a no-tools probe that proves the installed CLI
accepts exact model `claude-opus-4-8` and `--effort xhigh`; fail rather than fall
back. For each repository, provide the approved design, applicable plan, full PR
diff, workflow permission graph, test output, image-content report, and release
evidence. Commit accepted corrections, rerun affected gates, and record
evidence-backed rejected findings. Repeat after material corrections; no
blocking/high item remains open.

- [ ] **Step 3: Publish draft PRs and record them atomically**

Push each intentional branch and open a draft PR. Record repository, branch, head
SHA, PR URL/number, CI state, Opus review record/digest, and tested merge order.
The ledger must contain exactly router plus 21 backend rows.

### Task 10: Merge-gated standard activation

**Files (router):**
- Modify: `ci/fleet-container-prs.json`
- Create: `tests/release/test_merge_gate.py`

- [ ] **Step 1: Merge the router control-plane/self-adoption PR first**

Require approved review, passing required checks, clean diff, and exact control
evidence. Merge without rewriting already-published standard history, fetch
`origin/main`, and record the 40-hex merge commit.

- [ ] **Step 2: Re-render leaf callers against the protected merge commit**

Run the renderer in write mode for each leaf, prove every pin equals the recorded
router `origin/main` commit, rerun leaf CI/Opus review for changed pins, then merge
the 21 leaf PRs only after their required checks pass.

- [ ] **Step 3: Prove merge completion**

Run: `uv run pytest tests/release/test_merge_gate.py -q`

Expected: router-first merge order, 22 protected-main merge SHAs, one shared
standard SHA pin, and no open/deferred adoption PR.

```bash
git add ci/fleet-container-prs.json tests/release/test_merge_gate.py
git commit -m "chore(release): activate merged fleet container standard"
```

### Task 11: Reviewed post-adoption releases

**Files (router):**
- Modify: `ci/fleet-release-decisions.json`
- Create: `ci/fleet-application-releases.json`
- Create: `tests/release/test_application_releases.py`

- [ ] **Step 1: Prove release readiness**

For each `release` row, require merged adoption/data PR, clean `main`, coherent
version/lock/changelog, protected tag rules, immutable release setting, public
package, protected release environment, no standing package PAT, and passing CI.
Pre-adoption tags and branch dispatch are forbidden. Create one signed/protected
new exact `vX.Y.Z` tag from post-adoption `main` only after readiness passes.

- [ ] **Step 2: Monitor the complete state machine**

Require `VALIDATED`, `BUILT`, `GATED`, `PUSHED`, `ATTESTED`, `CAPTURED`,
`RELEASED`, and `ALIASED`. Download and verify the immutable release manifest,
attestation bundle/trusted root, SBOM, Trivy verdict, asset hashes, anonymous image
pull, and MCP definitions. Prove the build-declared OCI digest, source-SHA alias,
version alias, attested subject, and release manifest all equal. A rerun with an
existing source digest re-gates that digest without rebuilding. Never retry a
mismatch by deleting or overwriting.

- [ ] **Step 3: Record releases and test atomic coverage**

Run: `uv run pytest tests/release/test_application_releases.py -q`

Expected: every enabled repository has a verified manifest tuple. Any remaining
`defer` causes this task to fail and keeps candidate reconciliation blocked.

```bash
git add ci/fleet-release-decisions.json ci/fleet-application-releases.json tests/release/test_application_releases.py
git commit -m "chore(release): record verified fleet application releases"
```

### Task 12: Candidate reconciliation and digest deployment

**Files (router):**
- Modify: `ci/release-candidate-inventory.json`
- Modify: `ci/release-candidate-fleet.json`
- Modify: `genefoundry_router/data/fleet-baseline.json`
- Modify: `tests/integration/test_release_candidate_baseline.py`

- [ ] **Step 1: Generate candidate from verified release manifests**

The candidate binds image name/digest, source tag/revision, workflow digest,
SBOM/attestation, data mode/release/digest or live provenance, definition
contract/context/digest, endpoint, and version for every enabled backend. It must
cover exactly the registry and cannot retain a stale row.

- [ ] **Step 2: Verify and deploy the full tuple**

Before Compose, pull the digest, verify online and offline attestations/source,
verify/materialize data, record the tuple, then run Compose with no build and
`pull_policy: missing`. Capture definitions from running digests, compare to the
candidate, and roll back image plus data together on failure. For
`upstream-live`, do not claim joint data rollback: roll back the image, perform a
new observed upstream materialization, and record that degraded recovery as a
new provenance event. Deploy backends first and router last; on failure roll the
router back first.

- [ ] **Step 3: Verify and commit**

Run: `make snapshot-baseline RELEASE_CANDIDATE_INVENTORY=ci/release-candidate-inventory.json && make ci-local && make test-e2e`

Expected: exact full-fleet capture, passing drift baseline, local CI, and fake-fleet E2E.

```bash
git add ci/release-candidate-inventory.json ci/release-candidate-fleet.json genefoundry_router/data/fleet-baseline.json tests/integration/test_release_candidate_baseline.py
git commit -m "chore(release): reconcile verified digest deployment candidate"
```

### Task 13: Deployed-digest and configuration-drift operations

**Files (router):**
- Create: `.github/workflows/deployed-image-scan.yml`
- Create: `.github/workflows/fleet-container-drift.yml`
- Create: `docs/DEPLOYED-IMAGE-RESPONSE-RUNBOOK.md`
- Create: `tests/release/test_deployed_scan_workflow.py`
- Create: `tests/release/test_fleet_container_drift_workflow.py`

- [ ] **Step 1: Write scheduled-workflow tests**

Assert weekly/manual deployed-image triggers plus a daily fleet-configuration
drift trigger, read-only permissions, candidate-inventory digest
inputs only, no source build, bounded matrix, JSON Trivy plus policy evaluator,
artifact evidence, issue deduplication, and no mutation of immutable releases.
The drift job reads protected `main` for all 22 repositories, validates generated
callers/configuration/Compose against the pinned standard, and opens one deduped
issue per drift. Each leaf CI also runs renderer `--check`.

- [ ] **Step 2: Implement scan and response runbook**

Scan only deployed digests. A new fixable HIGH/CRITICAL finding opens/updates one
repository issue with digest/evidence; infrastructure failures produce a distinct
workflow failure. Document rebuild/new-version response, candidate replacement,
full tuple rollback, immutable-artifact retention, and a revoke/yank incident
that cannot pretend already-pulled public bytes disappeared.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_deployed_scan_workflow.py tests/release/test_fleet_container_drift_workflow.py tests/unit/test_workflows_parse.py -q && make lint-actions`

```bash
git add .github/workflows/deployed-image-scan.yml .github/workflows/fleet-container-drift.yml docs/DEPLOYED-IMAGE-RESPONSE-RUNBOOK.md tests/release/test_deployed_scan_workflow.py tests/release/test_fleet_container_drift_workflow.py
git commit -m "ci(release): monitor deployed image digests"
```

### Task 14: Final fleet evidence gate

**Files (router):**
- Modify: `ci/fleet-container-prs.json`
- Modify: `docs/superpowers/reviews/2026-07-13-fleet-container-release-pr-review.md`

- [ ] **Step 1: Run the final gate and record evidence**

Run: `make ci-local && make test-cov && uv run pytest tests/release -q && uv run python scripts/audit_container_controls.py --check ci/container-controls.json`

Expected: all router gates pass, coverage is at least 70%, fleet release tests pass,
and all 22 controls/PR/review rows are complete.

```bash
git add ci/fleet-container-prs.json docs/superpowers/reviews/2026-07-13-fleet-container-release-pr-review.md
git commit -m "docs(release): record adversarially reviewed fleet rollout"
```
