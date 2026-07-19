# P0 Dependabot Fleet Completion Implementation Plan

> Historical record — this plan records the approved execution sequence as of 2026-07-19.
> Current behavior is defined by merged code, immutable release evidence, GitHub state, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every actionable fleet Dependabot/security blocker, merge the remaining Contract
Truth PRs only with fresh evidence, and preserve truthful P0 release claims.

**Architecture:** The router documents the canonical inventory and coordinates independently owned
backend worktrees. Existing bot PRs are preferred only after their actual patch, upstream primary
source, resolved dependency graph, and fresh checks are accepted. Security resolution precedes
Contract Truth rebases; workflow and major-runtime changes stay separate and intentionally reviewed.

**Tech Stack:** Python 3.12, uv, FastMCP 3.x, MCP 1.28.1, GitHub Actions, Docker/Trivy/SBOM,
GitHub CLI, pytest, Ruff, mypy, Make.

---

### Task 1: Reconcile state and establish isolated ownership

**Files:**
- Create: no product files
- Read: each repository `AGENTS.md`, default branch, PRs, checks, `pyproject.toml`, `uv.lock`

- [ ] **Step 1: Fetch each remote without changing its current checkout**

Run in the dedicated worktree or cloned bare metadata path:

```bash
gh auth status
gh api -i user | rg '^X-Oauth-Scopes:.*\brepo\b'
gh api -i user | rg '^X-Oauth-Scopes:.*\bworkflow\b'
git fetch --prune origin
for repo in genefoundry-router autopvs1-link clingen-link clinvar-link gencc-link genereviews-link gnomad-link gtex-link hgnc-link hpo-link litvar-link mavedb-link metadome-link mgi-link mondo-link orphanet-link panelapp-link pubtator-link spliceailookup-link stringdb-link uniprot-link vep-link; do
  gh api "repos/berntpopp/$repo" --jq '[.default_branch, .archived] | @tsv'
  gh api "repos/berntpopp/$repo/pulls?state=open&per_page=100" --paginate \
    --jq '.[] | select(.user.login == "dependabot[bot]" or .user.login == "app/dependabot") | [.number, .head.sha, .html_url] | @tsv'
done
```

Expected: the authenticated user has `repo` and `workflow` scopes; every configured repository
prints its default branch and either one or more Dependabot rows or an explicitly recorded zero;
the ledger records PR head SHA, actual patch, merged P0 state, and Action result rather than a
stale summary.

- [ ] **Step 2: Create one ignored worktree and branch per writer**

```bash
git -C /home/bernt-popp/development/clinvar-link check-ignore -q .worktrees
git -C /home/bernt-popp/development/clinvar-link worktree add \
  .worktrees/dependabot-mcp-20260719 -b chore/dependabot-mcp-20260719 origin/main
```

Expected: `git status --short --branch` is clean; no writer uses `main`, and no two writers share
a repository/worktree.

- [ ] **Step 3: Read local instructions before touching a backend**

```bash
sed -n '1,260p' /home/bernt-popp/development/clinvar-link/AGENTS.md
```

Expected: its documented test and lockfile workflow is known before edits.

### Task 2: Merge/revalidate the fourteen MCP security candidates

**Files:**
- Modify only the existing Dependabot PR branches or their intentional replacement branches
- Inspect: `pyproject.toml`, `uv.lock`, live FastMCP entrypoint tests

- [ ] **Step 1: Inspect the resolution and prove the fixed installed version**

Run in each of ClinVar, GeneReviews, HGNC, HPO, MaveDB, MetaDome, MGI, Mondo, Orphanet,
PanelApp, SpliceAI Lookup, STRINGdb, UniProt, and VEP:

```bash
gh pr checkout 30 --repo berntpopp/clinvar-link
git rev-parse HEAD
git diff --exit-code origin/main...HEAD -- pyproject.toml uv.lock
uv sync --group dev
uv run python -c 'import importlib.metadata as m; print(m.version("mcp"))'
uv run python -c 'import fastmcp, mcp; print(fastmcp.__file__); print(mcp.__file__)'
```

Expected: MCP is exactly 1.28.1; no direct MCP declaration is added when the lock-only update
already resolves it. The test worktree is checked out at the PR head (ClinVar #30 in the command;
use the recorded repository/PR mapping for every other candidate), never at vulnerable `main`.

- [ ] **Step 2: Run the repository's focused import and Streamable-HTTP contract smoke**

Use its existing FastMCP factory/registry test and documented transport probe. For example:

```bash
uv run pytest tests/conformance -q
make test-integration
```

Expected: imports and live registry construction succeed on resolved MCP 1.28.1. If a behavior
probe fails, invoke systematic debugging, preserve its reproduction, and do not merge.

- [ ] **Step 3: Run native full verification and refresh GitHub evidence**

