# GeneFoundry Response-Envelope Standard v1

> **Status: ADOPTED v1, ratified 2026-06-30.** This is the normative, enforced contract
> for the GeneFoundry `*-link` fleet. The contract is the **flat envelope** the entire
> fleet already ships: every tool returns a structured JSON object with `success`, a domain
> payload, `_meta` carrying `tool`/`request_id`/tiered `next_commands`/`capabilities_version`,
> typed flat execution errors (`error_code`, `message`, `retryable`, `recovery_action`),
> declared `output_schema`, `READ_ONLY_OPEN_WORLD`, `response_mode` defaulting to `compact`,
> and backend-owned provenance/disclaimers. The router remains a thin aggregator and must not
> reshape backend results. Open questions OQ1–OQ4 are resolved; see the Open Questions section.
> A stricter nested-error/`_meta`→`meta` migration target is recorded in the non-normative
> v2 appendix.

> Drafted 2026-06-16; revised the same day after an external review against
> **MCP 2025-11-25** (stable) and verified against the installed `mcp` + `fastmcp 3.4.2`.
> v1 ratified 2026-06-30. Sibling to `TOOL-NAMING-STANDARD-v1.md` and the Logging & CLI Standard.

Part of the **GeneFoundry MCP router** initiative (`genefoundry-router`): all `*-link` MCP
servers are federated behind one endpoint. The router is a **thin aggregator** — it
namespaces tools and rewrites tool-reference hints, but it **never reshapes a backend's
result payload or fabricates provenance**. Therefore response consistency, token economy,
and typed honesty flags can only come from the backends speaking one envelope. This
standard defines that envelope.

It exists because two independent Claude usage reports (2026-06-16) graded the federated
experience **8/10**, with the only sub-9 axes being **token efficiency** (verbose defaults;
all-null nested blocks) and **consistency** (four divergent result shapes across backends).
Provenance, observability, and discoverability already scored 9 — so v1 **keeps the parts
that scored well** (`recommended_citation`, model-visible metadata, `unsafe_for_clinical_use`)
and fixes only the shape drift, the bloated defaults, and prose-only limitations.

## Two metadata levels — do not conflate them

MCP's `CallToolResult` carries `meta` (wire: `_meta`) as a **sibling** of `structuredContent`
/`content`/`isError` (FastMCP exposes it as `ToolResult.meta`). Clients preserve it for
caching/correlation, but it is **not guaranteed to be rendered to the model**. So:

- **MCP result `_meta`** (`ToolResult.meta`): protocol/gateway/transport metadata — correlation
  echo, cache hints, and (optionally) a router-stamped `gateway` block. Infra reads it.
- **The envelope `_meta` block** (a key **inside** `structuredContent`, §4): everything the
  **model** must reason about — provenance, versions, staleness, typed limitations,
  pagination. The model reads it (via `structuredContent` + the mirrored text).

Observability fields the model needs live in the envelope `_meta`, **not** only in MCP `_meta`.

## Rules

### 1. One envelope — a JSON **object** in `structuredContent`

Every tool result returns an MCP **`structuredContent` object** in this exact frame, and —
per the MCP backwards-compat SHOULD — **also** serializes it into a mirrored `content[]`
TextContent block. `structuredContent` is authoritative; `content[]` mirrors it. The frame
is always a JSON object (never a bare array/scalar) so it has room for `_meta`/citation/error.

**Success (collection-returning tool):**
```json
{
  "success": true,
  "results": [
    { "id": "NBK1227:0024", "title": "…",
      "recommended_citation": "Adam MP, et al. GeneReviews®. NBK1227. …" }
  ],
  "_meta": { "request_id": "…", "elapsed_ms": 27, "source": "genereviews", "data_version": "2026-05" },
  "recommended_citation": null,
  "unsafe_for_clinical_use": true
}
```

**Success (single-item tool):** identical frame, but the payload key is **`result`** (object),
not `results` (array), and `recommended_citation` may sit at the top level.

