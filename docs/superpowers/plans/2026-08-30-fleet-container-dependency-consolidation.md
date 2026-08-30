# Fleet Container and Dependency Consolidation Implementation Plan

> Historical record — this plan records the approved execution sequence as of 2026-08-30.
> Current behavior is defined by merged code, immutable release evidence, GitHub state, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all 165 audited backend Dependabot PRs with coherent, verified per-repository
updates, consume the router's ten mappings for a complete 175-PR program cutover, remove the shared
fixable container CVEs, and make every long-lived backend reboot-safe.

**Architecture:** Each backend receives one replacement branch from fresh `origin/main`, with
separate commits for base image, paired Actions, dependency union, and repo-specific restart/data
fixes. Same-shape mechanical work is batched into three seven-repository groups, but each repository
keeps its own worktree, commits, PR, native gates, and closure evidence.

**Tech Stack:** Python 3.12/3.14, uv, FastMCP 3.x, pytest, Ruff, mypy, Docker/BuildKit, Trivy,
SBOM/attestation workflows, GitHub Actions, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-08-30-fleet-security-pr-data-remediation-design.md`

## Global Constraints

- Primary scope is exactly the 21 non-archived backends in router `servers.yaml`.
- Use clean dedicated worktrees from freshly fetched exact `origin/main`; never modify primary
  user checkouts or unrelated worktrees.
- The per-repository cutover inventory records each original PR URL/number/head SHA,
  dependency/action, old/new declared and resolved version, affected files, replacement commit, and
  disposition. The audit-time baseline is immutable: later new PRs or changed heads are recorded
  in a separate typed delta collection rather than rewriting or inflating the baseline claim.
- Python 3.14 Docker stages use verified index
  `sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5`; GeneReviews'
  Python 3.12 stages use
  `sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217`, unless a
  fresher replacement is independently reverified and recorded before editing.
- CodeQL init/analyze and every occurrence of one action family move atomically to one full SHA.
- uv alone writes `uv.lock`; declared compatibility bounds change only with boundary evidence.
- Long-lived application/database services require `restart: unless-stopped`; one-shot init/build
  services require `restart: "no"`.
- No vulnerability waiver, mutable action/image authority, caller-token passthrough, host-published
  backend port, SSE transport, or clinical-support claim is introduced.
- Every changed repository passes its native `make ci-local`, exact container policy, SBOM,
  smoke/content checks, CodeQL, dependency review, and conformance before merge.
- Close a bot PR only after the replacement merge SHA and fresh checks are recorded in the PR
  comment and closure ledger.
- Worktree ownership follows the ledgered state machine
  `plan2-mechanical -> plan3-source-repair -> plan2-review-and-merge ->
  plan3-publication-and-pin -> plan4-version-and-release`. Only the named owner writes the
  repository worktree. Every transfer records the exact clean head SHA; publication-dependent pins
  use a separate PR after the source-repair PR merges.
- Every backend reusable container CI/release reference advances to the exact released router SHA
  from Plan 1 unless a reviewed exception proves the repository does not consume that workflow;
  all occurrences and their contract fixtures move atomically.

## Repository groups and cutover baseline

| Group | Repository | Audited open bot PRs | Python line |
| --- | --- | ---: | --- |
| A | `autopvs1-link` | 8 | 3.14 |
| A | `clingen-link` | 4 | 3.14 |
| A | `clinvar-link` | 10 | 3.14 |
| A | `gencc-link` | 4 | 3.14 |
| A | `genereviews-link` | 5 | 3.12 |
| A | `gnomad-link` | 4 | 3.14 |
| A | `gtex-link` | 4 | 3.14 |
| B | `hgnc-link` | 10 | 3.14 |
| B | `hpo-link` | 10 | 3.14 |
| B | `litvar-link` | 8 | 3.14 |
| B | `mavedb-link` | 10 | 3.14 |
| B | `metadome-link` | 10 | 3.14 |
| B | `mgi-link` | 8 | 3.14 |
| B | `mondo-link` | 8 | 3.14 |
| C | `orphanet-link` | 8 | 3.14 |
| C | `panelapp-link` | 10 | 3.14 |
| C | `pubtator-link` | 8 | 3.14 |
| C | `spliceailookup-link` | 8 | 3.14 |
| C | `stringdb-link` | 8 | 3.14 |
| C | `uniprot-link` | 10 | 3.14 |
| C | `vep-link` | 10 | 3.14 |

Audited shared authorities to refresh from their primary sources immediately before editing:

| Family | Audited target |
| --- | --- |
| Python 3.14 slim index | `sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5` |
| Python 3.12 slim index | `sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217` |
| `actions/attest-build-provenance` | `4d101475d8b20a2381f78447822ac1eab6504dd8` |
| paired `github/codeql-action` | `ff2f1c621b7f889edc0d3c761ac2e6a3f8cdb0dd` |
| every `astral-sh/setup-uv` occurrence | `20cfd1bf945f4377ade1205e4dbc17946fc9a30d`, preserving uv `0.8.7` |
| `docker/setup-buildx-action` | `37fe631027851001ddb9b187196cc803df7f5f0e` |
| declaration/lock union | FastMCP `>=3.4.7`, pydantic-settings `>=2.15.0`, pre-commit `>=4.6.2`, mypy `2.3.1`, Ruff `0.16.3` |

---

### Task 1: Freeze the current 21-repository cutover inventory

**Files:**
- Create: no tracked product files
- Read: each repository's `AGENTS.md`, linked instructions, Git state, open PRs, workflows,
  `pyproject.toml`, `uv.lock`, `docker/Dockerfile`, and Compose files

**Interfaces:**
- Consumes: router release/workflow tuple from the control-plane plan and live GitHub state.
- Produces: central ownership ledger and a machine-readable JSON inventory per repository.

- [ ] **Step 1: Authenticate read-only and refresh all remote refs**

```bash
install -d -m 700 /home/bernt-popp/development/fleet-remediation-evidence-20260830
export FLEET_CUTOVER_INVENTORY=/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-cutover-inventory.json
repos='autopvs1-link clingen-link clinvar-link gencc-link genereviews-link gnomad-link gtex-link hgnc-link hpo-link litvar-link mavedb-link metadome-link mgi-link mondo-link orphanet-link panelapp-link pubtator-link spliceailookup-link stringdb-link uniprot-link vep-link'
gh auth status
for repo in $repos; do
  root="/home/bernt-popp/development/$repo"
  git -C "$root" remote get-url origin
  git -C "$root" fetch --prune origin
  git -C "$root" rev-parse origin/main
  git -C "$root" status --short --branch
