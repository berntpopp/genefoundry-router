# GeneFoundry Response-Envelope Standard v1.1

> **Status: ADOPTED v1.1, ratified 2026-07-10.** This document extends the
> [v1 response-envelope contract](RESPONSE-ENVELOPE-STANDARD-v1.md) with a normative
> representation for externally sourced free text. Rules not amended here remain v1.

## Purpose and boundary

Biomedical abstracts, descriptions, annotations, and other upstream prose are data, not
instructions. A backend MUST fence such content at its public MCP serialization boundary. The
router MUST preserve the typed object and MUST treat its complete subtree as opaque; it does not
sanitize or manufacture backend provenance.

Fencing is defense in depth, not model isolation. Hosts still authorize subsequent tool calls
against user intent and prevent tainted external reads from triggering privileged writes.

## Normative object

Every externally sourced free-text value MUST use this structural shape in
`structuredContent` and in its JSON `TextContent` mirror:

```json
{
  "kind": "untrusted_text",
  "text": "NFC-normalized external content",
  "provenance": {
    "source": "pubtator",
    "record_id": "PMID:12345678",
    "retrieved_at": "2026-07-10T12:00:00Z"
  },
  "raw_sha256": "64 lowercase hexadecimal characters"
}
```

The object is structural, not a display convention:

- `kind` MUST be the literal `untrusted_text` and MUST be declared as a literal in the tool's
  output schema.
- `text` MUST contain the sanitized Unicode NFC representation described below.
- `provenance.source` MUST identify the upstream source or corpus.
- `provenance.record_id` MUST identify the upstream record precisely enough to retrieve or audit
  it. `provenance.retrieved_at` MUST be an RFC 3339 UTC timestamp.
- `raw_sha256` MUST be the lowercase SHA-256 digest of the exact raw UTF-8 bytes before Unicode
  normalization or character removal.
- A response MUST NOT duplicate the raw or sanitized external prose in another field. The typed
  object is the single model-facing representation.

## Unicode sanitation

Backends MUST normalize raw text with Unicode NFC, then remove exactly the following code points.
The digest remains over the pre-normalized raw UTF-8 bytes.

| Class | Removed code points |
|---|---|
| C0 controls | `U+0000-U+0008`, `U+000B-U+000C`, `U+000E-U+001F` |
| C1 controls | `U+007F-U+009F` |
| Zero-width controls | `U+200B-U+200D`, `U+2060`, `U+FEFF` |
| Bidirectional controls | `U+202A-U+202E`, `U+2066-U+2069` |

Backends MUST preserve tab (`U+0009`), LF (`U+000A`), CR (`U+000D`), ordinary Unicode, and
scientific symbols. They MUST NOT delete instruction-like prose with regular expressions or apply
compatibility normalization such as NFKC.

## Limits

Unless a narrower tool-specific inventory row says otherwise, a result is limited to:

- 2 MiB of UTF-8 text per untrusted object;
- 128 untrusted objects;
- nesting depth 8; and
- 8 MiB total untrusted UTF-8 text per result.

Exceeding a limit MUST produce an explicit typed truncation or execution error; silent omission is
not compliant. A measured exception MUST retain a hard ceiling and be recorded in the inventory.

### Limit-breach error codes

A limit breach MUST raise a backend-specific **typed execution error** — never the generic
`internal_error` fallthrough and never a silent truncation. The standard does not mandate one
fleet-uniform error code; it requires that the code be explicit, part of the backend's closed
error taxonomy, checked before any generic `ValueError`/`internal_error` fallthrough, and
distinguishable from an ordinary caller-input validation failure. Fleet backends adopting v1.1
have shipped several equally conformant names, e.g. `response_too_large`, `limit_exceeded`,
`untrusted_text_limit_exceeded`, `response_limit_exceeded`, `invalid_input`, and
`output_limit_exceeded`. A future fleet sweep MAY converge these into one canonical name; until
then, any explicit typed code that meets the above bar satisfies this standard.

## Mirrored content and routing

`structuredContent` is canonical. Its JSON `TextContent` mirror MUST contain the same typed object
and MUST NOT duplicate the prose in delimiter-wrapped free text. Human-facing delimiters may be
added only as advisory defense in depth, with escaped content and a per-response nonce; clients
must never infer trust from delimiters.

The router's trusted hint fields may still be namespaced. Once a dictionary has
`"kind": "untrusted_text"`, the router MUST NOT traverse, rewrite, validate as hints, or otherwise
mutate any descendant field, including fields named `tool`, `tool_name`, `next_tool`, or
`fallback_tool`.

## Error-message sanitation (secondary surface)

Upstream API error-body text echoed verbatim into an envelope's caller-visible `message` or
diagnostic field is a secondary untrusted-content surface: a caller-influenceable upstream 4xx/5xx
response body can carry the same injection prose, zero-width, and bidirectional-control payloads
as a primary fenced field, just outside the typed `untrusted_text` object. Backends MUST NOT echo
a raw upstream error body verbatim into a caller-visible message. They MUST strip the forbidden
code points listed under Unicode sanitation from every caller-visible message and error string,
and SHOULD prefer a fixed, status-keyed message over interpolating upstream detail (the raw body
MUST NOT be written to a log sink either, since it may carry caller-supplied PII).

