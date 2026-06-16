# GeneFoundry Response-Envelope Standard v1

> Canonical reference for the GeneFoundry `-link` MCP fleet. Drafted 2026-06-16.
> Sibling to `TOOL-NAMING-STANDARD-v1.md` and the Logging & CLI Standard. A tracking
> issue titled "Adopt GeneFoundry Response-Envelope Standard v1" should be filed in each
> `-link` repo (only with explicit go-ahead — these touch public, live APIs).

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
that scored well** (`recommended_citation`, `_meta`, `unsafe_for_clinical_use`) and fixes
only the shape drift, the bloated defaults, and prose-only limitations.

## Rules

### 1. One envelope, carried in `structuredContent`

Every tool result returns an MCP **`structuredContent`** object in this exact frame, and —
per the MCP spec's backwards-compat SHOULD — **also** serializes it into a mirrored
`content[]` TextContent block. `structuredContent` is authoritative; `content[]` mirrors it.

**Success (collection-returning tool):**
```json
{
  "success": true,
  "results": [ { "...": "one item per element" } ],
  "_meta": { "request_id": "…", "elapsed_ms": 27, "source": "genereviews", "...": "§4" },
  "recommended_citation": "Author A, et al. GeneReviews. NBK1227. …",
  "unsafe_for_clinical_use": true
}
```

**Success (single-item tool):** identical frame, but the payload key is **`result`** (object),
not `results` (array).

- The primary payload key is **`results`** (array) or **`result`** (object) — **never** a
  domain-specific alias (`records`, `diseases`, `partners`, `passages`). This single rule
  retires the report's four-shape drift; a consumer parses one shape for the whole fleet.
- **No outer wrapping.** `{"result": {"results": [...]}}` (genereviews today) is non-compliant.
  The frame is the top level of `structuredContent`.
- A tool MAY add domain top-level keys **beside** `results`/`result` (e.g. `query_echo`,
  `facets`) but MUST NOT replace or nest the primary key.
- Define an **`outputSchema`** for every tool that matches this frame (MCP: clients SHOULD
  validate against it; it gives the model type information).

### 2. Errors: MCP-native + in-band, always actionable

Distinguish the two MCP error classes:

- **Protocol errors** (unknown tool, malformed request) → JSON-RPC error, code **`-32602`**.
  Not this envelope's concern.
- **Execution errors** (bad input, not found, ambiguous, upstream down) → return a normal
  result with MCP **`isError: true`** AND this in-band frame:

```json
{
  "success": false,
  "error": {
    "code": "invalid_input",
    "message": "hgnc_id must look like 'HGNC:1100'; got '1100'. Prefix with 'HGNC:'.",
    "retriable": false,
    "details": { "field": "hgnc_id" }
  },
  "_meta": { "request_id": "…", "elapsed_ms": 3, "source": "hgnc" }
}
```

- `error.code` is a closed enum, harmonized with the codes already used in the fleet:
  **`invalid_input` · `not_found` · `ambiguous_query` · `upstream_unavailable` ·
  `rate_limited` · `internal`**.
