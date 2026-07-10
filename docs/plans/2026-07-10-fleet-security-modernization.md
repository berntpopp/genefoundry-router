# Fleet Security Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge, version, release, and verify every security, maintenance, and product change in
the approved 22-repository modernization design.

**Architecture:** Work is split into independently reviewable wave plans with explicit dependency
edges. Repositories are edited in isolated worktrees, every behavior change follows TDD, and no PR
merges until local CI, adversarial review, and required GitHub checks are green. Runtime verification
after deployment is part of completion.

**Tech Stack:** Python 3.12+, uv, FastMCP 3.4.4+, FastAPI/Starlette, HTTPX, Docker Compose,
GitHub Actions, pytest, Ruff, mypy, gh CLI, Claude Code.

## Global Constraints

- `pyproject.toml [project].version` is the only package-version literal.
- Streamable HTTP only; no legacy SSE transport.
- Caller `Authorization` is never forwarded to backends.
- Backends remain private or require a distinct backend service credential.
- Production allows no wildcard Host, Origin, or external-egress destination.
- No model inference or new network hop is added to the ordinary request path.
- Bulk downloads remain streaming and preserve the previous valid artifact on failure.
- Research use only; not clinical decision support.
- Every affected repository must pass its own `make ci-local` before PR publication and again on
  the final merged commit.
- Every behavior PR receives a Claude Code adversarial review; all must-fix findings are resolved
  before merge.
- Completion requires at least 9.5/10 under the approved rubric, no zero dimension, and no open
  P0/P1 finding.

---

## Plan Map and Dependency Graph

| Wave | Plan | Depends on |
|---|---|---|
| 0 | `2026-07-10-dependencies-genereviews-releases.md` tasks 1-2 | none |
| 1 | `2026-07-10-p0-policy-boundaries.md` | router service-header task before PubTator token enforcement |
| 2 | `2026-07-10-router-transport-drift-fencing.md` transport/drift tasks | Wave 0 FastMCP floor |
| 3 | `2026-07-10-router-transport-drift-fencing.md` fencing tasks | PubTator readonly inventory from Wave 1 |
| 4 | `2026-07-10-ingest-artifact-hardening.md` | none; may execute beside Waves 1-3 |
| 5 | `2026-07-10-dependencies-genereviews-releases.md` GeneReviews tasks | Wave 0 dependency refresh |
| 6 | this plan's release/runtime tasks | all behavior PRs merged |

Parallel workers may implement independent repositories within a wave. Shared router files and
deployment sequencing remain serialized.

### Task 1: Establish the Execution Ledger

**Files:**
- Create: `docs/plans/2026-07-10-fleet-execution-ledger.md`
- Read: `docs/specs/2026-07-10-fleet-security-modernization-design.md`

**Interfaces:**
- Consumes: approved issue closure matrix and four Dependabot PR identifiers.
- Produces: one row per PR/issue with branch, worktree, tests, review, merge, version, release,
  deployment, and live-evidence state.

- [ ] **Step 1: Create the ledger with every scoped item**

Use this exact header and rows:

```markdown
# Fleet Modernization Execution Ledger

| Repo | Issue/PR | Branch | Behavior tests | ci-local | Claude review | PR/checks | Merge SHA | Version/release | Runtime evidence | State |
|---|---|---|---|---|---|---|---|---|---|---|
| gencc-link | #20 | dependabot/github_actions/astral-sh/setup-uv-8.3.2 | pending | pending | pending | pending | pending | pending | n/a | open |
| gencc-link | #21 | dependabot/uv/uv-42d6deb6a8 | pending | pending | pending | pending | pending | pending | n/a | open |
| gnomad-link | #29 | dependabot/uv/uv-6350993ea2 | pending | pending | pending | pending | pending | pending | n/a | open |
| gnomad-link | #30 | dependabot/github_actions/astral-sh/setup-uv-8.3.1 | pending | pending | pending | pending | pending | pending | n/a | open |
| autopvs1-link | #41 / router #32 | fix/security-egress-production | pending | pending | pending | pending | pending | pending | pending | open |
| pubtator-link | #85 | fix/write-boundary | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | router #33 | feat/pubtator-write-boundary | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #31 | feat/untrusted-content-contract | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #36 | fix/transport-runtime-drift | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #35 | chore/phase5-tracker | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #3 | chore/close-stale-discovery | n/a | n/a | reviewed | pending | pending | n/a | reproduced | open |
| genereviews-link | #27 | feat/corpus-release-automation | pending | pending | pending | pending | pending | pending | pending | open |
| genereviews-link | #40 | feat/revision-variant-context | pending | pending | pending | pending | pending | pending | pending | open |
| genereviews-link | #49 | spike/hybrid-annotation | pending | pending | pending | pending | pending | pending | report | open |
```

