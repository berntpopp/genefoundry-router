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

## Discoverability is measured, not assumed

```bash
make bench-discoverability   # offline, over a snapshot of the real catalog
```

The benchmark scores how reliably ~50 realistic intents reach their canonical tool through
this exact surface. The bar is enforced in CI (`tests/discoverability/`), so tuning pins,
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