- `error.message` MUST be **specific and actionable** — tell the model how to fix the call
  (Anthropic: "communicate specific and actionable improvements, rather than opaque error
  codes or tracebacks"). No bare codes, no tracebacks.
- `error.retriable` (bool) lets a client branch on backoff vs. reformulate.
- Setting `isError: true` is REQUIRED so clients surface the error to the model for
  self-correction (MCP: clients SHOULD pass execution errors to the LLM).

### 3. Lean by default — token economy is a contract, not a lever

The reports' core token finding was *"the levers exist; the defaults don't respect them."*
v1 makes the lean path the default:

- **`response_mode` default is `compact`**, never `standard`/`full`. Enum (fleet canon):
  `minimal` (stable IDs + `recommended_citation` only) · `compact` (the triage-useful
  subset) · `standard` · `full` (everything, including structured sub-objects).
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

### 4. `_meta` — the observability contract (formalize the 9/10)

`_meta` is REQUIRED on every result (success and error). Field canon:

| Field | Req. | Notes |
|---|---|---|
| `request_id` | **MUST** | The `asgi-correlation-id` value (ties result ↔ structured logs; Logging Standard §3.2). |
| `elapsed_ms` | **MUST** | Server-side wall-clock for this call. |
| `source` | **SHOULD** | Backend short name (`gnomad`, `pubtator`, …). |
| `data_version` / `snapshot_version` / `corpus_version` | as applies | Data provenance for the underlying dataset. |
| `capabilities_version` | SHOULD | Lets a warm client skip re-fetching capabilities when unchanged. |
| `pagination` | when paged | `{ total_count, has_more, next_cursor }` (§5). |
| `staleness` | when known | `{ years_since_update, band, likely_stale_for_therapeutics }` — typed, never prose. |
| `filtering` | when filtered | `{ exhaustive: bool, applied: [...] }`. **Replaces** prose honesty notes such as `pubtator3_filtering: "year_range_local"`. |
| `source_versions` | optional | Map of upstream component → version. |
| `diagnostics` | opt-in | `{ rerank_used, candidate_counts, … }` — only when the caller asks. |

**Limitations MUST be typed** (booleans/enums under `_meta`), never prose-only — so a
consumer can branch programmatically (report Top-3 fix #3).

### 5. Pagination — opaque, stateless cursors

For any tool that can exceed a page:

- Accept the fleet-canon `limit`/`offset`, AND a forward-stable opaque **`cursor`** for
  large or mutating result sets (offset can skip/duplicate when rows change).
- The cursor is an **opaque** string — clients MUST NOT parse it; prefer **stateless**
  cursors encoded in the token over server-stored cursor IDs (MCP sessions are short-lived).
- Always populate `_meta.pagination`: **`total_count`** (or estimate), **`has_more`**,
  **`next_cursor`** (`null` on the last page) — the model needs these to decide whether to
  keep paging.
- An invalid/expired cursor is an **execution error** (`error.code: "invalid_input"`,
  `retriable: false`), never a silent first page.

### 6. Provenance & safety — keep, and make universal

These already scored 9/10; v1 makes them non-optional across the whole fleet:

- **`recommended_citation`** — a verbatim-pasteable citation, per result (and per record
  where records are independently citable). Stable field name fleet-wide. `null` only when
  the source genuinely has no citation. Paste verbatim; never paraphrased or fabricated.
- **`unsafe_for_clinical_use: true`** on every result that carries research data (mirrors
  the backends' disclaimer; research use only, not clinical decision support).
- Prefer stable, human-meaningful IDs (`NBK1227:0024`, MONDO, PMID/PMCID/DOI, HGNC) over
  internal row IDs.

## Per-backend adoption (from the 2026-06-16 reports)

| Backend | Today | v1 change |
|---|---|---|
| `genereviews-link` | `{"result": {"results": […]}}` | Unwrap to top-level `results` + frame |
| `stringdb-link` | bare `{partners, total_count}` | Adopt full frame (`success`/`_meta`/citation/safety); rename `partners`→`results` |
| `autopvs1-link` | `{ok, data, error, meta}` | Map to `{success, result(s), _meta, error}` |
| `spliceailookup-link` | bare typed dict; ~60s call | Adopt frame; type the latency/async hints in `_meta` |
| `pubtator-link` | verbose authors, null `coverage_hint`, prose `year_range_local` | §3 defaults + §4 `_meta.filtering.exhaustive` |
| the other 9 (`success`+`_meta`) | already close | Confirm key is `results`/`result`; align `error`; default `compact` |

## References

- MCP tools specification — `structuredContent`/`content`/`isError`/`_meta`, `outputSchema`
  validation, protocol-vs-execution errors, opaque stateful handles.
- MCP pagination utility — opaque cursors; invalid cursor → `-32602`.
- Anthropic Engineering, *Writing effective tools for AI agents* — `response_format`
  concise/detailed (206→72 tokens), 25k-token cap, semantic IDs, actionable errors,
  pagination/filtering/truncation defaults.
- Sibling fleet standards: `TOOL-NAMING-STANDARD-v1.md` (arg canon: `response_mode`,
  `limit`, `offset`, …); Logging & CLI Standard v1 (correlation IDs → `request_id`).

## Definition of Done (per repo)

- [ ] Every tool result is the §1 frame in `structuredContent` (primary key `results`/`result`),
      mirrored into a `content[]` text block; `outputSchema` declared.
- [ ] Execution errors use `isError: true` + the §2 error frame with the enum code and an
      actionable message; protocol errors stay JSON-RPC `-32602`.
- [ ] `response_mode` defaults to `compact`; null/empty blocks omitted; nested objects
      collapse to strings except at `full`.
- [ ] `_meta` carries `request_id` + `elapsed_ms` (min), plus the applicable provenance,
      `pagination`, `staleness`, and `filtering` (typed) fields.
- [ ] Pagination uses opaque cursors with `_meta.pagination.{total_count,has_more,next_cursor}`.
- [ ] `recommended_citation` + `unsafe_for_clinical_use` on every research-data result.
- [ ] CI contract test: assert the frame keys, the error shape, and `compact`-default token
      economy on representative tools.
- [ ] MAJOR version bump + one-line `CHANGELOG` note (pre-alpha: no shims, no deprecation).
- [ ] Once compliant, delete any router-side `transform` stopgap for this backend in
      `servers.yaml`.

## Open questions

1. **Framed object vs. bare array `structuredContent`.** v1 chooses the framed object
   (room for `_meta`/citation/`success`) over a bare typed array. Confirm before mass
   adoption. *(Default: framed object.)*
2. **`results`/`result` vs a generic `data`.** v1 picks `results`/`result` (reads better
   for a research fleet; `data` is contentless). *(Default: `results`/`result`.)*
3. **Cursor encoding.** Stateless signed/opaque token vs. server-stored ID — pick one
   fleet-wide so the router can document it once. *(Default: stateless opaque.)*
4. **Should the router stamp its own `_meta.gateway` (round-trip `elapsed_ms`, namespace)?**
   This is additive observability about the gateway itself, not payload reshaping — arguably
   inside the thin-aggregator boundary. *(Default: defer; revisit if speed measurement
   through the wrapper becomes a real need.)*