The gnomAD #30 row records the existing Dependabot branch name (`setup-uv-8.3.1`). Refresh that
branch's pinned action content to the accepted 8.3.2 target; Dependabot branch names are historical
identifiers and are not renamed when their content is refreshed.

Append one Phase 5 row each for ClinVar, GenCC, HPO, HGNC, MGI, Mondo, Orphanet, and MaveDB,
plus one FastMCP guard row for each of the 21 backends.

- [ ] **Step 2: Verify ledger completeness**

Run:

```bash
rg -n "#20|#21|#29|#30|#3([^0-9]|$)|#31|#32|#33|#35|#36|#41|#85|#27|#40|#49" \
  docs/plans/2026-07-10-fleet-execution-ledger.md
```

Expected: every scoped issue and Dependabot PR appears at least once.

- [ ] **Step 3: Commit the ledger**

```bash
git add docs/plans/2026-07-10-fleet-execution-ledger.md
git commit -m "docs(security): add fleet modernization execution ledger"
```

### Task 2: Execute Wave Plans with Isolated Worktrees

**Files:**
- Read: all plan files named in the plan map.
- Modify: the execution ledger after each task gate.

**Interfaces:**
- Consumes: focused plans and current `origin/main` for each repository.
- Produces: focused, green, adversarially reviewed PRs.

- [ ] **Step 1: Create each repository worktree from current remote main**

For repository `$repo` and branch `$branch`:

```bash
git -C "/home/bernt-popp/development/$repo" fetch origin --prune
git -C "/home/bernt-popp/development/$repo" worktree add \
  "/home/bernt-popp/development/.worktrees/$repo/${branch//\//-}" \
  -b "$branch" origin/main
```

Expected: the worktree starts at the current GitHub `main`; divergent local `main` branches in
MaveDB and MetaDome remain untouched.

- [ ] **Step 2: Execute the focused TDD plan**

Run the exact failing-test, implementation, targeted-test, and `make ci-local` commands in the
focused plan. Update the ledger immediately after each verified gate.

- [ ] **Step 3: Run a read-only Claude review**

```bash
claude -p --model opus --effort high --permission-mode dontAsk \
  --allowedTools Read Glob Grep Bash \
  "Adversarially review the current branch diff against the linked issue and approved design. \
Report concrete must-fix security, correctness, compatibility, performance, and test gaps. \
Do not edit files."
```

Expected: a bounded findings list. Implement and re-test every must-fix finding; record the review
and resolution commit in the ledger.

- [ ] **Step 4: Publish a focused PR**

```bash
git push -u origin "$branch"
gh pr create --draft --base main --head "$branch" --title "$title" --body-file "$body_file"
```

The PR body includes issue links, threat/behavior summary, exact tests, migration/rollback, Claude
review disposition, and runtime verification still required.

- [ ] **Step 5: Wait for required checks and merge**

```bash
gh pr checks "$pr" --watch --fail-fast
gh pr view "$pr" --json mergeable,mergeStateStatus,statusCheckRollup
gh pr merge "$pr" --squash --delete-branch
```

Expected: mergeable/CLEAN and every required check successful. Never use admin bypass.

### Task 3: Version and Release Every Affected Repository

**Files:**
- Modify per repo: `pyproject.toml`
- Modify per repo: `uv.lock`
- Modify per repo: `CHANGELOG.md` or `docs/CHANGELOG.md`
- Test per repo: `tests/unit/test_version_single_source.py`

**Interfaces:**
- Consumes: all behavior PRs merged for a repository.
- Produces: a separate version PR, merged version SHA, tag/release where supported, and immutable
  deployment image digest.

- [ ] **Step 1: Determine the SemVer increment**

Use PATCH for compatible fixes and additive tools. Use MINOR for new backward-compatible tool
surface. Use MAJOR only when legacy response fields are removed or reshaped. Record the decision in
the execution ledger.

