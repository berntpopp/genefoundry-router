# Fleet FastMCP-Core Not-Found Reflection Guard (Response-Envelope Standard v1.1 §Error-message sanitation — Fast-follow)

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- Status: DRAFT — pending operator review
- Author: security engineering (Claude, session 92270f93)
- Date: 2026-07-11
- Depends on: `docs/specs/2026-07-11-error-message-sanitation-fleet-sweep-design.md` (COMPLETE), which
  closed the *upstream* error-body leak. This spec closes the tracked fast-follow noted at the bottom
  of that programme and in `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md` §Error-message sanitation.
- Boundary: Research use only. Not clinical decision support. Mirror backend disclaimers. **No deploy** —
  operator owns redeploy and live probes.

## 1. Goal

Close the last error-path reflection surface fleet-wide: **FastMCP core reflects the caller's *own*
requested name/URI back to the caller (and to logs) *before* any backend middleware runs.** Add one
uniform, copied-per-repo guard across all 21 federated backends in `servers.yaml` **and the router
itself**, so that a hostile unknown tool name, unknown/malformed resource URI, or malformed params can
never reflect caller-supplied text — or the forbidden Unicode code points it may carry — into any
caller-visible field (structured_content **and** the TextContent mirror) or any log/telemetry sink.

### 1.1 Threat model and severity (do not overclaim)

This is a **caller self-reflection** surface: the hostile bytes are supplied *by* the caller and
reflected back *to* that same caller. That is materially lower-risk than the upstream external-data
injection the completed sweep closed, where a third party (an upstream API response) could inject into
a *different* caller's model context. We state this plainly and do **not** classify it as high-severity.

It is still worth closing, for two concrete reasons:

1. **Shared log/telemetry sinks.** The reflected name/URI — with its zero-width, bidi-override, C0/C1,
   and NUL code points — lands in operator logs and OTel spans. That is a log-injection / terminal-escape
   / PII vector against operators and downstream log processors, independent of who the caller is. The
   completed sweep already established that caller input must never reach a log sink; this extends that
   invariant to the pre-middleware core path.
2. **Agentic context integrity.** In a multi-tool agent loop the "caller" is an LLM. A tool result it did
   not author (`Unknown tool: '…ignore all previous instructions…'`) is attacker-shaped content entering
   that model's context. A fixed, name-free error removes the injection channel.

Severity: **Low–Medium** (self-reflection to caller + logs). Merge bar is correctness of the fix, not a
release-blocking emergency.

### 1.2 The residual, precisely (five sub-surfaces)

FastMCP 3.4.x (pinned `>=3.4.4,<4.0.0` fleet-wide) can leak on:

- **(a) Unknown TOOL name.** Core produces `Unknown tool: '<name>'`. Depending on the installed path this
  is either *raised* (as `fastmcp.exceptions.NotFoundError`, catchable by middleware) or *returned* as an
  `isError` `CallToolResult` whose TextContent echoes the name — the reference repos handle both.
- **(b) Unknown RESOURCE URI.** Core raises `NotFoundError("Unknown resource: '<uri>'")` /
  `ResourceError("Error reading resource '<uri>': <detail>")` and mirrors the URI into the JSON-RPC error.
- **(c) Malformed URI / params → pydantic `-32602`.** A URI that cannot be matched to a resource template,
  or malformed args, raise a pydantic invalid-params error that echoes the raw value *before* the
  backend's arg-validation middleware (installed by the completed sweep) can catch it. This is the same
  pydantic class as arg-validation, one layer earlier.
- **(d) OTel span exception attributes.** Where `opentelemetry-sdk` is installed, the reflected name/URI
  is captured as `exception.message` / `exception.stacktrace` on the active span.
- **(e) FastMCP validation-log handler.** FastMCP logs the pre-middleware validation failure on its own
  logger, which uses a **non-propagating Rich handler** — a filter attached only to the root logger does
  not see it. The scrub filter must be attached to FastMCP's actual logger *and its handlers*.

## 2. Authoritative inputs (the contract and the four references)

