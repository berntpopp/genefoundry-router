---
name: tool-search-change
description: Use when changing the router's tool-search / discovery surface — the search_tools or call_tool meta-tools, the BM25 index, entrypoint pinning, or hint/normalization rewriting.
---

# Tool-Search Change

Follow `AGENTS.md` first. The router doesn't author backend tools; it exposes two synthetic meta-tools over the federated fleet — `search_tools` (BM25 discovery, token-lean serialization) and `call_tool` (invoke a discovered tool by name) — in `tool_search.py`, plus discovery hints/normalization in `hints.py` / `normalization.py` / `discovery.py`.

## Workflow

1. Work in `tool_search.py` (`search_tools` / `call_tool`), and `discovery.py` / `hints.py` / `normalization.py` for the catalog and rewrites.
2. Keep `search_tools` output token-lean (the default serialization drops `_meta` and trims to a one-line `returns`); don't regress payload size.
3. Preserve the pinned entrypoints (the common first-call tools) and the `search_tools -> call_tool` contract in the tool descriptions.
4. Regenerate the discovery catalog (`make snapshot-catalog`) and keep golden-task / guard tests green; `make list-tools` to sanity-check the surface.
5. Tune `GF_SEARCH_MAX_RESULTS` / `GF_REWRITE_HINTS` via config, not hard-coded values. Run `make ci-local`.

## Common mistakes

- Bloating `search_tools` results with full schemas/`_meta` — it's the token-lean discovery path.
- Breaking the `search_tools -> call_tool` handshake the tool descriptions promise.
- Testing the strict `tools/list` projection only by listing — assert it with a REAL `call_tool` call; open maps (`additionalProperties`) travel as JSON strings and must round-trip.
- Forgetting what the reranker sees: name + description lead + params (incl. enums) + expansion keywords (`genefoundry_router/data/tool-expansions.json`) — a change to any of them moves rankings; re-run `make bench-rerank`.
- Assuming hint rewriting is catalog-checked: bare-name prefixing in `hints.py` is verb-prefix based (Tool-Naming v1.1 verbs), so any verb_noun word gets prefixed.
