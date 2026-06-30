# Fleet Stale-Branch Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal**: Ship a read-only branch classifier + a gated local-branch pruner + a remote-deletion runbook that delete ONLY branches proven (by `git cherry` patch-id) to already live in `main`, so the fleet's ~40 stale branches — including 17 conformance-reverting `chore/container-hardening-v1` branches — are removed without losing a single CI conformance gate.

**Architecture**: Two small Bash tools under `genefoundry-router/scripts/` plus one operator runbook. `audit_stale_branches.sh` walks every `-link` repo (derived from `servers.yaml`) and emits a TSV that classifies each non-`main` branch SAFE (0 unmerged patches) vs UNIQUE (carries work not in `main`), with a "behind-the-gate" column; `prune_merged_branches.sh` consumes that TSV and deletes ONLY cherry-proven-SAFE local branches (worktree-aware, with a delete-time re-check); the runbook handles the EXECUTION-GATED `origin` deletions (push `--delete`) after a per-repo supersession proof. Nothing is ever *merged*, so a gate can never be reverted.

**Tech Stack**: Bash (POSIX-ish, `set -euo pipefail`) + Git plumbing (`git cherry`, `git rev-list --count`, `git diff --numstat`, `git worktree list`); pytest fixtures (hermetic temp git repos) for verification; the router's existing `make ci-local` gate.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-`Authorization` passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

---

## Why the obvious tools are wrong (grounding)

Three commands were considered for "is this branch already in `main`?":