done
```

Expected: every origin has exact owner `berntpopp` and the ledger repository name, every default base exists, and dirty primary
checkouts are recorded but never used.

- [ ] **Step 2: Export exact PR metadata and patches**

For each repository, query open Dependabot PRs and store number, URL, head SHA, files, dependency
metadata, checks, and patch in the ignored plan workspace:

```bash
gh pr list --repo berntpopp/clinvar-link --state open --author 'app/dependabot' --limit 100 \
  --json number,url,headRefOid,files,statusCheckRollup
gh pr diff 55 --repo berntpopp/clinvar-link
```

Write each query result to a repository-keyed temporary JSON file in the evidence directory. After
all 21 queries, atomically author `$FLEET_CUTOVER_INVENTORY` with schema/version, captured UTC time,
the immutable 165-backend/ten-router audit baseline and an initially empty `post_audit_deltas`
collection per repository. Every baseline/delta row separates an `identity` object (repository,
PR number/URL/head SHA, dependency/action identity, old/new declared and resolved versions, and
affected files) from a `resolution` object (replacement commit/PR/merge/check fields, disposition,
and evidence URL). Unknown fields or duplicate repository/PR/head identity tuples fail validation.
Baseline identity objects and their aggregate SHA-256 are immutable; initially resolution fields
are typed null, never omitted, and may be filled later without rewriting audit identity. A delta
that replaces a changed head must carry an exact `supersedes`
tuple pointing at its baseline or prior-delta repository/number/head; a genuinely new PR uses typed
null. Validate exact 22-repository and 175-row **baseline** cardinality with `jq -e`, validate the
separately counted delta collection, hash the file, and record the hash in the central ledger. A
mismatch from refreshed live state is not ignored; add a delta before edits while keeping the
audited baseline unchanged.

```bash
jq -e '
  .post_audit_delta_count as $delta_count |
  .schema_version == 1 and
  (.repositories | type == "object" and length == 22) and
  ([.repositories[].audit_baseline[]] | length == 175) and
  ([.repositories[].audit_baseline[] | [.identity.repository, .identity.number,
      .identity.head_sha] | @tsv] |
    unique | length == 175) and
  ([.repositories[].post_audit_deltas[]] | length == $delta_count) and
  ([.repositories[].post_audit_deltas[] | [.identity.repository, .identity.number,
      .identity.head_sha] | @tsv] | unique | length == $delta_count)
