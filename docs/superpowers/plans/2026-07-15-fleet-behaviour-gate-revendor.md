# Fleet Behaviour Gate Re-Vendor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-vendor router behaviour gate blob `30d639242b700e556abf41be620172e1f3d497ec` into the
21 in-scope GeneFoundry backends and prove every backend's `main` conformance gate is green.

**Architecture:** Use the router gate as the byte-identity source, process relaxation-only repos
mechanically, process under-gated repos sequentially with live local Docker behaviour validation, and
gate all merges with GitHub check-runs API on the exact head SHA.

**Tech Stack:** Git, GitHub CLI/API, uv, pytest, Docker Compose, Make targets already present in each
backend, Codex CLI for adversarial review where stricter live validation requires runtime fixes.

---

## File Map

- Source: `/home/bernt-popp/development/genefoundry-router/docs/conformance/behaviour.py`
- Backend gate target for each repo in `AUDIT_REPOS`: `tests/conformance/behaviour.py`
- Backend changelog target for each repo in `SAFE_REPOS` and `UNDER_REPOS`: `CHANGELOG.md`
- Backend live workflow reference for each under-gated repo: `.github/workflows/conformance.yml`
- Do not edit: backend `tests/conformance/conformance.py`
- Do not edit: backend `tests/conformance/test_behaviour_v1.py`

## Shared Shell Variables

Run from `/home/bernt-popp/development/genefoundry-router`:

```bash
CANONICAL_BLOB=30d639242b700e556abf41be620172e1f3d497ec
CANONICAL_REF=ba09fdc:docs/conformance/behaviour.py
BASE=/home/bernt-popp/development
SAFE_REPOS="hpo-link panelapp-link mondo-link gtex-link uniprot-link autopvs1-link hgnc-link genereviews-link mgi-link gencc-link metadome-link gnomad-link stringdb-link vep-link spliceailookup-link mavedb-link"
UNDER_REPOS="clinvar-link orphanet-link clingen-link litvar-link"
AUDIT_REPOS="$SAFE_REPOS $UNDER_REPOS pubtator-link"
```

### Task 1: Preflight The Source And Local State

**Files:** read-only inspection

- [ ] **Step 1: Verify the canonical router blob.**

Run:

```bash
git rev-parse "$CANONICAL_REF"
```

Expected:

```text
30d639242b700e556abf41be620172e1f3d497ec
```

- [ ] **Step 2: Verify all in-scope local repositories exist.**

Run:

```bash
for repo in $AUDIT_REPOS; do test -d "$BASE/$repo/.git" || echo "MISSING $repo"; done
```

Expected: no output.

- [ ] **Step 3: Record dirty state before changing branches.**

Run:

```bash
for repo in $AUDIT_REPOS; do
  echo "## $repo"
  git -C "$BASE/$repo" status --short
done
```

Expected: clean for all repos except known preserved local state; do not delete or reset user work.

### Task 2: Re-Vendor One Safe Repo

**Files:**

- Modify: `tests/conformance/behaviour.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Start from current main.**

Run:

```bash
repo=hpo-link
cd "$BASE/$repo"
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c chore/revendor-behaviour-gate-ba09fdc
```

Expected: branch created from current `origin/main`.

- [ ] **Step 2: Copy the canonical gate.**

Run:

```bash
git --git-dir=/home/bernt-popp/development/genefoundry-router/.git show "$CANONICAL_REF" > tests/conformance/behaviour.py
git rev-parse :tests/conformance/behaviour.py 2>/dev/null || true
```

Expected before staging: working tree has only `tests/conformance/behaviour.py` modified.

- [ ] **Step 3: Add the changelog note at the top of `CHANGELOG.md`.**

Insert this entry below the title or current Unreleased heading, preserving the repo's existing
changelog style:

```markdown
- Re-vendored the behaviour conformance gate from genefoundry-router `ba09fdc`
  (`docs/conformance/behaviour.py` blob `30d639242b`) so live MCP contract checks treat
  not-found example probes as inconclusive instead of failures.