- [ ] **Step 2: Bump the single version source and changelog**

Edit only `[project].version` in `pyproject.toml`, add a dated changelog entry listing merged PRs,
then run:

```bash
uv lock
uv sync --group dev
make ci-local
```

Expected: version metadata, `__version__`, serverInfo, and health tests agree.

- [ ] **Step 3: Publish and merge the version PR**

```bash
git add pyproject.toml uv.lock CHANGELOG.md docs/CHANGELOG.md
git commit -m "chore(release): bump version for security modernization"
git push -u origin "$version_branch"
gh pr create --base main --head "$version_branch" --title "$version_title" --body-file "$body_file"
gh pr checks "$version_pr" --watch --fail-fast
gh pr merge "$version_pr" --squash --delete-branch
```

Stage only changelog paths that exist in that repository.

- [ ] **Step 4: Verify release artifacts**

If the repository has a tag/release workflow, watch it and record the tag, release URL, package or
image digest, SBOM, scan, and attestation. If it has no automated release, create the SemVer tag only
after `main` CI is green and document that no package registry publication is configured.

### Task 4: Deploy in Security Dependency Order

**Files:**
- Modify deployment secrets/config outside Git only through the operator's secret manager.
- Update: execution ledger with redacted configuration evidence and immutable digests.

**Interfaces:**
- Consumes: merged releases and configured service/egress credentials.
- Produces: live deployment matching the approved boundary.

- [ ] **Step 1: Deploy router PubTator service-header support**

Configure `GF_PUBTATOR_TOKEN` in the secret manager while the old backend still ignores it. Verify
router read calls still succeed.

- [ ] **Step 2: Deploy PubTator readonly plus token enforcement**

Deploy the matching backend token and readonly profile. Verify direct `/mcp` is 401, `/health` is
200, router reads succeed, and the public catalog contains no write tool.

- [ ] **Step 3: Deploy AutoPVS1 production/egress configuration**

Classify the deployment. Public research explicitly allowlists BGI/Ensembl; patient/on-prem omits
the backend and denies network egress. Verify effective runtime settings and denied-destination
probes without logging sensitive values.

- [ ] **Step 4: Deploy backend and router Host/Origin configuration**

Deploy exact public/internal/loopback Hosts before enabling strict guards. Verify router harvest,
health, browser Origin rejection, and invalid Host 421 across the real proxy.

- [ ] **Step 5: Deploy drift warn, observe, then enforce**

Deploy the reviewed baseline with `GF_DRIFT_MODE=warn`, observe one clean startup/poll, then switch
production to enforce. Verify changed tools are rejected/quarantined and the last accepted catalog
remains available.

### Task 5: Complete Runtime Security Audit and Close Trackers

**Files:**
- Create: `docs/reviews/2026-07-10-fleet-security-modernization-verification.md`
- Modify: execution ledger

**Interfaces:**
- Consumes: all merged releases, live endpoints, deployment configuration, and issue closure matrix.
- Produces: evidence-backed rubric score and final issue comments/closures.

- [ ] **Step 1: Run the live conformance and attack-surface suite**

Run router `make validate`, `make doctor`, `make list-tools`, MCP conformance, invalid Host/Origin,
unauthenticated backend, write-scope, egress denial, drift, and representative tool-call probes.
Record commands, timestamps, status codes, version/digest, and sanitized results.

- [ ] **Step 2: Verify containers and supply chain**

For every deployed image record non-root user, read-only rootfs, dropped capabilities,
no-new-privileges, resource/PID limits, public-port inventory, vulnerability scan, SBOM, action SHA
pins, and immutable image digest.

- [ ] **Step 3: Score the ten-dimension rubric**

Use only evidence captured in the verification report. Expected: at least 9.5/10, no zero, and no
open P0/P1. Any unproven dimension remains 0 or 0.5; do not infer completion from absent failures.

- [ ] **Step 4: Update and close issues**

Post the exact closure evidence required by the design matrix. Keep AutoPVS1 issues open if the
governance/network evidence is unavailable. Close no issue solely because code merged.

- [ ] **Step 5: Commit the verification report and final ledger**

```bash
git add docs/reviews/2026-07-10-fleet-security-modernization-verification.md \
  docs/plans/2026-07-10-fleet-execution-ledger.md
git commit -m "docs(security): record fleet modernization verification"
```
