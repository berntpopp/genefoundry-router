# Research workflow skills over MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve `skills/` over MCP from the router (SEP-2640 + `skill://` resources + a `search_tools` skill pointer) and ship six new research workflow skills, each gated by lint, trigger tests and a benchmark A/B.

**Architecture:** One `fastmcp.skills.provider.SkillsDirectoryProvider` on the router (never on a backend). Skills are synthetic BM25 documents in the existing tool-search transform and serialize as resource pointers, never as callable tools. Skill content is Markdown under `skills/<name>/` with a byte-identical shared rules block, checked by `scripts/check_skills.py`.

**Tech Stack:** Python 3.14, FastMCP 4.2.2, pytest, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-21-research-skills-over-mcp-design.md`

## Global Constraints

- Provider import: `from fastmcp.skills.provider import SkillsDirectoryProvider` (NOT `fastmcp.server.providers.skills`, deprecated 4.2 / removed 4.3).
- 600 LOC per module (`make lint-loc`); `tool_search.py` is at 510.
- Do not grow the `search_tools` docstring or any pinned tool text (strict listing budget 18,790/18,800, `tests/unit/test_strict_pinned_catalog.py`).
- Skills never under `genefoundry_router/data/` (image content gate).
- SKILL.md: agentskills.io frontmatter (`name` = folder, `a-z0-9-`, ≤64; `description` ≤1024, starts "Use when"); < 20 kB; body ≤ ~300 lines.
- Every SKILL.md contains the shared rules block from the spec, byte-identical to the copy in `skills/README.md` between `<!-- shared-rules:start -->` and `<!-- shared-rules:end -->`.
- Never name or quote any private repository or lab in any file, commit or PR. Cite public sources only (PMIDs in the spec).
- Secondary findings: check consistency with documented consent and lab policy only; no jurisdiction-specific gene-list rule.
- Research use only. Not clinical decision support.

## Review Focus

1. `GF_SKILLS_DIR` points to a missing dir (e.g. Docker without COPY) → server start fails with a clear error, not zero skills. Test in Task 1.
2. A skill hit is passed to `call_tool` by a model → `call_tool` returns the normal not-found error naming `search_tools`, never executes. Test in Task 2.
3. A skill mentions a tool name that was renamed or does not exist → `make lint-skills` fails naming the skill and tool. Test in Task 3.
4. Search with `detail="strict"` / `"full"` returns a skill hit → same pointer shape, valid JSON, no `inputSchema`. Test in Task 2.
5. A search query that matches no skill → result identical to today (no skill entries, ranking of tools unchanged). Test in Task 2.

---

### Task 1: Serve skills from the router (track R)

**Files:**
- Modify: `genefoundry_router/config.py` (next to `GF_SERVERS_FILE`, line ~71), `genefoundry_router/server.py` (after the backend mount loop, ~line 175), `genefoundry_router/instructions.py`, `docker/Dockerfile` (~line 42), `tests/integration/test_protocol_beyond_tools.py` (lines 40, 66, 108)
- Test: `tests/integration/test_skills_provider.py`

**Interfaces:**
- Produces: `RouterSettings.GF_SKILLS_DIR: str = "skills"`; the built server lists `skill://<name>/SKILL.md` for every folder in it.

