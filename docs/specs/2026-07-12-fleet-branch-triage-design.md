# Fleet Unmerged-Branch Triage (GeneFoundry `-link` fleet + router)

- Status: DRAFT — pending operator review
- Author: release/security engineering (Claude Fable 5, session aed790b7)
- Date: 2026-07-12
- Boundary: Research use only. Not clinical decision support. Mirror backend disclaimers.
  **No deploy** — operator owns redeploy and live probes.

## 1. Goal

Every local branch across the router and the 21 in-scope `-link` repos that is **not merged into its
repo's `main`** is triaged to a single, evidence-backed disposition: **FINISH-and-ship**,
**DELETE-safe**, or **ESCALATE**. Deleting an unmerged local branch is a destructive `-D` (the branch
was never pushed), so each deletion is gated on a recorded tip SHA + a *content* proof that its work is
already on `main` or is throwaway. The output is one `TRIAGE.md` summarizing each branch's disposition,
pruned worktrees, and a memory note of what shipped and what was dropped.

Scope is exactly the router-federated fleet (`servers.yaml`) per [[router-fleet-scope]]:
`genefoundry-router` + `autopvs1 clingen clinvar gencc genereviews gnomad gtex hgnc hpo litvar mavedb
metadome mgi mondo orphanet panelapp pubtator spliceailookup stringdb uniprot vep`-link. `omim-link`
is local-only (no `.git`) and out of scope; `hnf1b`/`phentrieve`/`sysndd` are not `-link` fleet.

## 2. Method — prove supersession by content, not by branch name

The decisive lens is the set of fleet standards already landed on `main` (from memory):
MCP-Transport-v1 (single `/mcp`, no 307 — `app.mount("/", mcp_app)`), Versioning/serverInfo
(package version + canonical `serverInfo.name = <ns>-link`), Container-Hardening-v1 (digest-pinned
base, `container-security.yml`, CORS-no-creds-on-wildcard, read-only rootfs), Response-Envelope-v1,
and the fleet Agent-Skills baseline. A branch whose name matches one of these is **presumed** superseded
but is only classified so after a **content check against current `main`** (does `main` already carry
the change?). Regenerated inventory + evidence sweeps are recorded in the session scratchpad
(`inventory.sh`, `evidence.sh`, `evidence2.sh` and their outputs).

### 2.1 Decision rubric (most-conservative wins)

- **DELETE-SAFE** — record tip SHA + rationale in `TRIAGE.md`, then `git branch -D`:
  (a) *throwaway* (`backup/pre-rebase-*`, `pr-*-review`); (b) *superseded* — the branch's net change
  is already on `main` via another path, proven by diffing/grepping `main`; (c) *stale/regressive* —
  merging it would revert `main` (older workflow pins, dropped config lines, deletes files `main` now
  has).
- **FULLY-MERGED** — `ahead == 0`. Prove with `git branch -d` (fails if not merged). Remove any
  attached worktree first.
- **FINISH-AND-SHIP** — real, still-relevant work not on `main` and not superseded. Rebase onto
  pristine `origin/main`, complete (TDD), `make ci-local` green, appropriate version bump, Codex
  adversarial gate, merge. No deploy.
- **ESCALATE** — genuinely ambiguous / partially-superseded / would-be-breaking / touches security
  posture with no clear call. Surface with a recommendation; do not guess.

## 3. Findings — the whole fleet is triaged (34 branches, 16 repos with branches)

**Regenerated 2026-07-12; supersedes the drifted snapshot in the task brief.** Counts:
**32 unmerged** + **2 fully-merged** branches. Disposition: **1 FINISH-AND-SHIP**, **31 DELETE-SAFE**,
**2 fully-merged (`-d`)**, **0 ESCALATE**.

### 3.1 The one FINISH-AND-SHIP

| Repo | Branch | Tip | Why it is live work |
|------|--------|-----|---------------------|
| metadome-link | `chore/agent-skills` | `472054c` | **Sole fleet laggard.** Every other `-link` repo has the Agent-Skills baseline on `main` (8–9 `.claude/skills/*/SKILL.md`); metadome has **0**. The branch adds the standard 8-skill backend baseline (additive docs, +185 LOC). Not superseded — never merged. |

### 3.2 Fully-merged (`ahead == 0` → `git branch -d`; remove worktree first)

