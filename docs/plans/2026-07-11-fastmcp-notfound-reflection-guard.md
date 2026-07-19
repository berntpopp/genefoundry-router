# Fleet FastMCP-Core Not-Found Reflection Guard — Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

Spec: `docs/specs/2026-07-11-fastmcp-notfound-reflection-guard-design.md`. Read it first.
Boundary: research-use-only; **no deploy** (operator owns redeploy + live probes).

## Global constraints (apply to every unit)

- **Branch off pristine remote, always.** Local clones are stale.
  `git -C <repo> fetch origin && git -C <repo> checkout -B feat/fastmcp-notfound-guard origin/main`.
- **TDD.** Failing hostile-Client test first → see it fail → minimal guard → see it pass.
- **Copy, don't invent.** Reuse the repo's existing envelope module, `error_code` vocabulary, and
  `FORBIDDEN_CODEPOINTS`. Standardize on the four reference implementations (spec §2).
- **Fixed constants only** in every error path — never interpolate name/URI/`str(exc)`/upstream detail.
- **`make ci-local` GREEN** before any commit. STOP + report on red ci-local or merge conflict.
- **Stage specific files** (never `git add -A`; leave stray `.sha256` / untracked artifacts alone).
- **Codex SHIP gate** before every push (spec §7). Extract the `VERDICT:` line; never Read the log.
- **Push** `git push origin HEAD:main` only after Codex SHIP + green ci-local; STOP if `git status`
  shows unseen upstream commits. Then `gh release create v<x.y.z>`.

## Task A — the guard primitive (per backend, copied)

Add to the repo's `mcp/middleware.py` (or the module holding its MCP middleware), reusing existing
builders. The union of reference layers (spec §3):

1. **Layer 1 preflight** in `on_call_tool`: `tool = await ctx.fastmcp_context.fastmcp.get_tool(name)`;
   `if tool is None: return ToolResult(structured_content=unknown_tool_envelope(), content=[TextContent(...)])`
   BEFORE `call_next`. `unknown_tool_envelope()` = fixed constants, `error_code="not_found"`, **no
   `_meta.tool` echo**. (mondo/hpo pattern.)
2. **Layer 2 `on_read_resource`**: `try: return await call_next(ctx) except Exception as exc:
   logger.warning("mcp_resource_error type=%s", type(exc).__name__); raise ResourceError("Resource
   unavailable or not found.") from None`. (all references.)
3. **Layer 3 protocol backstop** `install_protocol_error_handler(mcp_server)` wrapping
   `_mcp_server.request_handlers` for CallTool/ReadResource/GetPrompt, installed OUTERMOST at server
   build: replace any non-structured `isError` CallToolResult with the fixed envelope; catch
   ReadResource/GetPrompt dispatch exceptions (incl. malformed-URI `-32602`) → fixed input-free message.
   (clinvar pattern.) Wire it where the server/app is constructed, after existing handlers.