```bash
make ci-local
gh pr update-branch 30 --repo berntpopp/clinvar-link
head_sha="$(gh pr view 30 --repo berntpopp/clinvar-link --json headRefOid --jq .headRefOid)"
gh run list --repo berntpopp/clinvar-link --commit "$head_sha" --limit 1 --json databaseId,status,conclusion --jq '.[0]'
gh run rerun "$(gh run list --repo berntpopp/clinvar-link --commit "$head_sha" --limit 1 --json databaseId --jq '.[0].databaseId')" --repo berntpopp/clinvar-link
gh pr checks 30 --repo berntpopp/clinvar-link --watch
```

Expected: format/lint/type/unit/integration/container scan/SBOM/conformance gates succeed; note an
unavailable local `actionlint` exception, but require the GitHub check to pass. The completed
workflow must be newer than reconciliation and attached to the displayed current `head_sha`; an
old green check is not merge evidence.

- [ ] **Step 4: Review then merge only a fresh clean PR**

Inspect the PR files and mergeability; retain a reviewer report. Merge only when all required
checks are current and successful:

```bash
gh pr view 30 --repo berntpopp/clinvar-link --json mergeStateStatus,statusCheckRollup,files
gh pr merge 30 --repo berntpopp/clinvar-link --merge --delete-branch
```

Expected: merge state is CLEAN and every required check is SUCCESS. A stale or pending check is a
stop condition, not a reason to override protection.

### Task 3: Unblock and merge Contract Truth one repository at a time

**Files:**
- Modify: existing Contract Truth branch only after its security merge
- Test: `tests/conformance/test_contract_truth_v1.py` plus repository gate

- [ ] **Step 1: Rebase the matching open Contract Truth branch after MCP merges**

```bash
git fetch origin
git switch feat/p0-contract-truth-20260718
git rebase origin/main
git status --short
```

Expected: lockfile conflicts are resolved by retaining the reviewed contract helper and the
main-resolved MCP record; no generated file is accepted without inspection.

- [ ] **Step 2: Verify the byte-pinned live-registry contract**

```bash
uv run pytest tests/conformance/test_contract_truth_v1.py -q
make ci-local
```

Expected: the helper hash matches, a live FastMCP catalog is used, and all native gates pass.

- [ ] **Step 3: Push, watch required checks, and merge in P0 order**

```bash
git push --force-with-lease origin HEAD:feat/p0-contract-truth-20260718
gh pr checks 31 --repo berntpopp/clinvar-link --watch
gh pr merge 31 --repo berntpopp/clinvar-link --merge --delete-branch
```

Expected: only fresh green actions permit merge. Do not claim Tool-Surface Budget adoption for
PubTator, and do not refresh a router fleet baseline from this branch.

### Task 4: Process remaining workflow, container, and library bot work

**Files:**
- Modify: exactly the Dependabot or replacement branch files shown in the inventory
- Test: existing workflow, container, import, CLI, and type tests

- [ ] **Step 1: Treat immutable pins as supply-chain changes**

For ClinGen #52/#54 and workflow replacements, inspect actual SHA/digest provenance and preserve
exact pins. For #53/#56, first add or update a failing assertion that names router commit
`2e27a1b`, then make the minimum test/configuration change and run the focused test before the full
gate. For GenCC #44/#45, push one reviewed replacement that pins both reusable workflows to
`2e27a1b`; only then comment with the replacement URL and close the two stale bot PRs.

- [ ] **Step 2: Validate direct application library upgrades**

For ClinGen #55 and GenCC #46, run import, command-line, and type checks before `make ci-local`.
For PubTator #137, read the official PyTorch 2.13 migration/release notes; add a failing
compatibility/import test for the exercised CPU/CUDA resolution path, implement only the required
compatibility change, run it passing, then run the full native gate.

- [ ] **Step 3: Verify VEP's failed dependency PR before changing it**

Use systematic debugging to reproduce VEP #27's behavior-probe failure with its exact command,
compare it to the green Contract Truth conformance run, and classify it as dependency-caused or
pre-existing. Do not merge, close, or rewrite the PR until that root cause and a fresh green
behavior probe exist.

### Task 5: Revalidate router truth only from released evidence

**Files:**
- Modify: router evidence only when an immutable published release supplies new data
- Test: router conformance and surface gates

- [ ] **Step 1: Run evidence-bound fleet checks**

```bash
make ci-local
make lint-surface
make test-integration
```

Expected: results identify their immutable release evidence. A local checkout, unpublished branch,
or untagged router 0.7.0 cannot be used to refresh the fleet baseline.

- [ ] **Step 2: Report issue state without overstating deployment**

Comment on #63 only with observed published runtime identity; keep its rollout ledger unadopted
until then. Comment on #68 with merged Contract Truth commits and retain PubTator's truthful
surface exception. Comment on #79 with the exact administrator-only precondition; do not alter
rulesets, environments, or tokens.

- [ ] **Step 3: Preserve rollback and final evidence**

Record merge commit, PR URL, local command/output, required GitHub checks, and any external-tool
exception for every repository. A post-merge defect uses a normal corrective PR/release rollback;
never rewrites tags, release evidence, or historical commits.
