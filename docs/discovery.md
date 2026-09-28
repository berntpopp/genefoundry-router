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

Everything else is reached via `search_tools` → `call_tool` (and is also directly callable
by full name once known). A typical flow:

```text
search_tools(query="splicing prediction")        # → hit: name="spliceai_predict_splicing", inputSchema, returns
call_tool(name="spliceai_predict_splicing", arguments={...})
```

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
   calibrated probability. Hits are reordered, carry a `relevance` field, and with
   `GF_SEARCH_RERANK_MIN_SCORE` anything below the floor is dropped, so an out-of-scope
   request returns `[]` instead of five wrong tools. Any error or timeout serves the BM25
   order (fail-open) and pauses the stage for 30 s.

Measured on the held-out set (219 tasks, 24 out-of-scope; pins excluded, top-5 served):

| Stage | hit@1 | hit@5 | Out-of-scope → `[]` | Added latency |
|---|---|---|---|---|
| BM25 only (`GF_SEARCH_EXPANSIONS=off`) | 0.45 | 0.68 | 0% | – |
| + expansion (default) | 0.72 | 0.94 | 0% | none |
| + hosted Jev 1.13, `noul`, min 0.3 | 0.89 | 0.97 | 100% (0.5% of in-scope emptied) | ~330 ms p50 |
| + self-hosted OneJev-4B, `noul`, min 0.3 | 0.87 | 0.97 | 71% (none emptied) | GPU-dependent |
| + self-hosted OneJev-4B, `choice` | 0.92 | 0.97 | – | GPU-dependent |

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
GF_SEARCH_RERANK_MIN_SCORE=0.3

# self-hosted OneJev on a VPS (github.com/OmniJev/OneJev: `qev serve --model OmniJev/OneJev-4B`)
GF_SEARCH_RERANK=systemone
GF_SEARCH_RERANK_URL=http://onejev.internal:8000/v1/systemone
GF_SEARCH_RERANK_MIN_SCORE=0.3
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
