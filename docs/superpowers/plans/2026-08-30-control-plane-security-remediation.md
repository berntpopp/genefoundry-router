# Control-Plane Security Remediation Implementation Plan

> Historical record — this plan records the approved execution sequence as of 2026-08-30.
> Current behavior is defined by merged code, immutable release evidence, GitHub state, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair and release the router trusted-builder/control plane, enable production
monitoring, and establish an auditable security baseline for the three supporting repositories.

**Architecture:** The router remains the sole trusted builder. Source fixes land first in an
isolated router branch, then the exact release is used as the shared-workflow authority for backend
plans. GitHub controls are changed only through typed, fail-closed probes and least-privilege
credentials; supporting repositories receive controls appropriate to their role without becoming
trusted builders.

**Tech Stack:** Python 3.12+, uv, FastMCP 3.x, Pydantic 2, pytest, Ruff, mypy, Docker/BuildKit,
Trivy, Syft/SBOM attestations, GitHub Actions, GitHub CLI, Vue/Vite for `genefoundry`.

**Spec:** `docs/superpowers/specs/2026-08-30-fleet-security-pr-data-remediation-design.md`

## Global Constraints

- The router and 21 configured backends are the primary fleet; `servers.yaml` is authoritative.
- Never forward the caller's `Authorization` header to a backend.
- MCP remains Streamable HTTP at `/mcp`; SSE is not introduced.
- Images remain digest-pinned, non-root, read-only, capability-free, and unpublished to host ports.
- A production image passes the versioned zero-fixable-HIGH/CRITICAL policy without waivers.
- Long-lived serving services use `restart: unless-stopped`; one-shot init/build services use
  `restart: "no"`.
- GitHub Actions are full-SHA pinned; paired CodeQL steps move atomically.
- The broad local classic PAT is never copied into Actions.
- Every behavior fix follows RED/GREEN TDD; dependency/config-only changes use their native
  contract tests and full repository gates.
- No release asset, protected tag, merged history, or deployed rollback digest is overwritten.

---

### Task 1: Reconcile authority and create isolated workspaces

**Files:**
- Read: `AGENTS.md`, `servers.yaml`, `pyproject.toml`, `uv.lock`, `.github/workflows/*.yml`
- Create: no tracked product files

**Interfaces:**
- Consumes: GitHub repository state and local clones whose `origin` is the exact
  `berntpopp/` owner plus the ledger repository name.
- Produces: clean named worktrees based on current `origin/main` and an execution ledger containing
  repository, base SHA, open PR heads, workflow state, and external credential prerequisites.

- [ ] **Step 1: Prove identity, scope, and current heads without changing a checkout**

```bash
gh auth status
gh api user --jq .login
git fetch --prune origin
git status --short --branch
git remote get-url origin
gh api repos/berntpopp/genefoundry-router --jq '[.default_branch,.archived]'
gh api 'repos/berntpopp/genefoundry-router/pulls?state=open&per_page=100' --paginate \
  --jq '.[] | [.number,.user.login,.head.sha,.html_url] | @tsv'
```

Expected: the router origin is exact, the default branch is `main`, the checkout is clean, and
every open PR head SHA is recorded immediately before consolidation.

- [ ] **Step 2: Verify or create one worktree per repository writer**

```bash
git check-ignore -q .worktrees
git worktree list --porcelain
git worktree add .worktrees/fleet-security-data-remediation \
  -b codex/fleet-security-data-remediation origin/main
```

Expected: reuse the existing clean named worktree when present; never run the add command over an
existing path or branch. Supporting repositories use their own `.worktrees/fleet-security-20260830`
branches after their instruction files and user changes are inspected.

- [ ] **Step 3: Record external prerequisites without importing secret values**

```bash
gh secret list --repo berntpopp/genefoundry-router --env control-audit
gh variable list --repo berntpopp/genefoundry-router
gh api repos/berntpopp/genefoundry-router/environments/control-audit
```

Expected: secret names and environment metadata only. Record the GitHub App installation plus
`CONTROL_AUDIT_APP_CLIENT_ID` and `CONTROL_AUDIT_APP_PRIVATE_KEY` prerequisites as unavailable when
absent; continue every task that does not require them and never substitute a PAT.

