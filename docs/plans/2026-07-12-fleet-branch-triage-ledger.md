# Fleet Unmerged-Branch Triage — Ledger (COMPLETE 2026-07-12)

- Session: aed790b7 · Spec: `docs/specs/2026-07-12-fleet-branch-triage-design.md` · Plan: `docs/plans/2026-07-12-fleet-branch-triage.md`
- Baseline: pristine `origin/main` on every triage-target repo (origin_ahead=0). No deploy (operator owns redeploy).
- Codex gate: `gpt-5.6-sol` xhigh, read-only, `< /dev/null` (foreground). Legend: `-D` force-delete; `-d` delete-if-merged; MERGE finish-and-ship.

## Outcome

- **DELETE-SAFE `-D`: 31/31 DONE** — all tip SHAs matched at delete time (zero drift); reflog-durable `(was <sha>)` captured.
- **FINISH-AND-SHIP: 1/1 DONE** — metadome `chore/agent-skills` rebased, `ci-local` GREEN (373 passed), Codex FIX (3 inherited fleet-wide baseline warnings, operator chose merge-as-parity), merged FF → `main` `ea6c23a`, `-d`. **Fleet skills parity now 21/21.**
- **Fully-merged `-d`: 1 DONE / 1 REPORTED** — genereviews `…4.0.0` pruned+`-d`; vep `…2.0.0` LEFT (dirty worktree, guardrail: no `--force`).
- **Housekeeping:** genereviews stray `-f` removed; clinvar `.sha256` data sidecar KEPT.
- **Final fleet state:** every in-scope repo is `main`-only EXCEPT vep-link (1 merged branch held by dirty worktree — reported).

## Dispositions

