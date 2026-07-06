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