- The primary payload key is **`results`** (array) or **`result`** (object) — **never** a
  domain-specific alias (`records`, `diseases`, `partners`, `passages`). This single rule
  retires the report's four-shape drift; a consumer parses one shape for the whole fleet.
- **No outer wrapping.** `{"result": {"results": [...]}}` (genereviews today) is non-compliant.
  The frame is the top level of `structuredContent`.
- A tool MAY add domain keys **beside** `results`/`result` (e.g. `query_echo`, `facets`) but
  MUST NOT replace or nest the primary key.
- Declare an **`outputSchema`** matching this frame for every tool (MCP: clients SHOULD
  validate against it; it gives the model type information).

### 2. Errors: MCP-native + in-band, always actionable

- **Protocol errors** → standard JSON-RPC errors. Invalid `tools/call` params and unknown
  tool are usually **`-32602`**; malformed JSON (`-32700`), invalid request (`-32600`),
  method-not-found (`-32601`), and server faults (`-32000..-32099`) use their own codes. Not
  this envelope's concern.
- **Execution errors** (bad input, not found, ambiguous, upstream down) → a normal result
  with MCP **`isError: true`** AND this in-band **flat** error frame:

```json
{
  "success": false,
  "error_code": "invalid_input",
  "message": "hgnc_id must look like 'HGNC:1100'; got '1100'. Prefix with 'HGNC:'.",
  "retryable": false,
  "recovery_action": "prefix the value with 'HGNC:'",
  "_meta": { "request_id": "…", "elapsed_ms": 3, "source": "hgnc" }
}
```

- `error_code` is a closed enum, harmonized with codes already used in the fleet:
  **`invalid_input` · `not_found` · `ambiguous_query` · `upstream_unavailable` ·
  `rate_limited` · `internal`**.
