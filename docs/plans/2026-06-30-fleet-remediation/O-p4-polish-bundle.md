# Workstream O — P4 Polish Bundle (dead names, repo hygiene, tickets, docs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal** Clear the P4 polish backlog across six fleet repos — a mondo dead-code/naming bug, two repo-hygiene cleanups (panelapp memory commit, orphanet typo-dir clone), and a batch of genereviews/gnomad/router ticket-and-docs work — as independent, atomic, testable changes.

**Architecture** Each task is a self-contained change in exactly one repo with no cross-repo dependency, so they can be executed in any order (or in parallel by separate subagents). Code changes follow strict TDD (failing test first); pure-hygiene changes (file/dir deletion, `.gitignore`, GitHub issue/PR admin) are verified by shell assertions or `gh` state rather than unit tests. This is the GeneFoundry `*-link` fleet (FastMCP 3.x backends + the router aggregator); no runtime architecture changes.

**Tech Stack** Python 3.12+, uv, FastMCP 3.x, FastAPI (gnomad host), pytest, ruff, mypy; MkDocs Material + mkdocstrings (gnomad docs); GitHub Actions; `gh` CLI for ticket/PR admin.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

Paths are absolute (each task names its repo root).

**mondo-link** (`/home/bernt-popp/development/mondo-link`)
- Modify `mondo_link/mcp/next_commands.py` — `default_error_next_commands` tool tuple: replace bare hierarchy names with registered `get_disease_*` names (fixes the dead error-recovery branch).
- Modify `mondo_link/mcp/resources.py` — `MONDO_SERVER_INSTRUCTIONS` + `MONDO_USAGE_NOTES` prose: bare hierarchy names → registered `get_disease_*` names.
- Modify `tests/unit/test_next_commands.py` — add a regression test that hierarchy-tool errors route to `resolve_xref`.
- Create `tests/unit/test_resources.py` — guard that instruction/usage prose only names registered hierarchy tools.

**panelapp-link** (`/home/bernt-popp/development/panelapp-link`)
- Modify `.gitignore` — add the fleet-canonical `.claude/*` stanza (keeps developer-local Claude Code state out of git).
- Drop unpushed commit `8b94269` (the `.claude/.../memory/v0.3.0-beyond-9-complete.md` personal-agent-state note) — local history rewrite only.

**orphanet (typo-dir)** (`/home/bernt-popp/development/orhpanet-link`)
- Delete the stale TYPO-DIR clone entirely (no git commit — it is a local duplicate, not on GitHub); canonical `/home/bernt-popp/development/orphanet-link` is untouched.

**genereviews-link** (`/home/bernt-popp/development/genereviews-link`)
- Delete tracked 0-byte junk file `./-f` (`git rm`).
- Merge Dependabot PR #82 (setup-python 6.2.0→6.3.0) — remote op.
- Issue #40 — tracker hygiene: confirm the two cheap wins are ALREADY shipped and check them off (no code).
- Issue #27 — close-or-rescope: corpus release bundle is already published (no code).
- Issue #49 — write a phased outline only (no implementation).

**gnomad-link** (`/home/bernt-popp/development/gnomad-link`)
- Create `mkdocs.yml` — MkDocs Material site config + mkdocstrings(python) plugin.
- Create `docs/api-reference.md` — mkdocstrings auto-API page for `gnomad_link.config` / `gnomad_link.exceptions`.
- Modify `pyproject.toml` — add a `docs` dependency group.
- Create `.github/workflows/docs.yml` — build + `mkdocs gh-deploy` to `gh-pages` (remote deploy on merge).

**genefoundry-router** (`/home/bernt-popp/development/genefoundry-router`)
- Issue #3 — verify the discoverability fix via `make bench-discoverability`, then close with an explanatory note (no code; remote issue close).

---

### Task 1: mondo — fix dead error-recovery branch (registered tool names in `next_commands.py`)

The error boundary's `default_error_next_commands` gates its xref/search recovery on a tuple of tool names, but four entries are the **service-method** names (`get_ancestors`, `get_descendants`, `get_parents`, `get_children`) instead of the **registered tool** names (`get_disease_*`). The hierarchy tools pass their registered name into `McpErrorContext` (e.g. `hierarchy.py:70-72` → `"get_disease_ancestors"`), so the `if tool in (...)` check never matches for a hierarchy-tool error and the helpful `resolve_xref`/`search_diseases` recovery is dead — those errors fall through to the generic `get_server_capabilities` step.

**Files**
- Modify: `mondo_link/mcp/next_commands.py:52-60` (the `default_error_next_commands` tuple)
- Test: `tests/unit/test_next_commands.py` (add `test_hierarchy_tool_error_recovery_uses_registered_names`)