### Task 2: Accept GitHub's security-positive ruleset field fail-closed

**Files:**
- Modify: `scripts/audit_container_controls.py`
- Modify: `genefoundry_router/release/controls.py`
- Modify: `tests/release/test_control_audit.py`
- Modify: `tests/release/test_controls.py`

**Interfaces:**
- Consumes: the exact GitHub pull-request ruleset response dictionary.
- Produces: `_matches_neutral_pull_request_parameters(parameters: dict[str, Any]) -> bool` that
  accepts `require_extra_approval_for_unattributed_changes` only when its JSON type is `bool` and
  its value is `True`; both the live parser and sealed control model accept only the current exact
  zero-approval policy while the independent-maintainer count remains one.

- [ ] **Step 1: Add the real response field to the verified fixture and strict rejection cases**

Add this literal to `MAIN_RULESET_DETAIL["rules"][-1]["parameters"]`:

```python
"require_extra_approval_for_unattributed_changes": True,
```

Add parameterized cases proving that `False`, `0`, `1`, `"true"`, `None`, and an unknown sibling
field are rejected. The production mutation each test catches is accepting a weakened, coerced, or
unmodeled GitHub rule. Change the fixture's `required_approving_review_count` to `0` and add a
focused case proving that `1` is rejected while the independent-maintainer count is one.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
uv run pytest tests/release/test_control_audit.py::test_main_branch_ruleset_probe_returns_exact_verified_model -q
```

Expected: FAIL because the exact-key parser does not recognize the new field.

- [ ] **Step 3: Add the field to the exact typed security-positive values**

In `MAIN_PULL_REQUEST_PARAMETER_VALUES`, add exactly:

```python
"require_extra_approval_for_unattributed_changes": True,
```

Do not move the field to the optional-neutral map and do not permit `False`. Replace the audit
approval set with the single exact allowed value `frozenset({0})`; change
`MainBranchRulesetControl.required_approving_review_count` to `Literal[0]`; update its strict
validator, comments, and ledger tests so a stored `1` cannot bypass the live exact-policy check.

- [ ] **Step 4: Verify GREEN and all control-ledger tests**

```bash
uv run pytest tests/release/test_control_audit.py tests/release/test_controls.py -q
```

Expected: all tests pass; unknown keys, wrong types, false values, bypass actors, extra rule types,
and relaxed branch conditions still fail closed.

- [ ] **Step 5: Commit the parser repair**

```bash
git add scripts/audit_container_controls.py genefoundry_router/release/controls.py \
  tests/release/test_control_audit.py tests/release/test_controls.py
git commit -m "fix: accept unattributed-change approval control"
```

### Task 3: Make reboot-safe serving restart semantics enforceable

**Files:**
- Modify: `docs/CONTAINER-HARDENING-STANDARD-v1.md`
- Modify: `genefoundry_router/release/compose_policy.py`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `tests/release/test_compose.py`
- Modify: `tests/release/test_compose_policy.py`
- Modify: `tests/release/test_compose_auxiliary.py`
- Modify: `tests/release/test_smoke_profiles.py`
- Modify: `tests/release/test_compose_isolation.py`
- Modify: `tests/release/test_compose_service_set.py`

**Interfaces:**
- Consumes: effective Compose mappings and `ComposePolicy.allowed_restart`.
- Produces: a role-aware invariant: application/database serving roles require
  `unless-stopped`; an `init` role requires `no`.

- [ ] **Step 1: Write failing effective-Compose behavior tests**

Add assertions that the rendered router production application is `unless-stopped`, that
`on-failure` is rejected for a long-lived application policy, and that the existing init-sidecar
fixture still requires `restart: "no"`.

```python
assert rendered["services"]["genefoundry-router"]["restart"] == "unless-stopped"
```

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
uv run pytest tests/release/test_compose.py tests/release/test_compose_policy.py \
  tests/release/test_compose_auxiliary.py tests/release/test_smoke_profiles.py \
  tests/release/test_compose_isolation.py tests/release/test_compose_service_set.py -q
```

Expected: FAIL on the current production override and default application policy.