' "$FLEET_CUTOVER_INVENTORY" >/dev/null
sha256sum "$FLEET_CUTOVER_INVENTORY"
```

- [ ] **Step 3: Create dedicated replacement worktrees**

```bash
git -C /home/bernt-popp/development/clinvar-link check-ignore -q .worktrees
git -C /home/bernt-popp/development/clinvar-link worktree add \
  .worktrees/fleet-security-20260830 -b codex/fleet-security-20260830 origin/main
```

Repeat with the literal repository root for all 21. Reuse only an existing clean worktree whose
branch/base/owner match the ledger; otherwise choose a unique path/branch without deleting anything.

### Task 2: Refresh both Python stages in all 21 backends

**Files:**
- Modify in every repository: `docker/Dockerfile`
- Modify base-digest contract test in every repository: `tests/unit/test_dockerfile_bootstrap.py`,
  except ClinVar/GenCC/GTEx/MaveDB/MetaDome/Orphanet/PanelApp use
  `tests/test_dockerfile_bootstrap.py`, gnomAD uses `tests/unit/docker/test_dockerfile.py`, and
  PubTator uses `tests/unit/docker/test_dockerfile_bootstrap.py` plus
  `tests/unit/docker/test_dockerfile_hardening.py`

**Interfaces:**
- Consumes: repository Python line and reverified multi-platform Python slim digest.
- Produces: one base-image-only commit per repository; both builder/prepared stages share the exact
  digest for that Python line.

- [ ] **Step 1: Reverify digest platform and fixed Debian packages**

```bash
docker buildx imagetools inspect python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5
docker buildx imagetools inspect python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217
docker run --rm python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5 \
  dpkg-query -W util-linux
docker run --rm python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 \
  dpkg-query -W util-linux
