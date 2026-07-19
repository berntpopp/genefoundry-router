# Fleet Agent Skills — Design

- **Date:** 2026-07-06
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Scope:** `genefoundry-router` + all real `-link` backends (21; `omim-link` is an empty
  stub and is skipped)
- **Goal:** Give every repo a consistent set of Claude Code / Agent-Skills-format
  `.claude/skills/*/SKILL.md` guides so agents follow fleet conventions (AGENTS.md,
  `make ci-local`, the fleet standards, and MCP security best practices) by default.
- **Status:** Implemented on a `chore/agent-skills` branch per repo (no push/PR).

## Why

Six `-link` repos already ship `.claude/skills/` (autopvs1, gencc, genereviews, gtex,
pubtator, spliceailookup) with short, workflow-oriented skills that all open with
"Follow `AGENTS.md` first." The router and 15 backends had none, and no repo captured the
newer, cross-cutting concerns the fleet actually spends time on: code-quality review,
security review, the recurring Dependabot/CodeQL sweep, and adopting a fleet standard to
its Definition of Done.

## Format

Agent Skills standard (agentskills.io / Anthropic): a directory per skill containing
`SKILL.md` with YAML frontmatter (`name`, `description`) + Markdown body. Conventions
followed:

- `name`: lowercase-hyphen, matches the directory name.
- `description`: third person, starts with "Use when …", triggering conditions only —
  never a workflow summary (a workflow summary in the description causes agents to follow
  the description instead of reading the body).
- Body: concise (≈150–300 words), keyword-rich, "Follow `AGENTS.md` first."

## Inventory

### Fleet baseline (every real `-link` repo; write only where missing — never clobber)

1. `ci-failure-triage` — triage a failing `make ci-local` sub-target.
2. `mcp-tool-change` — add/rename/change MCP tools, resources, prompts, schemas.
3. `fastapi-route-change` — add/change FastAPI routes on the `-link` HTTP facade.
4. `release-readiness` — pre-tag / pre-publish checklist.
5. `code-quality-review` *(new)* — severity-classified review, security-first scan order.
6. `security-review` *(new)* — MCP + fleet security checklist (token passthrough, PII in
   logs, SSRF fixed-host, CORS, container-hardening DoD, prompt-injection = treat retrieved
   text as data, research-use scope, destructive-tool gating).
7. `dependency-cve-sweep` *(new)* — one-PR-per-repo Dependabot/CodeQL sweep recipe, incl.
   the `fastmcp.exceptions.ValidationError` `__cause__` gotcha and SHA-pinned actions.
8. `fleet-standard-adoption` *(new)* — drive a fleet standard (tool-naming / response-
   envelope / container-hardening / versioning / transport) to its documented DoD.

### Router-tailored set (`genefoundry-router` only)

`ci-failure-triage`, `release-readiness`, `code-quality-review`, `security-review`,
`dependency-cve-sweep`, `fleet-standard-adoption` (as above, tailored to the aggregator),
plus four router-only skills:

9. `backend-registry-change` *(new)* — edit `servers.yaml` (`name`/`repo`/`url_env`/`namespace`/
   `tags`/`entrypoints`, `enabled`, optional `server_name`/`transform`), then `make validate
   list-tools fleet-probe snapshot-catalog`.
10. `auth-change` *(new)* — `auth.py` + `config.py` + `cli.py`: JWT/OAuth modes, RFC 9728 PRM,
    RFC 8707 audience, the no-token-passthrough invariant (`make_proxy_client` in
    `composition.py`), and the secure-by-default bind guard (`is_insecure_public_bind` in
    `cli.py`).
11. `fleet-drift-conformance` *(new)* — `drift.py`, conformance, `fleet-probe`, the snapshot
    catalog/baseline; the CI≠prod gap and the tool-poisoning/rug-pull tripwire.
12. `tool-search-change` *(new)* — the router's synthetic `search_tools` / `call_tool` meta-tools
    (`tool_search.py`), the BM25 discovery index, and hint/normalization rewriting.

The router has no `mcp-tool-change` (it authors no backend *leaf* tools — only the synthetic
`search_tools`/`call_tool`, covered by `tool-search-change`) and no `fastapi-route-change` (it
runs a FastMCP host with `/health`, `/metrics`, and auth well-known routes, but those changes
are covered by `security-review`, `auth-change`, and `fleet-drift-conformance` rather than a
backend-style REST-route skill).

**Distinct skill bodies:** 14 — 4 shared (`ci-failure-triage`, `code-quality-review`,
`dependency-cve-sweep`, `fleet-standard-adoption`), 4 `-link`-specific (`mcp-tool-change`,
`fastapi-route-change`, `release-readiness`, `security-review`), 6 router-only
(`release-readiness`, `security-review`, `backend-registry-change`, `auth-change`,
`fleet-drift-conformance`, `tool-search-change`).

## Grounding

- Fleet docs: `TOOL-NAMING-STANDARD-v1`, `RESPONSE-ENVELOPE-STANDARD-v1`,
  `CONTAINER-HARDENING-STANDARD-v1`, `VERSIONING-STANDARD-v1`, `MCP-TRANSPORT-STANDARD-v1`,
  `SECURITY-ASSESSMENT-2026-06-29`.
- External MCP security (2026): modelcontextprotocol.io security best practices, OWASP MCP
  Security Cheat Sheet + "Practical Guide for Secure MCP Server Development", CSA Agentic MCP
  guide, NSA/CISA CSI. Token passthrough is spec-forbidden; tool poisoning / confused deputy
  / indirect prompt injection are the primary read-only-server risks.

## Delivery

One generator (`scripts/one-off, not committed`) writes the templates into each repo, creates
a `chore/agent-skills` branch, and commits only `.claude/skills/`. Never overwrites an existing
skill. `omim-link` skipped (no code).