- `message` MUST be **specific and actionable** — tell the model how to fix the call
  (Anthropic: "communicate specific and actionable improvements, rather than opaque error
  codes or tracebacks"). No bare codes, no tracebacks.
- `retryable` (bool) lets a client branch on backoff vs. reformulate. (Note: use
  `retryable`, not `retriable` — the fleet ships `retryable` fleet-wide.)
- `recovery_action` (string, optional) — a short imperative hint for self-correction.
- `isError: true` is REQUIRED so clients surface the error to the model for self-correction
  (MCP: clients SHOULD pass execution errors to the LLM).

> The nested `error: {code, message, retriable, details}` shape from the pre-ratification
> draft is **not** the v1 contract. It is recorded in the non-normative v2 appendix.

### 3. Lean by default — token economy is a contract, not a lever

The reports' core token finding was *"the levers exist; the defaults don't respect them."*
v1 makes the lean path the default:

- **`response_mode` default is `compact`**, never `standard`/`full`. Enum (fleet canon):
  `minimal` (mandatory envelope — `success`, `_meta`, `recommended_citation`,
  `unsafe_for_clinical_use` — plus stable identifiers, omitting all optional record detail) ·
  `compact` (the triage-useful record subset) · `standard` · `full` (everything, including
  structured sub-objects).
- **Omit, don't null-pad.** Do NOT emit all-null nested objects or empty arrays. If
  `coverage_hint` is empty and `resolver_attempts` is `[]`, **drop the keys** (gate them
  behind the request that populates them, e.g. `coverage="preflight"`).
- **Collapse nested objects to a display string at `compact`.** Expose the structured form
  only at `full`. (E.g. an author is `"J. Doe"` by default; the
  `{last_name, fore_name, initials, …}` object appears only at `metadata="full"`.)
- **Surface semantic identifiers alongside opaque ones** — `name`/`label`/`symbol` next to
  any UUID or accession (Anthropic: semantic fields "directly inform agents' downstream
  actions" and cut hallucination).
- **Soft cap a single result at ~25,000 tokens** (Claude Code's default tool-response
  ceiling); beyond that, paginate (§5) or truncate with an explicit `_meta.truncated: true`.

### 4. The envelope `_meta` block — the observability contract (formalize the 9/10)

A `_meta` object inside `structuredContent` is REQUIRED on every result (success and error).
Field canon:

| Field | Req. | Notes |
|---|---|---|
| `request_id` | **MUST** | The `asgi-correlation-id` value (ties result ↔ structured logs; Logging Standard §3.2). Echo it into MCP `_meta` too. |
| `elapsed_ms` | **MUST** | Server-side wall-clock for this call. |
| `source` | **SHOULD** | Backend short name (`gnomad`, `pubtator`, …). |
| `data_version` / `snapshot_version` / `corpus_version` | as applies | Data provenance for the underlying dataset. |
| `capabilities_version` | SHOULD | Lets a warm client skip re-fetching capabilities when unchanged. |
| `pagination` | when paged | `{ total_count, has_more, next_cursor }` (§5). |
| `staleness` | when known | `{ years_since_update, band, likely_stale_for_therapeutics }` — typed, never prose. |
| `filtering` | when filtered | `{ exhaustive: bool, applied: [...] }`. **Replaces** prose honesty notes such as `pubtator3_filtering: "year_range_local"`. |
| `cost_tier` / `expected_cold_latency_ms` | long-running | Typed latency hints (see §7). |
| `source_versions` | optional | Map of upstream component → version. |
| `diagnostics` | opt-in | `{ rerank_used, candidate_counts, … }` — only when the caller asks. |

**Limitations MUST be typed** (booleans/enums under `_meta`), never prose-only — so a consumer
can branch programmatically (report Top-3 fix #3). Protocol/gateway metadata (cache hints, a
router `gateway` block) belongs in **MCP result `_meta`** (wire-level sibling of
`structuredContent`), not inside the envelope `_meta`.

> The rename of the envelope block from `_meta` to `meta` (dropping the leading underscore)
> is **not** part of the v1 contract. It is recorded in the non-normative v2 appendix.
> All v1 backends use `_meta` inside `structuredContent`.

### 5. Pagination — opaque cursors, distinct from MCP list pagination

> **Scope:** MCP's *native* pagination (top-level `nextCursor`) applies to **list**
> operations — `tools/list`, `resources/list`, `prompts/list`. This rule covers **tool
> result-payload** pagination, which is a GeneFoundry fleet convention carried in the
> envelope `_meta`, not an MCP-native field.

For any tool that can exceed a page:

- Accept the fleet-canon `limit`/`offset`, AND a forward-stable opaque **`cursor`** for large
  or mutating result sets (offset can skip/duplicate when rows change).
- The cursor is an **opaque** string — clients MUST NOT parse it; prefer **stateless** cursors
  encoded in the token over server-stored cursor IDs (MCP sessions are short-lived).
- Always populate `_meta.pagination`: **`total_count`** (or estimate), **`has_more`**,
  **`next_cursor`** (`null` on the last page) — the model needs these to decide whether to
  keep paging.
- An invalid/expired **tool-payload** cursor is an **execution error** (`isError: true`,
  `error_code: "invalid_input"`, `retryable: false`), never a silent first page. (Reserve
  JSON-RPC `-32602` for invalid cursors on MCP *list* operations.)

### 6. Provenance & safety — keep, and make universal

These already scored 9/10; v1 makes them non-optional across the whole fleet:

- **`recommended_citation`** — a verbatim-pasteable citation. For **heterogeneous** result
  sets (mixed PMID/DOI/NBK/MONDO), put it at the **record level** (each item carries its own);
  reserve the **top-level** `recommended_citation` for corpus/tool-level provenance (and set it
  `null` when records cite themselves). Stable field name fleet-wide. Paste verbatim; never
  paraphrased or fabricated.
- **`unsafe_for_clinical_use: true`** on every result that carries research data (mirrors the
  backends' disclaimer; research use only, not clinical decision support).
- Prefer stable, human-meaningful IDs (`NBK1227:0024`, MONDO, PMID/PMCID/DOI, HGNC) over
  internal row IDs.

### 7. Long-running tools declare task support

For tools with material cold latency (e.g. `spliceailookup-link`'s ~60s `predict_splicing`):

- Declare it in the **tool definition** via MCP `execution.taskSupport`
  (`"optional"` or `"required"`; verified present in `mcp`/`fastmcp 3.4.2` as
  `Tool.execution.taskSupport ∈ {forbidden, optional, required}`) so clients can run the call
  as an MCP **task** instead of blocking a turn.
- ALSO type the latency in the envelope `_meta` (`cost_tier`, `expected_cold_latency_ms`, §4) so
  an agent can plan before calling. (Verify FastMCP's task surface against the installed
  package before relying on it — per CLAUDE.md, FastMCP 3.x symbols are post-cutoff.)

## Per-backend adoption (from the 2026-06-16 reports)

| Backend | Today | v1 change |
|---|---|---|
| `genereviews-link` | `{"result": {"results": […]}}` | Unwrap to top-level `results` + frame |
| `stringdb-link` | bare `{partners, total_count}` | Adopt full frame (`success`/`_meta`/citation/safety); rename `partners`→`results` |
| `autopvs1-link` | `{ok, data, error, meta}` | Map to `{success, result(s), _meta, error_code/retryable}` |
| `spliceailookup-link` | bare typed dict; ~60s call | Adopt frame; `execution.taskSupport` + typed `_meta` latency (§7) |
| `pubtator-link` | verbose authors, null `coverage_hint`, prose `year_range_local` | §3 defaults + §4 `_meta.filtering.exhaustive` |
| the other 9 (`_meta`+payload) | already close | Confirm key is `results`/`result`; align `error_code`/`retryable`; default `compact` |

## References

- MCP tools spec (2025-11-25) — `structuredContent`/`content`/`isError`/`meta` (sibling),
  `outputSchema` validation, protocol-vs-execution errors, opaque stateful handles,
  `Tool.execution.taskSupport`. <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>
- MCP pagination (2025-11-25) — list-operation `nextCursor`; opaque cursors; invalid cursor
  → `-32602`. <https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination>
- MCP changelog (2025-11-25). <https://modelcontextprotocol.io/specification/2025-11-25/changelog>
- Anthropic Engineering, *Writing effective tools for AI agents* — `response_format` concise
  default (206→72 tokens), 25k-token cap, semantic IDs, actionable errors, pagination/
  filtering/truncation defaults. <https://www.anthropic.com/engineering/writing-tools-for-agents>
- Sibling fleet standards: `TOOL-NAMING-STANDARD-v1.md` (arg canon: `response_mode`, `limit`,
  `offset`, …); Logging & CLI Standard v1 (correlation IDs → `request_id`).

## Definition of Done (per repo)

- [ ] Every tool result is the §1 frame (object) in `structuredContent` (primary key
      `results`/`result`), mirrored into a `content[]` text block; `outputSchema` declared.
- [ ] Execution errors use `isError: true` + the §2 flat error frame (`error_code`,
      `message`, `retryable`, `recovery_action`) with the closed enum code and an
      actionable message; protocol errors use standard JSON-RPC codes.
- [ ] `response_mode` defaults to `compact`; null/empty blocks omitted; nested objects collapse
      to strings except at `full`.
- [ ] Envelope `_meta` carries `request_id` + `elapsed_ms` (min), plus the applicable provenance,
      `pagination`, `staleness`, and typed `filtering` fields; protocol/gateway metadata kept
      in MCP result `_meta` (wire-level sibling of `structuredContent`).
- [ ] Pagination uses opaque stateless cursors with `_meta.pagination.{total_count,has_more,next_cursor}`.
- [ ] `recommended_citation` (record-level for heterogeneous sets) + `unsafe_for_clinical_use`
      on every research-data result.
- [ ] Long-running tools declare `execution.taskSupport` + typed `_meta` latency hints.
- [ ] CI contract test: assert the frame keys, the error shape, and `compact`-default token
      economy on representative tools.
- [ ] MAJOR version bump + one-line `CHANGELOG` note (pre-alpha: no shims, no deprecation).
- [ ] Once compliant, delete any router-side `transform` stopgap for this backend in
      `servers.yaml`.

## Open questions — resolved 2026-06-30

All four open questions are resolved as part of v1 ratification.

- **OQ1 — Primary payload key: `results`/`result` vs generic `data`.**
  **Resolved → `results`/`result`.** Reads naturally for a research fleet; semantic over
  generic per Anthropic's tool-authoring guidance; near-zero migration cost since conformant
  backends already use this.

- **OQ2 — Cursor encoding: stateless opaque token vs server-stored cursor ID.**
  **Resolved → stateless opaque token.** Mandated by the fleet's Streamable-HTTP-only,
  stateless posture (MCP sessions are short-lived; server-stored IDs force sticky state the
  architecture forbids). `limit`/`offset` is the simple path; opaque `cursor` required only
  for large/mutating sets.

- **OQ3 — Should the router stamp MCP result `_meta.gateway`?**
  **Resolved → defer.** It is additive and legitimate (router-owned, wire-level `_meta`),
  but it is a router enhancement that touches the response hot path for a "nice to measure"
  gain and is not an envelope-standard blocker. Track as a separate router ticket; verify the
  FastMCP 3.4.2 `ToolResult.meta` write surface against the installed package before
  implementing (per CLAUDE.md post-cutoff rule).

- **OQ4 — Error + meta shape: flat banner contract vs strict nested Rules §2/§4 migration.**
  **Resolved → flat banner contract as v1 (Option A).** The entire live fleet implements
  the flat shape (`clingen_link/mcp/errors.py:322-337`; `error_code`+`retryable` fleet-wide;
  zero backends use nested `error:{…}`). MCP does **not** mandate an in-band error shape —
  only `isError: true` is required. The flat contract is fully spec-compliant; the nested
  rewrite buys nothing the model can observe and costs a fleet-wide breaking change. The
  nested-error and `_meta`→`meta` rename targets are recorded in the v2 appendix below.

*Resolved earlier (not re-litigated):* `structuredContent` is a framed **object**, not a
bare array (MCP stable defines structured content as an object; the frame needs room for
`_meta`/citation/`success`).

---

## Appendix: Non-normative v2 / future targets

The following were proposed in the pre-ratification draft but are **not part of the v1
contract**. They are recorded here so the work is not lost and can be revisited as a
coordinated v2 migration when justified.

### v2-A: Nested execution-error frame (§2 strict draft)

The pre-ratification draft proposed a nested `error` object:

```json
{
  "success": false,
  "error": {
    "code": "invalid_input",
    "message": "hgnc_id must look like 'HGNC:1100'; got '1100'. Prefix with 'HGNC:'.",
    "retryable": false,
    "details": { "field": "hgnc_id" }
  },
  "_meta": { "request_id": "…", "elapsed_ms": 3, "source": "hgnc" }
}
```

**Why deferred:** zero backends implement this shape; migrating would be a coordinated
breaking change across ~20 repos for an agent-ergonomics delta the live usage reports did not
flag. Note also the one-letter spelling trap: the pre-ratification draft mistakenly used
`retriable` (a one-letter trap — easy to misread as correct); any future v2 MUST use
`retryable` (the fleet-wide spelling, already canonical in v1). The code example above has
been corrected to `retryable` so it is safe to copy-paste.

### v2-B: Envelope block rename `_meta` → `meta`

The pre-ratification §4 proposed renaming the envelope block from `_meta` to `meta` (dropping
the leading underscore) to disambiguate from MCP's wire-level `_meta` sibling. **Why
deferred:** all conformant backends use `_meta` today; the rename costs a coordinated
fleet-wide change for a cosmetic readability gain. If picked up in v2, it must be a MAJOR
bump with a migration note in every backend's CHANGELOG.
