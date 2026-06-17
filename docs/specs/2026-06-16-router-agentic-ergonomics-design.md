# Router Agentic Ergonomics — Design Spec

- **Date:** 2026-06-16
- **Status:** Approved for implementation
- **Owner:** Bernt Popp
- **Repo:** `github.com/berntpopp/genefoundry-router`

## 1. Summary

Two Claude usage reports (one literature-review workload, one 13-backend test pass)
graded the router **8/10 overall** with strong marks for discoverability, observability,
citation discipline, and error handling. Two systemic issues hold it below 9/10, plus a
cluster of backend-conformance gaps. This spec scopes the **router-side** fixes and
records the disposition of the **backend-side** findings.

The guiding principle is unchanged (AGENTS.md): the router is a **thin aggregator**.
Namespacing is its job. It must never fabricate provenance a backend did not emit, nor
rewrite a backend's response envelope. Both fixes below are extensions of work the router
already does — namespacing and tool-surface shaping — not new response munging.

## 2. Findings → disposition

| # | Severity | Finding | Disposition |
|---|----------|---------|-------------|
| F1 | 🔴 correctness | Embedded `next_commands`/`fallback_tool` hints use **bare** leaf names (`search_genes`); `call_tool` only resolves namespaced names (`clingen_search_genes`), so following a self-healing hint fails every time. | **Router fix** — §3 |
| F2 | 🟠 token cost | `search_tools` dumps the full nested `outputSchema` + repeats the verbose `_meta` block per hit. Discovering one tool can cost more tokens than every data call combined. | **Router fix** — §4 |
| F7 | 🟢 polish | gnomAD's `resolve_variant_id`/`search_genes` are pinned (first-class) while its other tools route via `call_tool`; the asymmetry is undocumented. | **Docs** — §5 (resolves design-spec §19 Q4) |
| F3 | 🟡 consistency | `stringdb` returns a bare `{partners, total_count}` — no `success`, no `_meta`, no `recommended_citation`, no `unsafe_for_clinical_use`. | **Backend fix** (`stringdb-link`) — §6. Router MUST NOT synthesize citations/safety stamps. |
| F4 | 🟡 consistency | Four envelope shapes across the fleet (`success+_meta`, `ok/data/error/meta`, result-wrapped, bare typed dicts). | **Inherent to federation** — §6. Documented; fleet-standardization tracked, router stays thin. |
| F5 | 🟡 latency | `spliceai.predict_splicing` took 59.5 s even warm; a naive caller blocks. | **Backend/agent guidance** — §6. Router surfaces the backend's own `cost_tier`/`expected_cold_latency_ms`; agents should use background tasks. |

F6 (error handling) was 🟢 positive — no action.

## 3. F1 — Namespace-aware hint rewriting

**Root cause.** The fleet invests in agentic self-healing: an error or empty result
embeds `fallback_tool` and `next_commands[].tool` so an agent can recover. Each backend
emits its **own** (bare) leaf names because, un-federated, those are correct. The router
namespaces tool *names* at mount time but not tool *references inside payloads*, so the
two drift: the agent reads `search_genes`, calls `call_tool("search_genes")`, and the
router — which only knows `clingen_search_genes` — rejects it.

**Fix.** A `NamespaceHintMiddleware` (`hints.py`) post-processes `tools/call` results and
rewrites known tool-reference fields from `<leaf>` → `<namespace>_<leaf>`, consistent with
how the same backend's tool names were namespaced. This makes the hints the agent reads
already-correct, so the existing `call_tool` path works unchanged.

- **Namespace source.** `on_call_tool` derives the namespace from the invoked tool name
  (`clingen_get_gene_validity` → `clingen`). The synthetic `call_tool`/`search_tools`
  passes are skipped: FastMCP re-enters the middleware chain for the *real* target
  (`self.call_tool(..., run_middleware=True)`), so the inner pass carries the namespaced
  name. Only registry namespaces are acted on; `_root`/unknown prefixes pass through.
- **Fields.** Recursively rewrite string values at keys in a small allow-list
  (`tool`, `fallback_tool`, `tool_name`, `next_tool`). This captures top-level
  `fallback_tool` and `next_commands[].tool` without guessing at prose.
- **Guards (no false rewrites).** Rewrite only when the value (a) is a plausible bare
  identifier (`^[A-Za-z_][A-Za-z0-9_]*$`), and (b) is not already prefixed with the
  current namespace. Idempotent by construction.
- **Both channels.** Rewrite `ToolResult.structured_content` (dict) **and** any
  `TextContent` block that parses as JSON (the text mirror many clients display, and the
  only channel for bare-typed-dict backends). Non-JSON text is left untouched.
- **Scope.** `CreateTaskResult` (background tasks) and error-typed results pass through
  the same rewrite. Toggle via `GF_REWRITE_HINTS` (default `true`).

This is removable per the same lifecycle as the `transform` blocks: once the fleet adopts
a "hints are namespaced by the emitter is impossible / gateway-rewrites" standard, the
middleware can be dropped. It is convention-coupled, not backend-coupled.

## 4. F2 — Compact discovery payloads

**Root cause.** `BaseSearchTransform` defaults to `serialize_tools_for_output_json`, which
emits `tool.to_mcp_tool().model_dump()` per hit — the entire nested `outputSchema` and the
full `_meta` description block. For schema-heavy tools (`autopvs1_get_variant_pvs1_data`,
`pubtator_get_publication_passages`) this dominates the token budget, and the cost lands on
*discovery*, which the per-record `response_mode` knobs cannot touch.

