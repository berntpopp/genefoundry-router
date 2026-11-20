# Research workflow skills

This folder holds **research workflow skills** in the [agentskills.io](https://agentskills.io)
format (`<name>/SKILL.md`, `name` + `description` frontmatter, optional `references/`). The router
serves every folder here to MCP clients as a `skill://<name>/SKILL.md` resource (MCP Skills,
SEP-2640: `skills/list`, `skills/get`) via FastMCP's `SkillsDirectoryProvider`, and `search_tools`
returns a `skill_<name>` hit that points at it. `GF_SKILLS_DIR` (default `skills`) must exist or
the router refuses to start.

These are **product content** for agents using the GeneFoundry fleet (e.g. how to chain tools for
a variant review). Developer instructions for working on this repo live in `.claude/skills/`
(mirrored at `.agents/skills`), not here.

## Rules for every skill

`make lint-skills` (part of `make ci-local`, script `scripts/check_skills.py`) enforces:

- frontmatter `name` equals the folder name (`a-z0-9-`, at most 64 chars); `description` starts
  with "Use when" and is at most 1024 chars;
- `SKILL.md` is under 20 kB (put detail in `references/`);
- every backticked `<namespace>_<tool>` in the skill's Markdown exists in the catalog snapshot
  (`tests/discoverability/catalog.json`);
- `SKILL.md` contains the shared rules block below, byte-identical.

Research use only. Not clinical decision support; skills must mirror the backends' disclaimers.

## Shared rules block

Copy this block unchanged into every `SKILL.md`:

<!-- shared-rules:start -->
## Evidence rules (all GeneFoundry skills)

- Research use only. Not clinical decision support. Use de-identified input only; if the input
  contains names, dates of birth or record numbers, stop and ask for a de-identified version.
- Every fact comes from a tool result in this session. Cite the record id the tool returned
  (PMID, VCV, HGNC id, MONDO id, ...). Never cite from memory.
- Give every checked claim one state: **confirmed** (tool agrees), **contradicted** (tool disagrees;
  show the evidence), **not confirmable** (tool returned nothing or failed), **not checked**.
  "Not found in source X" never means "negative" or "benign".
- State genome build and transcript before any variant lookup. Resolve identifiers once, in batch.
- Scores, points and risks are added up step by step by the stated rule, never estimated.
- If a call errors or is rate limited, do not repeat the identical call: use another source or
  report the gap. Stop when the skill's stop rule is met.
- End with: tools used and the data versions they report.
<!-- shared-rules:end -->