**Interfaces**
- Consumes: `default_error_next_commands(tool: str, error_code: str, arguments: dict[str, Any]) -> list[dict[str, Any]]`
- Registered names (truth source) are `capabilities.TOOLS` (`capabilities.py:43-57`): `get_disease_ancestors`, `get_disease_descendants`, `get_disease_parents`, `get_disease_children`.
- Produces: for an xref-looking `term` (e.g. `OMIM:182212`, `infer_xref_source` truthy) the recovery `[cmd("resolve_xref", xref_id=value), cmd("search_diseases", query=value)]`.

Steps:

- [ ] (1) Write the failing test. Append to `tests/unit/test_next_commands.py`:
```python
def test_hierarchy_tool_error_recovery_uses_registered_names() -> None:
    # Regression: default_error_next_commands listed the bare service-method names
    # (get_ancestors/...) instead of the registered tool names (get_disease_*), so an
    # error from a hierarchy tool fell through to the generic get_server_capabilities
    # step instead of the xref/search recovery. An xref-looking term must route to
    # resolve_xref (its source is inferable) for every hierarchy tool.
    for tool in (
        "get_disease_ancestors",
        "get_disease_descendants",
        "get_disease_parents",
        "get_disease_children",
    ):
        steps = nc.default_error_next_commands(tool, "not_found", {"term": "OMIM:182212"})
        _assert_steps(steps)
        assert steps[0]["tool"] == "resolve_xref", (tool, steps)
```
- [ ] (2) Run it, expect FAIL. `cd /home/bernt-popp/development/mondo-link && uv run pytest tests/unit/test_next_commands.py -q -k hierarchy_tool_error_recovery` → FAILS with `AssertionError: ('get_disease_ancestors', [{'tool': 'get_server_capabilities', ...}])` (the generic fallback, because the bare names don't match the registered name).
- [ ] (3) Minimal implementation — edit the tuple in `mondo_link/mcp/next_commands.py` (`default_error_next_commands`, currently lines 52-60). Replace:
```python
    if tool in (
        "resolve_disease",
        "get_disease",
        "get_ancestors",
        "get_descendants",
        "get_parents",
        "get_children",
        "map_cross_ontology",
    ):
```
with:
```python
    if tool in (
        "resolve_disease",
        "get_disease",
        "get_disease_ancestors",
        "get_disease_descendants",
        "get_disease_parents",
        "get_disease_children",
        "map_cross_ontology",
    ):
```
- [ ] (4) Run, expect PASS. `cd /home/bernt-popp/development/mondo-link && uv run pytest tests/unit/test_next_commands.py -q` → all pass. Then `make lint typecheck` clean.
- [ ] (5) Commit. `fix(mcp): route hierarchy-tool errors to xref/search recovery (registered tool names)`

---

### Task 2: mondo — fix bare hierarchy names in instruction/usage prose (`resources.py`)

`MONDO_SERVER_INSTRUCTIONS` (the server's `instructions=` text) and `MONDO_USAGE_NOTES` describe the hierarchy tools by their non-existent bare names. The "Workflow:" line in the same string already uses the correct `get_disease_*` names, so this is an internal inconsistency that tells a model to call tools that aren't registered.

**Files**
- Modify: `mondo_link/mcp/resources.py:24-25` (`MONDO_SERVER_INSTRUCTIONS`, Hierarchy bullet) and `:44-45` (`MONDO_USAGE_NOTES`)
- Test: Create `tests/unit/test_resources.py`

**Interfaces** Pure string content; the guard asserts the prose names a subset of `capabilities.TOOLS`.

Steps:

- [ ] (1) Write the failing test. Create `tests/unit/test_resources.py`:
```python
"""Guard: instruction/usage prose names only registered hierarchy tools.

The bare service-method names (get_parents, get_ancestors, ...) are NOT registered
MCP tools; the registered names are get_disease_* (capabilities.TOOLS). Prose that
uses the bare forms tells a model to call tools that don't exist.
"""

from __future__ import annotations

from mondo_link.mcp.resources import MONDO_SERVER_INSTRUCTIONS, MONDO_USAGE_NOTES

_BARE = ("get_ancestors", "get_descendants", "get_parents", "get_children")


def test_prose_uses_registered_hierarchy_tool_names() -> None:
    for prose in (MONDO_SERVER_INSTRUCTIONS, MONDO_USAGE_NOTES):
        for bare in _BARE:
            assert bare not in prose, f"prose references unregistered tool name {bare!r}"
    # the real registered names are present where hierarchy is described
    assert "get_disease_ancestors" in MONDO_SERVER_INSTRUCTIONS
    assert "get_disease_parents" in MONDO_USAGE_NOTES
```
- [ ] (2) Run it, expect FAIL. `cd /home/bernt-popp/development/mondo-link && uv run pytest tests/unit/test_resources.py -q` → FAILS: `AssertionError: prose references unregistered tool name 'get_parents'` (from the Hierarchy bullet at `resources.py:24` and the usage note at `:44`).
- [ ] (3) Minimal implementation — edit `mondo_link/mcp/resources.py`. In `MONDO_SERVER_INSTRUCTIONS` replace the two lines (currently `:24-25`):
```python
    "- Hierarchy: get_parents / get_children for the immediate neighbours and "
    "get_ancestors / get_descendants for the transitive closure.\n"
```
with:
```python
    "- Hierarchy: get_disease_parents / get_disease_children for the immediate "
    "neighbours and get_disease_ancestors / get_disease_descendants for the "
    "transitive closure.\n"
```
In `MONDO_USAGE_NOTES` replace the line (currently `:44-45`):
```python
    "get_parents/get_children (immediate) and get_ancestors/get_descendants "
```
with:
```python
    "get_disease_parents/get_disease_children (immediate) and "
    "get_disease_ancestors/get_disease_descendants "
```
- [ ] (4) Run, expect PASS. `cd /home/bernt-popp/development/mondo-link && uv run pytest tests/unit/test_resources.py -q` → pass. `make ci-local` clean (LOC budget unaffected — net wording change).
- [ ] (5) Commit. `docs(mcp): name registered get_disease_* hierarchy tools in instructions/usage prose`

---

### Task 3: panelapp — drop unpushed personal-memory commit and ignore `.claude/`

Unpushed commit `8b94269` ("chore: add panelapp v0.3.0 project memory note") tracks `.claude/projects/-home-bernt-popp-development-panelapp-link/memory/v0.3.0-beyond-9-complete.md` — developer-local Claude Code agent state that should never be in version control — and `.gitignore` has no `.claude` entry. HEAD is exactly 1 commit ahead of `origin/main` and that commit's ONLY change is this file, so dropping it is a clean local-history rewrite (nothing was pushed; no force-push to remote).

**Files**
- Modify: `panelapp-link/.gitignore` (append `.claude/*` stanza)
- History: drop local commit `8b94269`

**Interfaces** None (git + ignore rule). Canonical stanza copied from `genereviews-link/.gitignore:184-188` for fleet consistency.

Steps:

- [ ] (1) Write the failing check (shell assertion, run from repo root). `cd /home/bernt-popp/development/panelapp-link && git ls-files | grep -q '^\.claude/' && echo "STILL-TRACKED (expected before fix)"` → prints `STILL-TRACKED`; and `git rev-list --count origin/main..HEAD` → `1` (the unpushed memory commit). Record these as the pre-state.
- [ ] (2) Confirm the commit is unpushed and solely the memory file. `git log --oneline origin/main..HEAD` → only `8b94269`; `git show --stat 8b94269` → 1 file changed (the memory note). If HEAD is NOT 1-ahead-and-only-this-file, STOP and reassess (do not blindly reset).
- [ ] (3) Apply the fix:
  - Drop the commit but keep the file on disk (it is live agent memory, just untracked): `git reset --mixed HEAD~1`. HEAD now equals `origin/main`; the memory file becomes untracked on disk.
  - Append the ignore stanza to `.gitignore` (after the existing `.env.docker` line):
```gitignore

# CLAUDE files — keep developer-local Claude Code state out of version control
# (track repo workflows under .claude/skills/ if/when added).
.claude/*
!.claude/skills/
```
  - Stage and commit only the ignore change: `git add .gitignore && git commit -m "chore: ignore developer-local .claude/ state"`.
- [ ] (4) Verify, expect PASS. `git ls-files | grep -c '^\.claude/'` → `0`; `git check-ignore .claude/projects/-home-bernt-popp-development-panelapp-link/memory/v0.3.0-beyond-9-complete.md` → echoes the path (now ignored); `git log --oneline origin/main..HEAD` → exactly one commit, the `.gitignore` change (the memory note is gone from history). Working tree clean except the now-ignored untracked file.
- [ ] (5) Commit. (Already committed in step 3: `chore: ignore developer-local .claude/ state`.) Atomic-commit boundary = this single `.gitignore` commit; the reset is a history drop, not a commit.

---

### Task 4: orphanet — delete the stale typo-dir clone `orhpanet-link`

`/home/bernt-popp/development/orhpanet-link` (note the `orhpanet` typo) is a second, stale git clone of the same `berntpopp/orphanet-link` repo. It is 4 commits behind the canonical `/home/bernt-popp/development/orphanet-link` (typo-dir HEAD `7ce4984` vs canonical `9d72d5d`) and carries an untracked `.env` whose contents are **non-secret** (host port `8076`, prebuilt-bundle release config — no tokens/passwords/keys). The typo name is not a GitHub repo, so deletion is purely local workspace hygiene; there is no git commit.

**Files**
- Delete: directory `/home/bernt-popp/development/orhpanet-link` (entire clone)
- Untouched: `/home/bernt-popp/development/orphanet-link` (canonical)

**Interfaces** None.

Steps:

- [ ] (1) Re-confirm the `.env` holds no real secret (idempotent safety gate). `grep -RniE 'token|secret|password|api[_-]?key|bearer|authorization' /home/bernt-popp/development/orhpanet-link/.env` → **no matches** (the file only sets `ORPHANET_LINK_HOST_PORT`, `ORPHANET_LINK_DATA__PREFER_PREBUILT`, `ORPHANET_LINK_DATA__RELEASE_REPO`, `ORPHANET_LINK_DATA__RELEASE_TAG`). If ANY match appears, STOP and handle the secret first.
- [ ] (2) Confirm it is the typo clone, not the canonical, and not unique work. `git -C /home/bernt-popp/development/orhpanet-link remote get-url origin` → `https://github.com/berntpopp/orphanet-link.git`; `git -C /home/bernt-popp/development/orhpanet-link status --short` → clean (only the untracked `.env`); `git -C /home/bernt-popp/development/orhpanet-link rev-list --left-right --count origin/main...HEAD` → `4	0` (behind 4, ahead 0 — no local commits to lose).
- [ ] (3) (Optional preservation) The canonical dir has no `.env`; if the runtime config is wanted there, copy it first: `cp /home/bernt-popp/development/orhpanet-link/.env /home/bernt-popp/development/orphanet-link/.env` (canonical `.gitignore` already excludes `.env`, so it stays untracked). This is optional — the values are reproducible defaults.
- [ ] (4) Delete the typo-dir and verify. `rm -rf /home/bernt-popp/development/orhpanet-link` then `test ! -e /home/bernt-popp/development/orhpanet-link && echo GONE` → `GONE`; `git -C /home/bernt-popp/development/orphanet-link rev-parse --abbrev-ref HEAD` → still resolves (canonical intact, on `main`).
- [ ] (5) Commit. None — this is local workspace cleanup of a non-tracked duplicate directory; there is nothing to commit in any repo.

---

### Task 5: genereviews — remove tracked 0-byte junk file `./-f`

A 0-byte file literally named `-f` is tracked at the repo root (added accidentally in `c59ad42`, likely a stray shell redirect/flag artifact). It serves no purpose and clutters the tree.

**Files**
- Delete: `genereviews-link/-f` (tracked, 0 bytes)

**Interfaces** None.

Steps:

- [ ] (1) Write the failing check. `cd /home/bernt-popp/development/genereviews-link && git ls-files -- ':(literal)-f' | grep -q '^-f$' && echo "TRACKED (expected before fix)"` → prints `TRACKED`; `stat -c %s ./-f` → `0`.
- [ ] (2) Confirm nothing references it (sanity). `grep -RIn --exclude-dir=.git -- '"-f"' genereview_link/ tests/ 2>/dev/null | grep -v 'rg\|ripgrep\|grep' | head` → no meaningful reference to a project file named `-f`. (Bare `-f` flags to tools are unrelated.)
- [ ] (3) Remove it. The leading dash needs the `--` end-of-options guard: `git rm -- ./-f`.
- [ ] (4) Verify, expect PASS. `git ls-files -- ':(literal)-f'` → empty; `test ! -e ./-f && echo GONE` → `GONE`.
- [ ] (5) Commit. `chore: remove accidental 0-byte junk file ./-f`

---

### Task 6: genereviews — merge mergeable Dependabot PR #82 (setup-python 6.2.0→6.3.0)

PR #82 bumps `actions/setup-python` from 6.2.0 to 6.3.0. It is verified `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN`, with all checks green (quality job, CodeQL ×2, Dependency review). **EXECUTION-GATED**: merging mutates the remote `main`.

**Files** None local (GitHub Actions dependency bump on the PR branch).

**Interfaces** None.

Steps:

- [ ] (1) Re-verify green-and-mergeable immediately before merging (state can drift). `cd /home/bernt-popp/development/genereviews-link && gh pr view 82 --json mergeable,mergeStateStatus,statusCheckRollup --jq '{mergeable, mergeStateStatus, checks:[.statusCheckRollup[]|{name:(.name//.context),c:(.conclusion//.state)}]}'` → expect `mergeable=MERGEABLE`, `mergeStateStatus=CLEAN`, every check `SUCCESS`.
- [ ] (2) If ANY check is not SUCCESS or it is not CLEAN/MERGEABLE, STOP and do not merge.
- [ ] (3) Merge (squash, delete the dependabot branch). `gh pr merge 82 --squash --delete-branch`.
- [ ] (4) Verify, expect PASS. `gh pr view 82 --json state --jq .state` → `MERGED`.
- [ ] (5) Commit. None local — the merge IS the change (remote). **EXECUTION-GATED.**

---

### Task 7: genereviews — issue #40 tracker hygiene (the two cheap wins are already shipped)

**Finding correction (verified against current code):** the audit listed "ship the cheap #40 win `format=markdown_table` on `get_table`" as outstanding, but it is **already shipped and tested**: `genereview_link/api/routes/tables.py:46-56` exposes the `format: Literal["structured","markdown_table"]` query param and `:97-103` calls `render_table_markdown` (the renderer now lives at `corpus/tables.py:180`, not `:143` — line drifted); it landed in commit `277ddb5` / PR #61, with 46 assertions across `tests/test_routes_table.py`. The other cheap #40 item — `total_tokens_estimate` on `get_chapter_metadata` — is **also already shipped** (`genereview_link/api/routes/chapters.py:312`: `total_tokens_estimate=total_chars // 4`). So the correct action is tracker hygiene, not implementation.

**Files** None local — GitHub issue update only.

**Interfaces** None.

Steps:

- [ ] (1) Re-verify the two items are shipped (evidence before edit). `cd /home/bernt-popp/development/genereviews-link && grep -n 'markdown_table' genereview_link/api/routes/tables.py | head` (param + render call present) and `grep -n 'total_tokens_estimate' genereview_link/api/routes/chapters.py` → `312:        total_tokens_estimate=total_chars // 4,`.
- [ ] (2) No test to fail — this is admin. (The behaviour is already protected by `tests/test_routes_table.py`.)
- [ ] (3) Update issue #40: post a comment marking items 2 and 3 complete with refs, and check their boxes in the body. `gh issue comment 40 --body "Closing out two of the listed wishes — both already shipped:\n\n- **Item 2 (markdown-table output mode for get_table):** done in #61 (commit 277ddb5). \`format=markdown_table\` query param on \`get_table\` renders GFM via \`render_table_markdown\` (now \`corpus/tables.py:180\`); covered by tests/test_routes_table.py.\n- **Item 3 (token-count estimate on get_chapter_metadata):** done — \`total_tokens_estimate = total_char_count // 4\` at api/routes/chapters.py:312.\n\nRemaining open items on this tracker: 1 (revision_history), 4 (get_variant_context), 5 (get_abstract/get_links docs). Leaving #40 open for those."` (If the body uses GFM checklists, also `gh issue edit 40 --body-file <(...)` to tick the boxes; optional.)
- [ ] (4) Verify. `gh issue view 40 --json comments --jq '.comments[-1].body' | head` shows the note; issue remains `OPEN` for the residual items.
- [ ] (5) Commit. None local — issue-tracker hygiene only. (Mildly remote: writes a GitHub comment.)

---

### Task 8: genereviews — issue #27 close-or-rescope (corpus bundle already published)

Issue #27 ("Prebuild GeneReviews corpus database bundles and publish them as GitHub Release assets") is substantially satisfied: release `corpus-2026-05-12-r1` is published (Latest), and the `BUNDLE_URL`/prebuilt-bundle bootstrap machinery exists (`genereview_link/config.py`, `genereview_link/server_lifecycle.py`, `.env.docker.example`, `docker/README.md`). The acceptance criterion "a fresh deploy restores a populated corpus from GitHub Releases" is met.

**Files** None local — GitHub issue update only.

**Interfaces** None.

Steps:

- [ ] (1) Re-verify the release + bootstrap exist. `cd /home/bernt-popp/development/genereviews-link && gh release view corpus-2026-05-12-r1 --json tagName,isLatest --jq '{tagName,isLatest}'` → `corpus-2026-05-12-r1 / true`; `grep -rl 'BUNDLE_URL' genereview_link/ .env.docker.example` → bootstrap present.
- [ ] (2) No test — admin.
- [ ] (3) Decide and act:
  - **Close** if no recurring-refresh follow-up is wanted: `gh issue close 27 --comment "Done: corpus bundle published as release corpus-2026-05-12-r1 (Latest); BUNDLE_URL prebuilt-bundle bootstrap is wired in config.py/server_lifecycle.py/.env.docker.example so a fresh Docker/NPM deploy restores the populated corpus without live ingest. Scheduled bundle refresh tracked separately if needed."`
  - **OR rescope** to the residual (a recurring rebuild workflow): `gh issue edit 27 --title "Schedule periodic GeneReviews corpus bundle refresh" --body "Initial bundle published (corpus-2026-05-12-r1) and restore path wired. Residual: a scheduled (e.g. monthly) GitHub Actions workflow that rebuilds + republishes the corpus bundle so deployments don't drift from upstream GeneReviews."`
  - Default recommendation: **rescope** (keeps the genuinely-open "keep it fresh" work visible).
- [ ] (4) Verify. `gh issue view 27 --json state,title --jq '{state,title}'` reflects the chosen action.
- [ ] (5) Commit. None local — issue-tracker hygiene only.

---

### Task 9: genereviews — issue #49 phased outline only (do NOT implement)

Issue #49 ("Evaluate hybrid local biomedical entity annotation for C-gamma retrieval") is a LARGE research feature (HunFlair2 + GLiNER + tmVar-PubMedBERT hybrid annotation over the corpus). **Outline only** — produce a phased plan as an issue comment; write no code, add no model-inference runtime dependency (the issue's own acceptance forbids that until a probe proves value).

**Files** None local — GitHub issue comment only.

**Interfaces** None.

Steps:

- [ ] (1) No test — this deliverable is a written outline.
- [ ] (2) Re-read the issue to anchor the phases. `gh issue view 49 --json body --jq .body | head -60` (confirms the hybrid recommendation table and the "bounded probe harness" next-task).
- [ ] (3) Post the phased outline as a comment (verbatim text below), keeping it a roadmap, not a build:
```
gh issue comment 49 --body "$(cat <<'EOF'
Phased approach (outline only — no production model-inference dependency until Phase 1 proves value, per acceptance):

**Phase 0 — Scope & guardrails (no code).** Confirm target entity types (gene, variant, disease/phenotype, drug/chemical) and the success metric: recall of the C-gamma marquee-miss anchors. Decide artifact shape: offline-produced `passage_entities` / `chapter_entities` JSON + a small runtime gazetteer. Hard rule: NO model inference in the server runtime.

**Phase 1 — Bounded probe harness (spike branch only).** On `spike/pubtator-local-annotation`, build a reproducible `scripts/` probe that runs the recommended hybrid (HunFlair2 linkers; GLiNER-biomed for typed spans; tmVar-PubMedBERT + HGVS regex for variants) over: the 299 locked ranking-bench queries, the 3 marquee-miss gold passages, and 50 random corpus passages. Emit JSONL (source id, model/tool versions, spans, labels, normalized IDs, latency, anchor-recovered bool). Probe outputs gitignored or summarized. **Gate:** report recall per category; proceed only if useful.

**Phase 2 — Offline annotation pipeline (if Phase 1 passes).** Promote the winning hybrid into an offline batch job that annotates the full corpus into versioned `*_entities` artifacts with provenance (model versions, corpus_version). Still no runtime inference; artifacts ship like the corpus bundle (ties into #27's bundle machinery).

**Phase 3 — Runtime gazetteer + retrieval boost (separate, gated).** Add a compact query-time gazetteer and an OPT-IN retrieval boost behind a flag, measured against the ranking bench. No schema/boost change until Phase 1 demonstrates coverage (explicit acceptance constraint).

Each phase is independently reviewable; Phases 2-3 are separate issues spun off once Phase 1 lands.
EOF
)"
```
- [ ] (4) Verify. `gh issue view 49 --json comments --jq '.comments[-1].body' | head` shows the outline; #49 stays `OPEN`.
- [ ] (5) Commit. None local — outline posted to the tracker; no implementation.

---

### Task 10: gnomad — issue #3 docs site (MkDocs Material + mkdocstrings + gh-pages workflow)

Add an automated documentation site per issue #3 (Option A). gnomad-link currently has hand-written `docs/*.md` but **no** `mkdocs.yml` and **no** docs workflow. Note `scripts/generate_gnomad_docs.py` introspects the **upstream gnomAD GraphQL API**, not this project, so it does NOT satisfy #3. This makes gnomad-link the fleet's docs-site exemplar (no sibling has a `docs.yml`). **EXECUTION-GATED**: the workflow deploys to the `gh-pages` branch on merge to `main` and requires a one-time Pages-source enablement in repo settings.

Research basis (canonical patterns):
- Material for MkDocs publishing workflow uses `mkdocs gh-deploy --force` with `permissions: contents: write` (pushes the built site to the `gh-pages` branch): https://squidfunk.github.io/mkdocs-material/publishing-your-site/
- mkdocstrings Python handler: `pip install "mkdocstrings[python]"`, `plugins: [mkdocstrings: {default_handler: python}]`, and `::: module.path` injection: https://mkdocstrings.github.io/python/usage/
- Adapt to the fleet's uv-native, SHA-pinned-action CI conventions (`actions/checkout@…#v7.0.0`, `actions/setup-python@…#v6`, `astral-sh/setup-uv@…#v8.2.0`) seen in `.github/workflows/ci.yml`.

**Files**
- Create: `gnomad-link/mkdocs.yml`
- Create: `gnomad-link/docs/api-reference.md`
- Modify: `gnomad-link/pyproject.toml` (add `[dependency-groups] docs`)
- Create: `gnomad-link/.github/workflows/docs.yml`
- Test: local `mkdocs build` (no pytest needed; the build IS the test)

**Interfaces** mkdocstrings introspects `gnomad_link.config` and `gnomad_link.exceptions` (both stable, docstringed public modules). Package is installed editable in the uv venv, so `::: gnomad_link.config` resolves.

Steps:

- [ ] (1) Write the failing check. `cd /home/bernt-popp/development/gnomad-link && uv run mkdocs build 2>&1 | tail -3` → FAILS (`mkdocs` not installed / no `mkdocs.yml`): e.g. `No module named mkdocs` or `Config file 'mkdocs.yml' does not exist.`. This is the red state.
- [ ] (2) Add the `docs` dependency group to `pyproject.toml` (after the `dev` group, currently ending at line 56):
```toml
docs = [
    "mkdocs-material>=9.5,<10",
    "mkdocstrings[python]>=0.27,<1",
]
```
- [ ] (3a) Create `mkdocs.yml`:
```yaml
site_name: gnomAD-Link
site_description: MCP server bridging the gnomAD database to AI applications.
repo_url: https://github.com/berntpopp/gnomad-link
docs_dir: docs

theme:
  name: material
  features:
    - navigation.sections
    - content.code.copy
    - search.suggest

plugins:
  - search
  - mkdocstrings:
      default_handler: python

nav:
  - Home: index.md
  - Usage: usage.md
  - Architecture: architecture.md
  - MCP connection: MCP_CONNECTION_GUIDE.md
  - Development: development.md
  - API reference: api-reference.md

markdown_extensions:
  - admonition
  - pymdownx.highlight
  - pymdownx.superfences

# Research use only; not for clinical decision support, diagnosis, treatment,
# or patient management.
```
- [ ] (3b) Create `docs/api-reference.md`:
```markdown
# API reference

Auto-generated from the package docstrings via mkdocstrings.

## Configuration

::: gnomad_link.config

## Exceptions

::: gnomad_link.exceptions
```
- [ ] (3c) Create `.github/workflows/docs.yml` (uv-native, SHA-pinned to match `ci.yml`; `gh-deploy` only on push to `main`, build-check on PRs):
```yaml
name: Docs

on:
  push:
    branches: [main]
  pull_request:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: write   # mkdocs gh-deploy pushes the built site to the gh-pages branch

jobs:
  docs:
    name: Build (and deploy on main) MkDocs site
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Set up Python
        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: "3.12"

      - name: Set up uv
        uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true
          version: "0.8.7"

      - name: Install docs dependencies
        run: uv sync --group docs --frozen

      - name: Build site
        run: uv run mkdocs build

      - name: Deploy to GitHub Pages
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: uv run mkdocs gh-deploy --force
```
- [ ] (3d) Refresh the lockfile for the new group: `uv lock` (so `--frozen` in CI succeeds).
- [ ] (4) Run, expect PASS. `cd /home/bernt-popp/development/gnomad-link && uv sync --group docs && uv run mkdocs build 2>&1 | tail -5` → `INFO - Documentation built in …`, `site/` produced, and `site/api-reference/index.html` exists (`test -f site/api-reference/index.html && echo OK`). The mkdocstrings page renders the `gnomad_link.config`/`exceptions` docstrings. (Use plain `mkdocs build`, not `--strict`, to avoid failing on any pre-existing relative-link warning in the hand-written docs; tighten to `--strict` in a later pass once existing links are audited.)
- [ ] (5) Commit. `docs: add MkDocs Material + mkdocstrings site and gh-pages workflow (#3)`. **Post-merge one-time manual step (EXECUTION-GATED):** in repo Settings → Pages, set Source = "Deploy from a branch", branch = `gh-pages` / `(root)`. The first push to `main` runs `gh-deploy` and creates `gh-pages`.

---

### Task 11: genefoundry-router — issue #3 verify discoverability fix, then close with a note

Router issue #3 ("discoverability is confusing") is substantially fixed in code: pinned per-backend resolvers (`tool_search.py:40-43` `DEFAULT_ALWAYS_VISIBLE` + `resolve_entrypoints`/`build_pinned_names` ~`:253-269`), a self-healing `call_tool` description (`_CALL_TOOL_DESCRIPTION` `:58-67`, applied in `_make_call_tool` `:224-226`), and field-boosted + stemmed BM25 (`_FIELD_BOOST` `:53`, `_STEM_SUFFIXES` `:75+`). It is protected by a regression gate: `tests/discoverability/test_discoverability.py::test_discoverability_meets_bar` asserts score ≥ 9.0 / reachable ≥ 0.95 / hit@5 ≥ 0.95 (bar reached 2026-06-18: 9.79/10, 100% reachable), runnable via `make bench-discoverability`. The residual (a host/client dropping `call_tool` or never loading `search_tools`) is **not router-fixable**. Action: verify, then close #3 with an explanatory note. No code.

**Files** None local — verification + GitHub issue close.

**Interfaces** `make bench-discoverability` → `scripts/discoverability_report.py --min-score 9.0` (exit 0 iff score ≥ 9.0).

Steps:

- [ ] (1) Run the benchmark (the verification, not a new test). `cd /home/bernt-popp/development/genefoundry-router && make bench-discoverability` → prints `Discoverability: <score>/10 (reachable …% | hit@1 … | hit@3 … | hit@5 … | MRR …)` and exits 0 (score ≥ 9.0). Record the printed score.
- [ ] (2) Run the gate test too. `uv run pytest tests/discoverability/test_discoverability.py -q` → all pass (confirms the snapshot is the real fleet and the bar holds).
- [ ] (3) Close issue #3 with the note (substitute the score from step 1):
```
gh issue close 3 --comment "Fixed router-side and protected by a regression gate.

Implemented:
- Pinned per-backend canonical resolvers so first-step tools need zero search (tool_search.py DEFAULT_ALWAYS_VISIBLE + resolve_entrypoints).
- Self-healing call_tool description: explains the <namespace>_<tool> name format and that an 'Unknown tool' eviction is recoverable by re-running search_tools (tool_search.py _CALL_TOOL_DESCRIPTION).
- Field-boosted + stemmed BM25 so a tool ranks on its own name/leaf/tags, not just prose mentions.

Gate: make bench-discoverability (scripts/discoverability_report.py --min-score 9.0) and tests/discoverability/test_discoverability.py enforce score >= 9.0 / reachable >= 0.95 / hit@5 >= 0.95 over the real catalog snapshot. Current: <SCORE>/10.

Residual (not router-fixable): a host that drops call_tool from its loaded set, or never loads search_tools, will still 'see' only the pinned entry points. The server already surfaces both and the description tells the client to re-discover; closing as fixed on the router side."
```
- [ ] (4) Verify. `gh issue view 3 --json state --jq .state` → `CLOSED`.
- [ ] (5) Commit. None local — verification + issue close only.

---

## Acceptance criteria

- **mondo Task 1:** `uv run pytest tests/unit/test_next_commands.py -q` passes including `test_hierarchy_tool_error_recovery_uses_registered_names`; `default_error_next_commands("get_disease_ancestors","not_found",{"term":"OMIM:182212"})[0]["tool"] == "resolve_xref"`. `make ci-local` clean.
- **mondo Task 2:** `uv run pytest tests/unit/test_resources.py -q` passes; none of `get_ancestors|get_descendants|get_parents|get_children` (bare) appear in `MONDO_SERVER_INSTRUCTIONS`/`MONDO_USAGE_NOTES`.
- **panelapp Task 3:** `git ls-files | grep -c '^\.claude/'` → 0; `git check-ignore .claude/projects/.../memory/v0.3.0-beyond-9-complete.md` echoes the path; `git log --oneline origin/main..HEAD` shows only the `.gitignore` commit.
- **orphanet Task 4:** `/home/bernt-popp/development/orhpanet-link` does not exist; canonical `/home/bernt-popp/development/orphanet-link` still on `main`.
- **genereviews Task 5:** `git ls-files -- ':(literal)-f'` empty; `./-f` gone.
- **genereviews Task 6:** `gh pr view 82 --json state --jq .state` → `MERGED`.
- **genereviews Task 7:** issue #40 carries the comment marking items 2 & 3 shipped (with `tables.py`/`chapters.py:312` refs); #40 still OPEN for residual items.
- **genereviews Task 8:** issue #27 CLOSED-with-note or rescoped to scheduled refresh.
- **genereviews Task 9:** issue #49 carries the 4-phase outline comment; no code/dep added; #49 OPEN.
- **gnomad Task 10:** `uv run mkdocs build` exits 0 and produces `site/api-reference/index.html` rendering `gnomad_link.config`/`exceptions` docstrings; `docs.yml` present and SHA-pinned; `uv lock` updated.
- **router Task 11:** `make bench-discoverability` exits 0 with score ≥ 9.0; `tests/discoverability/test_discoverability.py` passes; issue #3 CLOSED with the explanatory note.

## Risk & rollback

- **EXECUTION-GATED** (push / redeploy / destructive-remote-op):
  - **Task 6** merges PR #82 to remote `main` (`gh pr merge`). Rollback: `gh pr revert` / revert the merge commit.
  - **Task 10** deploys the docs site to the `gh-pages` branch on merge to `main` and needs a one-time Pages-source toggle in repo settings. Rollback: delete the `gh-pages` branch and remove `docs.yml`; the workflow only deploys on push to `main`, never on PRs.
  - **Tasks 7, 8, 9, 11** write to / close GitHub issues (remote, non-destructive). Rollback: reopen (`gh issue reopen`) / delete the comment.
- **Local-only history rewrite (low risk):** Task 3 `git reset --mixed HEAD~1` drops an UNPUSHED commit — no force-push, no remote impact. Rollback: `git reflog` → `git reset --hard <old-HEAD>` (the memory file remains on disk regardless).
- **Irreversible local delete (low risk):** Task 4 `rm -rf` the typo-dir clone. It is 4 commits behind canonical with no local commits (`4 0`) and only a non-secret untracked `.env`; everything is recoverable from `origin`. Optional `.env` copy in step 3 preserves the runtime config.
- **No-risk code changes:** Tasks 1, 2, 5 are pure local commits guarded by TDD / `make ci-local`; revert with `git revert`.
- **Cross-cutting:** run each repo's `make ci-local` before its commit; do not batch tasks across repos into one commit (atomicity requirement).

## Effort

- Tasks 1, 2, 5: ~15 min each (small TDD/hygiene).
- Task 3: ~15 min (careful local reset + ignore).
- Task 4: ~10 min (verify-then-delete).
- Task 6: ~5 min (verify + merge).
- Tasks 7, 8, 9, 11: ~10-15 min each (verify + `gh` admin / outline).
- Task 10: ~45-60 min (mkdocs config + build iteration + workflow + lock + manual Pages enablement).
- **Total: ~3-3.5 h**, fully parallelizable across the six repos (no inter-task dependencies).