**Fix.** A `CompactBM25SearchTransform` (subclass) whose `search_tools` returns, per hit:
`name`, `description`, the **full `inputSchema`** (kept — it is the agent's argument
contract, the substitute for native argument validation), a **one-line `returns`** summary
derived from the output schema's top-level fields, and `tags`. The nested `outputSchema`
and the repeated `_meta` block are dropped.

- **Opt-in full mode.** `search_tools(query, detail="full")` returns the original full
  JSON dump (the existing `serialize_tools_for_output_json`) for the rare case an agent
  needs the complete output schema. `detail="compact"` is the default.
- **`returns` summary.** `{field: type, …}` using FastMCP's own `_schema_type` heuristic;
  truncated past ~12 fields. Non-object schemas render as their type label.

Expected effect (per the report's own estimate): token efficiency 5 → ~8, overall → ~9.

## 5. F7 — Document the pinned-tool pattern

`tool_search.DEFAULT_ALWAYS_VISIBLE` pins `gnomad_resolve_variant_id` and
`gnomad_search_genes`. These are the fleet's **entry-point resolvers**: symbol→ID and
variant-ID normalization that almost every downstream call depends on, so keeping them in
the default listing saves a `search_tools` round-trip on the most common first step. All
other tools (gnomAD's included) are reachable via `search_tools` → `call_tool`. Document
this in the README discovery section and resolve design-spec §19 Q4 (it was an open
question; this is the answer).

## 6. Backend-conformance findings (not router code)

- **F3 (`stringdb`).** Fix in `berntpopp/stringdb-link`: adopt the fleet response envelope
  (`success`, `_meta` with versions/citation, `unsafe_for_clinical_use`). The router will
  **not** inject a `recommended_citation` — fabricating provenance in a research-safety
  tool is worse than its absence. Track upstream; document as a known gap meanwhile.
- **F4 (envelope heterogeneity).** Expected federation cost. The router does not normalize
  envelopes (would violate the thin-aggregator boundary and risk lossy reshaping). Document
  the four shapes so client authors branch correctly; converge via the fleet standard.
- **F5 (`spliceai` latency).** Backend compute cost. The router already passes through the
  backend's `cost_tier`/`expected_cold_latency_ms`/`taskSupport` signals; document that
  agents should prefer background tasks for compute-tier tools.

## 7. Testing

- **F1 unit:** `_rewrite_value` rewrites `fallback_tool` + `next_commands[].tool`, is
  idempotent, ignores prose, ignores already-namespaced values, ignores unknown keys.
- **F1 integration:** a fake backend tool returns a payload with a bare `fallback_tool`;
  calling it directly and via `call_tool` yields a namespaced reference in both
  `structured_content` and the JSON text block.
- **F2 unit:** compact serializer keeps `inputSchema`, drops `outputSchema`/`_meta`, emits
  a `returns` string. **integration:** `search_tools` default omits `outputSchema`;
  `detail="full"` includes it.
- `make ci-full` green (format, lint, lint-loc ≤600, mypy, unit, integration, e2e),
  coverage ≥70.

## 8. Out of scope

Backend-repo changes (F3/F4/F5 implementations), auth, transport, deployment of the router
itself. No new module exceeds the 600-LOC budget; `hints.py` is new and single-purpose.

## 9. Addendum (2026-06-18) — F8 discoverability orientation (issue #3)

**Finding (🟠 ergonomics).** A model driving the router twice misread its two-layer
discovery model ([#3](https://github.com/berntpopp/genefoundry-router/issues/3)):

- It searched its **host/client-side** tool list for "spliceai", saw only the router's
  top-level entry points (`search_tools`, `call_tool`, two pinned gnomAD resolvers), and
  concluded the capability did not exist — instead of querying the router's own
  `search_tools`. The host cannot see behind the search surface; only `search_tools` can.
- Separately, re-running a **host-side** tool search evicted the deferred `call_tool` from
  the loaded set; the resulting `Unknown tool: call_tool` was read as router flakiness
  rather than a recoverable client eviction.

**Root cause.** The router shipped **no MCP `instructions`** — the spec-native channel a
server uses to teach a host's model how to drive it — and FastMCP's default
`search_tools`/`call_tool` descriptions do not convey the gateway model, the
`<namespace>_<tool>` name format, or that an eviction is recoverable. Neither failure is a
code defect; both are missing orientation. (Per the MCP spec, instruction injection into the
system prompt is "up to the implementer" and best-effort, so the guidance is duplicated into
the tool descriptions for defense in depth. The official Anthropic Tool Search Tool docs do
not cover a host search layered over a server that itself exposes a search tool — this
two-layer case is genuinely undocumented, so the router must self-document.)

**Fix (router-side, this repo).**

1. `instructions.py` — `build_instructions(registry)` produces the server `instructions`
   string (set on `FastMCP("genefoundry", instructions=…)`): names the search→call
   workflow, lists the **enabled** namespaces so breadth is visible without a round-trip,
   states that absence from the top-level list ≠ missing, and frames `Unknown tool` as a
   recoverable client eviction (re-run `search_tools`). Disabled backends are omitted.
2. `tool_search.py` — `search_tools` description rewritten to frame it as the gateway and
   seeded with backend keywords (so a host tool-search for "spliceai" surfaces the router
   entry point); `call_tool` description overridden (reusing FastMCP's proxy) to document
   the `<namespace>_<tool>` format and the self-healing recovery step.

**Deliberately NOT changed.** The pinned set stays at the two §5 resolvers — expanding
`always_visible` to dodge the search step trades away the token savings that are the whole
point of the surface (design-spec §19 Q4). Host-side tool-search eviction is a host
behavior the router cannot fix; it is documented, not worked around.
