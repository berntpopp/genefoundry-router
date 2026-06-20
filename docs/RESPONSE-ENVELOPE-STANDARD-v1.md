# GeneFoundry Response-Envelope Standard v1

> **Status as of 2026-06-20:** this document records the stricter envelope migration target
> from the 2026-06-16 review. It is **not yet the enforced current fleet gate**. The current
> router-compatible `*-link` contract, used by Mondo/HPO/Orphanet/MaveDB/MetaDome-style
> servers, is: every tool returns a structured JSON object with `success`, a domain payload,
> `_meta` carrying `tool`/`request_id`/tiered `next_commands`/`capabilities_version`, typed
> flat execution errors (`error_code`, `message`, `retryable`, `recovery_action`), declared
> `output_schema`, `READ_ONLY_OPEN_WORLD`, `response_mode` defaulting to `compact`, and
> backend-owned provenance/disclaimers. The router remains a thin aggregator and must not
> reshape backend results to this target frame. Treat the `result`/`results` + `meta` frame
> below as a future fleet-wide breaking migration, not as an orphanet/HPO/Mondo-specific
> compatibility defect.

> Drafted 2026-06-16; revised the same day after an external review against
> **MCP 2025-11-25** (stable) and verified against the installed `mcp` + `fastmcp 3.4.2`.
> Sibling to `TOOL-NAMING-STANDARD-v1.md` and the Logging & CLI Standard.

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
- **The envelope `meta` block** (a key **inside** `structuredContent`, §4): everything the
  **model** must reason about — provenance, versions, staleness, typed limitations,
  pagination. The model reads it (via `structuredContent` + the mirrored text).

Observability fields the model needs live in the envelope `meta`, **not** only in MCP `_meta`.

## Rules

### 1. One envelope — a JSON **object** in `structuredContent`

Every tool result returns an MCP **`structuredContent` object** in this exact frame, and —
per the MCP backwards-compat SHOULD — **also** serializes it into a mirrored `content[]`
TextContent block. `structuredContent` is authoritative; `content[]` mirrors it. The frame
is always a JSON object (never a bare array/scalar) so it has room for `meta`/citation/error.