- [ ] **Step 3: Implement the role-specific minimum**

Set the default application `allowed_restart` to `frozenset({"unless-stopped"})`, change the router
production overlay to `restart: unless-stopped`, and update Standard v1 item 26 to state that
`unless-stopped` is mandatory for serving/database services while one-shot init/build roles use
`no`. Update the valid serving fixtures in `test_compose_isolation.py` and
`test_compose_service_set.py` to `unless-stopped`; preserve the existing init-role rejection and
focused negative `on-failure` assertions in `compose_roles.py` tests.

- [ ] **Step 4: Verify GREEN and render both deployment models**

```bash
uv run pytest tests/release/test_compose.py tests/release/test_compose_policy.py \
  tests/release/test_compose_auxiliary.py tests/release/test_smoke_profiles.py \
  tests/release/test_compose_isolation.py tests/release/test_compose_service_set.py -q
make docker-prod-config >/tmp/genefoundry-router-prod.yml
make docker-npm-config >/tmp/genefoundry-router-npm.yml
```

Expected: tests pass; the serving service is reboot-safe; no init role becomes a daemon; production
still exposes only port 8000 to the internal proxy network.

- [ ] **Step 5: Commit restart semantics**

```bash
git add docs/CONTAINER-HARDENING-STANDARD-v1.md genefoundry_router/release/compose_policy.py \
  docker/docker-compose.prod.yml tests/release/test_compose.py \
  tests/release/test_compose_policy.py tests/release/test_compose_auxiliary.py \
  tests/release/test_smoke_profiles.py tests/release/test_compose_isolation.py \
  tests/release/test_compose_service_set.py
git commit -m "fix: require reboot-safe serving restarts"
```