| Repo | Branch | Tip | Note |
|------|--------|-----|------|
| genereviews-link | `chore/release-strict-host-origin-4.0.0` | `1ae1787` | Merged (behind 14). **Worktree dirty (1 file)** at `.worktrees/genereviews-link/…` — report, don't `--force`. |
| vep-link | `chore/release-strict-host-origin-2.0.0` | `5879570` | Merged (behind 7). **Worktree dirty (4 files)** at `.worktrees/vep-link/…` — report, don't `--force`. |

### 3.3 DELETE-SAFE — superseded / throwaway / stale (content-proven)

Each row's rationale was verified against **current `main`** (file:line or `main` log commit noted).

| # | Repo | Branch | Tip | Classification — proof on `main` |
|---|------|--------|-----|----------------------------------|
| 1 | autopvs1 | `fix/mcp-path-and-servername` | `d141b5c` | SUPERSEDED — `server_manager.py:103 app.mount("/", mcp_app)` **and** `server_info.py:20 SERVER_NAME="autopvs1-link"` both on `main`. |
| 2 | clingen | `chore/container-hardening-v1` | `06deeee` | SUPERSEDED — digest-pin + `container-security.yml` + CORS guard + CHANGELOG 2.0.1 on `main`. |
| 3 | clingen | `fix/mcp-path-direct` | `9ac8045` | SUPERSEDED — `main` mounts at `/` (`server_manager.py:150`). |
| 4 | clinvar | `ci/data-bundle-workflow` | `7f85d8c` | SUPERSEDED — `.github/workflows/data-bundle.yml` present on `main` (PR #11). |
| 5 | clinvar | `fix/mcp-path-direct` | `47af1a3` | SUPERSEDED — `main` mounts at `/` (`server_manager.py:174`). |
| 6 | gencc | `fix/dependabot-base-format` | `aff4134` | STALE/REGRESSIVE — merging drops `GENCC_LINK_ALLOWED_HOSTS/ORIGINS` from `.env.example` and downgrades `setup-uv` v8.3.2→v8.2.0 that `main` has. |
| 7 | genereviews | `fix/live-corpus-version` | `6aa425d` | SUPERSEDED — `orchestration.py:29 live_corpus_version()` + route stamping on `main` (PR #88). |
| 8 | genereviews | `fix/search-live-corpus-version` | `313ed7c` | SUPERSEDED — search live-fallback stamping on `main` (PR #89). |
| 9 | genereviews | `fix/serverinfo-consistency` | `82cec74` | SUPERSEDED — `server_manager.py:299 name="genereviews-link"` on `main`; branch also carries stray `-f` junk. |
| 10 | genereviews | `fix/serverinfo-version` | `588d720` | SUPERSEDED — versioning standard on `main` (released 5.0.2). |
| 11 | gnomad | `chore/container-hardening-v1` | `144d80b` | SUPERSEDED — digest-pin + `container-security.yml` + CORS guard on `main`. |
| 12 | gnomad | `fix/dependabot-base-format` | `a64320b` | STALE/REGRESSIVE — merging drops `MCP_ALLOWED_HOSTS/ORIGINS` lines `main` has. |
| 13 | gnomad | `fix/mcp-path-direct` | `28ae031` | SUPERSEDED — `main` mounts at `/` (`server_manager.py:133`). |
| 14 | gtex | `backup/pre-rebase-0ff2c24` | `0ff2c24` | THROWAWAY — GENCODE enum + dataset-aware resolution shipped on `main` via PR #54 (`4cc17d4`). |
| 15 | hpo | `test/hpo-coverage-to-80` | `12ad61d` | SUPERSEDED — all 3 test files on `main`; two-dot diff shows branch would revert 4000+ LOC (predates the standards). |
| 16 | litvar | `chore/container-hardening-v1` | `b74468c` | SUPERSEDED — digest-pin + CORS + `container-security.yml` on `main`. |
| 17 | litvar | `fix/litvar-docker-mcp-unified` | `04b756a` | SUPERSEDED — `main` compose runs `serve unified` (`docker/docker-compose.yml:41`, PR #31). |
| 18 | litvar | `fix/litvar-entrypoint-reliability` | `2a8c070` | **SUPERSEDED — overturns the task's central assumption.** All 4 root-cause fixes on `main` (percent-encode `client.py:49`, canonical-id resolve `variant_service.py:312`, rsID/HGVS entrypoint, recoverable not-found) shipped via `a1bd540 …entrypoint reliability (#20)(#35)`. Merge-base 2026-06-16; `main` is 37 commits / ~1 month ahead. **Gate deletion behind a dedicated Codex supersession-confirm.** |
| 19 | litvar | `fix/mcp-path-direct` | `83f91a5` | SUPERSEDED — `main` mounts at `/` (`server_manager.py:80`). |
| 20 | panelapp | `backup/pre-rebase-85e4199` | `85e4199` | THROWAWAY — pre-rebase backup (a v0.3.0 memory note). |
| 21 | panelapp | `chore/container-hardening-v1` | `063eed1` | SUPERSEDED — hardening on `main`. |
| 22 | panelapp | `chore/prod-read-only` | `cb210e9` | SUPERSEDED — `read_only: true` + tmpfs on `main` (`docker/docker-compose.prod.yml:38`). |
| 23 | pubtator | `chore/container-hardening-v1` | `11b45b1` | SUPERSEDED — digest-pin + `container-security.yml` on `main`. |
| 24 | spliceailookup | `fix/mcp-path-direct` | `6b4757f` | SUPERSEDED — `main` mounts at `/` (`server_manager.py:151`). |
| 25 | spliceailookup | `pr-13-review` | `a6d1db0` | THROWAWAY (`pr-*-review`) + SUPERSEDED — Response-Envelope v1 on `main` via PR #13 (`5356391`). |
| 26 | stringdb | `chore/ruff-eof-newline` | `8de4ecd` | STALE/REGRESSIVE — merging would delete the `.claude/skills` baseline `main` now has. |
| 27 | stringdb | `fix/serverinfo-consistency` | `f081c11` | SUPERSEDED — `config_models.py` `server_name="stringdb-link"` on `main`. |
| 28 | uniprot | `fix/serverinfo-consistency` | `2bc9ce2` | SUPERSEDED — the branch's only change (compose `published :-8013`) is already on `main` (`docker/docker-compose.yml:24`); serverInfo standardized fleet-wide. |
| 29 | vep | `fix/mcp-path-direct` | `b26c86a` | SUPERSEDED — `main` mounts at `/` (`server_manager.py:191`). |
| 30 | vep | `fix/serverinfo-version` | `a0b9aa5` | SUPERSEDED — versioning standard on `main`. |
| 31 | vep | `pr-9-review` | `4371573` | THROWAWAY (`pr-*-review`) + SUPERSEDED — Response-Envelope v1 on `main` via PR #9 (`5762f08`). |

### 3.4 ESCALATE

**None.** The two branches that looked like escalation candidates were resolved by content:
`litvar/fix/litvar-entrypoint-reliability` (contradicted the brief's belief, but is content-proven
superseded — deletion still gated behind an explicit Codex confirm) and
`uniprot/fix/serverinfo-consistency` (mislabeled, but its actual change is already on `main`).

## 4. Housekeeping (non-branch)

- **Worktrees to prune** (both attached to fully-merged branches, both **dirty** → report, no `--force`;
  inspect the dirty files, remove only if throwaway build artifacts, else leave for operator):
  `.worktrees/genereviews-link/release-strict-host-origin-4.0.0`,
  `.worktrees/vep-link/release-strict-host-origin-2.0.0`.
- **Stray untracked files on `main` work trees:** genereviews `-f` (0-byte accidental file, also
  committed in the throwaway `fix/serverinfo-consistency` branch) — safe to `rm`. clinvar
  `data/clinvar.sqlite.zst.sha256` — a legitimate data-bundle checksum sidecar; **leave it**.

## 5. Non-goals / guardrails

- No deploy, no baseline re-pin, no admin toggles — operator owns those.
- Never `-D` an unmerged branch without its tip SHA + rationale recorded in `TRIAGE.md` first.
  Prefer `git branch -d` wherever the work proves merged.
- Branch/rebase only off pristine `origin/main`. STOP + report on any red `ci-local`, merge conflict,
  or unseen upstream commit. No force-push; no push at all unless the operator later asks.
- The single FINISH-AND-SHIP (metadome skills) merges only after Codex `gpt-5.6-sol xhigh` returns
  SHIP **and** `make ci-local` is green.

## 6. Risks

- **Content-proof staleness:** a few "already on `main`" calls in §3.3 lean on `main` log/release
  history in addition to a grep (e.g. envelope equivalence for `pr-*-review`). The execution phase
  re-runs `git diff main...<branch>` per branch and records it in `TRIAGE.md` **before** any `-D`, so
  the destructive step always has a fresh content proof.
- **The litvar reversal:** because it contradicts the brief, its deletion is gated behind a dedicated
  Codex "confirm the branch adds nothing `main` lacks" verdict, not just the grep evidence here.