- [ ] Step 1: Write failing tests: `test_resources_list_serves_skills` (built with `build_server`, `list_resources()` contains `skill://somatic-variant-interpretation/SKILL.md`); `test_skills_extension_on_modern_protocol` (on the 2026-07-28 protocol `skills/list` returns the skill — follow the agent probe at `/tmp/claude-1000/-home-bernt-popp-development-genefoundry-router/112bb955-693b-4e39-8dc0-6125ad00e5fa/scratchpad/probe.py` if present); `test_missing_skills_dir_fails` (`GF_SKILLS_DIR="does-not-exist"` → `build_server` raises an error naming the path); `test_instructions_mention_skills` (instructions contain `skill://` within the first 600 chars).
- [ ] Step 2: Run `uv run pytest tests/integration/test_skills_provider.py -v` → FAIL.
- [ ] Step 3: Implement: setting; `server.add_provider(SkillsDirectoryProvider(path))` after the mount loop with an explicit `is_dir()` check raising `RouterError`; one early sentence in `build_instructions`. Update the three `test_protocol_beyond_tools.py` assertions to exclude `skill://` URIs (backend federation is what those tests are about). Dockerfile: `COPY skills ./skills` and add to the existing build-time python assert that `Path('skills').glob('*/SKILL.md')` is non-empty.
- [ ] Step 4: `uv run pytest tests/integration -q` and `uv run pytest tests/unit/test_instructions.py tests/unit/test_strict_pinned_catalog.py -q` → PASS.
- [ ] Step 5: Commit `feat(skills): serve skills/ over MCP via SkillsDirectoryProvider (#336)`.

### Task 2: Skill pointer in search_tools (track R)

**Files:**
- Modify: `genefoundry_router/tool_search.py` (`_ranked` ~264, `_compact_entry` ~187, full/strict serializers)
- Test: `tests/unit/test_tool_search_skills.py`

**Interfaces:**
- Consumes: skills dir from Task 1.
- Produces: a search hit `{"name": "skill_<name>", "skill": "skill://<name>/SKILL.md", "description": str, "read": {"method": "resources/read", "uri": "skill://<name>/SKILL.md"}}` (no `call`, no `inputSchema`).

- [ ] Step 1: Failing tests: `test_query_returns_skill_pointer` ("somatic variant interpretation" → a hit with `skill == "skill://somatic-variant-interpretation/SKILL.md"` and no `call` key); `test_skill_pointer_shape_in_full_and_strict`; `test_call_tool_rejects_skill_name` (`call_tool(name="skill_somatic-variant-interpretation")` → not-found error); `test_unrelated_query_unchanged` (a gnomAD frequency query returns the same tool names as with no skills); `test_pinned_listing_budget_unchanged` (existing strict budget test still passes — run it).
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement: build skill documents once from the skills dir (frontmatter name + description) as synthetic `Tool` objects named `skill_<name>`, included only in the BM25 candidate set inside `_ranked`, never in the listing or the `call_tool` catalog; serialize them with the pointer shape in all detail modes. Keep `tool_search.py` < 600 LOC (move the skill doc builder to `genefoundry_router/skill_search.py` if needed).
- [ ] Step 4: `uv run pytest tests/unit -q -k "tool_search or strict_pinned or discover"` and `uv run pytest tests/discoverability -q` → PASS.
- [ ] Step 5: Commit `feat(search): search_tools returns skill:// pointers (#336)`.

### Task 3: Skill lint gate (track R)

**Files:**
- Create: `scripts/check_skills.py`; Modify: `Makefile` (`lint-skills` target, add to `ci-local`), `skills/README.md` (canonical shared block between markers; drop "no skills ship yet" wording)
- Test: `tests/unit/test_check_skills.py`

**Interfaces:**
- Produces: `check_skills(skills_dir: Path, catalog_names: set[str]) -> list[str]` (error strings, empty = pass); CLI `uv run python scripts/check_skills.py` exits 1 on errors.

- [ ] Step 1: Failing tests on tmp fixtures: bad name / folder mismatch; description > 1024 or not starting "Use when"; SKILL.md ≥ 20 kB; a backticked `` `ns_tool` `` whose namespace is a known backend namespace but whose name is not in the catalog → error naming skill + tool; missing or altered shared block → error; valid skill → `[]`.
- [ ] Step 2: Run → FAIL.
- [ ] Step 3: Implement. Catalog names: the most current checked-in catalog snapshot that includes the cancer backends (check `tests/discoverability/catalog.json`; if civic/cbioportal names are missing, find the current snapshot used by `make snapshot-catalog` or the fleet baseline, and use that). Only names whose prefix is a known namespace from `servers.yaml` are checked (so `skill://`, `call_tool`, `search_tools` are ignored).
- [ ] Step 4: Add the shared block to `skills/somatic-variant-interpretation/SKILL.md`; `make lint-skills` → PASS; tests PASS.
- [ ] Step 5: Commit `feat(skills): lint-skills gate (frontmatter, size, tool names, shared rules)`.