- The standard: `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md` §Error-message sanitation (Fast-follow note).
- **Reference fixes to copy** (already merged — study, do not re-derive):
  - `panelapp-link` v0.5.1 — `panelapp_link/mcp/middleware.py`: `on_call_tool` catches
    `FastMCPNotFoundError` → `unknown_tool_envelope()`; `on_read_resource` re-raises fixed URI-free
    `NotFoundError`/`ResourceError`; `_ValidationLogScrubFilter` + `install_validation_log_filter()`;
    it is also the one repo with `opentelemetry` as a hard dependency.
  - `clinvar-link` v0.4.1 — `clinvar_link/mcp/output_validation.py`: `install_protocol_error_handler`
    wraps the raw `_mcp_server.request_handlers` for `CallTool`/`ReadResource`/`GetPrompt` as the
    OUTERMOST layer; inspects the `isError` result (FastMCP *returns*, not raises, for unknown tool) and
    replaces any non-structured-envelope `isError` with a fixed message; catches dispatch exceptions.
  - `mondo-link` v0.3.1 + `hpo-link` v0.3.1 — `mcp/middleware.py`: registry **preflight**
    (`await fctx.fastmcp.get_tool(name)` returns `None` for unknown) → fixed name-free envelope BEFORE
    core dispatch; `on_read_resource` catches all → fixed URI-free `ResourceError`; mondo additionally
    installs an `_ExceptionSpanRedactor` OTel span processor behind a guarded import.

## 3. The fix: one layered guard, standardized

No shared runtime library exists across the fleet (each `-link` is its own repo/package), so — exactly
as with container hardening — the pattern is **copied per repo** into that repo's existing MCP
middleware / envelope module, reusing its existing envelope builders and `FORBIDDEN_CODEPOINTS`. The
guard is the *union* of the reference patterns, layered so it is correct regardless of whether the
installed FastMCP raises-or-returns for an unknown tool:

**Layer 1 — Registry preflight in `on_call_tool` (PRIMARY; mondo/hpo).** Before `call_next`, resolve
`await fctx.fastmcp.get_tool(name)`; if it returns `None`, return a fixed, name-free `unknown_tool_envelope()`
as a `ToolResult` carrying **both** `structured_content` and a matching TextContent mirror. Because this
never reaches core, sub-surface (a) is closed independent of the raise/return question. `_meta.tool` MUST
NOT echo the requested name.

**Layer 2 — `on_read_resource` boundary (all references).** Wrap `call_next`; on ANY exception, log the
exception *class* only and re-raise a fixed URI-free `ResourceError("Resource unavailable or not found.")`
`from None`. Closes (b) and the read-time part of (c).

**Layer 3 — Protocol-handler backstop (clinvar).** Wrap the raw `_mcp_server.request_handlers` for
`CallToolRequest` / `ReadResourceRequest` / `GetPromptRequest`, installed OUTERMOST. For CallTool: if the
result is an `isError` `CallToolResult` that is **not** one of our structured envelopes (no `error_code`),
replace it with the fixed tool-not-found envelope — this catches the *return* path of (a) and any
malformed dispatch. For ReadResource/GetPrompt: catch dispatch exceptions (including the malformed-URI
`-32602` raised before `on_read_resource` fires) and re-raise a fixed input-free message. Closes the
return-path of (a) and the protocol-parse part of (c).

**Layer 4 — arg/params-validation middleware (panelapp/mondo — MOSTLY ALREADY PRESENT).** The completed
error-sanitation sweep already installed `mask_error_details=True` + catch of FastMCP's own
`ValidationError`/bare `PydanticValidationError` in `on_call_tool` → fixed envelope redacting arg
name/value. This spec only *confirms* it and adds the **resource-URI** malformed `-32602` case (via
Layer 3), which the tool-arg guard does not cover.

**Layer 5 — validation-log scrub filter (panelapp).** Attach `_ValidationLogScrubFilter` to FastMCP's
actual logger (`fastmcp.server.server`, and the lowlevel `mcp.server.lowlevel` request logger where it
logs unknown-tool/read errors) **and to that logger's handlers** if the logger does not propagate. The
per-repo test must assert the hostile name/URI is absent from captured log records, which is what proves
the attachment point is correct.

**Layer 6 — OTel span redaction (mondo/panelapp).** Where `opentelemetry-sdk` is installed — a hard
dependency only in `panelapp-link`; `mondo-link` references it behind a guarded import — install a span
processor that redacts exception-event attributes (`exception.message`, `exception.stacktrace`). Where
the SDK is absent, the guarded import is a no-op; do not add the dependency.

