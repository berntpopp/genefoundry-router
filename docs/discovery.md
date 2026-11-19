# How discovery works

`genefoundry` is a **meta-router**, not a flat tool server. Listing the whole federated
catalog to a model is unworkable, so the router exposes a **search surface** instead.

Design detail lives in
[`specs/2026-06-16-router-agentic-ergonomics-design.md`](specs/2026-06-16-router-agentic-ergonomics-design.md).

## The two-layer surface

- **`search_tools`** — relevance search over the *entire* federated catalog.
- **`call_tool`** — invoke a hit by its `<namespace>_<tool>` name.
- A small set of pinned **canonical entry points** — each backend's front-door tool (a
  free-text→ID resolver and/or the primary query), declared per-backend via `entrypoints:`
  in [`servers.yaml`](../servers.yaml) and generated into both the pinned `always_visible`
  set *and* the server `instructions` map.

Pinning makes each domain's canonical tool discoverable **deterministically** rather than by
relevance luck — the fix for FastMCP's flat BM25 index (no field weighting, no stemming),
which let a terse canonical tool lose to verbose tools that merely repeat a keyword.

### Pinned mode (#320)

`GF_PINNED_MODE` chooses which pins `tools/list` shows; the request header
`X-GF-Pinned-Mode: default|minimal|none` overrides it per request (so one deployment can be
measured in every mode; invalid values fall back to the server default). It filters only the
listing: search, `call_tool`, authorization and rate limits are unchanged.

| Mode | Listed | Listing (~tokens, strict, fleet baseline) |
|---|---|---|
| `default` | `search_tools`, `call_tool` + every `entrypoints:` tool (30) | ~17.1k |
| `minimal` | the meta-tools + `GF_PINNED_MINIMAL` (6: gene, variant, ClinVar, ClinGen, disease and phenotype search) | ~3.2k |
| `none` | `search_tools`, `call_tool` | ~0.8k |

The server `instructions` follow `GF_PINNED_MODE` (the header cannot change the
`initialize` result). Where a backend has both an exact resolver and a ranked text search,
the search is pinned: it returns ranked candidates with IDs, so an exact name still comes
first, and a near miss is a candidate list instead of a `not_found`.