### Task 4: Consolidate the router image, Actions, and dependency updates

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml` when represented by current PRs
- Modify: `.github/workflows/*.yml` represented by current PRs
- Modify: `.github/workflows/control-audit.yml`
- Create: `scripts/validate_container_controls.py`
- Modify: action-pin constants under `tests/release/`
- Modify: `tests/unit/docker/test_dockerfile.py`
- Modify: `tests/release/test_control_audit_workflow.py`
- Modify: `CHANGELOG.md` if present; otherwise use release notes generated from the reviewed PR

**Interfaces:**
- Consumes: reviewed union of the router's ten current Dependabot diffs and current primary-source
  action/package release metadata.
- Produces: one coherent dependency graph and one action version per action family; both Dockerfile
  stages use Python 3.14 index
  `sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5` unless a freshly
  reverified newer digest is substituted and recorded. The audited action union is
  `actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8`, paired
  `github/codeql-action/{init,analyze}@ff2f1c621b7f889edc0d3c761ac2e6a3f8cdb0dd`, all ten
  `astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d` occurrences with explicit
  uv `0.8.7`, and `docker/setup-buildx-action@37fe631027851001ddb9b187196cc803df7f5f0e`.

- [ ] **Step 1: Capture the union without merging bot branches serially**

```bash
gh pr list --repo berntpopp/genefoundry-router --state open --author 'app/dependabot' \
  --limit 100 --json number,headRefOid,files,url
for number in $(gh pr list --repo berntpopp/genefoundry-router --state open \
  --author 'app/dependabot' --limit 100 --json number --jq '.[].number'); do
  gh pr diff "$number" --repo berntpopp/genefoundry-router >"/tmp/router-pr-${number}.diff"
done
```

Expected: each current head and changed file is reviewed; split CodeQL init/analyze updates are
treated as one action change.

- [ ] **Step 2: Add the exact base-image regression, then change both stages**

In `tests/unit/docker/test_dockerfile.py`, first change
`test_dockerfile_uses_reviewed_python_314_index_in_both_stages` to require the reviewed digest and
run it to observe RED. Replace both existing `python:3.14-slim@sha256:...` authorities in
`docker/Dockerfile` with the same verified index digest, rerun the test to GREEN, and keep the
human-readable tag only as a label; the digest is authoritative.

- [ ] **Step 3: Reproduce the dependency union through uv and update action tests atomically**

```bash
uv lock --upgrade-package fastmcp --upgrade-package pydantic-settings \
  --upgrade-package pre-commit --upgrade-package mypy --upgrade-package ruff
uv sync --group dev --frozen
uv run python - <<'PY'
from importlib.metadata import version
import fastmcp
from fastmcp import FastMCP
print(version("fastmcp"), fastmcp.__file__, FastMCP)
PY
```

Apply the audited compatible declaration union—FastMCP `>=3.4.7`, pydantic-settings `>=2.15.0`,
pre-commit `>=4.6.2`—and resolve mypy `2.3.1` plus Ruff `0.16.3`, after refreshing each primary
release record immediately before editing. Every workflow pin and its exact constant in
`tests/release/` moves in the same commit; CodeQL `init` and `analyze` use one identical SHA/comment.
Run the installed FastMCP import, router construction, auth middleware/token-stripping tests,
list-tools/schema tests, and the integration transport contract before accepting the lock.

- [ ] **Step 4: TDD the short-lived App audit and successful live-ledger artifact**

In `tests/release/test_control_audit_workflow.py`, first require:

- `actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1` (`v3.2.0`);
- secrets `CONTROL_AUDIT_APP_CLIENT_ID` and `CONTROL_AUDIT_APP_PRIVATE_KEY`, owner `berntpopp`,
  the exact router-plus-21 repository list, and only Metadata/Admin read permissions;
- generated `"$RUNNER_TEMP/container-controls.json"` from a successful live probe, followed by
  strict `load_control_ledger`/`require_compliant_controls` validation;
- `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` (`v7.0.1`) with exact
  name `container-controls-live`, exact path, `retention-days: 30`, and `if: success()`;
- absence of `CONTROL_AUDIT_TOKEN`, persisted checkout credentials, PAT fallback,
  `skip-token-revoke`, `continue-on-error`, and failure-suppressing shell constructs.

Run the focused test and observe RED. Implement the workflow so the App token is `GH_TOKEN`, the
live non-`--check` audit writes the temporary ledger and exits nonzero on any unavailable row, a
separate credential-free `scripts/validate_container_controls.py PATH` CLI calls strict
`load_control_ledger`/`require_compliant_controls` validation for the exact 22-row fleet, and only
then does the pinned upload step run. The action's default post-step token revocation remains
enabled. Add CLI success/malformed/noncompliant cases to `tests/release/test_controls.py`, parse all
workflow YAML, and rerun both focused test modules to GREEN.

- [ ] **Step 5: Run the repository and production-image gates**

```bash
make ci-local
make ci-full
make docker-build
make docker-prod-config >/tmp/genefoundry-router-prod.yml
make docker-npm-config >/tmp/genefoundry-router-npm.yml
```

Then run the same `fixable-high-critical-v1` build/scan/SBOM/content/smoke path used by
`.github/workflows/_container-ci.yml` against the exact locally built production image. Expected:
zero fixable HIGH/CRITICAL results, non-root runtime, read-only compatibility, valid MCP `/mcp`, and
no authorization-header passthrough regression.

- [ ] **Step 6: Commit logical concerns separately**

```bash
git add docker/Dockerfile tests/unit/docker/test_dockerfile.py
git commit -m "fix: refresh Python runtime image"
git add pyproject.toml uv.lock .pre-commit-config.yaml .github tests/release \
  scripts/validate_container_controls.py
git commit -m "chore: consolidate dependency and action updates"
```

### Task 5: Open, review, merge, and release the router control-plane change

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, release notes/changelog according to repository convention
- External: router pull request, protected `vX.Y.Z` tag, GitHub/container release evidence

**Interfaces:**
- Consumes: Tasks 2–4 and a current green base.
- Produces: the next patch release after v0.8.2 and its immutable OCI digest, SBOM, provenance,
  vulnerability report, MCP definition, and reusable-workflow commit/tag identity.

- [ ] **Step 1: Bump the single source and verify version propagation**

```bash
uv version --bump patch
uv lock
uv sync --group dev --frozen
uv run python -c 'from importlib.metadata import version; from genefoundry_router import __version__; assert __version__ == version("genefoundry-router")'
make ci-local
```

- [ ] **Step 2: Commit, push, and create the replacement PR**

```bash
git add pyproject.toml uv.lock
test ! -f CHANGELOG.md || git add CHANGELOG.md
git commit -m "chore: prepare router security release"
git push -u origin codex/fleet-security-data-remediation
gh pr create --repo berntpopp/genefoundry-router --base main \
  --head codex/fleet-security-data-remediation \
  --title 'fix: remediate fleet control-plane security failures' \
  --body-file /tmp/genefoundry-router-pr-body.md
```

The PR body links all replaced bot PRs and includes exact local gates, image scan result, restart
contract, and ruleset parser RED/GREEN evidence.

- [ ] **Step 3: Require current checks and independent adversarial review**

```bash
gh pr checks --repo berntpopp/genefoundry-router --watch
gh pr view --repo berntpopp/genefoundry-router --json mergeStateStatus,headRefOid,statusCheckRollup
```

Expected: source, CodeQL, dependency review, container, SBOM, and release-control checks are green
for the displayed head. Address Critical/Important review findings before merge.

- [ ] **Step 4: Merge normally and create the protected tag at the recorded merge SHA**

```bash
router_pr=$(gh pr view --repo berntpopp/genefoundry-router \
  codex/fleet-security-data-remediation --json number,headRefOid)
router_pr_number=$(jq -er .number <<<"$router_pr")
reviewed_head=$(jq -er .headRefOid <<<"$router_pr")
gh pr merge --repo berntpopp/genefoundry-router --merge --delete-branch
merge_sha=$(gh pr view "$router_pr_number" --repo berntpopp/genefoundry-router \
  --json mergeCommit --jq .mergeCommit.oid)
[[ "$merge_sha" =~ ^[0-9a-f]{40}$ ]] || exit 65
git fetch origin "$merge_sha" --tags
version=$(git show "$merge_sha":pyproject.toml | sed -n 's/^version = "\([^"]*\)"/\1/p')
test -n "$version"
git merge-base --is-ancestor "$reviewed_head" "$merge_sha"
git tag -s "v${version}" "$merge_sha" -m "genefoundry-router v${version}"
tagged_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
git push origin "v${version}"
for attempt in $(seq 1 12); do
  run_id=$(gh run list --repo berntpopp/genefoundry-router --workflow container-release.yml \
    --limit 30 --json databaseId,headSha,createdAt \
    --jq ".[] | select(.headSha == \"$merge_sha\" and .createdAt >= \"$tagged_at\") | .databaseId" \
    | head -n 1)
  test -n "$run_id" && break
  sleep 5
done
test -n "$run_id"
gh run watch --repo berntpopp/genefoundry-router "$run_id"
```

Expected: no force push, mutable tag, or check override. Verify the GitHub release, anonymous GHCR
pull, attestations, manifest revision, and zero-fixable vulnerability evidence.

### Task 6: Enable monitoring and regenerate control evidence

**Files:**
- Modify only if tests prove a post-release defect: `.github/workflows/drift.yml`,
  `.github/workflows/fleet-probe.yml`
- External: GitHub repository variables, `control-audit` environment, sealed ledger PR

**Interfaces:**
- Consumes: released router source and a dedicated installed control-audit GitHub App.
- Produces: enabled schedules, successful manual runs, and a ledger generated exclusively by live
  probes.

- [ ] **Step 1: Enable both opt-in schedules**

First capture the typed API response or exact 404 for each variable in the mutation ledger. Record
the rollback as restoring its prior value or deleting only the newly created variable. Then run:

```bash
gh variable set DRIFT_ENABLED --repo berntpopp/genefoundry-router --body true
gh variable set FLEET_PROBE_ENABLED --repo berntpopp/genefoundry-router --body true
gh variable list --repo berntpopp/genefoundry-router
```

- [ ] **Step 2: Dispatch and watch the drift and fleet probes**

```bash
router_head=$(gh api repos/berntpopp/genefoundry-router/commits/main --jq .sha)
for workflow in drift.yml fleet-probe.yml; do
  dispatched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  gh workflow run "$workflow" --repo berntpopp/genefoundry-router --ref main
  run_id=
  for attempt in $(seq 1 12); do
    run_id=$(gh run list --repo berntpopp/genefoundry-router --workflow "$workflow" \
      --limit 30 --json databaseId,headSha,createdAt,event \
      --jq ".[] | select(.event == \"workflow_dispatch\" and .headSha == \"$router_head\" and .createdAt >= \"$dispatched_at\") | .databaseId" \
      | head -n 1)
    test -n "$run_id" && break
    sleep 5
  done
  test -n "$run_id"
  gh run watch --repo berntpopp/genefoundry-router "$run_id"
done
```

Expected: no definition drift and the scheduled monitoring workflow's exact configured 21/21
transport success. It is not used as behavior-closure evidence; Plan 4 adds the strict
schema-derived 21/21 behavior sweep. A transient backend failure is rerun only after its exact
cause is classified.

- [ ] **Step 3: Supply only the GitHub App identity secrets**

When the owner-provided client ID and private-key files exist, store them in the protected
`control-audit` environment:

```bash
test -n "${CONTROL_AUDIT_APP_CLIENT_ID:-}"
test -n "${CONTROL_AUDIT_APP_PRIVATE_KEY_FILE:-}" && test -r "$CONTROL_AUDIT_APP_PRIVATE_KEY_FILE"
printf '%s' "$CONTROL_AUDIT_APP_CLIENT_ID" | gh secret set CONTROL_AUDIT_APP_CLIENT_ID \
  --repo berntpopp/genefoundry-router --env control-audit
gh secret set CONTROL_AUDIT_APP_PRIVATE_KEY --repo berntpopp/genefoundry-router \
  --env control-audit <"$CONTROL_AUDIT_APP_PRIVATE_KEY_FILE"
```

Before writing secrets, read back and ledger the environment ID, protection rules, deployment
branch policy, App installation ID, selected repositories, permission map, key identifier/creation
time (never key material), rotation owner, and revocation procedure. The App installation itself
grants only Metadata read and Administration read to the exact 22 repositories. If the protected
environment, App, or secrets are unavailable, never substitute a PAT; ledger the external blocker
and continue other work.

- [ ] **Step 4: Run the audit and seal only successful evidence**

```bash
router_head=$(gh api repos/berntpopp/genefoundry-router/commits/main --jq .sha)
dispatched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run control-audit.yml --repo berntpopp/genefoundry-router --ref main
audit_run=
for attempt in $(seq 1 12); do
  audit_run=$(gh run list --repo berntpopp/genefoundry-router --workflow control-audit.yml \
    --limit 30 --json databaseId,headSha,createdAt,event \
    --jq ".[] | select(.event == \"workflow_dispatch\" and .headSha == \"$router_head\" and .createdAt >= \"$dispatched_at\") | .databaseId" \
    | head -n 1)
  test -n "$audit_run" && break
  sleep 5
done
test -n "$audit_run"
gh run watch --repo berntpopp/genefoundry-router "$audit_run"
evidence_dir=/home/bernt-popp/development/fleet-remediation-evidence-20260830
gh run download --repo berntpopp/genefoundry-router "$audit_run" \
  --name container-controls-live --dir "$evidence_dir/control-audit-$audit_run"
uv run python scripts/validate_container_controls.py \
  "$evidence_dir/control-audit-$audit_run/container-controls.json"
sha256sum "$evidence_dir/control-audit-$audit_run/container-controls.json"
```

Expected: 22/22 verified. Download the ledger artifact, replace `ci/container-controls.json` only
with that live probe output, run `make ci-local` against its exact contents in a new evidence-only
branch, and merge it only through a reviewed PR. Never hand-edit a control result to passing.

- [ ] **Step 5: Activate the scheduled audit and record its rollback**

`control-audit.yml` gates its scheduled run on the `CONTROL_AUDIT_ENABLED` repository variable,
opt-in exactly like the drift and fleet probes in Step 1. Step 3's secrets and Step 4's green
manual dispatch are therefore necessary but **not sufficient**: while the variable is unset every
scheduled run is skipped, and GitHub reports a skipped job as a *successful* workflow. Following
this plan without this step yields a green control audit that has never actually audited anything
-- the precise failure the audit exists to detect. Do this only after Step 4 has genuinely passed.

First capture the typed API response or exact 404 for the variable in the mutation ledger. Record
the rollback as restoring its prior value or deleting only the newly created variable. Then run:

```bash
gh variable set CONTROL_AUDIT_ENABLED --repo berntpopp/genefoundry-router --body true
gh variable list --repo berntpopp/genefoundry-router
```

Rollback re-parks the schedule without touching the App, its installation, or its secrets:

```bash
gh variable delete CONTROL_AUDIT_ENABLED --repo berntpopp/genefoundry-router
```

Expected: within a day, `gh run list --repo berntpopp/genefoundry-router --workflow
control-audit.yml --json event,conclusion` shows a run whose `event` is `schedule` and whose
`conclusion` is `success`. Verify that a scheduled run exists at all; absence of runs, not a red
run, is this step's failure mode. Then refresh `ci/container-controls.json` from that live probe
per Step 4, so the release gate's evidence-age bound (`MAX_CONTROL_EVIDENCE_AGE` in
`genefoundry_router/release/controls.py`) is measured against a current capture rather than the
2026-07-30 one.

### Task 7: Harden and release the public `genefoundry` website repository

**Files:**
- Modify from its current green PRs only: files shown by `gh pr diff 89` and `gh pr diff 90`
- Modify as required by release validation: `.github/workflows/*.yml`, `docker/Dockerfile`,
  `package.json`, `package-lock.json`, version metadata, changelog/release notes, and existing
  workflow/container contract tests
- External: `main` and semantic-tag rulesets, immutable releases, release workflow

**Interfaces:**
- Consumes: current `origin/main`, PRs 89/90, repository `CLAUDE.md`, and existing Vue/Vite tests.
- Produces: one reviewed website change, solo-maintainer-safe protected main/tags, and a fresh
  immutable release proving the explicit repository target fix.

- [ ] **Step 1: Create a fresh worktree and reproduce the two green PR diffs**

```bash
git -C /home/bernt-popp/development/genefoundry fetch --prune origin
git -C /home/bernt-popp/development/genefoundry worktree add \
  .worktrees/fleet-security-20260830 -b codex/fleet-security-20260830 origin/main
gh pr diff 89 --repo berntpopp/genefoundry >/tmp/genefoundry-89.diff
gh pr diff 90 --repo berntpopp/genefoundry >/tmp/genefoundry-90.diff
```

- [ ] **Step 2: Apply the reviewed union and harden the mutable supply-chain authorities**

Do not merge the stale branch when current `main` already contains the safer digest implementation;
reproduce only missing behavior from PRs 89/90 on the fresh worktree. Under the repository's
existing contract tests, replace mutable Docker base authorities and mutable GitHub Action refs
with freshly verified index/full-commit digests, preserving paired action families. Run exactly:

```bash
npm ci
npm test
npm run type-check
npm run lint
npm run build
npm audit --audit-level=high
bash tests/test-health-container.sh
```

Commit functional changes, supply-chain pins, and release metadata as separate logical commits.

- [ ] **Step 3: Create exact solo-maintainer-safe controls**

Using `gh api`, create/verify active main-only PR protection with zero approvals, no bypass actor,
deletion/non-fast-forward blocking, and the security-positive unattributed-change rule. Create/verify
semantic-tag update/deletion protection and enable immutable releases. Do not add a tag-creation
restriction unless the exact release workflow actor has a proven narrow bypass. Read the created
rulesets back and compare the typed response before accepting them. Before mutation, record the
exact existing ruleset IDs/payloads and immutable-release response plus literal rollback payloads;
on partial failure, restore only the captured pre-state and verify it.

- [ ] **Step 4: Merge and publish a fresh patch release**

Bump the single version source and release notes on the replacement branch, rerun all native and
container gates, create the PR, and require current CI/security/container checks. Merge normally,
capture the exact replacement merge SHA, tag that SHA through the repository's protected workflow,
and select the tag-triggered run by matching head SHA/time rather than `latest`. Verify GitHub
release immutability, the exact source SHA, OCI digest, SBOM/attestations, anonymous pull, and the
corrected explicit release target.

- [ ] **Step 5: Close PRs 89/90 only after replacement evidence exists**

Re-query both original head SHAs, confirm their complete reviewed union is in the replacement merge,
comment with the replacement PR/merge SHA and immutable release URL, then close them. If either head
or diff changed, leave it open until the delta is incorporated or explicitly dispositioned.

### Task 8: Add a minimal reproducible baseline to `genefoundry-bench`

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/security.yml`, `.github/dependabot.yml`
- Modify: `pyproject.toml`, `README.md`, `uv.lock` if current source uses it
- Create: `tests/test_repository_automation.py`

**Interfaces:**
- Consumes: the private benchmark's declared Python version and CLI/tests.
- Produces: least-privilege CI, CodeQL/dependency scanning where GitHub permits, and Dependabot
  configuration without publishing benchmark data or secrets.

- [ ] **Step 1: Read the repository and establish its actual native commands**

Discover and read every current repository instruction file first. Then run:

```bash
git -C /home/bernt-popp/development/genefoundry-bench fetch --prune origin
git -C /home/bernt-popp/development/genefoundry-bench remote get-url origin
git -C /home/bernt-popp/development/genefoundry-bench status --short --branch
sed -n '1,260p' /home/bernt-popp/development/genefoundry-bench/pyproject.toml
sed -n '1,260p' /home/bernt-popp/development/genefoundry-bench/README.md
git -C /home/bernt-popp/development/genefoundry-bench worktree add \
  .worktrees/fleet-security-20260830 -b codex/fleet-security-20260830 origin/main
```

If the named branch/path already exists, reuse only when its base/owner/clean state match the
central ledger.

- [ ] **Step 2: Add a failing repository-automation contract**

Create `tests/test_repository_automation.py` to parse the workflows/Dependabot YAML and require
full-SHA actions, `persist-credentials: false`, `contents: read`, paired CodeQL revisions, frozen uv
sync, no publishing permissions, and bounded weekly pip/uv plus Actions update schedules. Run it and
observe RED because the baseline files do not exist.

- [ ] **Step 3: Add SHA-pinned least-privilege workflows**

CI checks out without persisted credentials, installs the exact supported Python and uv versions,
runs frozen sync plus formatter/linter/type/test commands actually declared by the repository, and
has `contents: read`. Security runs paired CodeQL init/analyze at one SHA plus dependency review on
PRs. Dependabot covers pip/uv and GitHub Actions on a bounded weekly schedule.

- [ ] **Step 4: Verify locally and on GitHub**

Run exactly:

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run inspect list tasks src/gfbench/tasks
```

Create a PR, require fresh CI/security checks, merge normally, and verify the repository security
settings now expose the intended scanners. Create/read back a solo-maintainer-safe main ruleset
that requires the exact new CI and security check names with zero approvals and no bypass actors;
capture pre-state and rollback payload before mutation. Do not add a public release or container
when the benchmark has no release contract.

### Task 9: Record control-plane outputs for downstream plans

**Files:**
- Modify: this plan's ignored SDD ledger only
- External: release/control evidence references

**Interfaces:**
- Consumes: final router and supporting-repository results.
- Produces: exact router reusable-workflow commit, application version, OCI digest, release manifest,
  monitoring run IDs, control-audit status, and any credential blocker for the fleet plans.

- [ ] **Step 1: Verify evidence is current and immutable**

```bash
gh release view --repo berntpopp/genefoundry-router --json tagName,isImmutable,targetCommitish,assets
gh run list --repo berntpopp/genefoundry-router --limit 20 \
  --json databaseId,workflowName,headSha,status,conclusion,createdAt
gh variable list --repo berntpopp/genefoundry-router
```

- [ ] **Step 2: Write the downstream tuple to the ledger**

Record literal values, not `latest`: router merge SHA, protected release tag, application manifest
SHA-256, OCI digest, reusable workflow ref, and the run IDs for container release, drift, fleet
probe, and control audit. Also record all ten router Dependabot PR numbers/head SHAs and their exact
replacement commits; Plan 2 Task 9 completes the program-wide 175-PR cutover handoff, the final
router release seals it, and Plan 4 Task 9 closes the router PRs only after that immutable permalink
exists.