### 3.1 The `unknown_tool_envelope()` / fixed-message contract (Decision D1)

Every fixed error MUST be built from constant strings only — never interpolate the requested name/URI,
`str(exc)`, or any upstream detail (the completed sweep established: sanitation strips code points but not
injection *prose*, so fixed constants are the only safe source). Reuse the repo's existing envelope shape
and `error_code` vocabulary (`not_found` / `invalid_input`). A backstop `sanitize_envelope()` over the
final payload is defense-in-depth, not the primary control.

## 4. Scope and per-repo classification

**22 units of work: 21 backends in `servers.yaml` + the router.** Each unit's subagent PROBES first
(drives the real MCP `Client` with the hostile unknown tool name + hostile unknown/malformed resource
URI) and only adds the pieces that are actually missing — matching "a backend already carrying (1)+(2)
only needs the residual pieces + a regression test."

| Class | Repos | Expected work |
|---|---|---|
| **Full fix present — verify + regression test only** | clinvar, panelapp | Confirm all three types guarded; add regression test if the hostile-Client test is missing. |
| **Preflight + resource present — close residual** | mondo (malformed-URI `-32602` OPEN per ledger), hpo | Add Layer 3 protocol backstop for the resource `-32602`; confirm log filter + (mondo) OTel. |
| **Partial (arg-guard only)** | autopvs1 | Add Layer 1 preflight + Layer 2 `on_read_resource` + Layer 3 resource `-32602`. |
| **Status unknown — PROBE then add missing** | uniprot, clingen, spliceai, stringdb, metadome, gtex, gnomad, vep, genereviews, gencc, pubtator, orphanet, mavedb, litvar, hgnc, mgi | Probe; add whichever layers are missing (most likely all of 1–3 + confirm 5). |
| **Router (architecturally unique)** | genefoundry-router | See §4.1. |

`omim-link` is out of scope (not in `servers.yaml`, no GitHub repo).

### 4.1 Router specifics

The router is a FastMCP aggregator that (a) mounts one proxy per namespace and (b) exposes meta-tools
`search_tools` / `call_tool`. It already runs a middleware stack (`WriteAuthorization`, `Metrics`,
`AuditLog`, `NamespaceHint`), so a **`NotFoundGuardMiddleware`** slots in cleanly — preflight via
`get_tool` covers both the meta-tools and the mounted-proxy tool names, and `on_read_resource` covers
mounted-proxy resources. Because each backend now carries its own guard, a call that *reaches* a backend
already returns a fixed envelope; the router residual is specifically the name/URI it rejects *itself*
before proxying (its own FastMCP core path) and its meta-tool surface. The router subagent must also
verify the `call_tool` meta-tool does not echo a bogus target tool name in its own error.

## 5. Versioning (Decision D2): non-breaking → PATCH

The guard only changes error-string *content* and adds fixed error paths; it does not change any success
schema or the shape of a structured error envelope (still `success`/`error_code`/`message`/`_meta`). Per
SemVer this is a **PATCH** bump for every repo (single-source `pyproject.toml`; `uv lock && uv sync`;
update the version-guard test if it hardcodes the version; CHANGELOG per repo convention — repos without a
CHANGELOG use the commit + `gh release` notes, as in the prior sweep). Router: `0.6.1 → 0.6.2`.

## 6. Testing strategy (TDD, per unit)

Write the failing test first, watch it fail, implement minimally, watch it pass. Every test drives the
**real** MCP surface via the FastMCP in-memory `Client` (`call_tool` / `read_resource`), never a shaping
helper (a first-pass mistake the prior sweeps caught). A shared hostile corpus keeps every repo
consistent:

- **Hostile tool name** carrying injection prose + every forbidden class: bidi override (`‮`),
  zero-width (`​`), NUL (`\x00`), and instruction prose, e.g.
  `"evil‮​\x00__IGNORE_ALL_PREVIOUS__nonexistent"`.
- **Hostile unknown resource URI** in the repo's scheme, e.g. `"<scheme>://‮​\x00evil/nope"`.
- **Malformed resource URI** that cannot match any template, e.g. `"::::‮%00not-a-uri"`.