```

- [ ] **Step 4: Run local CI.**

Run:

```bash
make ci-local
```

Expected: exit 0.

- [ ] **Step 5: Commit and push.**

Run:

```bash
git add tests/conformance/behaviour.py CHANGELOG.md
git commit -m "chore: re-vendor behaviour conformance gate"
git push -u origin chore/revendor-behaviour-gate-ba09fdc
```

Expected: commit contains only the gate file and changelog.

### Task 3: Repeat Safe Repos With Capped Concurrency

**Files:** same as Task 2 for each safe repo

- [ ] **Step 1: Apply Task 2 to every safe repo.**

Use these repos:

```text
hpo-link panelapp-link mondo-link gtex-link uniprot-link autopvs1-link hgnc-link genereviews-link mgi-link gencc-link metadome-link gnomad-link stringdb-link vep-link spliceailookup-link mavedb-link
```

Expected: each branch has exactly `tests/conformance/behaviour.py` plus `CHANGELOG.md` changed.

- [ ] **Step 2: Keep local validation concurrency below six.**

Run no more than four `make ci-local` jobs at once on this host. If memory pressure appears, switch
to sequential validation.

### Task 4: Open Safe PRs And Gate Merges With API

**Files:** GitHub PR metadata only

- [ ] **Step 1: Open a PR for each safe repo.**

Run from each repo:

```bash
gh pr create --fill --base main --head chore/revendor-behaviour-gate-ba09fdc
```

Expected: PR URL printed.

- [ ] **Step 2: Get the exact PR head SHA.**

Run:

```bash
head_sha=$(gh pr view --json headRefOid -q .headRefOid)
echo "$head_sha"
```

Expected: the commit SHA for the PR branch.

- [ ] **Step 3: Verify the PR conformance check through the API.**

Run:

```bash
repo_name=$(basename "$PWD")
gh api "repos/berntpopp/$repo_name/commits/$head_sha/check-runs" \
  --jq '.check_runs[] | select(.name | test("onformance"; "i")) | {name, status, conclusion}'
```

Expected: at least one conformance check-run with `status` `completed` and `conclusion` `success`.
If no conformance check is present, wait and query again.

- [ ] **Step 4: Merge only after conformance success.**

Run:

```bash
gh pr merge --squash --delete-branch
```

Expected: PR merged.

- [ ] **Step 5: Verify main conformance after merge.**

Run:

```bash
main_sha=$(git ls-remote origin refs/heads/main | awk '{print $1}')
gh api "repos/berntpopp/$repo_name/commits/$main_sha/check-runs" \
  --jq '.check_runs[] | select(.name | test("onformance"; "i")) | {name, status, conclusion}'
```

Expected: conformance check-run on `main_sha` is `success`.

### Task 5: Re-Vendor And Live-Validate One Under-Gated Repo

**Files:**

- Modify: `tests/conformance/behaviour.py`
- Modify: `CHANGELOG.md`
- Potentially modify backend runtime and unit tests only if live behaviour conformance fails.

- [ ] **Step 1: Start from current main and branch.**

Run:

```bash
repo=clinvar-link
cd "$BASE/$repo"
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c chore/revendor-behaviour-gate-ba09fdc
```

Expected: branch created from current `origin/main`.

- [ ] **Step 2: Copy the canonical gate and changelog note.**

Run the same copy and changelog steps from Task 2.

- [ ] **Step 3: Read the live conformance workflow for server identity and port.**

Run:

```bash
sed -n '1,160p' .github/workflows/conformance.yml
```

Expected: `CONFORMANCE_NAME` and `MCP_PORT` are visible. Current under-gated values:

```text
clinvar-link   CONFORMANCE_NAME=clinvar-link   MCP_PORT=8000
orphanet-link  CONFORMANCE_NAME=orphanet-link  MCP_PORT=8000
clingen-link   CONFORMANCE_NAME=clingen-link   MCP_PORT=8479
litvar-link    CONFORMANCE_NAME=litvar-link    MCP_PORT=8000
```

- [ ] **Step 4: Run local CI.**

Run:

```bash
make ci-local
```

Expected: exit 0.

- [ ] **Step 5: Run live behaviour conformance sequentially.**

Run:

```bash
make docker-down || true
make docker-build
make docker-up
case "$(basename "$PWD")" in
  clinvar-link) name=clinvar-link; port=8000 ;;
  orphanet-link) name=orphanet-link; port=8000 ;;
  clingen-link) name=clingen-link; port=8479 ;;
  litvar-link) name=litvar-link; port=8000 ;;
  *) echo "unexpected repo $(basename "$PWD")" >&2; exit 2 ;;