**Status: COMPLETE fleet-wide as of 2026-07-11.** All 21 federated backends have hardened their
error paths (litvar-link and mavedb-link during v1.1 adoption; the remaining 19 in the
error-message-sanitation sweep, `docs/specs/2026-07-11-error-message-sanitation-fleet-sweep-design.md`).
Released patches: uniprot 3.0.1, clingen 3.0.1, spliceai 3.0.3, stringdb 4.0.1, metadome 0.1.4,
gtex 3.0.1, gnomad 8.0.1, vep 1.0.4, genereviews 5.0.1, gencc 0.7.1, pubtator 6.1.1, orphanet 0.3.1,
panelapp 0.5.1, clinvar 0.4.1, mgi 0.5.1, autopvs1 4.0.1, hgnc 2.0.1, mondo 0.3.1, hpo 0.3.1.
Every merge was gated by an adversarial Codex review that drove the real MCP tools with hostile
upstream 4xx/5xx/timeout bodies and hostile inputs (injection prose + zero-width/bidi/NUL). The
sweep established, beyond code-point stripping, that caller-visible **structured** fields (`field`,
`allowed_values`, `hint`, `candidates`, `withdrawn_status`, `replaced_by`, batch/partial-success
rows, `_meta.next_commands` arguments) MUST be built only from fixed strings, closed enums, or
grammar-validated identifiers — because instruction-shaped prose carries no forbidden code points,
so sanitation alone does not neutralize it. Bootstrap/refresh/ingest error paths sever the upstream
artifact body (`BadGzipFile`/decode errors) and log only the exception class.

> **Fast-follow — COMPLETE fleet-wide as of 2026-07-11.** FastMCP-core *not-found* surfaces reflect
> the caller's own requested name back — an unknown tool name, unknown resource URI, unknown prompt
> name (and a malformed-URI pydantic `-32602`) can echo caller-supplied text (and forbidden code
> points) to the caller or into logs before backend middleware runs. This is a caller self-reflection
> surface (lower-tier than the upstream external-data residual closed above), but it still reaches
> shared log/telemetry sinks and an agent's context. All 21 federated backends and the router now
> carry a uniform guard (`docs/specs/2026-07-11-fastmcp-notfound-reflection-guard-design.md`), each
> hostile-tested via the real MCP surface (raw JSON-RPC + FastMCP Client) and gated by an adversarial
> Codex review. Released patches: uniprot 3.0.2, clingen 3.0.2, spliceai 3.0.4, stringdb 4.0.2,
> metadome 0.1.5, gtex 3.0.2, gnomad 8.0.2, vep 1.0.5, genereviews 5.0.2, gencc 0.7.2, pubtator 6.1.2,
> orphanet 0.3.2, panelapp 0.5.2, clinvar 0.4.2, mgi 0.5.2, autopvs1 4.0.2, hgnc 2.0.2, mondo 0.3.2,
> hpo 0.3.2, mavedb 0.4.1, litvar 5.0.1; router 0.6.2.
>
> The uniform pattern: (1) registry **preflight** in `on_call_tool` → fixed name-free `not_found`
> envelope with `is_error=True` (so the FastMCP Client does not re-log the name during output-schema
> validation) and no `_meta.tool` echo; (2) `on_read_resource` boundary → fixed URI-free error;
> (3) a **protocol-level backstop** wrapping the raw CallTool/ReadResource/GetPrompt handlers (covers
> the unknown-tool *return* path and the unknown-*prompt* echo), gated on a registry-proven-unresolved
> check so a known tool's real error is never masked as `not_found`; (4) a marker-based **log scrub**
> filter attached to FastMCP's actual source loggers **and** its non-propagating Rich handlers at all
> levels — covering the pre-middleware DEBUG traces (`mcp.server.lowlevel.server` "Tool cache miss",
> `fastmcp.server.mixins.mcp_operations` "Handler called"), the session-layer malformed-request record
> (root / `mcp.shared.session` "Failed to validate request"), and the aggregate-provider fault log
> (`fastmcp.server.providers.aggregate` "Error during …"). Every fixed message is built from constant
> strings — never the requested name/URI or `str(exc)`. OTel span-attribute redaction is applied only
> where `opentelemetry-sdk` is a locked dependency (panelapp-link; elsewhere the tracer is a
> non-recording no-op, so the span sink is not reachable). The router additionally buckets any
> unresolved tool name to `_unknown` in its structlog audit sink and Prometheus labels, so a
> grammar-valid but nonexistent name cannot inject into operator logs/metrics.
>
> Note: the malformed-URI `-32602` fires at MCP session deserialization *before* any request handler,
> so mcp core already returns a fixed `"Invalid request parameters"` to the caller; the guard closes
> only its log residual. The `fastmcp.server.providers.aggregate` fault log is externally reachable
> only where multiple/remote providers can genuinely fault during lookup (the router, which fans across
> 21 proxies); on single-provider backends it requires a white-box-injected faulting provider and is
> covered as defense-in-depth.

## Adoption and compatibility

The machine-readable completeness source is
[`docs/conformance/untrusted-text-inventory.yml`](conformance/untrusted-text-inventory.yml). Each
fleet backend is present even before adoption. `audit-pending` rows deliberately retain sentinel
tool/pointer values until a backend PR cites exact source evidence; a verified backend with no
external free-text surface uses `classification: no-untrusted-text` plus an evidence path.

For an additive migration, a v1 consumer may retain a legacy data field for one compatibility
release only when the model-facing mirror contains only the fenced representation. Reshaping or
removing a previously public string field is breaking and requires the backend's next major
version. A backend is v1.1 conformant only when its row names exact tools and JSON pointers, its
hostile test vector passes, and its output schema exposes the literal typed object.