| Tool | What it compares | Verdict on the fleet |
|------|------------------|----------------------|
| `git log --oneline main..<branch>` / `git rev-list --count` | raw commit **reachability** (SHAs) | **over-counts.** Every fleet branch shows ≥1 "unique" commit because branches were squash- or rebase-merged, so the original SHAs never become reachable from `main`. |
| `git branch --merged main` | reachability of the branch **tip** | **misses squash-merges.** Verified live: `git -C clinvar-link branch --merged main` does **not** list `fix/mcp-path-direct` even though its content is fully in `main`. A squash-merge leaves no merge commit, so git cannot see the branch as merged. ([git-tower](https://www.git-tower.com/blog/how-to-clean-up-merged-feature-branches), [saveman71](https://saveman71.com/2025/cleaning-up-local-branches-after-squash-merges)) |
| `git cherry main <branch>` | per-commit **patch-id** (content diff) | **correct.** Marks `-` when an equivalent patch already exists in `main` (squash/rebase-safe), `+` when not. `0` lines of `+` ⇒ every change is in `main` ⇒ ref deletion loses nothing. ([git-scm cherry/patch-id](https://git-scm.com/docs/git-rebase), [LabEx](https://labex.io/tutorials/git-how-to-check-if-a-git-branch-is-fully-merged-into-main-560045)) |

So the classifier uses **`git cherry` as the authority** and keeps `git rev-list --count` only as an informational "look how badly reachability over-counts" column.

### The conformance-gate "footgun", measured precisely

`git diff --stat main..<branch> -- .github/workflows/conformance.yml tests/conformance` shows a **deletion** for *every* stale branch (e.g. `-240` lines). That is NOT because the branch actively removes the gate — it is because the gate was added to `main` *after* every one of these branches diverged, so the branch is simply **behind** it. Verified with a real 3-way merge simulation: `git merge-tree --write-tree main origin/chore/container-hardening-v1` in `pubtator-link` produces a tree that **still contains** all four gate files. A normal PR merge would KEEP the gate.

The gate is only lost under **force-style integration** (`git reset --hard <branch>`, force-push, `merge -X theirs`, or "restore this old branch"). The container-hardening branches additionally carry **superseded** content (older base-image digest, duplicate version bumps) that would regress `main` if reintroduced. Therefore the policy is: **never merge these branches — only delete the refs.** Deleting a ref is non-destructive to `main` and recoverable, so "zero conformance gates lost" is guaranteed by construction. The `GATE_DEL` column is informational: it flags "this branch predates the gate, do not be tempted to restore it."

### Ground-truth evidence captured 2026-06-30 (the table the script reproduces)

`origin/chore/container-hardening-v1` exists on exactly **17 repos**: autopvs1, clingen, clinvar, genereviews, gnomad, gtex, hgnc, hpo, mavedb, metadome, mgi, panelapp, pubtator, spliceailookup, stringdb, vep, **litvar**. (Correction to the audit brief: `uniprot-link` has **no** such branch on origin — already cleaned — and `litvar-link` **does**.) Local copies exist in 5 repos: clingen, gnomad, panelapp, pubtator, litvar. Supersession is proven: each repo's `main` carries a **newer** digest-pinned base (e.g. `python:3.14-slim@sha256:b877e5…`), the same-or-higher version (pubtator `main`==3.0.1==branch target; hgnc `main`==1.0.1), a `docker/docker-compose.prod.yml` overlay, AND the conformance gate the branch lacks; `main` tip (2026-06-29 23:17) post-dates every branch tip (17:38–19:23).

**SAFE (cherry: 0 unmerged) — 15 local branches, deletable by Task 2** (3 are checked out in `/tmp/wt/*` worktrees and must be released first): autopvs1 `fix/mcp-path-and-servername`; clingen `fix/mcp-path-direct`; clinvar `fix/mcp-path-direct`; genereviews `fix/serverinfo-consistency` **[WT]**; gtex `backup/pre-rebase-0ff2c24`; hpo `test/hpo-coverage-to-80`; panelapp `backup/pre-rebase-85e4199`, `chore/prod-read-only`; spliceailookup `fix/mcp-path-direct`; stringdb `chore/ruff-eof-newline`, `fix/serverinfo-consistency` **[WT]**; uniprot `fix/serverinfo-consistency` **[WT]**; vep `fix/mcp-path-direct`; litvar `fix/litvar-docker-mcp-unified`, `fix/mcp-path-direct`.

**UNIQUE — genuine unfinished work, NEVER auto-deleted** (owner must PR/rebase/abandon): clingen `feat/clingen-guidance-manifest` (6 patches), clingen `origin/data-refresh/snapshot`, genereviews `origin/dependabot/…setup-python-6.3.0`, gnomad `fix/mcp-path-direct` (re-implemented differently in `main`; cherry still `+`), gtex & panelapp `origin/feat/mcp-stateless-transport-reapply` (these *modify* the gate, `+14/-6`), litvar `fix/flaky-ratelimiter-timing` (local+origin), litvar `fix/litvar-entrypoint-reliability` (4 patches — valuable, per MEMORY "FIXED upstream, not pushed").

**UNIQUE but superseded — Task 3 manual-confirm tier (origin push-delete + 5 local)**: the 17 `chore/container-hardening-v1`.

---

## File Structure

Created:
- `scripts/audit_stale_branches.sh` — read-only classifier; emits a SAFE/UNIQUE TSV (one row per repo+branch) with patch-id, raw-SHA, gate-behind, and worktree columns. Repo list derived from `servers.yaml`.
- `scripts/prune_merged_branches.sh` — gated deleter; deletes ONLY local SAFE non-worktree branches via `git branch -D` after a delete-time `git cherry` re-check. Dry-run by default; `--execute` to act. Never touches `origin`, never touches UNIQUE.
- `tests/unit/test_audit_stale_branches.py` — pytest; builds a hermetic temp git repo whose topology mirrors the fleet (squash-merged, genuinely-new, predates-gate) and asserts classification + that the pruner deletes only the SAFE branch.
- `tests/unit/test_branch_cleanup_runbook.py` — pytest; structural assertions on the runbook (lists all 17 container-hardening repos, contains the supersession-proof command, the recovery command, and the EXECUTION-GATED banner).
- `docs/runbooks/2026-06-30-fleet-branch-cleanup.md` — operator runbook for the EXECUTION-GATED `origin` deletions (push `--delete`) + the 5 local container-hardening copies, gated behind a per-repo supersession proof, with recovery steps.

Modified: none. No `genefoundry_router/` module is touched, so the 600-LOC budget and `make lint-loc` are unaffected; the Bash scripts live under `scripts/` (outside the package the budget governs) and the tests under `tests/`.

---

### Task 1: `audit_stale_branches.sh` — cherry-based SAFE/UNIQUE classifier

**Files**
- Create: `scripts/audit_stale_branches.sh`
- Test: `tests/unit/test_audit_stale_branches.py::test_audit_classifies_squash_merge_as_safe`

**Interfaces**
- Consumes: env `DEV_ROOT` (default `/home/bernt-popp/development`), env `BASE` (default `main`), optional positional repo basenames; when none given, derives the 21 `-link` repos from `servers.yaml` (`repo: berntpopp/<repo>`).
- Produces: TSV on **stdout**, header `REPO\tSCOPE\tBRANCH\tCLASS\tUNMERGED\tSHA_UNIQ\tGATE_DEL\tWORKTREE`; `CLASS=SAFE` iff `UNMERGED==0` (from `git cherry`). Diagnostics (`skip: …`) on **stderr**. Exit 0 (read-only audit).

Steps:

- [ ] (1) Write the failing test. Create `tests/unit/test_audit_stale_branches.py` with the fixture builder and the classification assertion:

```python
"""Tests for scripts/audit_stale_branches.sh and scripts/prune_merged_branches.sh.

Builds a throwaway git repo whose branch topology mirrors the real fleet:
  * feature/squashed — squash-merged (content in main, SHA differs)      -> SAFE
  * feature/real     — a commit whose content is NOT in main             -> UNIQUE
  * chore/hardening  — predates the conformance gate, content not in main -> UNIQUE
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _make_fleet_repo(root: Path) -> str:
    name = "demo-link"
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")

    # main adds the conformance gate AFTER base.
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "conformance.yml").write_text("name: conformance\n")
    (repo / "tests" / "conformance").mkdir(parents=True)
    (repo / "tests" / "conformance" / "probe.py").write_text("def test_probe():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ci: add conformance gate")

    # feature/squashed: branch from base, change app.py, squash-merge into main.
    _git(repo, "checkout", "-q", "-b", "feature/squashed", base)
    (repo / "app.py").write_text("v2\n")
    _git(repo, "commit", "-qam", "feat: app v2")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "feature/squashed")
    _git(repo, "commit", "-qm", "feat: app v2 (squashed)")

    # feature/real: a commit whose content is NOT in main.
    _git(repo, "checkout", "-q", "-b", "feature/real", "main")
    (repo / "new.py").write_text("brand new\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "feat: genuinely new work")

    # chore/hardening: from base (predates gate), content not in main.
    _git(repo, "checkout", "-q", "-b", "chore/hardening", base)
    (repo / "Dockerfile").write_text("FROM python:3.12\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "chore: old hardening")

    _git(repo, "checkout", "-q", "main")
    return name


def _run(script: str, *args: str, dev_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script), *args],
        capture_output=True, text=True,
        env={"DEV_ROOT": str(dev_root), "BASE": "main", "PATH": os.environ["PATH"]},
    )


def _rows(stdout: str) -> dict[str, dict[str, str]]:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    header = lines[0].split("\t")
    return {
        cells["BRANCH"]: cells
        for cells in (dict(zip(header, ln.split("\t"))) for ln in lines[1:])
    }


def test_audit_classifies_squash_merge_as_safe(tmp_path: Path) -> None:
    name = _make_fleet_repo(tmp_path)
    res = _run("audit_stale_branches.sh", name, dev_root=tmp_path)
    assert res.returncode == 0, res.stderr
    rows = _rows(res.stdout)

    assert rows["feature/squashed"]["CLASS"] == "SAFE"
    assert rows["feature/squashed"]["UNMERGED"] == "0"
    assert int(rows["feature/squashed"]["SHA_UNIQ"]) >= 1   # raw count over-counts
    assert int(rows["feature/squashed"]["GATE_DEL"]) >= 1   # branch is behind the gate

    assert rows["feature/real"]["CLASS"] == "UNIQUE"
    assert int(rows["feature/real"]["UNMERGED"]) >= 1
    assert rows["chore/hardening"]["CLASS"] == "UNIQUE"
```

- [ ] (2) Run it, expect FAIL (script does not exist yet):
  `uv run pytest tests/unit/test_audit_stale_branches.py::test_audit_classifies_squash_merge_as_safe -q`
  Expected: `FileNotFoundError` / `errno 2` for `scripts/audit_stale_branches.sh` → **1 failed**.

- [ ] (3) Minimal implementation. Create `scripts/audit_stale_branches.sh` exactly:

```bash
#!/usr/bin/env bash
# audit_stale_branches.sh — read-only stale-branch classifier for the GeneFoundry -link fleet.
#
# For every repo+branch, against the repo's default branch (BASE, default "main"):
#   UNMERGED = patches whose CONTENT is NOT yet in BASE (git cherry; patch-id, squash/rebase-safe)
#   SHA_UNIQ = raw commit count BASE..branch            (git rev-list; over-counts after squash/rebase)
#   GATE_DEL = lines the branch is BEHIND on the conformance gate (git diff --numstat deletions)
#   WORKTREE = whether the branch is checked out in a linked worktree (cannot be -d/-D deleted)
# CLASS = SAFE   when UNMERGED == 0 (content fully in BASE -> ref deletion loses nothing)
#         UNIQUE when UNMERGED  > 0 (carries work not in BASE -> never auto-deleted)
#
# READ-ONLY. TSV to stdout (pipe to `column -t -s$'\t'` for humans); diagnostics to stderr.
# Research use only / not clinical decision support — see AGENTS.md.
set -euo pipefail

DEV_ROOT="${DEV_ROOT:-/home/bernt-popp/development}"
BASE="${BASE:-main}"
GATE_PATHS=(.github/workflows/conformance.yml tests/conformance)
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER_ROOT="$(dirname "$SELF_DIR")"

repos_from_yaml() {
  grep -oE 'repo:[[:space:]]*berntpopp/[a-z0-9-]+' "$ROUTER_ROOT/servers.yaml" \
    | sed -E 's#.*/##' | sort -u
}

REPOS=("$@")
if [ "${#REPOS[@]}" -eq 0 ]; then
  mapfile -t REPOS < <(repos_from_yaml)
fi

printf 'REPO\tSCOPE\tBRANCH\tCLASS\tUNMERGED\tSHA_UNIQ\tGATE_DEL\tWORKTREE\n'

for name in "${REPOS[@]}"; do
  repo="$DEV_ROOT/$name"
  [ -d "$repo/.git" ] || { echo "skip: $name (no git repo at $repo)" >&2; continue; }
  git -C "$repo" rev-parse --verify --quiet "$BASE" >/dev/null \
    || { echo "skip: $name (no $BASE branch)" >&2; continue; }

  wt_list="$(git -C "$repo" worktree list --porcelain 2>/dev/null \
    | awk '/^branch /{sub("refs/heads/","",$2); print $2}')"

  {
    git -C "$repo" for-each-ref --format='local%09%(refname:short)' refs/heads/
    git -C "$repo" for-each-ref --format='origin%09%(refname:short)' refs/remotes/origin/
  } | while IFS=$'\t' read -r scope br; do
    case "$br" in "$BASE"|"origin/$BASE"|origin/HEAD|origin|"") continue;; esac

    unmerged="$(git -C "$repo" cherry "$BASE" "$br" 2>/dev/null | grep -c '^+' || true)"
    sha_uniq="$(git -C "$repo" rev-list --count "$BASE..$br" 2>/dev/null || echo ERR)"
    gate_del="$(git -C "$repo" diff --numstat "$BASE..$br" -- "${GATE_PATHS[@]}" 2>/dev/null \
      | awk '{d+=$2} END{print d+0}')"

    cls="UNIQUE"; [ "$unmerged" = "0" ] && cls="SAFE"
    if printf '%s\n' "$wt_list" | grep -qxF -- "$br"; then wt="yes"; else wt="no"; fi

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$name" "$scope" "$br" "$cls" "$unmerged" "$sha_uniq" "$gate_del" "$wt"
  done
done
```

  Then `chmod +x scripts/audit_stale_branches.sh`.

- [ ] (4) Run, expect PASS:
  `uv run pytest tests/unit/test_audit_stale_branches.py::test_audit_classifies_squash_merge_as_safe -q`
  Expected: **1 passed**. Also smoke against the live fleet (read-only):
  `bash scripts/audit_stale_branches.sh | column -t -s$'\t' | sort`
  Expected: ~40 rows; the 17 `origin/chore/container-hardening-v1` rows show `CLASS=UNIQUE … GATE_DEL≥240`; the 15 SAFE rows match the evidence list above.

- [ ] (5) Commit:
  `git add scripts/audit_stale_branches.sh tests/unit/test_audit_stale_branches.py && git commit -m "feat(scripts): cherry-based stale-branch classifier (SAFE vs UNIQUE)"`

---

### Task 2: `prune_merged_branches.sh` — gated local deletion (dry-run default)

**Files**
- Create: `scripts/prune_merged_branches.sh`
- Test: `tests/unit/test_audit_stale_branches.py::test_pruner_dry_run_changes_nothing`, `::test_pruner_execute_deletes_only_safe`

**Interfaces**
- Consumes: stdout TSV of `audit_stale_branches.sh` (forwarding any repo args), env `DEV_ROOT`/`BASE`, flag `--execute`.
- Produces: side effects only — `git branch -D <branch>` on rows where `CLASS=SAFE AND SCOPE=local AND WORKTREE=no`, after a delete-time `git cherry` re-check. Dry-run prints `DRY-RUN would delete: …`; worktree rows print `SKIP (worktree): …`. Never deletes UNIQUE, never touches `origin`. Exit 0.

Steps:

- [ ] (1) Write the failing tests. Append to `tests/unit/test_audit_stale_branches.py`:

```python
def test_pruner_dry_run_changes_nothing(tmp_path: Path) -> None:
    name = _make_fleet_repo(tmp_path)
    before = _git(tmp_path / name, "branch", "--format=%(refname:short)")
    res = _run("prune_merged_branches.sh", name, dev_root=tmp_path)  # no --execute
    assert res.returncode == 0, res.stderr
    assert "DRY-RUN" in res.stdout
    after = _git(tmp_path / name, "branch", "--format=%(refname:short)")
    assert before == after


def test_pruner_execute_deletes_only_safe(tmp_path: Path) -> None:
    name = _make_fleet_repo(tmp_path)
    res = _run("prune_merged_branches.sh", "--execute", name, dev_root=tmp_path)
    assert res.returncode == 0, res.stderr
    remaining = set(
        _git(tmp_path / name, "branch", "--format=%(refname:short)").splitlines()
    )
    assert "feature/squashed" not in remaining          # cherry-proven merged -> deleted
    assert {"main", "feature/real", "chore/hardening"} <= remaining  # untouched
```

- [ ] (2) Run, expect FAIL (script missing):
  `uv run pytest tests/unit/test_audit_stale_branches.py -q -k pruner`
  Expected: `FileNotFoundError` for `scripts/prune_merged_branches.sh` → **2 failed**.

- [ ] (3) Minimal implementation. Create `scripts/prune_merged_branches.sh` exactly:

```bash
#!/usr/bin/env bash
# prune_merged_branches.sh — gated deletion of cherry-proven-merged LOCAL branches.
#
# Consumes audit_stale_branches.sh and deletes ONLY rows where
#   CLASS == SAFE  AND  SCOPE == local  AND  WORKTREE == no.
# It NEVER deletes UNIQUE branches, NEVER touches origin, and re-verifies patch
# equivalence at delete time. Dry-run by default; pass --execute to act.
#
# NOTE: uses `git branch -D` (force), not -d, on purpose: squash-merged branches are
# unreachable from main, so git's own -d reachability check refuses them. Our git-cherry
# patch-id proof is the compensating control; -D prints the SHA so deletion is recoverable
# via reflog. Research use only / not clinical decision support — see AGENTS.md.
set -euo pipefail

DEV_ROOT="${DEV_ROOT:-/home/bernt-popp/development}"
BASE="${BASE:-main}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

EXECUTE=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--execute" ]; then EXECUTE=1; else ARGS+=("$a"); fi
done

"$SELF_DIR/audit_stale_branches.sh" ${ARGS[@]+"${ARGS[@]}"} | tail -n +2 \
| while IFS=$'\t' read -r repo scope br cls unmerged sha_uniq gate_del wt; do
    [ "$cls" = "SAFE" ] && [ "$scope" = "local" ] || continue
    if [ "$wt" = "yes" ]; then
      echo "SKIP (worktree): $repo $br — 'git -C $DEV_ROOT/$repo worktree remove <path>' first, then re-run" >&2
      continue
    fi
    recheck="$(git -C "$DEV_ROOT/$repo" cherry "$BASE" "$br" 2>/dev/null | grep -c '^+' || true)"
    if [ "$recheck" != "0" ]; then
      echo "ABORT (drifted to UNIQUE since audit): $repo $br" >&2
      continue
    fi
    if [ "$EXECUTE" = "1" ]; then
      git -C "$DEV_ROOT/$repo" branch -D "$br"
    else
      echo "DRY-RUN would delete: $repo $br  (git -C $DEV_ROOT/$repo branch -D $br)"
    fi
  done
```

  Then `chmod +x scripts/prune_merged_branches.sh`.

- [ ] (4) Run, expect PASS:
  `uv run pytest tests/unit/test_audit_stale_branches.py -q`
  Expected: **3 passed**. Live dry-run smoke (read-only — prints, deletes nothing):
  `bash scripts/prune_merged_branches.sh`
  Expected: 12 `DRY-RUN would delete: …` lines + 3 `SKIP (worktree): …` lines (genereviews/stringdb/uniprot `fix/serverinfo-consistency`), zero UNIQUE branches listed.

- [ ] (5) Commit:
  `git add scripts/prune_merged_branches.sh tests/unit/test_audit_stale_branches.py && git commit -m "feat(scripts): gated local pruner for cherry-proven-merged branches"`

---

### Task 3: Operator runbook for EXECUTION-GATED `origin` deletions

**Files**
- Create: `docs/runbooks/2026-06-30-fleet-branch-cleanup.md`
- Test: `tests/unit/test_branch_cleanup_runbook.py::test_runbook_lists_all_hardening_repos_and_guards`

**Interfaces**
- Consumes: the two scripts from Tasks 1–2 + a per-repo supersession proof.
- Produces: a human runbook whose *execution* (push `--delete`) is EXECUTION-GATED and performed by an operator, not during plan implementation.

Steps:

- [ ] (1) Write the failing test. Create `tests/unit/test_branch_cleanup_runbook.py`:

```python
from __future__ import annotations

from pathlib import Path

RUNBOOK = (
    Path(__file__).resolve().parents[2]
    / "docs" / "runbooks" / "2026-06-30-fleet-branch-cleanup.md"
)

HARDENING_REPOS = [
    "autopvs1-link", "clingen-link", "clinvar-link", "genereviews-link",
    "gnomad-link", "gtex-link", "hgnc-link", "hpo-link", "mavedb-link",
    "metadome-link", "mgi-link", "panelapp-link", "pubtator-link",
    "spliceailookup-link", "stringdb-link", "vep-link", "litvar-link",
]


def test_runbook_lists_all_hardening_repos_and_guards() -> None:
    text = RUNBOOK.read_text()
    for repo in HARDENING_REPOS:
        assert repo in text, f"runbook missing repo {repo}"
    # supersession proof, remote-delete command, recovery, and the gate banner.
    assert "git ls-tree" in text and "conformance.yml" in text  # supersession proof
    assert "git push origin --delete chore/container-hardening-v1" in text
    assert "git push origin" in text and "refs/heads/chore/container-hardening-v1" in text  # recovery
    assert "EXECUTION-GATED" in text
    # uniprot-link must NOT be in the origin-delete set (already cleaned).
    assert "uniprot-link" not in text.split("## Out of scope")[0]
```

- [ ] (2) Run, expect FAIL (runbook missing):
  `uv run pytest tests/unit/test_branch_cleanup_runbook.py -q`
  Expected: `FileNotFoundError` → **1 failed**.

- [ ] (3) Minimal implementation. Create `docs/runbooks/2026-06-30-fleet-branch-cleanup.md`:

```markdown
# Fleet stale-branch cleanup runbook (2026-06-30)

> **EXECUTION-GATED.** Step 3 runs `git push origin --delete` (destructive remote op).
> Do NOT run it during plan implementation. Research use only; not clinical decision support.

## 0. Audit (read-only)
    bash scripts/audit_stale_branches.sh | column -t -s$'\t' | sort

## 1. Delete SAFE local branches (gated; auto)
    bash scripts/prune_merged_branches.sh            # dry-run, prints only
    bash scripts/prune_merged_branches.sh --execute  # git branch -D on SAFE local rows
Release the 3 worktree-held SAFE branches first, then re-run --execute:
    git -C /home/bernt-popp/development/genereviews-link worktree remove /tmp/wt/genereviews-link
    git -C /home/bernt-popp/development/stringdb-link    worktree remove /tmp/wt/stringdb-link
    git -C /home/bernt-popp/development/uniprot-link     worktree remove /tmp/wt/uniprot-link

## 2. Prove supersession of chore/container-hardening-v1 (per repo, read-only)
For each repo below, ALL THREE must hold on `main` before deleting the branch:
  (a) gate present:    git -C <repo> ls-tree -r --name-only main -- .github/workflows/conformance.yml
  (b) digest-pinned:   git -C <repo> show main:docker/Dockerfile | grep -m1 'FROM .*@sha256'
  (c) main newer:      git -C <repo> log -1 --format=%ci main   # > branch tip date
Reject the delete for any repo where (a) is empty.

## 3. EXECUTION-GATED — delete the 17 origin/chore/container-hardening-v1 branches
Repos (origin has the branch; uniprot-link is already clean and excluded):
autopvs1-link, clingen-link, clinvar-link, genereviews-link, gnomad-link, gtex-link,
hgnc-link, hpo-link, mavedb-link, metadome-link, mgi-link, panelapp-link, pubtator-link,
spliceailookup-link, stringdb-link, vep-link, litvar-link

    for r in autopvs1-link clingen-link clinvar-link genereviews-link gnomad-link \
             gtex-link hgnc-link hpo-link mavedb-link metadome-link mgi-link \
             panelapp-link pubtator-link spliceailookup-link stringdb-link vep-link litvar-link; do
      repo="/home/bernt-popp/development/$r"
      git -C "$repo" ls-tree -r --name-only main -- .github/workflows/conformance.yml \
        | grep -q conformance.yml || { echo "REFUSE $r: no gate on main"; continue; }
      echo "$r was $(git -C "$repo" rev-parse origin/chore/container-hardening-v1)"  # record for recovery
      git -C "$repo" push origin --delete chore/container-hardening-v1
    done

Delete the 5 LOCAL container-hardening copies (UNIQUE by cherry, so the pruner skips them —
delete only after the supersession proof above):
    for r in clingen-link gnomad-link panelapp-link pubtator-link litvar-link; do
      git -C "/home/bernt-popp/development/$r" branch -D chore/container-hardening-v1
    done

## Recovery (origin delete is reversible ~90 days)
    git -C <repo> push origin <recorded-sha>:refs/heads/chore/container-hardening-v1

## Out of scope — DO NOT DELETE (genuine unfinished work; owner decides PR/rebase/abandon)
clingen-link feat/clingen-guidance-manifest; clingen-link origin/data-refresh/snapshot;
genereviews-link origin/dependabot/github_actions/actions/setup-python-6.3.0;
gnomad-link fix/mcp-path-direct (re-implemented differently in main);
gtex-link & panelapp-link origin/feat/mcp-stateless-transport-reapply (these modify the gate);
litvar-link fix/flaky-ratelimiter-timing (local+origin); litvar-link fix/litvar-entrypoint-reliability.
```

- [ ] (4) Run, expect PASS:
  `uv run pytest tests/unit/test_branch_cleanup_runbook.py -q`
  Expected: **1 passed**.

- [ ] (5) Commit:
  `git add docs/runbooks/2026-06-30-fleet-branch-cleanup.md tests/unit/test_branch_cleanup_runbook.py && git commit -m "docs(runbook): gated origin deletion for superseded container-hardening branches"`

---

**Acceptance criteria**

- `uv run pytest tests/unit/test_audit_stale_branches.py tests/unit/test_branch_cleanup_runbook.py -q` → **4 passed**.
- `make ci-local` passes (format-check, lint-ci, lint-loc, typecheck, test-fast, test-integration) — no `genefoundry_router/` module changed, LOC budget untouched.
- `bash scripts/audit_stale_branches.sh` against the live fleet emits a TSV in which: every `origin/chore/container-hardening-v1` row is `CLASS=UNIQUE` with `GATE_DEL≥240`; exactly the 15 evidence branches are `CLASS=SAFE`; every genuine-work branch (e.g. `feat/clingen-guidance-manifest`, `fix/litvar-entrypoint-reliability`) is `CLASS=UNIQUE`.
- `bash scripts/prune_merged_branches.sh` (no flag) deletes nothing and prints 12 `DRY-RUN` + 3 `SKIP (worktree)` lines.
- After the operator runs the runbook, `git -C <repo> ls-tree -r --name-only main -- .github/workflows/conformance.yml` returns the gate for ALL 17 repos (zero gates lost), and `git -C <repo> branch -a | grep container-hardening-v1` is empty for all 17.

**Risk & rollback**

- **EXECUTION-GATED.** Task 3's runbook ends in `git push origin --delete chore/container-hardening-v1` across 17 repos — a destructive remote op. The plan's *implementation* (Tasks 1–3) only commits scripts/tests/docs locally; the push-delete is a separate, human-gated operation. Tasks 1–2 are non-destructive to remotes (Task 2 touches only local refs, behind dry-run-by-default + a delete-time cherry re-check).
- **False-positive guard:** a branch is deleted only if `git cherry` shows 0 unmerged patches — re-verified at delete time, so an audit that drifted stale aborts that branch.
- **Rollback (local):** `git branch -D` prints `Deleted branch X (was <sha>)`; restore via `git branch X <sha>` (reflog-recoverable). **Rollback (origin):** record `git rev-parse origin/chore/container-hardening-v1` before deleting; restore with `git push origin <sha>:refs/heads/chore/container-hardening-v1` (GitHub retains unreferenced objects ~90 days).
- **Gate-loss risk: none by construction** — branches are only ever *deleted*, never *merged*; `main` already holds the gate in all 17 repos.

**Effort**

~3–4 hours: ~1.5 h Task 1 (script + fixture harness), ~0.75 h Task 2, ~0.75 h Task 3 (runbook + test), ~0.5 h live dry-run smoke + `make ci-local`. The gated execution (operator running the runbook) is a further ~30 min, separately.