```

Expected: required build platforms exist and util-linux is at least `2.41.5-0+deb13u1`. Record the
index and selected platform manifest digests.

- [ ] **Step 2: Move each literal contract to the new authority and observe RED**

In each repository's listed test file, first replace the expected old digest with the reverified
target and run that exact file. Expected: RED because both Dockerfile stages still use the old
authority. PubTator runs both listed files.

- [ ] **Step 3: Replace exactly two authoritative base references per Dockerfile**

For each repository, verify the number before editing:

```bash
rg -n '^FROM python:3\.(12|14)-slim@sha256:' docker/Dockerfile
```

Replace builder and prepared references with the line-appropriate digest; preserve every other
Dockerfile instruction. If a repository does not have exactly the expected stages, stop that repo's
batch and inspect its installed production layout instead of blind replacement.

- [ ] **Step 4: Verify GREEN, then build and smoke the exact production target per repository**

```bash
make docker-build
```

Run the repository's `container-validate`, production-image content, Trivy
`fixable-high-critical-v1`, SBOM, and runtime/MCP smoke commands from its Makefile/shared workflow.
Expected: zero fixable HIGH/CRITICAL findings and no hardening regression.

- [ ] **Step 5: Commit independently in each repository**

```bash
git add docker/Dockerfile tests
git commit -m "fix: refresh Python runtime image"
```

### Task 3: Consolidate Group A action and dependency unions

**Files:**
- Modify in Group A: current PR-listed `.github/workflows/*.yml`, `pyproject.toml`, `uv.lock`,
  `.pre-commit-config.yaml`, and exact workflow-pin tests
- Do not modify data behavior here; ClinVar/GeneReviews/gnomAD data changes belong to the data plan

**Interfaces:**
- Consumes: immutable inventories for AutoPVS1, ClinGen, ClinVar, GenCC, GeneReviews, gnomAD, GTEx.
- Produces: one coherent resolved dependency/action state and separately reviewable commits in each.

- [ ] **Step 1: Derive the exact union file-by-file**

For each Group A repo, compare all stored bot patches with current `origin/main`. Record action name,
old/new SHA/comment and package old/new declared/resolved version. Do not cherry-pick split bot
commits; reproduce their reviewed union on the replacement branch.

- [ ] **Step 2: Apply paired Actions atomically**

Update every occurrence of CodeQL `init` and `analyze` to one identical verified SHA; update every
`setup-uv` occurrence together with explicit uv version preserved. When reusable router workflows
move, use the exact released router ref from the control-plane ledger and update associated
constants/fixtures in the same commit. The reusable ref move is mandatory for every consumer,
including all restart-policy repositories; ClinGen's hard-coded router SHA fixtures move with it.

```bash
rg -n 'github/codeql-action/(init|analyze)@|astral-sh/setup-uv@|berntpopp/genefoundry-router/' .github tests
```

- [ ] **Step 3: Resolve Python unions through uv**

Use targeted `uv lock --upgrade-package` with each literal inventoried distribution name; use a full
`uv lock --upgrade` only when the bot inventory itself covers the resulting transitive union.

```bash
uv lock --upgrade-package fastmcp
uv sync --group dev --frozen
uv run python -c 'import fastmcp, pydantic, uvicorn; print(fastmcp.__file__, pydantic.__version__, uvicorn.__version__)'
```

Verify FastMCP imports against the installed package, server construction/list-tools, Pydantic
schemas, Uvicorn CLI, and native/ABI imports for NumPy/Torch where present.

- [ ] **Step 4: Run all seven native gates and commit per repository**

```bash
make ci-local
git add .github pyproject.toml uv.lock tests
test ! -f .pre-commit-config.yaml || git add .pre-commit-config.yaml
git commit -m "chore: consolidate dependency and action updates"
```

Expected: each repo's format/lint/LOC/type/unit/integration/surface/conformance gates pass. Do not
commit an empty or over-broad path; inspect `git diff --cached` per repository first.

### Task 4: Consolidate Group B action and dependency unions

**Files:**
- Modify in HGNC, HPO, LitVar, MaveDB, MetaDome, MGI, Mondo: current PR-listed workflows,
  `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, and workflow-pin tests
- Do not modify HPO/MaveDB data-publisher behavior here

**Interfaces:**
- Consumes: Group B cutover inventories.
- Produces: one coherent action/dependency state per Group B repository.

- [ ] **Step 1: Reproduce every inventoried dependency/action change**

Perform the same explicit file-by-file union derivation for all seven Group B repositories. The
split CodeQL pairs in HGNC, HPO, MaveDB, and MetaDome must be one identical init/analyze revision.

- [ ] **Step 2: Update full-SHA action families and their tests together**

```bash
rg -n 'github/codeql-action/(init|analyze)@|astral-sh/setup-uv@|berntpopp/genefoundry-router/' .github tests
```

Verify each SHA at its primary repository, preserve explicit uv version input, and reject mutable
tags or mixed action versions. Every discovered reusable router CI/release occurrence must equal the
literal Plan 1 trusted-builder SHA from the handoff; an older 40-hex router SHA is a failure.

- [ ] **Step 3: Resolve package changes with targeted uv locks and boundary smokes**

Run exact imports/CLI/server-schema tests for every changed runtime package; compiler/linter-only
changes still run the repository's full native gate.

- [ ] **Step 4: Commit after complete native verification**

```bash
make ci-local
git diff --check
git add .github pyproject.toml uv.lock tests
test ! -f .pre-commit-config.yaml || git add .pre-commit-config.yaml
git diff --cached --check
git commit -m "chore: consolidate dependency and action updates"
```

### Task 5: Consolidate Group C action and dependency unions

**Files:**
- Modify in Orphanet, PanelApp, PubTator, SpliceAI Lookup, STRINGdb, UniProt, VEP: current PR-listed
  workflows, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, and workflow-pin tests

**Interfaces:**
- Consumes: Group C cutover inventories and the exact router reusable-workflow release.
- Produces: coherent dependency/action states, with VEP and UniProt failures classified rather than
  hidden.

- [ ] **Step 1: Reproduce the seven reviewed unions**

Use the same immutable inventory process; pair every CodeQL step and update all setup-uv/shared
workflow occurrences atomically.

- [ ] **Step 2: Diagnose VEP and UniProt evidence before accepting changes**

For VEP, reproduce any current behavior-probe failure at the exact PR head and at current main;
write a failing regression test only if the dependency causes a real behavior defect. For UniProt,
rerun the exact transient-503 integration job once; do not weaken assertions, timeouts, or live
availability semantics based on one upstream 503.

- [ ] **Step 3: Verify runtime-heavy dependency boundaries**

PubTator/Torch changes require import plus exercised CPU/CUDA resolution tests from the repository;
FastMCP/Pydantic/Uvicorn/Orjson changes require installed import, list-tools/schema, transport, and
serialization tests.

- [ ] **Step 4: Run native gates and commit per repository**

```bash
make ci-local
git add .github pyproject.toml uv.lock tests
test ! -f .pre-commit-config.yaml || git add .pre-commit-config.yaml
git diff --cached --check
git commit -m "chore: consolidate dependency and action updates"
```

### Task 6: Fix reboot restart behavior in the six affected backends

**Files:**
- AutoPVS1: `docker/docker-compose.prod.yml`, `tests/unit/test_docker_compose_prod.py`
- ClinGen: `docker/docker-compose.prod.yml`, `docker/docker-compose.npm.yml`,
  `tests/unit/test_compose_hardening.py`
- GeneReviews: `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`,
  `tests/test_docker_compose_config.py`
- gnomAD: `docker/docker-compose.prod.yml`, `tests/unit/docker/test_docker_compose.py`
- LitVar: `docker/docker-compose.prod.yml`, `tests/unit/test_docker_compose_hardening.py`
- PubTator: `docker/docker-compose.prod.yml`, `tests/unit/docker/test_compose_hardening.py`,
  `tests/unit/docker/test_docker_compose_postgres.py`

**Interfaces:**
- Consumes: the released router validator that requires `unless-stopped` for long-lived services.
- Produces: effective base+production and base+production+NPM models with every serving application
  or database `unless-stopped` and every one-shot auxiliary `no`.

- [ ] **Step 1: Add failing rendered-model assertions in all six repositories**

Each test invokes the repository's existing Compose render helper and asserts every long-lived
service literal:

```python
assert services["app"]["restart"] == "unless-stopped"
assert services["data-init"]["restart"] == "no"
```

Use actual service names from the rendered mapping; never add a text-grep assertion.

- [ ] **Step 2: Run each focused test and verify RED**

```bash
uv run pytest tests/unit/test_docker_compose_prod.py -q
```

Run the listed native file in its owning repo. Expected: failure identifies the current
`on-failure` effective value, not a fixture/setup error.

- [ ] **Step 3: Change only long-lived effective overrides**

Replace `on-failure` with `unless-stopped` for application/PostgreSQL/Redis serving services in the
listed overlays. Preserve `restart: "no"` on ClinGen/GeneReviews init/build services.

- [ ] **Step 4: Verify GREEN, both deployment renders, and native gates**

```bash
case "$(basename "$(git rev-parse --show-toplevel)")" in
  autopvs1-link) test_file=tests/unit/test_docker_compose_prod.py ;;
  clingen-link) test_file=tests/unit/test_compose_hardening.py ;;
  genereviews-link) test_file=tests/test_docker_compose_config.py ;;
  gnomad-link) test_file=tests/unit/docker/test_docker_compose.py ;;
  litvar-link) test_file=tests/unit/test_docker_compose_hardening.py ;;
  pubtator-link) test_file=tests/unit/docker/test_compose_hardening.py ;;
  *) exit 64 ;;
esac
repo_name=$(basename "$(git rev-parse --show-toplevel)")
uv run pytest "$test_file" -q
make ci-local
```

The focused tests render base+production and base+production+NPM with their repository fixture
environment; a raw Compose command with missing mandatory image/data variables is not evidence.
Task 7 renders again from the exact candidate manifest and its complete environment tuple.

- [ ] **Step 5: Commit the fixes per repository**

```bash
git add docker tests
git commit -m "fix: restore services after host reboot"
```

### Task 7: Build exact release candidates and run supply-chain gates

**Files:**
- Modify only when required by native evidence: `container-release.json` and its exact tests
- Create externally: CI artifacts, Trivy JSON, SBOM, smoke/content reports

**Interfaces:**
- Consumes: all per-repo commits from Tasks 2–6 and the literal Plan 1 trusted-builder router SHA.
- Produces: one exact candidate image digest and evidence package per backend plus a central
  workflow-reference report proving every reusable router CI/release occurrence equals that SHA.

- [ ] **Step 1: Run each repository's complete local gate once on its final tree**

```bash
make ci-local
git status --short
git diff --check origin/main...HEAD
```

Expected: clean tracked state after commits and no failure/warning hidden as success.

For every backend, record the exact credential-boundary test. Where a backend calls a credentialed
upstream, the test supplies a sentinel inbound Authorization header and asserts the upstream mock
receives only the configured upstream-service credential (or no credential), never the sentinel.
Where no upstream credential exists, assert inbound Authorization is ignored and not copied into
outbound requests/logs/errors. Add a RED test and minimal fix before merge when this named evidence
is absent; record each test result in the closure package.

- [ ] **Step 2: Build once per local candidate and validate exact contents**

Use the repository Makefile/shared workflow commands to build the production target, scan that
exact image under `fixable-high-critical-v1`, generate the SBOM, run layer/content policy, and run
runtime/health/MCP smoke under read-only/cap-drop/no-new-privileges constraints. Render production
and NPM through the shared candidate validator with the exact candidate image digest plus complete
data/environment tuple from the candidate evidence; never invoke raw Compose with unresolved
mandatory variables or zero-digest examples.

- [ ] **Step 3: Create a combined review package without losing repository boundaries**

Before packaging, invoke the versioned router validator from Plan 4 Task 1 against the exact 21
candidate worktrees recorded in the ownership ledger and the exact Plan 1 handoff:

```bash
router_closure_worktree=/home/bernt-popp/development/genefoundry-router/.worktrees/fleet-closure-20260830
workflow_report=/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-workflow-refs.json
candidate_map=/home/bernt-popp/development/fleet-remediation-evidence-20260830/candidate-worktrees.json
trusted_builder_sha=$(jq -er '.router.trusted_builder_sha' \
  /home/bernt-popp/development/fleet-remediation-evidence-20260830/control-plane-handoff.json)
uv run --directory "$router_closure_worktree" python \
  scripts/validate_fleet_workflow_refs.py \
  --worktree-map "$candidate_map" \
  --required-router-sha "$trusted_builder_sha" --json-out "$workflow_report"
sha256sum "$workflow_report"
```

Require every configured backend to be a validated consumer or carry a reviewed typed
`not_applicable` reason, and require native workflow-contract fixtures in every consumer repo to
assert the same literal SHA. This pre-merge report is candidate review evidence only; it is not the
final closure report because Plan 3 may still add workflow/source commits. No merely
immutable-but-older router ref is accepted.

For each repo record base/head, commit list, stat, and full diff in a separate named section of the
plan workspace. The reviewer verifies every inventoried PR mapping, Docker stage, action family,
lock result, and restart role; one repository's green result never substitutes for another's.

### Task 8: Create and merge one replacement PR per backend

**Files:**
- External: 21 branches, PRs, current checks, merge commits

**Interfaces:**
- Consumes: reviewed local candidate and immutable cutover inventory.
- Produces: 21 normal merge commits with current GitHub source/security/container/conformance gates.

- [ ] **Step 1: Push each named replacement branch and create its PR**

```bash
git push -u origin codex/fleet-security-20260830
gh pr create --repo berntpopp/clinvar-link --base main \
  --head codex/fleet-security-20260830 \
  --title 'fix: consolidate security and dependency updates' \
  --body-file /tmp/clinvar-link-replacement-pr.md
```

The body lists every covered PR/head, commits by concern, local gates, base image/index/platform
digest, scan policy/result, and any repo-specific data task intentionally deferred to Plan 3.

- [ ] **Step 2: Re-query the inventory before merge**

```bash
gh pr list --repo berntpopp/clinvar-link --state open --author 'app/dependabot' --limit 100 \
  --json number,url,headRefOid,files
gh pr checks --repo berntpopp/clinvar-link --watch
gh pr view --repo berntpopp/clinvar-link --json mergeStateStatus,headRefOid,statusCheckRollup
```

Incorporate and reverify changed/new applicable bot work or explicitly leave it open; require green
checks attached to the displayed replacement head.

- [ ] **Step 3: Merge normally in dependency-safe order**

Merge repositories without data-plan commits only after review. For every repository that receives
Plan 3 source/workflow commits—ClinVar, GeneReviews, HPO, MaveDB, ClinGen, gnomAD, Orphanet, and
GTEx—transfer the worktree lease to Plan 3, add approved source/workflow repair commits to this
source-repair PR, then return the exact clean head to Plan 2 for review and merge. If live audit
shows that another Plan 3 repository needs a source commit, add it to the ledger before transfer.
Publication-dependent runtime pins are unknowable before this merge and therefore use a separate
Plan 3 pin PR. Never force-push a shared bot branch.

```bash
gh pr merge --repo berntpopp/clinvar-link --merge --delete-branch
```

### Task 9: Complete the immutable 175-PR baseline plus cutover deltas for the final router release

**Files:**
- External: machine-readable cutover inventory handoff

**Interfaces:**
- Consumes: 21 backend replacement merge SHAs, the ten router mappings, and final default-branch
  checks.
- Produces: one validated JSON handoff whose immutable 175-row audit baseline and separately
  counted post-audit deltas map every original, new, or changed-head PR to
  merged/replacement/still-open with no silent disposition. Plan 4 adds it to the final router
  release before any superseded bot PR is closed.

- [ ] **Step 1: Re-query, complete, and validate the cutover inventory**

Refresh all 22 repositories, keep every original baseline `identity` object byte-stable and verify
its aggregate audit hash, and add a typed delta for each genuinely new PR or changed head. Fill the
separate `resolution` object with every applicable replacement
commit/PR/merge/check/disposition field and validate exact baseline cardinality, the recorded delta
count, supersession chains, effective tuple uniqueness, and coverage of the live query. Run Plan 4
Task 1's versioned cutover validator, write a detached SHA-256, and obtain independent adversarial
review of every effective mapping.

After all Plan 2 replacement/source-repair merges and all Plan 3 source/workflow merges, fetch every
`origin/main`, create clean detached verification worktrees at those exact default SHAs, write a
new 21-entry `final-default-worktrees.json`, and rerun `validate_fleet_workflow_refs.py` against that
map. Store its canonical `fleet-workflow-refs-final.json` plus SHA-256 beside the cutover inventory;
this supersedes the candidate report and is the only workflow-ref report handed to Plan 4. Any
later Plan 3 data-pin PR must be rechecked; if it touches workflows, regenerate again.

- [ ] **Step 2: Hand the exact file to Plan 4 without closing PRs**

```bash
router_closure_worktree=/home/bernt-popp/development/genefoundry-router/.worktrees/fleet-closure-20260830
uv run --directory "$router_closure_worktree" python \
  scripts/validate_dependabot_cutover.py "$FLEET_CUTOVER_INVENTORY"
sha256sum "$FLEET_CUTOVER_INVENTORY" \
  >/home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-cutover-inventory.sha256
uv run --directory "$router_closure_worktree" python \
  scripts/validate_fleet_workflow_refs.py \
  --worktree-map /home/bernt-popp/development/fleet-remediation-evidence-20260830/final-default-worktrees.json \
  --required-router-sha "$(jq -er '.router.trusted_builder_sha' \
    /home/bernt-popp/development/fleet-remediation-evidence-20260830/control-plane-handoff.json)" \
  --json-out /home/bernt-popp/development/fleet-remediation-evidence-20260830/fleet-workflow-refs-final.json
```

Do not comment on or close an original PR yet. Plan 4 Task 6 commits this exact content as
`ci/dependabot-cutover.json`, includes it in the final immutable router release, and Plan 4 Task 9
then uses that release permalink/hash as evidence for comments and closures.
