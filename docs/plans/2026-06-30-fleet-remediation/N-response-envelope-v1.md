# Response-Envelope Standard v1 — Finalization Decision Brief

> Workstream N (P3, type=brief) of the 2026-06-30 fleet remediation.
> Scope: `docs/RESPONSE-ENVELOPE-STANDARD-v1.md` (DRAFT) + the `*-link` fleet + the router.
> This is **analysis, not a code plan**. No source file was modified; nothing was pushed.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin
generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per
task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local`
must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff,
fast-moving); no caller-`Authorization` passthrough to backends; Streamable-HTTP only (no SSE);
backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-
clinical-decision-support disclaimer preserved.

---

## Context & problem

The Response-Envelope Standard is the last un-ratified fleet contract. It is the strategic
consistency item: two independent 2026-06-16 Claude usage reports graded the federated experience
8/10, and the only sub-9 axes were **token efficiency** and **consistency** (four divergent result
shapes across backends). The standard exists to make all `*-link` backends speak one envelope so the
router — a thin aggregator that "never reshapes a backend's result payload" — does not have to.

**The document carries two contradictory contracts, and that contradiction is the real blocker.**

1. **The banner / de-facto contract** (`docs/RESPONSE-ENVELOPE-STANDARD-v1.md:3-13`). The
   "Status as of 2026-06-20" header pins the *currently enforced* `*-link` shape: every tool returns
   a JSON object with `success`, a domain payload, **`_meta`** (carrying `tool`/`request_id`/tiered
   `next_commands`/`capabilities_version`), **typed flat execution errors
   (`error_code`, `message`, `retryable`, `recovery_action`)**, declared `output_schema`,
   `READ_ONLY_OPEN_WORLD`, `response_mode` defaulting to `compact`, and backend-owned
   provenance/disclaimers. The header explicitly says the stricter frame below is "a future fleet-wide
   breaking migration, not as an orphanet/HPO/Mondo-specific compatibility defect."

2. **The strict Rules body §1-§7** (`docs/RESPONSE-ENVELOPE-STANDARD-v1.md:47-204`). This prescribes
   a *different* frame: primary key **`results`/`result`** (never a domain alias, §1, lines 49-82);
   a **nested** error object `error: {code, message, retriable, details}` with `isError: true`
   (§2, lines 83-113) — note `retriable`, one letter off from the fleet's `retryable`; and an
   envelope **`meta`** block that §4 (line 215) reaches by *renaming* the payload `_meta`→`meta`.

**Grounding evidence — what the fleet actually ships today:**

- **clingen-link is the de-facto conformant target, and it implements the BANNER, not the Rules.**
  `clingen_link/mcp/errors.py:401-405` (`run_mcp_tool`) injects `result.setdefault("success", True)`
  and merges a provenance **`_meta`** (not `meta`) into every success. Its error frame
  (`clingen_link/mcp/errors.py:322-337`) is **flat**: `{success: False, error_code, message,
  retryable, recovery_action, fallback_tool, recovery, _meta:{...}}` — not the nested `error:{...}`
  of Rules §2. `clingen_link/mcp/envelope.py:50-77` builds `_meta` from `data_version`/`next_commands`.
- **The flat shape is fleet-wide, not clingen-specific.** Across gnomad-link, hgnc-link, uniprot-link,
  mondo-link, and hpo-link the tool code uses `"error_code"` + `"retryable"` (e.g. 120/25, 25/6, 27/9,
  35/7, 13/4 occurrences respectively). A repo-wide grep for the strict nested `"error": {` in tool
  code of clingen/gnomad/hgnc/uniprot/mondo/hpo/stringdb returns **zero** hits (only docs and
  vendored `node_modules`). **No backend implements Rules §1-§7 as written.** Greenlighting the strict
  Rules verbatim would force a breaking rewrite on *every* backend, not just the laggards.

- **stringdb-link is the one genuine non-conformant backend, and it is a different animal.** It is a
  FastAPI REST app wrapped into MCP (`stringdb_link/server_manager.py` `UnifiedServerManager`;
  `mcp_server.py:28`), not a native FastMCP tool layer. Its list responses carry **no** envelope:
  `stringdb_link/models/responses.py:586-597` `InteractionPartnerListResponse = {partners, total_count}`
  — and the five sibling wrappers `:558-639` (`mappings`/`interactions`/`terms`/`annotations`/`scores`
  + `total_count`) are identical — with no `success`, no `_meta`, no `response_mode`, no `error_code`,
  no `recommended_citation`, no `unsafe_for_clinical_use`. Failures are raised as FastAPI
  `HTTPException(status_code=500, detail="Internal server error …")`
  (`stringdb_link/api/routes/networks.py:91-113, 188-200, 257-259`), so they cross the router as a raw
  HTTP 5xx `{detail}` with **no `isError: true` and no in-band envelope** — the model cannot
  self-correct. `stringdb_link/models/responses.py:468-490` `ErrorResponse = {error, message,
  status_code, details}` is its own shape, unrelated to either contract.

- **The router neither normalizes nor validates envelopes — by design.** `genefoundry_router/
  normalization.py:1-99` only applies FastMCP name/arg/tag `ToolTransform`s ("Stopgap normalization
  transforms for non-compliant backends"); a grep for `structuredContent`/`isError`/`CallToolResult`/
  `model_dump` across `genefoundry_router/` returns nothing. Per AGENTS.md and the standard's own §1,
  the router is a thin aggregator and **must not** reshape backend payloads. So envelope consistency
  can only come from the backends — there is no router-side rescue layer to lean on.

Net: the standard is undecidable as written because its banner and its body disagree, and the body
describes a fleet that does not exist. The finalization decision is therefore less "answer 4 trivia
questions" and more "pick which of the two contracts is v1, then scope the migration honestly."

## Open question(s)

The document's "Open questions" section (`docs/RESPONSE-ENVELOPE-STANDARD-v1.md:252-265`) lists three
numbered items plus one already-resolved note. The audit's "4 open questions" maps to these three
plus the load-bearing **banner-vs-Rules reconciliation** that the document never states explicitly but
which blocks any greenlight. Enumerated precisely:

- **OQ1 — Primary payload key.** `results`/`result` vs a generic `data`
  (doc Q1, line 254; current default: `results`/`result`).
- **OQ2 — Cursor encoding.** Stateless signed/opaque token vs server-stored cursor ID, picked once
  fleet-wide so the router can document it (doc Q2, line 256; current default: stateless opaque).
- **OQ3 — Router-stamped `_meta.gateway`.** Should the router add an MCP result `_meta.gateway` block
  (round-trip `elapsed_ms`, namespace) for through-the-wrapper observability (doc Q3, line 258;
  current default: defer).
- **OQ4 — Error + meta shape reconciliation (the real blocker).** Ratify the **flat** error contract
  the entire fleet already ships (`error_code`/`retryable` + `_meta`, banner, lines 3-13 and
  `clingen_link/mcp/errors.py:322-337`) vs migrate everyone to the **strict nested** Rules §2/§4
  shape (`error:{code,message,retriable,details}` + renamed `meta`). This is the doc's fourth, unstated
  open question and the one that determines whether v1 is "ratify what runs" or "rewrite the fleet."

*Already resolved (not re-litigated):* `structuredContent` is a framed JSON **object**, never a bare
array/scalar (`docs/RESPONSE-ENVELOPE-STANDARD-v1.md:263-265`), confirmed by the MCP 2025-11-25 tools
spec ("Structured content is returned as a JSON object in the `structuredContent` field").

## Options

### OQ1 — Primary payload key
- **A. Keep `results`/`result`** (array for collections, object for single items). Reads naturally for
  a research fleet; the doc's stated default. Cost: cosmetic only where a backend uses a domain alias.
- **B. Generic `data`.** One key for every tool, trivially machine-parseable. But `data` is
  contentless and the fleet/Anthropic guidance favors semantic naming; churns every backend for no
  agent-ergonomics win.
- **C. Allow either, document both.** Reintroduces exactly the four-shape drift v1 exists to kill.

### OQ2 — Cursor encoding
- **A. Stateless opaque token** (cursor encodes offset/filter state, signed/base64). Survives the
  fleet's short-lived stateless-HTTP sessions; nothing to store; router documents one rule. Cost: each
  paginating backend encodes/decodes a token.
- **B. Server-stored cursor ID.** Simple per-call, but needs server-side cursor state that dies with
  the stateless session and forces sticky routing through the gateway — at odds with Streamable-HTTP-
  only, stateless backends.
- **C. Offset/limit only (no cursor).** Cheapest, but can skip/duplicate rows on mutating sets; the
  standard already calls this out (§5, lines 166-178).

### OQ3 — Router-stamped `_meta.gateway`
- **A. Defer.** No router result-reshaping today (`normalization.py` does none); adding it touches the
  thin-aggregator boundary and the response hot path for a "nice to measure" gain. Revisit on demand.
- **B. Stamp now.** Answers the Speed-8 "can't measure through the wrapper" nit; it is *additive
  protocol* metadata in MCP result `_meta` (legitimately the gateway's own data, not the envelope
  `meta`), so it does not violate "never reshape the backend payload." Cost: new router middleware +
  tests + a careful read of the FastMCP 3.4.2 `ToolResult.meta` write surface.

### OQ4 — Error + meta shape (the blocker)
- **A. Ratify the flat banner contract as v1** (`success` + payload + `_meta` + flat
  `error_code`/`retryable`/`recovery_action`, `response_mode=compact`, `output_schema`,
  `recommended_citation`, `unsafe_for_clinical_use`). Matches what clingen/gnomad/hgnc/uniprot/mondo/
  hpo already ship; only stringdb (and the small genereviews/autopvs1/spliceai deltas in the doc's
  per-backend table, lines 206-216) must change. Cost: low; reuse the doc's banner as the normative
  text and demote §1-§7 to non-normative "rationale / future v2 ideas."
- **B. Migrate the fleet to the strict Rules §2/§4** (nested `error:{code,…,retriable}`, payload
  `_meta`→`meta`, primary key `results`/`result` enforced everywhere). Maximally clean on paper. Cost:
  a coordinated breaking change across ~20 repos, each needing a tool-layer rewrite, a contract-test
  rewrite, and a MAJOR bump — for an agent-ergonomics delta the live 8/10 reports did not flag (they
  praised provenance/observability 9/10; the gaps were token economy and *shape drift*, which the flat
  banner already fixes).
- **C. Hybrid.** Ratify the flat banner now (A) and record the nested/`meta`-renamed frame as an
  explicit, separate **v2** target with its own go-ahead — so the strict ideas are not lost but do not
  block shipping consistency now.

## Recommendation

**Greenlight a re-scoped v1 = the flat banner contract (OQ4 → A/C hybrid), and migrate only
stringdb-link now.** Then resolve the cosmetic questions as below.

- **OQ4 → Option A, with the Rules §1-§7 demoted to non-normative (a touch of C).** The whole live
  fleet already implements the flat shape (`clingen_link/mcp/errors.py:322-337`; `error_code`+
  `retryable` fleet-wide; zero backends use nested `error:{…}`). MCP does **not** mandate an in-band
  error *shape* — it defines `isError: true` as the only execution-error signal and leaves the payload
  to the application: *"Tool Execution Errors: Reported in tool results with `isError: true`… contain
  actionable feedback that language models can use to self-correct"* and the spec's own example is just
  a TextContent message, not a code/message object (MCP 2025-11-25, Tools → Error Handling). So the
  flat contract is fully spec-compliant; the nested rewrite buys nothing the model can observe and
  costs a fleet-wide breaking change. **Ratify the banner as the normative v1; move §2's nested-error
  and §4's `_meta`→`meta` rename into a clearly-labeled "v2 / future" appendix** so the work is
  recorded but not gating. Also fix the one-letter trap: standardize on **`retryable`** (what 100% of
  shipping code uses); strike `retriable` from the doc.
- **OQ1 → Option A: `results`/`result`.** Keep the doc default. Semantic over generic per Anthropic's
  tool-authoring guidance; near-zero migration cost since the conformant backends already do this.
- **OQ2 → Option A: stateless opaque cursor.** Mandated by the fleet's Streamable-HTTP-only, stateless
  posture (MCP sessions are short-lived; server-stored IDs force sticky state the architecture forbids).
  Keep `limit`/`offset` as the simple path and require an opaque `cursor` only for large/mutating sets,
  exactly as §5 states. An expired tool-payload cursor is an execution error (`isError:true`,
  `error_code:"invalid_input"`), never a silent first page.
- **OQ3 → Option A: defer.** It is additive and legitimate (router-owned `_meta.gateway`), but it is a
  router enhancement, not an envelope-standard blocker, and touches the response hot path. Track it as
  a separate router ticket; do not gate v1 on it. If picked up later it must verify the FastMCP 3.4.2
  `ToolResult.meta` write surface against the installed package first (CLAUDE.md post-cutoff rule).

**Go / defer call: GREENLIGHT the re-scoped (banner) v1; DEFER the strict §1-§7 migration to a future
v2.** This converts a stalled "rewrite the fleet" debate into a single concrete backend fix
(stringdb) plus a documentation tightening, and it is the smallest correct change that closes the
consistency gap the usage reports actually flagged.

## Impact / migration

If accepted, the changes are:

- **`docs/RESPONSE-ENVELOPE-STANDARD-v1.md` (router repo) — small edit.** Promote the banner (lines
  3-13) to the normative contract; move §2 nested-error and §4 `_meta`→`meta` rename into a non-
  normative "v2 future" appendix; replace `retriable` with `retryable`; flip "Status" from DRAFT to
  ADOPTED v1 with the resolved OQ1/OQ2/OQ3/OQ4 calls. No code. ~1 file, ~40 lines changed.
- **stringdb-link — the one real migration (Medium, ~1.5–2.5 days).** Add an MCP-tool wrapper layer
  (mirror `clingen_link/mcp/errors.py:run_mcp_tool`) that, per tool: injects `success`/`_meta`
  (`request_id` from `asgi-correlation-id`, `elapsed_ms`, `source:"stringdb"`, `data_version` =
  STRING version, `unsafe_for_clinical_use`), renames the list key to `results` (drop bare
  `partners`/`interactions`/… in `stringdb_link/models/responses.py:558-639`), and converts the
  `HTTPException(status_code=…)` raises in `stringdb_link/api/routes/*.py` into the flat in-band error
  frame with `isError: true` + closed `error_code` enum. Add `response_mode=compact`, declared
  `output_schema`, and a CI contract test. Watch the 600-LOC budget (the new wrapper is a new module,
  not bolted onto an existing route file). Note the open conformance residual (stringdb homology
  camelCase / `required_score ×1000`) lives in the same code — coordinate.
- **genereviews-link — Small (~0.5 day).** Unwrap `{"result": {"results": […]}}` to top-level
  `results` + banner frame (doc table, line 210). Mostly already close.
- **autopvs1-link — Small (~0.5 day).** Map `{ok, data, error, meta}` → `{success, result(s), _meta,
  error_code…}` (doc table, line 212).
- **spliceailookup-link — Small (~0.5 day).** Adopt the frame; declare `execution.taskSupport` and
  type the ~60s latency in `_meta` (§7) — verify the FastMCP 3.4.2 task surface against the installed
  package before relying on it.
- **The other ~13 backends (clingen/gnomad/hgnc/uniprot/mondo/hpo/…) — XS (~0–0.25 day each).** Already
  conform to the banner. Work is limited to: add/confirm a CI contract test asserting the banner keys
  and `compact`-default token economy, and confirm the primary key is `results`/`result`. **No
  `_meta`→`meta` rename, no nested-error rewrite** (that was the strict-Rules tax we are deferring).
- **genefoundry-router — None required for v1.** No envelope normalization or validation is added (thin
  aggregator). Optional, separate: a `make`-level conformance probe that asserts a sampled backend
  result carries `success` + `isError` semantics; OQ3 `_meta.gateway` is a deferred router ticket.

Rough total: one Medium (stringdb) + three Small + documentation = ~3–4 engineer-days to a consistent
fleet, vs the multi-week fleet-wide rewrite the strict Rules would have required.

## If accepted, the follow-on implementation plan is:

1. **Ratify the doc.** Edit `docs/RESPONSE-ENVELOPE-STANDARD-v1.md`: banner→normative, §1-§7 strict
   deltas→"v2 future" appendix, `retriable`→`retryable`, Status→ADOPTED, record OQ1-OQ4 resolutions and
   the per-backend conformance table. One atomic commit in the router repo.
2. **Lock the contract with a test fixture.** Add a tiny shared "envelope conformance" assertion (banner
   keys on success; `isError:true` + flat `error_code` on failure; `compact` default; `output_schema`
   declared) — start as a copy in the router's conformance probe, mirroring how the Transport Standard's
   vendored probe gates every repo's CI.
3. **Migrate stringdb-link (TDD).** Failing contract test first → add the `run_mcp_tool`-style wrapper
   module (new file, <600 LOC) → rename list keys to `results` → convert `HTTPException` raises to in-band
   `isError` envelopes → declare `output_schema` + `response_mode` → green. MAJOR bump + one-line
   CHANGELOG (pre-alpha: no shims). One atomic commit per step.
4. **Migrate genereviews-link, autopvs1-link, spliceailookup-link** the same TDD way (unwrap / map /
   taskSupport+latency respectively); one repo per branch, each with its own conformance test.
5. **Backfill CI tests on the already-conformant backends** (clingen/gnomad/hgnc/uniprot/mondo/hpo/…):
   add the conformance assertion to each repo's CI; fix any incidental drift it surfaces. No behavior
   change expected.
6. **Close out.** As each backend goes green, delete any router-side `transform` stopgap for it in
   `servers.yaml` (per CLAUDE.md / standard DoD line 250). File OQ3 `_meta.gateway` as a standalone
   router enhancement ticket; do not gate v1 on it.
7. **Run `make ci-local` in each touched repo before handoff.** Preserve the research-use-only /
   not-clinical-decision-support disclaimer in every envelope (`unsafe_for_clinical_use: true`).

## References

- MCP Tools spec (2025-11-25) — `isError:true` is the only tool-execution-error signal; no mandated
  in-band error *shape*; `structuredContent` is a JSON object that SHOULD mirror to a TextContent
  block; `outputSchema` "Servers MUST provide structured results that conform… Clients SHOULD
  validate"; `execution.taskSupport ∈ {forbidden, optional, required}`.
  <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>
- MCP Pagination spec (2025-11-25) — list-operation `nextCursor`; opaque cursors; invalid list cursor
  → `-32602` (grounds OQ2 and the §5 tool-payload-cursor distinction).
  <https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination>
- Anthropic Engineering, *Writing effective tools for AI agents* — concise `response_format` default,
  ~25k-token cap, semantic IDs over opaque ones, actionable errors (grounds OQ1 and the flat-error
  recommendation). <https://www.anthropic.com/engineering/writing-tools-for-agents>
- In-repo evidence: `docs/RESPONSE-ENVELOPE-STANDARD-v1.md:3-13` (banner), `:47-204` (strict Rules),
  `:206-216` (per-backend table), `:252-265` (open questions);
  `clingen-link/clingen_link/mcp/errors.py:322-337,401-405` and `mcp/envelope.py:50-77` (conformant
  flat banner);
  `stringdb-link/stringdb_link/models/responses.py:468-490,558-639` and
  `api/routes/networks.py:91-113` and `server_manager.py` (non-conformant FastAPI-wrapped outlier);
  `genefoundry-router/genefoundry_router/normalization.py:1-99` (router does no envelope reshaping).
- Sibling standards: `docs/TOOL-NAMING-STANDARD-v1.md` (arg canon: `response_mode`/`limit`/`offset`);
  `docs/MCP-TRANSPORT-STANDARD-v1.md` (the vendored-conformance-probe gating pattern this plan reuses);
  Logging & CLI Standard v1 (`request_id` correlation IDs).