**Success (collection-returning tool):**
```json
{
  "success": true,
  "results": [
    { "id": "NBK1227:0024", "title": "…",
      "recommended_citation": "Adam MP, et al. GeneReviews®. NBK1227. …" }
  ],
  "meta": { "request_id": "…", "elapsed_ms": 27, "source": "genereviews", "data_version": "2026-05" },
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
  with MCP **`isError: true`** AND this in-band frame:

```json
{
  "success": false,
  "error": {
    "code": "invalid_input",
    "message": "hgnc_id must look like 'HGNC:1100'; got '1100'. Prefix with 'HGNC:'.",
    "retriable": false,
    "details": { "field": "hgnc_id" }
  },
  "meta": { "request_id": "…", "elapsed_ms": 3, "source": "hgnc" }
}
```

- `error.code` is a closed enum, harmonized with codes already used in the fleet:
  **`invalid_input` · `not_found` · `ambiguous_query` · `upstream_unavailable` ·
  `rate_limited` · `internal`**.
- `error.message` MUST be **specific and actionable** — tell the model how to fix the call
  (Anthropic: "communicate specific and actionable improvements, rather than opaque error
  codes or tracebacks"). No bare codes, no tracebacks.
- `error.retriable` (bool) lets a client branch on backoff vs. reformulate.
- `isError: true` is REQUIRED so clients surface the error to the model for self-correction
  (MCP: clients SHOULD pass execution errors to the LLM).

### 3. Lean by default — token economy is a contract, not a lever

The reports' core token finding was *"the levers exist; the defaults don't respect them."*
v1 makes the lean path the default:

- **`response_mode` default is `compact`**, never `standard`/`full`. Enum (fleet canon):
  `minimal` (mandatory envelope — `success`, `meta`, `recommended_citation`,
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
  ceiling); beyond that, paginate (§5) or truncate with an explicit `meta.truncated: true`.

### 4. The envelope `meta` block — the observability contract (formalize the 9/10)

A `meta` object inside `structuredContent` is REQUIRED on every result (success and error).
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

**Limitations MUST be typed** (booleans/enums under `meta`), never prose-only — so a consumer
can branch programmatically (report Top-3 fix #3). Protocol/gateway metadata (cache hints, a
router `gateway` block) belongs in **MCP result `_meta`**, not here.

### 5. Pagination — opaque cursors, distinct from MCP list pagination

> **Scope:** MCP's *native* pagination (top-level `nextCursor`) applies to **list**
> operations — `tools/list`, `resources/list`, `prompts/list`. This rule covers **tool
> result-payload** pagination, which is a GeneFoundry fleet convention carried in the
> envelope `meta`, not an MCP-native field.

For any tool that can exceed a page:

- Accept the fleet-canon `limit`/`offset`, AND a forward-stable opaque **`cursor`** for large
  or mutating result sets (offset can skip/duplicate when rows change).
- The cursor is an **opaque** string — clients MUST NOT parse it; prefer **stateless** cursors
  encoded in the token over server-stored cursor IDs (MCP sessions are short-lived).
- Always populate `meta.pagination`: **`total_count`** (or estimate), **`has_more`**,
  **`next_cursor`** (`null` on the last page) — the model needs these to decide whether to
  keep paging.
- An invalid/expired **tool-payload** cursor is an **execution error** (`isError: true`,
  `error.code: "invalid_input"`, `retriable: false`), never a silent first page. (Reserve
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
- ALSO type the latency in the envelope `meta` (`cost_tier`, `expected_cold_latency_ms`, §4) so
  an agent can plan before calling. (Verify FastMCP's task surface against the installed
  package before relying on it — per CLAUDE.md, FastMCP 3.x symbols are post-cutoff.)

## Per-backend adoption (from the 2026-06-16 reports)

| Backend | Today | v1 change |
|---|---|---|
| `genereviews-link` | `{"result": {"results": […]}}` | Unwrap to top-level `results` + frame |
| `stringdb-link` | bare `{partners, total_count}` | Adopt full frame (`success`/`meta`/citation/safety); rename `partners`→`results` |
| `autopvs1-link` | `{ok, data, error, meta}` | Map to `{success, result(s), meta, error}` |
| `spliceailookup-link` | bare typed dict; ~60s call | Adopt frame; `execution.taskSupport` + typed `meta` latency (§7) |
| `pubtator-link` | verbose authors, null `coverage_hint`, prose `year_range_local` | §3 defaults + §4 `meta.filtering.exhaustive` |
| the other 9 (`_meta`+payload) | already close | Rename payload `_meta`→`meta`; confirm key is `results`/`result`; align `error`; default `compact` |

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
- [ ] Execution errors use `isError: true` + the §2 error frame with the enum code and an
      actionable message; protocol errors use standard JSON-RPC codes.
- [ ] `response_mode` defaults to `compact`; null/empty blocks omitted; nested objects collapse
      to strings except at `full`.
- [ ] Envelope `meta` carries `request_id` + `elapsed_ms` (min), plus the applicable provenance,
      `pagination`, `staleness`, and typed `filtering` fields; payload `_meta` renamed to `meta`;
      protocol/gateway metadata kept in MCP result `_meta`.
- [ ] Pagination uses opaque cursors with `meta.pagination.{total_count,has_more,next_cursor}`.
- [ ] `recommended_citation` (record-level for heterogeneous sets) + `unsafe_for_clinical_use`
      on every research-data result.
- [ ] Long-running tools declare `execution.taskSupport` + typed `meta` latency hints.
- [ ] CI contract test: assert the frame keys, the error shape, and `compact`-default token
      economy on representative tools.
- [ ] MAJOR version bump + one-line `CHANGELOG` note (pre-alpha: no shims, no deprecation).
- [ ] Once compliant, delete any router-side `transform` stopgap for this backend in
      `servers.yaml`.

## Open questions

1. **`results`/`result` vs a generic `data`.** v1 picks `results`/`result` (reads better for a
   research fleet; `data` is contentless). *(Default: `results`/`result`.)*
2. **Cursor encoding.** Stateless signed/opaque token vs. server-stored ID — pick one
   fleet-wide so the router can document it once. *(Default: stateless opaque.)*
3. **Should the router stamp MCP result `_meta.gateway`** (round-trip `elapsed_ms`, namespace)?
   This is additive protocol metadata about the gateway itself — inside the thin-aggregator
   boundary, and the correct home for it (not the envelope `meta`). Addresses the Speed-8
   "can't measure through the wrapper" nit. *(Default: defer; revisit if needed.)*

*Resolved in revision:* `structuredContent` is a framed **object**, not a bare array (MCP
stable defines structured content as an object for our purposes, and the frame needs room for
`meta`/citation/`success`) — this is now mandatory, not open.