### Task 4: Skills S1 — `phenotype-to-candidate-genes`, `identifier-resolution-and-lookup`

**Files:** Create `skills/phenotype-to-candidate-genes/SKILL.md` (start from the A/B draft at `/tmp/claude-1000/-home-bernt-popp-development-genefoundry-router/112bb955-693b-4e39-8dc0-6125ad00e5fa/scratchpad/phenotype-to-candidate-genes/SKILL.md`), `skills/identifier-resolution-and-lookup/SKILL.md`

- [ ] Write both per the spec table; verify every tool name and parameter with `search_tools` on the live GeneFoundry MCP. Each has: when to use, steps with batching, stop rule, failure handling, output template, shared block.
- [ ] `make lint-skills` → PASS (or, before Task 3 lands, self-check frontmatter + size).
- [ ] Add 2–3 trigger queries per skill to the discoverability task set expecting `skill_<name>` (after Task 2).
- [ ] Hand to track B for A/B. Commit `feat(skills): phenotype-to-candidate-genes, identifier-resolution-and-lookup`.

### Task 5: Skills S2 — `germline-variant-classification` (+ references), `literature-evidence-review`

**Files:** `skills/germline-variant-classification/SKILL.md` + `references/{pvs1,splicing,functional,mitochondrial,repeat-expansion}.md`; `skills/literature-evidence-review/SKILL.md`

- [ ] Write per spec: ACMG/AMP 2015 + SVI; Tavtigian points table and class thresholds stated exactly with PMID; double-count rules; VCEP specs override; literature skill verifies every PMID to the allele/gene with `litvar_get_variant_literature` / `pubtator_search_literature`, plus a "new since <date>" mode. Verify tool names live.
- [ ] Lint, trigger queries, hand to track B, commit `feat(skills): germline-variant-classification, literature-evidence-review`.

### Task 6: Skills S3 — `genetic-report-review` (+ references), `lay-summary`

**Files:** `skills/genetic-report-review/SKILL.md` + `references/{germline,cnv,negative,somatic,pgx,predictive-segregation}.md`; `skills/lay-summary/SKILL.md`; synthetic test reports `skills/genetic-report-review/references/examples/` NOT shipped — put scenario fixtures under `tests/fixtures/skills/` instead.

- [ ] Write per spec "Report-review specifics". Passes in order: anti-anchoring expectation → test-vs-question → identity/consistency → per-variant re-derivation → citation-to-allele → gene–disease & inheritance math → what was not tested → recommendations → freshness → communication. Output: verdict + Critical/Major/Minor + checks out + not checked/needs human.
- [ ] Build 3 synthetic reports with planted errors (wrong HGVS/transcript, PMID not about the allele, single het in an AR gene called diagnostic, missing limitations, VUS worded as causal) as pressure fixtures.
- [ ] Lint, trigger queries, commit `feat(skills): genetic-report-review, lay-summary`.

### Task 7: Benchmark A/B (track B, via the `genefoundry-bench-90` session)

- [ ] Send each finished skill's path; bench runs with-skill vs without, paired, development items only, local GPU/cluster, no paid runs.
- [ ] Phenotype and identifier skills on existing suites; report-review and lay-summary on the Task 6 synthetic scenarios (ask bench to run them as a small suite if its harness can grade with a rubric; otherwise run them with subagents here).
- [ ] Revise any skill that is worse; record results in the PR description.

### Task 8: Release prep

- [ ] `make ci-local` green; bump `pyproject.toml` patch/minor (new feature → 0.14.0), `uv lock`, CHANGELOG `## [0.14.0] - <date>`, `CITATION.cff` `version:`. ponytail-review of the branch. Open PR closing #336. Do not tag (owner).
