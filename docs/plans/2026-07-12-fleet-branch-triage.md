# Fleet Unmerged-Branch Triage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development` to
> execute — one subagent per repo. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive every unmerged local branch across the router + 21 `-link` repos to a recorded
disposition — 1 merged+released (Codex-gated), 31 force-deleted with recorded rationale, 2 proven-merged
and `-d` — with a single `TRIAGE.md` and pruned worktrees. No deploy.

**Architecture:** Per-repo subagents. Each opens `TRIAGE.md`, and for every branch: (1) records the tip
SHA, (2) re-proves supersession with a fresh `git diff main...<branch>` against pristine `origin/main`,
(3) takes the recorded action (`-D`, `-d`, or finish-and-ship). Two branches get a dedicated Codex
`gpt-5.6-sol xhigh` gate: the one FINISH-AND-SHIP (metadome skills) and the one reversal
(litvar-entrypoint) whose deletion contradicts the brief. STOP-and-report on any red `ci-local`, merge
conflict, dirty tree, or unseen upstream commit.

**Tech Stack:** git, `make ci-local` (per repo), Codex CLI (`codex exec -s read-only -m gpt-5.6-sol -c
model_reasoning_effort=xhigh`).

## Global Constraints (verbatim from spec + AGENTS.md)

- Prove supersession by **content against current `main`**, not branch name. Re-diff before every `-D`.
- Never `-D` an unmerged branch without its tip SHA + rationale in `TRIAGE.md` **first**.
- Branch/rebase only off **pristine `origin/main`** (`git fetch origin && git rebase origin/main`).
- `git worktree remove` **without `--force`**; report dirty worktrees, do not clobber.
- No deploy. No push. No force-push. Merges to `main` are authorized (unprotected) but each
  FINISH-AND-SHIP needs Codex **SHIP** + green `make ci-local` first.
- STOP + report on any red `ci-local`, merge conflict, or unseen upstream commit on `main`.
- Scope: the 21 `-link` repos in `servers.yaml` + router. `omim-link` (no `.git`) and
  hnf1b/phentrieve/sysndd are out of scope.
- Research use only; mirror backend disclaimers. Codex is driven synchronously per
  [[workflow-codex-superpowers-authoring]] (foreground/background per the fleet loop), from a trusted
  git repo dir, extracting only the final VERDICT.

---

### Task 0: Pre-flight — pristine baseline + TRIAGE.md scaffold (router repo, orchestrator)

**Files:**
- Create: `genefoundry-router/scratchpad/TRIAGE.md` (working ledger; not committed to a `-link` repo)
- Reference: `scratchpad/inventory.sh`, `scratchpad/evidence*.sh` (session-recorded evidence)

- [ ] **Step 1: Confirm pristine `origin/main` on every in-scope repo.** For each repo run
  `git -C <repo> fetch origin -q && git -C <repo> rev-list --count origin/main..main` (expect `0`; a
  non-zero means local `main` is ahead of origin → STOP + report that repo) and
  `git -C <repo> status --porcelain` (expect clean except the two known strays). Record a one-line
  baseline per repo.
- [ ] **Step 2: Snapshot all tip SHAs.** Re-run `scratchpad/inventory.sh`; confirm the branch set still
  matches the spec's 34 (32 unmerged + 2 merged). If any branch appeared/vanished, add it to `TRIAGE.md`
  as `NEEDS-TRIAGE` and STOP for a fresh classification before touching it.
- [ ] **Step 3: Write the `TRIAGE.md` header + one pre-filled row per branch** (repo, branch, tip SHA,
  planned disposition, proof) from spec §3. Leave an `action_taken` column blank to fill during
  execution.

**Expected:** every repo clean + `main == origin/main`; `TRIAGE.md` has 34 rows. No branch touched yet.

---

### Task 1: FINISH-AND-SHIP — metadome `chore/agent-skills`

**Files:** `metadome-link/.claude/skills/*/SKILL.md` (8 files the branch adds).

- [ ] **Step 1: Rebase onto pristine origin/main.**
  `git -C metadome-link fetch origin -q && git -C metadome-link switch chore/agent-skills && git -C
  metadome-link rebase origin/main`. Expected: clean rebase (additive docs). On conflict → STOP + report.