Everything else is reached ONLY via `search_tools` → `call_tool`: clients that check names
against `tools/list` reject an unlisted name called directly (#299). Each compact hit carries
a ready `call` example. A typical flow:

```text
search_tools(query="splicing prediction")        # → hit: name="spliceai_predict_splicing", inputSchema, returns
call_tool(name="spliceai_predict_splicing", arguments={...})
```

`search_tools` returns the top `GF_SEARCH_MAX_RESULTS` (5) hits by default; a caller passes
`limit` (1 to max(`GF_SEARCH_MAX_RESULTS`, `GF_SEARCH_RERANK_POOL`), so 20 by default) for
fewer or more. Each compact hit costs ~1–3 kB, almost all of it the `inputSchema` the agent
needs to call the tool, so `limit` is the lever on discovery cost. The default stays at 5
because the right tool is not always first: on the held-out set, BM25 hit@1/@3/@5 is
0.72/0.88/0.94.

The model is oriented on this two-layer model via the MCP **`instructions`** field (set on
the server) plus the `search_tools` / `call_tool` descriptions.

## Improving the search itself

The router's `CompactBM25SearchTransform` folds the tool name/leaf and tags into the index
and stems both documents and queries, so word-form mismatches (`expressed` ↔ `expression`)
and keyword-stuffed prose no longer hide the right tool. Federated names are also valid for
Gemini Remote MCP (snake_case, `[a-z0-9_]`, ≤ 64 chars).

## Search ranking stages

`search_tools` ranks in up to three stages:

1. **Field-weighted, stemmed BM25** over each tool's name, description, parameters and tags.
2. **Offline document expansion** (on by default, `GF_SEARCH_EXPANSIONS=builtin`). For every
   tool, LLMs from two model families wrote ~20 realistic requests and keywords *once*
   (`scripts/gen_tool_expansions.py`, from the catalog entry only), and the router folds them
   into that tool's index document. It closes the vocabulary gap BM25 cannot: lay wording,
   paraphrase, abbreviations, other languages. The text is index-only and never appears in a
   hit. Zero runtime cost; the 1-CPU container keeps sub-millisecond search.
3. **Optional reranker** (`GF_SEARCH_RERANK=systemone`, off by default). The top
   `GF_SEARCH_RERANK_POOL` BM25 hits go to a [System One](https://docs.typesafe.ai) decision
   model, which answers "can this tool directly fulfil the request?" per candidate with a
   calibrated probability. Each candidate is its name plus its description lead, a compact
   parameter summary (names and enum values) and its expansion keywords, capped at 800
   characters (#306): the lead alone often does not say what a tool returns. Hits are reordered, carry a `relevance` field, and with
   `GF_SEARCH_RERANK_MIN_SCORE` anything below the floor is dropped. If the floor drops every
   candidate, the top `GF_SEARCH_MAX_RESULTS` BM25 hits (or the caller's `limit`) are served
   with `"low_confidence": true` (and their
   `relevance`) instead of unflagged wrong tools or a bare `[]`, which agents read as "the fleet
   cannot do this" (#303). The flag is the abstention signal; `[]` now means BM25 found no
   candidate at all. Any error or timeout serves the BM25 order (fail-open) and pauses the
   stage for 30 s.

Measured on the held-out set before v0.12.8 (219 tasks, 24 out-of-scope; pins excluded,
top-5 served; v0.12.8 added 40 more out-of-scope tasks, so re-measure before comparing):

| Stage | hit@1 | hit@5 | Out-of-scope abstained | Added latency |
|---|---|---|---|---|
| BM25 only (`GF_SEARCH_EXPANSIONS=off`) | 0.45 | 0.68 | 0% | – |
| + expansion (default) | 0.72 | 0.94 | 0% | none |
| + hosted Jev 1.13, `noul`, min 0.3 | 0.89 | 0.97 | 100% (0.5% of in-scope emptied) | ~330 ms p50 |
| + self-hosted OneJev-4B, `noul`, min 0.3 | 0.87 | 0.97 | 71% (none emptied) | GPU-dependent |
| + self-hosted OneJev-4B, `choice` | 0.92 | 0.97 | – | GPU-dependent |

"Abstained" counts no confident hit (`[]` or `low_confidence`). The curated set
under-represents agent phrasing: on genefoundry-bench agent traces the 0.3 floor emptied 24%
of searches, many in scope (literature full text, figures, tables; #303).
`tests/discoverability/agent_queries.yaml` holds those 858 agent queries (synthetic, no gold
tool) for offline measurement only: `uv run python scripts/search_rerank_report.py --tasks
tests/discoverability/agent_queries.yaml` reports the share each pipeline empties.

### The floor is model-specific

`GF_SEARCH_RERANK_MIN_SCORE` is a probability cut-off, and each decision model calibrates its
probabilities differently, so a floor tuned for one model does not carry over to another.

`typesafe/jev-1.13`, measured by genefoundry-bench on the v0.13.3 candidate text (65
out-of-scope negatives, 195 in-scope tasks, 856 agent queries of which 272 ask for literature
full text; no fallbacks). hit@1 0.908 and hit@5 0.979 at every floor below. The highest
out-of-scope score is 0.21.

| Floor | Out-of-scope rejected | In-scope emptied | Agent queries emptied | Full-text queries emptied |
|---|---|---|---|---|
| 0.20 | 64/65 | 1/195 (0.5%) | 3.7% | 6.3% |
| 0.22 | 65/65 | 1/195 | 4.9% | 8.1% |
| 0.25 | 65/65 | 1/195 | 6.8% | 11.5% |
| 0.30 | 65/65 | 1/195 | 9.6% | 16.7% |

`google/gemini-decision-1`, last measured on the older v0.12.8 candidate text (64 negatives,
so not like-for-like with the table above): hit@1 0.897, p50/p95 356/673 ms.

| Floor | Out-of-scope rejected | In-scope emptied | Agent queries emptied |
|---|---|---|---|
| 0.10 | 96.9% | 0% | 0.8% |
| 0.20 | 100% | 0.5% | 2.6% |
| 0.30 | 100% | 1.0% | 4.1% |

For Jev 1.13 with the current candidate text, use `GF_SEARCH_RERANK_MIN_SCORE=0.25`. 0.22 is
the lowest floor that rejects all 65 negatives; 0.25 leaves a 0.04 margin over the highest
negative. A search the floor empties still gets up to `GF_SEARCH_MAX_RESULTS` BM25 candidates
flagged `low_confidence`, so the higher floor costs little. The margin is small, so the code
default stays `0.0` (floor off), the production value is a deploy decision, and you should
re-measure whenever the model or the candidate text changes, on both eval sets:

```bash
uv run python scripts/search_rerank_report.py                      # held-out set, 65 out-of-scope
uv run python scripts/search_rerank_report.py --tasks tests/discoverability/agent_queries.yaml
```

The first reports out-of-scope abstention and in-scope hits; the second reports how many
real agent searches the floor empties.

### Choosing a reranker deployment

| | Hosted Jev (TypeSafe / OpenRouter) | Self-hosted OneJev (VPS) |
|---|---|---|
| Setup | API key only | GPU host (OneJev-4B ≈ 10–18 GB VRAM); OneJev-0.8B did **not** help |
| Data flow | **Every search query leaves your infrastructure** | Stays on your network |
| Cost | ≈ $0.0002 per search | the GPU |
| Abstention | strongest | good at `min_score 0.3` |

Search queries can echo user text (gene, variant, sometimes phenotype). For patient-adjacent
deployments prefer self-hosting or leave the reranker off: a hosted endpoint makes the
provider a recipient of that text (GDPR Art. 28/44 considerations apply).

```bash
# hosted Jev through OpenRouter
GF_SEARCH_RERANK=systemone
GF_SEARCH_RERANK_URL=https://openrouter.ai/api/v1/systemone
GF_SEARCH_RERANK_MODEL=typesafe/jev-1.13
GF_SEARCH_RERANK_API_KEY=sk-or-...
GF_SEARCH_RERANK_MIN_SCORE=0.25

# self-hosted OneJev on a VPS (github.com/OmniJev/OneJev: `qev serve --model OmniJev/OneJev-4B`)
GF_SEARCH_RERANK=systemone
GF_SEARCH_RERANK_URL=http://onejev.internal:8000/v1/systemone
GF_SEARCH_RERANK_MIN_SCORE=0.3  # OneJev not re-measured on v0.12.8; re-run the report first
```

With the reranker on, every `search_tools` call is an upstream request (paid, when hosted):
keep `GF_RATE_LIMIT_RPM` > 0 in production. Queries are cut to 2,000 characters before they
leave the router, and only endpoint faults (timeouts, 5xx, 401/403/408/429) pause the stage;
a malformed answer or a per-request 4xx falls back for that one search only.

Validate an endpoint before enabling it: `make bench-rerank` runs the held-out set through
the served pipeline with and without the reranker. After `make snapshot-catalog`, run
`make expansions` to regenerate expansions for new or changed tools (a CI guard fails once
more than 5% are stale).

## Discoverability is measured, not assumed

```bash
make bench-discoverability   # offline, over a snapshot of the real catalog
```

The benchmark scores how reliably realistic intents reach their canonical tool through this
exact surface: ~50 golden tasks plus a 219-task held-out set written blind to the search code
(`tests/discoverability/heldout_tasks.yaml`: lay, jargon, paraphrase, crosslingual, …). The bar is enforced in CI (`tests/discoverability/`), so tuning pins,
search, or descriptions stays evidence-driven rather than vibes-driven.

## Search analytics in production

The metric to watch is the **zero-result rate** (zero-result searches / all searches, the
"no results rate" of Algolia, Kendra and Meilisearch): each zero-result search is a request
the catalog could not place. Every `search_tools` audit record (`event: tool_call`) carries:

| Field | Present when | Meaning |
|---|---|---|
| `hit_count`, `top_hits` | `outcome: ok` | Hits served; top-3 names (catalog-verified, else `_unknown`) |
| `zero_results` | `outcome: ok` | `true` iff `hit_count == 0` |
| `low_confidence` | `outcome: ok` | `true` when the rerank floor dropped every hit and the BM25 fallback was served |
| `error_type` | `outcome: error` | `tool_error` for an `isError` result, else the exception class name (OTel `error.type`) |

The query text is never logged (it can carry patient data). `/metrics` exposes the same split
as `genefoundry_search_tools_results_total{result="hits|zero|low_confidence|error"}`, so the
zero-result rate is `rate(…{result="zero"}[1h]) / sum(rate(…[1h]))`.

## Two traps to avoid

See [issue #3](https://github.com/berntpopp/genefoundry-router/issues/3).

> [!WARNING]
> **A capability missing from your host/client-side tool list is not missing.** The host
> only sees `search_tools`, `call_tool`, and the pinned entry points. Call `search_tools`
> before concluding a tool does not exist.

> [!WARNING]
> **`search_tools` returns *data*, so do not re-run a host tool search to invoke a hit** —
> just call `call_tool`. An `Unknown tool: call_tool` means your client evicted it; re-run
> `search_tools` to rediscover and continue. This is recoverable, not a router fault.