| Repo | Branch | Tip SHA | Disposition | Proof (on `main`) / verdict | action_taken |
|------|--------|---------|-------------|------------------------------|--------------|
| metadome | chore/agent-skills | 472054c→ea6c23a | FINISH-AND-SHIP | sole laggard (0 skills); Codex FIX=inherited fleet warnings, operator=merge-parity | **MERGED FF `ea6c23a`, `-d`** |
| litvar | fix/litvar-entrypoint-reliability | 2a8c070 | DELETE-SAFE (Codex-gated) | Codex **VERDICT: SUPERSEDED** — all 4 fixes on main (client.py:40/297/302, variant_service.py:311/54/396, literature.py:51); PR #35 | **`-D` (was 2a8c070)** |
| genereviews | chore/release-strict-host-origin-4.0.0 | 1ae1787 | MERGED `-d` | ahead=0; worktree pruned (only stray `-f`) | **`-d` (was 1ae1787)** |
| vep | chore/release-strict-host-origin-2.0.0 | 5879570 | MERGED `-d` (HELD) | ahead=0 BUT worktree has real uncommitted edits | **LEFT + REPORTED (no --force)** |
| autopvs1 | fix/mcp-path-and-servername | d141b5c | DELETE-SAFE | server_manager.py:103 mount `/` + server_info.py:20 SERVER_NAME=autopvs1-link | **`-D` (was d141b5c)** |
| clingen | chore/container-hardening-v1 | 06deeee | DELETE-SAFE | digest-pin + container-security.yml + CORS + 2.0.1 | **`-D` (was 06deeee)** |
| clingen | fix/mcp-path-direct | 9ac8045 | DELETE-SAFE | server_manager.py:150 mount `/` | **`-D` (was 9ac8045)** |
| clinvar | ci/data-bundle-workflow | 7f85d8c | DELETE-SAFE | .github/workflows/data-bundle.yml (PR #11) | **`-D` (was 7f85d8c)** |
| clinvar | fix/mcp-path-direct | 47af1a3 | DELETE-SAFE | server_manager.py:174 mount `/` | **`-D` (was 47af1a3)** |
| gencc | fix/dependabot-base-format | aff4134 | DELETE-SAFE (stale/regressive) | would drop ALLOWED_HOSTS/ORIGINS + downgrade setup-uv | **`-D` (was aff4134)** |
| genereviews | fix/live-corpus-version | 6aa425d | DELETE-SAFE | orchestration.py:29 live stamping (PR #88) | **`-D` (was 6aa425d)** |
| genereviews | fix/search-live-corpus-version | 313ed7c | DELETE-SAFE | search live-fallback stamping (PR #89) | **`-D` (was 313ed7c)** |
| genereviews | fix/serverinfo-consistency | 82cec74 | DELETE-SAFE | server_manager.py:299 name=genereviews-link; carried stray `-f` | **`-D` (was 82cec74)** |
| genereviews | fix/serverinfo-version | 588d720 | DELETE-SAFE | versioning standard (5.0.2) | **`-D` (was 588d720)** |
| gnomad | chore/container-hardening-v1 | 144d80b | DELETE-SAFE | digest-pin + container-security.yml + CORS | **`-D` (was 144d80b)** |
| gnomad | fix/dependabot-base-format | a64320b | DELETE-SAFE (stale/regressive) | would drop MCP_ALLOWED_HOSTS/ORIGINS | **`-D` (was a64320b)** |
| gnomad | fix/mcp-path-direct | 28ae031 | DELETE-SAFE | server_manager.py:133 mount `/` | **`-D` (was 28ae031)** |
| gtex | backup/pre-rebase-0ff2c24 | 0ff2c24 | DELETE-SAFE (throwaway) | GENCODE via PR #54 `4cc17d4` | **`-D` (was 0ff2c24)** |
| hpo | test/hpo-coverage-to-80 | 12ad61d | DELETE-SAFE | 3 test files on main; branch reverts 4000+ LOC | **`-D` (was 12ad61d)** |
| litvar | chore/container-hardening-v1 | b74468c | DELETE-SAFE | digest-pin + CORS + container-security.yml | **`-D` (was b74468c)** |
| litvar | fix/litvar-docker-mcp-unified | 04b756a | DELETE-SAFE | compose serve unified docker-compose.yml:41 (PR #31) | **`-D` (was 04b756a)** |
| litvar | fix/mcp-path-direct | 83f91a5 | DELETE-SAFE | server_manager.py:80 mount `/` | **`-D` (was 83f91a5)** |
| panelapp | backup/pre-rebase-85e4199 | 85e4199 | DELETE-SAFE (throwaway) | pre-rebase backup (v0.3.0 memory note) | **`-D` (was 85e4199)** |
| panelapp | chore/container-hardening-v1 | 063eed1 | DELETE-SAFE | hardening on main | **`-D` (was 063eed1)** |
| panelapp | chore/prod-read-only | cb210e9 | DELETE-SAFE | docker-compose.prod.yml:38 read_only+tmpfs | **`-D` (was cb210e9)** |
| pubtator | chore/container-hardening-v1 | 11b45b1 | DELETE-SAFE | digest-pin + container-security.yml | **`-D` (was 11b45b1)** |
| spliceailookup | fix/mcp-path-direct | 6b4757f | DELETE-SAFE | server_manager.py:151 mount `/` | **`-D` (was 6b4757f)** |
| spliceailookup | pr-13-review | a6d1db0 | DELETE-SAFE (throwaway pr-*-review) | Response-Envelope v1 PR #13 `5356391` | **`-D` (was a6d1db0)** |
| stringdb | chore/ruff-eof-newline | 8de4ecd | DELETE-SAFE (stale/regressive) | would delete merged .claude/skills | **`-D` (was 8de4ecd)** |
| stringdb | fix/serverinfo-consistency | f081c11 | DELETE-SAFE | config_models.py server_name=stringdb-link | **`-D` (was f081c11)** |
| uniprot | fix/serverinfo-consistency | 2bc9ce2 | DELETE-SAFE | docker-compose.yml:24 published :-8013 already present | **`-D` (was 2bc9ce2)** |
| vep | fix/mcp-path-direct | b26c86a | DELETE-SAFE | server_manager.py:191 mount `/` | **`-D` (was b26c86a)** |
| vep | fix/serverinfo-version | a0b9aa5 | DELETE-SAFE | versioning standard | **`-D` (was a0b9aa5)** |
| vep | pr-9-review | 4371573 | DELETE-SAFE (throwaway pr-*-review) | Response-Envelope v1 PR #9 `5762f08` | **`-D` (was 4371573)** |

## Operator follow-ups (NOT done — need operator)

1. **vep dirty worktree** `.worktrees/vep-link/release-strict-host-origin-2.0.0` — real uncommitted edits (CHANGELOG.md, pyproject.toml, uv.lock, tests/unit/test_resources.py) on a merged branch. Inspect/discard, then `git worktree remove` + `git branch -d chore/release-strict-host-origin-2.0.0`.
2. **Fleet Agent-Skills baseline refresh — DONE** (Codex gpt-5.5 xhigh SHIP): fixed all findings fleet-wide (600-LOC → per-file budget in check_file_size.py; docker-*-config generalized; Response-Envelope v1→v1.1 + fencing/error-sanitation; added Logging&CLI standard; added error-message/identity sanitation to backend + router security-review). 101 SKILL.md / 22 repos committed on branch `chore/skills-baseline-refresh-2026-07-12` (NOT merged/pushed). **Operator: merge the 22 branches per repo.**
3. **mavedb-link** local `main` diverged from origin (9 behind / 1 ahead) — outside branch-triage scope (no branches); reconcile separately.