- [ ] **Step 2: Confirm parity with the fleet baseline.** `ls metadome-link/.claude/skills/` shows the
  standard 8 backend skills (ci-failure-triage, code-quality-review, dependency-cve-sweep,
  fastapi-route-change, fleet-standard-adoption, mcp-tool-change, release-readiness, security-review);
  diff one SKILL.md body against a sibling repo (e.g. `clingen-link/.claude/skills/ci-failure-triage/
  SKILL.md`) to confirm the baseline text matches. Fix drift if the fleet body evolved.
- [ ] **Step 3: Green `make ci-local`.** Run `make -C metadome-link ci-local`. Expected: PASS (docs-only;
  watch `lint-loc` — SKILL.md is not Python, so the LOC budget is unaffected). On red → triage per
  `ci-failure-triage`, do not merge.
- [ ] **Step 4: Codex adversarial gate (background, read-only).**
  `codex exec -s read-only -m gpt-5.6-sol -c model_reasoning_effort=xhigh -C metadome-link "Adversarially
  verify branch chore/agent-skills is correct, complete, not superseded by current main, changes no
  success schema, adds only the fleet Agent-Skills baseline, and keeps make ci-local green. Report
  file:line + severity + SHIP/FIX." -o scratchpad/codex_metadome_verdict.txt < /dev/null`. Extract the
  final VERDICT. Merge bar = SHIP + green ci-local. On FIX → apply via the same implementer, re-run
  ci-local, re-gate only the blocking item.
- [ ] **Step 5: Merge + clean up.**
  `git -C metadome-link switch main && git -C metadome-link merge --no-ff chore/agent-skills -m "chore(skills): adopt fleet Agent-Skills baseline"`
  then `git -C metadome-link branch -d chore/agent-skills`. Record `MERGED` + merge SHA in `TRIAGE.md`.
  No version bump (docs-only); no deploy.

**Expected:** metadome `main` now carries the 8-skill baseline (fleet parity); branch deleted via `-d`.

---

### Task 2: Gated reversal — litvar `fix/litvar-entrypoint-reliability` (Codex-confirm, then `-D`)

This branch is the brief's flagged "genuine never-deployed fix". Content shows it is superseded (PR #35).
Because deleting it contradicts the brief, gate the `-D` behind an independent Codex confirm.

- [ ] **Step 1: Record tip SHA + re-diff.** `git -C litvar-link rev-parse fix/litvar-entrypoint-reliability`
  (= `2a8c070`). `git -C litvar-link diff main...fix/litvar-entrypoint-reliability --stat`. Confirm `main`
  log carries `a1bd540` (`…entrypoint reliability (#20)(#35)`) and the 4 markers (`client.py` percent-encode,
  `variant_service.py` canonical-id resolve, `literature.py` rsID/HGVS, recoverable not-found).
- [ ] **Step 2: Codex supersession-confirm (background, read-only).**
  `codex exec -s read-only -m gpt-5.6-sol -c model_reasoning_effort=xhigh -C litvar-link "The local branch
  fix/litvar-entrypoint-reliability (tip 2a8c070, merge-base 2026-06-16) predates current main by ~37
  commits. Determine whether current main already implements ALL behaviors this branch fixes (LitVar id
  percent-encoding of '#'/'@', rsID/HGVS->canonical-id resolution before publications, recoverable
  'variant not found' mapping, PMID str-coercion). Report per-fix file:line on main + a single verdict:
  SUPERSEDED (safe to delete) or LIVE (branch carries work main lacks — name it). Do not modify files." -o
  scratchpad/codex_litvar_entrypoint_verdict.txt < /dev/null`.