4. **Layer 4 (confirm only)** the arg-validation middleware from the prior sweep still catches FastMCP's
   own `ValidationError` + bare `PydanticValidationError`. If absent (shouldn't be), add it.
5. **Layer 5 log filter** `install_validation_log_filter()`: attach `_ValidationLogScrubFilter` to
   `logging.getLogger("fastmcp.server.server")` **and** its handlers, plus `mcp.server.lowlevel` if that
   is where the repo's captured unknown-tool log originates. Call it at server startup.
6. **Layer 6 OTel** only where `opentelemetry-sdk` is a dep (panelapp; mondo behind guarded import):
   install `_ExceptionSpanRedactor` span processor. Elsewhere: guarded no-op, do NOT add the dep.

**PROBE FIRST.** Before writing code, the subagent drives the real `Client` against the pristine branch
with the hostile vectors and records which sub-surfaces (a)–(e) actually leak on THIS repo. Only add the
layers needed to close observed leaks; always add the regression test.

## Task B (template) — harden one backend

Executed by one general-purpose subagent per repo (TDD). Steps:

1. `git fetch origin && git checkout -B feat/fastmcp-notfound-guard origin/main`.
2. **Probe** the real MCP surface with the hostile corpus (spec §6); record leaking sub-surfaces.
3. **Write failing tests** (`tests/…/test_notfound_guard.py` or repo convention): real `Client`
   `call_tool(hostile_name)`, `read_resource(hostile_uri)`, `read_resource(malformed_uri)`; assert the
   hostile substrings + forbidden code points are absent from structured_content (recursive),
   TextContent mirror, and captured logs (capture FastMCP's own handler). Watch them fail.
4. **Implement** the missing layers (Task A) minimally; watch tests pass.
5. `make ci-local` GREEN.
6. PATCH bump (`pyproject.toml`; `uv lock && uv sync`; version-guard test; CHANGELOG per convention).
7. Report back: leaking sub-surfaces found, layers added, files changed, ci-local result. **Do not push
   yet** — orchestrator runs the Codex gate.

### §B.table — per-repo starting parameters

| Repo | Ver → | Start class (spec §4) | Notes |
|---|---|---|---|
| clinvar | 0.4.1→0.4.2 | full — verify + regression test | reference; likely test-only |
| panelapp | 0.5.1→0.5.2 | full — verify + regression test | reference; has OTel hard dep + log filter |
| mondo | 0.3.1→0.3.2 | close residual | malformed-URI `-32602` OPEN → add Layer 3 resource path |
| hpo | 0.3.1→0.3.2 | close residual | confirm Layer 3 + log filter |
| autopvs1 | 4.0.1→4.0.2 | partial (arg-guard only) | add Layers 1+2+3 |
| uniprot | 3.0.1→3.0.2 | probe then add | SPARQL passthrough — check resource surface |
| clingen | 3.0.1→3.0.2 | probe then add | |
| spliceai | 3.0.3→3.0.4 | probe then add | server_name = spliceailookup-link |
| stringdb | 4.0.1→4.0.2 | probe then add | |
| metadome | 0.1.4→0.1.5 | probe then add | |
| gtex | 3.0.1→3.0.2 | probe then add | |
| gnomad | 8.0.1→8.0.2 | probe then add | has diagnostics ring |
| vep | 1.0.4→1.0.5 | probe then add | action-verb server |
| genereviews | 5.0.1→5.0.2 | probe then add | **branch AFTER Task 2 Dependabot merges** |
| gencc | 0.7.1→0.7.2 | probe then add | |
| pubtator | 6.1.1→6.1.2 | probe then add | large tool surface |
| orphanet | 0.3.1→0.3.2 | probe then add | |
| mavedb | 0.4.x→patch | probe then add | v1.1 reference for upstream sanitation |
| litvar | 5.0.x→patch | probe then add | v1.1 reference for upstream sanitation |
| hgnc | 2.0.1→2.0.2 | probe then add | |
| mgi | 0.5.1→0.5.2 | probe then add | |

(Confirm the exact current version from each repo's pristine `origin/main` `pyproject.toml` — the table's
"from" values are the last released versions per memory; bump the actual value found.)

## Task C — router (do carefully; spec §4.1)

One dedicated subagent (or orchestrator-driven). Branch `feat/fastmcp-notfound-guard` off router
`origin/main`.

1. Add `NotFoundGuardMiddleware` to `genefoundry_router/` (new module, ≤600 LOC) and register it in
   `server.py`'s middleware stack (after `NamespaceHint`), plus `install_protocol_error_handler` on the
   built server as the Layer-3 backstop. Reuse the router's existing envelope/error vocabulary
   (`genefoundry_router/exceptions.py`).
2. Verify `get_tool` resolves mounted-proxy tool names without a blocking remote round-trip; if not, rely
   on the Layer-3 backstop (spec §10). Verify the `call_tool` meta-tool does not echo a bogus target tool
   name in its own error.
3. Tests: real `Client` against the composed router (use the in-process fake fleet in
   `genefoundry_router/devtools/fake_fleet.py`) with hostile unknown tool + resource + malformed URI;
   assert absence in both mirrors + logs. `make ci-local` GREEN (incl. lint-loc 600-LOC budget, mypy).
4. Router bump `0.6.1 → 0.6.2`.

## Wave plan (≤6 concurrent — spend-limit safe, resumable)

Pilot wave validates the whole loop (pattern + hostile test + Codex gate + release) before fanning out:

- **Wave 0 (pilot, 3):** hpo (close-residual), autopvs1 (partial), gtex (probe-then-add). Confirm the
  test harness, the guard layers, and the Codex background gate all work end-to-end; refine the Task-A
  template and review prompt from what Wave 0 surfaces.
- **Wave 1 (verify refs, 2):** clinvar, panelapp — cheap; confirm the "verify-only" path.
- **Wave 2 (6):** uniprot, clingen, spliceai, stringdb, metadome, mondo.
- **Wave 3 (6):** gnomad, vep, gencc, pubtator, orphanet, mgi.
- **Wave 4 (4):** hgnc, mavedb, litvar, genereviews (genereviews only after Task 2 merges).
- **Wave 5 (1):** router (Task C).

Per wave: dispatch subagents (background) → collect "ready for review" → run Codex background gate per
repo → on SHIP + green, run release steps → on FIX, SendMessage the same implementer with the blocking
items, re-review only for Critical/reachable-leak. STOP the wave on any red ci-local / conflict / unseen
upstream commit and report.

## Release steps (per unit, after Codex SHIP + green ci-local)

```
# on feat/fastmcp-notfound-guard, ci-local green, Codex VERDICT: SHIP
# 1. bump pyproject.toml to §B.table target (PATCH); uv lock && uv sync
# 2. update version-guard test if it hardcodes the version
# 3. CHANGELOG per repo convention (repos w/o CHANGELOG: rely on commit + gh release notes)
# 4. git add <specific files>   (NOT -A)
# 5. git commit -m "fix(security): guard FastMCP-core not-found reflection (unknown tool/resource/-32602)"
# 6. git status  # STOP if unseen upstream commits
# 7. git push origin HEAD:main
# 8. gh release create v<x.y.z> --title "v<x.y.z>" --notes "..."
```

## Task 2 — genereviews Dependabot HIGH advisories (independent lane)

Use the `dependency-cve-sweep` recipe. **Sequence: do this FIRST for genereviews**, before the
genereviews Task-1 branch, so the not-found guard branches off the updated main.

1. `gh -R berntpopp/genereviews-link` list open Dependabot PRs + `gh api` Dependabot alerts (confirm the 2
   HIGHs; identify the package(s)).
2. Consolidate into ONE branch/PR: `uv lock --upgrade-package <pkg>` for each; SHA-pin any GitHub Action
   touched by the bump; regenerate any tool-catalog/version-guard fixture if the bump changes it.
3. Watch the **fastmcp `ValidationError` re-raise gotcha** (fastmcp 3.4.x re-wraps pydantic
   call-validation as `fastmcp.exceptions.ValidationError` with the pydantic error as `__cause__` — walk
   `__cause__`); a fastmcp bump can shift this. `make ci-local` GREEN.
4. Squash-merge on green. Confirm 0 open HIGH Dependabot alerts afterwards.

Task 2 can run in parallel with Waves 0–3 (different concern); it only blocks the genereviews Task-1
branch (Wave 4).

## Task D — router finish (after all 21 backends released)

1. `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md` §Error-message sanitation: change the Fast-follow note to
   **"COMPLETE fleet-wide as of 2026-07-11"** listing the released patch versions.
2. Ensure spec + plan are committed.
3. Router release (Task C) → `0.6.1 → 0.6.2`; push; `gh release create v0.6.2`.
4. Update memory: mark the FastMCP-core not-found fast-follow CLOSED on
   `error-message-sanitation-sweep-2026-07-11.md` and `untrusted-content-fencing-2026-07-11.md`;
   add MEMORY.md pointer if a new memory file is warranted.

## Verification (verification-before-completion)

Before claiming done: every unit shows Codex `VERDICT: SHIP` (or closed Criticals), green `make ci-local`
output, a pushed commit on main, and a created `gh release`. Re-run the hostile probe against at least the
router + one backend post-merge to confirm the guard is live in the built server (not just in tests).

## Self-review against the spec

- Every spec §4 unit appears in §B.table or Task C. ✔ (21 backends + router = 22)
- Every sub-surface (a)–(e) maps to a layer (1–6). ✔
- Versioning = PATCH everywhere, single-source. ✔
- Codex gate + merge bar match spec §7. ✔
- Task 2 sequencing vs genereviews Task-1 is explicit. ✔
- No deploy; operator owns redeploy. ✔