Each test asserts the literal hostile substrings **and** the forbidden code points appear in **neither**
`structured_content` (recursively) **nor** the TextContent JSON mirror **nor** any captured log record
(via a `caplog`/handler capture that includes FastMCP's own non-propagating handler). For unknown tools,
assert the returned envelope carries the repo's `not_found`/`invalid_input` `error_code` and no
`_meta.tool` echo.

## 7. Adversarial review — Codex gpt-5.6-sol xhigh gates every merge

Run in the **background** from a trusted git repo dir (never Read the ~300KB log; extract the final
`VERDICT:` line only):

```
codex exec -s read-only -m gpt-5.6-sol -c model_reasoning_effort=xhigh -C <repo> "<prompt>" -o <verdict-file> < /dev/null &
```

Review prompt (tailored to this surface): "Adversarially verify this `feat/fastmcp-notfound-guard`
branch: drive the real MCP tools+resources via `call_tool`/`read_resource` with hostile unknown tool
names, unknown + malformed resource URIs, and hostile arg names, and confirm NO caller-supplied name/URI
and NO forbidden code points reach any caller-visible message/field or log in BOTH structured_content and
the TextContent mirror. Check the log-scrub filter is on FastMCP's actual (non-propagating) handler and,
where opentelemetry-sdk is installed, that span exception attributes are redacted. Report file:line +
severity + SHIP/FIX."

**Merge bar:** no reachable name/URI reflection to caller or logs. **Non-blocking** (mention, don't
block): version-bump semantics, test-assertion completeness, wording of fixed messages. Resume the same
implementer via SendMessage to apply fixes; re-review only to confirm a Critical/reachable leak is closed.

## 8. Per-unit definition of done

On a branch `feat/fastmcp-notfound-guard` cut from pristine `origin/main`
(`git fetch origin && git checkout -B feat/fastmcp-notfound-guard origin/main`):

1. Hostile-vector tests (real `Client` `call_tool` + `read_resource`; unknown tool, unknown + malformed
   URI) assert name/URI + forbidden code points absent from structured_content, TextContent mirror, and
   captured logs — **written first and seen to fail**.
2. Guard layers 1–3 present (5 confirmed; 6 where SDK present); built from fixed constants only.
3. `make ci-local` GREEN.
4. PATCH bump (`pyproject.toml`, `uv lock && uv sync`, version-guard test, CHANGELOG per convention).
5. Codex verdict `SHIP` (or all Critical/reachable-leak findings closed and re-confirmed).
6. Commit specific files (never `git add -A`), `git push origin HEAD:main` (STOP on unseen commits), and
   `gh release create v<x.y.z>`.

## 9. Definition of done (whole programme)

- All 21 backends + router carry the not-found/params guard, hostile-tested via the real MCP surface, and
  are patch-released.
- Router: standard `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md` §Error-message sanitation **Fast-follow note
  updated to "COMPLETE fleet-wide as of 2026-07-11"** with released versions listed; spec + plan committed;
  router `0.6.1 → 0.6.2` released.
- **Task 2 (independent):** genereviews-link's 2 pre-existing HIGH Dependabot advisories resolved in one
  consolidated PR, squash-merged on green (see the plan for sequencing vs the genereviews Task-1 branch).
- Memory updated (mark the fast-follow closed on both 2026-07-11 memory files).
- Operator owns redeploy + live probes.

## 10. Risks

- **`get_tool` on mounted proxies (router).** Preflight assumes `get_tool` resolves mounted-proxy tool
  names without a blocking remote round-trip. The router subagent verifies this against the installed
  FastMCP; if `get_tool` is unsuitable for proxied names, fall back to the Layer-3 protocol backstop,
  which does not depend on registry resolution.
- **Layer placement / raise-vs-return drift.** The exact FastMCP path may differ by installed patch
  version. The layered union is designed to be correct either way; the real-`Client` hostile test is the
  contract that proves it per repo (CLAUDE.md: "the integration test is the contract").
- **Log-handler attachment (Layer 5).** FastMCP's non-propagating Rich handler means a root-logger filter
  silently misses. The test's log-capture assertion is what catches a wrong attachment point.
- **Spend limit.** The prior sweep hit the Anthropic monthly spend limit mid-run. Execute in waves
  (≤6 concurrent) so a limit hit strands at most one wave, resumable via SendMessage.
- **Fleet-wide merges to unprotected main.** Gated by the Codex SHIP verdict + green ci-local per repo;
  STOP + report on any red ci-local or merge conflict; never push a broken main.