- [ ] **Step 3: Branch on the verdict.**
  - **SUPERSEDED** → record SHA `2a8c070` + "superseded by PR #35 (a1bd540); Codex-confirmed" in
    `TRIAGE.md`, then `git -C litvar-link branch -D fix/litvar-entrypoint-reliability`.
  - **LIVE** → do NOT delete. Reclassify as FINISH-AND-SHIP, record what `main` lacks, and STOP for
    operator direction (this would re-open the brief's original intent).

**Expected:** Codex confirms SUPERSEDED (per the collected evidence) → branch `-D` with recorded rationale.

---

### Task 3: Fully-merged branches + worktree prune (genereviews, vep)

- [ ] **Step 1: genereviews worktree.** `git -C genereviews-link worktree remove
  ../.worktrees/genereviews-link/release-strict-host-origin-4.0.0`. It is **dirty (1 file)** → the command
  refuses. Inspect: `git -C <worktree> status --porcelain`. If the sole file is a throwaway build/junk
  artifact, note it in `TRIAGE.md` and re-run with the file removed; if it looks like real work, **leave
  the worktree and report** — do not `--force`.
- [ ] **Step 2: genereviews merged branch.** Once the worktree is detached,
  `git -C genereviews-link branch -d chore/release-strict-host-origin-4.0.0` (uses `-d` — proves merged;
  ahead=0). Record `-d (merged)`.
- [ ] **Step 3: vep worktree.** `git -C vep-link worktree remove
  ../.worktrees/vep-link/release-strict-host-origin-2.0.0`. **Dirty (4 files)** → same rule: inspect,
  report, no `--force`.
- [ ] **Step 4: vep merged branch.** `git -C vep-link branch -d chore/release-strict-host-origin-2.0.0`.
  Record `-d (merged)`.

**Expected:** both merged branches gone via `-d`; worktrees removed if trivially dirty, else reported.

---

### Tasks 4–19: DELETE-SAFE sweeps — one subagent per repo

For each repo below, the subagent runs the **same procedure** (repeat, don't reference):
1. `git -C <repo> fetch origin -q`; confirm `main == origin/main` and clean tree (except known strays).
2. For each listed branch: `git -C <repo> rev-parse <branch>` (record tip SHA);
   `git -C <repo> diff main...<branch> --stat` + a targeted check of the spec §3.3 proof marker on `main`
   (grep the cited file:line); write the `TRIAGE.md` row (repo, branch, SHA, classification, proof,
   `action_taken=-D`).
3. `git -C <repo> branch -D <branch>`.
STOP + report if any branch's content is NOT superseded (i.e. `main` lacks the change) — reclassify.

- [ ] **Task 4 — autopvs1:** `fix/mcp-path-and-servername` (`d141b5c`). Proof: `server_manager.py:103`
  mount `/` + `server_info.py:20 SERVER_NAME="autopvs1-link"` on `main`.
- [ ] **Task 5 — clingen:** `chore/container-hardening-v1` (`06deeee`), `fix/mcp-path-direct` (`9ac8045`).
  Proof: digest-pin + `container-security.yml` + CORS on `main`; `server_manager.py:150` mount `/`.
- [ ] **Task 6 — clinvar:** `ci/data-bundle-workflow` (`7f85d8c`), `fix/mcp-path-direct` (`47af1a3`).
  Proof: `.github/workflows/data-bundle.yml` on `main`; `server_manager.py:174` mount `/`.
- [ ] **Task 7 — gencc:** `fix/dependabot-base-format` (`aff4134`). Proof: STALE/REGRESSIVE — two-dot
  `git diff main..fix/dependabot-base-format` shows it would drop `GENCC_LINK_ALLOWED_HOSTS/ORIGINS` and
  downgrade `setup-uv`. `-D`.
- [ ] **Task 8 — genereviews:** `fix/live-corpus-version` (`6aa425d`), `fix/search-live-corpus-version`
  (`313ed7c`), `fix/serverinfo-consistency` (`82cec74`), `fix/serverinfo-version` (`588d720`). Proof:
  `orchestration.py:29` live stamping + `server_manager.py:299 name="genereviews-link"` + versioning
  standard (5.0.2) on `main`. Also `rm` the stray `-f` file (Step in Task 20).
- [ ] **Task 9 — gnomad:** `chore/container-hardening-v1` (`144d80b`), `fix/dependabot-base-format`
  (`a64320b`, STALE/REGRESSIVE), `fix/mcp-path-direct` (`28ae031`). Proof: hardening on `main`;
  drops `MCP_ALLOWED_HOSTS/ORIGINS`; `server_manager.py:133` mount `/`.
- [ ] **Task 10 — gtex:** `backup/pre-rebase-0ff2c24` (`0ff2c24`). Proof: THROWAWAY; GENCODE work on `main`
  via PR #54 (`4cc17d4`). `-D`.
- [ ] **Task 11 — hpo:** `test/hpo-coverage-to-80` (`12ad61d`). Proof: 3 test files on `main`; two-dot diff
  shows branch reverts 4000+ LOC. `-D`.
- [ ] **Task 12 — litvar (remaining):** `chore/container-hardening-v1` (`b74468c`),
  `fix/litvar-docker-mcp-unified` (`04b756a`), `fix/mcp-path-direct` (`83f91a5`). Proof: hardening on
  `main`; compose `serve unified` (`docker/docker-compose.yml:41`); `server_manager.py:80` mount `/`.
  (litvar-entrypoint handled in Task 2.)
- [ ] **Task 13 — panelapp:** `backup/pre-rebase-85e4199` (`85e4199`, THROWAWAY),
  `chore/container-hardening-v1` (`063eed1`), `chore/prod-read-only` (`cb210e9`). Proof: hardening +
  `docker-compose.prod.yml:38 read_only: true` + tmpfs on `main`.
- [ ] **Task 14 — pubtator:** `chore/container-hardening-v1` (`11b45b1`). Proof: digest-pin +
  `container-security.yml` on `main`.
- [ ] **Task 15 — spliceailookup:** `fix/mcp-path-direct` (`6b4757f`), `pr-13-review` (`a6d1db0`,
  THROWAWAY). Proof: `server_manager.py:151` mount `/`; Response-Envelope v1 on `main` via PR #13
  (`5356391`).
- [ ] **Task 16 — stringdb:** `chore/ruff-eof-newline` (`8de4ecd`, STALE/REGRESSIVE — would delete
  `.claude/skills`), `fix/serverinfo-consistency` (`f081c11`). Proof: `config_models.py`
  `server_name="stringdb-link"` on `main`.
- [ ] **Task 17 — uniprot:** `fix/serverinfo-consistency` (`2bc9ce2`). Proof: compose `published :-8013`
  already on `main` (`docker/docker-compose.yml:24`); serverInfo standardized. `-D`.
- [ ] **Task 18 — vep:** `fix/mcp-path-direct` (`b26c86a`), `fix/serverinfo-version` (`a0b9aa5`),
  `pr-9-review` (`4371573`, THROWAWAY). Proof: `server_manager.py:191` mount `/`; versioning standard;
  Response-Envelope v1 on `main` via PR #9 (`5762f08`).
- [ ] **Task 19 — reconcile:** verify every DELETE-SAFE row in `TRIAGE.md` has `action_taken` filled and
  `git -C <repo> for-each-ref refs/heads/` shows only `main` for each swept repo.

---

### Task 20: Housekeeping — stray files

- [ ] **Step 1: genereviews stray `-f`.** Confirm 0-byte + untracked: `git -C genereviews-link status
  --porcelain` shows `?? -f`. Remove: `rm -- genereviews-link/-f`. Record in `TRIAGE.md`.
- [ ] **Step 2: clinvar `.sha256`.** `git -C clinvar-link status --porcelain` shows `??
  data/clinvar.sqlite.zst.sha256` — a legitimate data-bundle checksum. **Leave it**; note "kept — data
  artifact" in `TRIAGE.md`.

---

### Task 21: Final ledger + memory

- [ ] **Step 1: Finalize `TRIAGE.md`.** Every branch has a disposition row; add a summary block
  (1 merged, 31 `-D`, 2 `-d`, worktrees status). Save it to the router repo
  (`docs/` or keep in `scratchpad/` per operator preference).
- [ ] **Step 2: Update memory.** Write/refresh a memory note: what shipped (metadome skills baseline —
  fleet parity now 21/21), what was dropped (31 superseded/throwaway/stale branches), the litvar-entrypoint
  reversal (brief believed live; content-proven superseded by PR #35), worktrees pruned/reported. Link
  [[fleet-agent-skills-baseline-2026-07-06]], [[mcp-transport-standard-adoption]],
  [[fleet-modernization-2026-07-10]]. Add the index line to `MEMORY.md`.
- [ ] **Step 3: Report.** Summarize dispositions + any STOP/report items (dirty worktrees left in place,
  any branch reclassified LIVE) for the operator. No deploy — operator owns redeploy.

---

## Self-Review

- **Spec coverage:** every branch in spec §3.1–3.3 maps to a task (metadome→T1; litvar-entrypoint→T2;
  merged pair→T3; the 31 DELETE-SAFE→T4–T18; reconcile→T19; strays→T20; ledger/memory→T21). ✓
- **Placeholders:** none — every delete carries an exact tip SHA + a `main` proof marker; the two Codex
  gates carry full prompts. ✓
- **Consistency:** tip SHAs and proof markers match spec §3 verbatim; `-d` used only for the two
  `ahead==0` branches, `-D` for all unmerged. ✓
- **Destructive-op safety:** every `-D` is preceded by SHA-record + fresh content re-diff; worktrees use
  `remove` without `--force`; the one belief-contradicting delete (litvar) is Codex-gated. ✓