esac
CONFORMANCE_NAME="$name" CONFORMANCE_MCP_URL="http://127.0.0.1:$port" uv run pytest tests/conformance/test_behaviour_v1.py -v
make docker-down
```

Expected: exit 0. If the live probe fails or reports UNGATED, continue to Task 6 for this repo.

### Task 6: Fix Any Under-Gated Live Failure TDD-Style

**Files:** specific to the repo and concrete live failure

- [ ] **Step 1: Stop and write the repo-specific mini-plan.**

Before editing runtime code, capture the exact failing behaviour probe line and write a short
repo-specific checklist in `docs/superpowers/plans/2026-07-15-REPO-behaviour-gate-fix.md` in this
router repo, replacing `REPO` with the repository name. The checklist must name the exact backend
runtime file and exact test file after inspecting the failure path. Do not use this generic fleet
plan to guess runtime files.

- [ ] **Step 2: Write a focused failing regression test.**

Add a unit or integration test that reproduces the live conformance defect without relying on a
network secret. The assertion must encode the contract violation reported by the gate, such as an
invalid closed-vocabulary filter returning `success: true`, a lying `total_count`, missing
`isError`, or an ungated required parameter with no examples.

- [ ] **Step 3: Run the focused test red.**

Run:

```bash
uv run pytest tests/unit -v
```

Expected: the new regression fails for the contract reason.

- [ ] **Step 4: Implement the minimal backend fix.**

Change only the runtime path required by the failing test.

- [ ] **Step 5: Run the focused test green, then local CI.**

Run:

```bash
uv run pytest tests/unit -v
make ci-local
```

Expected: both exit 0.

- [ ] **Step 6: Rerun live behaviour conformance.**

Run the live command from Task 5.

Expected: exit 0.

- [ ] **Step 7: Commit.**

Run:

```bash
git status --short
git add tests/conformance/behaviour.py CHANGELOG.md
git add $(git status --short | awk '/^( M|A |\\?\\?) / {print $2}' | grep -E '^(clinvar_link|orphanet_link|clingen_link|litvar_link|tests)/')
git diff --cached --stat
git commit -m "fix: satisfy current behaviour conformance gate"
```

Expected: one focused commit.

### Task 7: Codex Review For Under-Gated Runtime Fix PRs

**Files:** review artifact only

- [ ] **Step 1: Open the PR.**

Run:

```bash
gh pr create --fill --base main --head chore/revendor-behaviour-gate-ba09fdc
```

Expected: PR URL printed.

- [ ] **Step 2: Run one adversarial Codex review if runtime code changed.**

Run the local review harness if present:

```bash
pr_number=$(gh pr view --json number -q .number)
review2.sh "$PWD" "$pr_number" 0 "Review the current behaviour-gate re-vendor and any runtime rework for missed MCP contract regressions."
```

If `review2.sh` is unavailable, run:

```bash
pr_number=$(gh pr view --json number -q .number)
codex exec -m gpt-5.6-sol -c model_reasoning_effort=high --sandbox read-only "Review PR ${pr_number} in $(basename "$PWD") for MCP behaviour conformance regressions. Return APPROVE or REQUEST-CHANGES first."
```

Expected: verdict `APPROVE` or actionable `REQUEST-CHANGES`.

- [ ] **Step 3: Rework any requested changes, then rerun local and live validation.**

Expected: local CI and live behaviour conformance pass after rework.

### Task 8: Merge Under-Gated PRs With API Gate

**Files:** GitHub PR metadata only

- [ ] **Step 1: Verify PR conformance by check-runs API.**

Use Task 4's API query for the exact PR head SHA.

Expected: conformance check-run is `success`.

- [ ] **Step 2: Merge the PR.**

Run:

```bash
gh pr merge --squash --delete-branch
```

Expected: PR merged.

- [ ] **Step 3: Verify main conformance by check-runs API.**

Use Task 4's main SHA query.

Expected: conformance check-run on `main` is `success`.

### Task 9: Final Fleet Audit

**Files:** read-only inspection

- [ ] **Step 1: Verify byte identity on all 21 backend main branches.**

Run:

```bash
for repo in $AUDIT_REPOS; do
  blob=$(git -C "$BASE/$repo" rev-parse origin/main:tests/conformance/behaviour.py)
  test "$blob" = "$CANONICAL_BLOB" && echo "OK $repo $blob" || echo "BAD $repo $blob"
done
```

Expected: every line starts with `OK`.

- [ ] **Step 2: Verify every main conformance check is green by API.**

Run:

```bash
for repo in $AUDIT_REPOS; do
  sha=$(git -C "$BASE/$repo" ls-remote origin refs/heads/main | awk '{print $1}')
  echo "## $repo $sha"
  gh api "repos/berntpopp/$repo/commits/$sha/check-runs" \
    --jq '.check_runs[] | select(.name | test("onformance"; "i")) | {name, status, conclusion}'
done
```

Expected: each repo has a completed conformance check-run with conclusion `success`.
