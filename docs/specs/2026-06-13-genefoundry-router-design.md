# GeneFoundry Router (`genefoundry-router`) — Design Spec v1

- **Date:** 2026-06-13
- **Status:** Draft for review
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Owner:** Bernt Popp
- **Repo (planned):** `github.com/berntpopp/genefoundry-router` (deployed MCP server name: `genefoundry`)

## 1. Summary

`genefoundry-router` is a thin **FastMCP 3.x aggregator** that federates the GeneFoundry fleet of `*-link` domain MCP servers behind a **single MCP endpoint**. A host (Claude, Cursor, etc.) adds one server — `genefoundry` — and transparently gets access to every backend (gnomAD, GTEx, HGNC, MGI, UniProt, ClinGen, GenCC, LitVar, STRING, AutoPVS1, SpliceAI, GeneReviews, PubTator), with **collision-free namespacing**, **normalized tool names**, and **search-based tool discovery** so the model is never shown ~189 raw tools at once.

The router is a **client** to each backend and a **server** to hosts. It introduces no new infrastructure paradigm: same stack and conventions as the `-link` servers (Python 3.12+, `uv`+hatchling, `typer` CLI, `structlog`, `prometheus-client`, Docker behind nginx-proxy-manager, `/mcp` + `/health`).

## 2. Goals / Non-goals

**Goals**
- One endpoint federating all `-link` backends; config-driven registry.
- Collision-free namespacing (`<token>_<tool>`) per the GeneFoundry Tool-Naming Standard v1.
- Tool-overload control via FastMCP `BM25SearchTransform` (`search_tools` + `call_tool` + pinned essentials).
- Pluggable, OAuth-ready auth (`none | jwt | oauth`) on the router endpoint.
- Optional per-backend normalization transforms to paper over non-compliant servers until source fixes land.
- Observability + health parity with the fleet.

**Non-goals (v1)**
- No REST/gRPC→MCP virtualization (backends are already MCP-native — that's ContextForge's job if ever needed).
- No RFC 8693 per-user token-exchange to backends (backends are public, read-only research APIs).
- No multi-tenant admin UI.
- No write/clinical features — research use only, mirrors backend disclaimers.

## 3. Context

The fleet is 13 FastMCP `-link` servers (~189 tools total). They are **inconsistent**: some self-prefix tools (`pubtator_*`), some leak FastAPI routes (`from_fastapi`), verbs vary. Normalization is being fixed at the source (one tracking issue per repo, Standard v1; canonical text in [`docs/TOOL-NAMING-STANDARD-v1.md`](../TOOL-NAMING-STANDARD-v1.md)). The router must work **today** (with overrides for non-compliant servers) and get simpler as the source fixes land.

> ⚠️ **Deployment caveat:** the live `hgnc-link` endpoint currently serves the `mgi-link` binary (deployment mismatch). Keep `hgnc` `enabled: false` in the registry until that deployment is corrected.

## 4. Architecture

```
host (Claude/Cursor)
      │  Streamable HTTP + auth (MultiAuth)
      ▼
┌─────────────────────────────────────────────┐
│ genefoundry-router  (FastMCP "genefoundry")  │
│  • MultiAuth (none|jwt|oauth)                │
│  • BM25SearchTransform (search_tools/call)  │
│  • per-backend ToolTransform (normalize)    │
│  • mount(create_proxy(url), namespace=token)│  ◄── servers.yaml + .env
│  • discovery: list_changed + polling        │
│  • /health, /metrics, structlog             │
└───────┬───────────┬───────────┬─────────────┘
        ▼           ▼           ▼
   gnomad-link  pubtator-link  …  (remote HTTP MCP /mcp, metadata-cached)
```

**Startup:** load + validate `servers.yaml` (URLs from `.env`) → for each enabled backend, build `create_proxy(url)` with metadata caching → apply per-backend `ToolTransform` (prefix strip / arg rename) → `mount(proxy, namespace=token)` → apply `BM25SearchTransform` → start Streamable HTTP with the configured auth.

## 5. Backend registry & configuration

`servers.yaml` (committed, no secrets) + `.env` (gitignored, URLs/tokens).

```yaml
# servers.yaml
defaults:
  transport: http
  enabled: true
  cache_ttl: 300          # proxy metadata cache TTL (s)
  tags: []
servers:
  - { name: gnomad,      repo: berntpopp/gnomad-link,        url_env: GF_GNOMAD_URL,      namespace: gnomad,      tags: [variant, gene, frequency, population] }
  - { name: gtex,        repo: berntpopp/gtex-link,          url_env: GF_GTEX_URL,        namespace: gtex,        tags: [expression, tissue] }
  - { name: hgnc,        repo: berntpopp/hgnc-link,          url_env: GF_HGNC_URL,        namespace: hgnc,        tags: [gene, nomenclature], enabled: false }  # deployment blocker
  - { name: mgi,         repo: berntpopp/mgi-link,           url_env: GF_MGI_URL,         namespace: mgi,         tags: [mouse, phenotype, model] }
  - { name: uniprot,     repo: berntpopp/uniprot-link,       url_env: GF_UNIPROT_URL,     namespace: uniprot,     tags: [protein, function] }
  - { name: clingen,     repo: berntpopp/clingen-link,       url_env: GF_CLINGEN_URL,     namespace: clingen,     tags: [gene-disease, curation] }
  - { name: gencc,       repo: berntpopp/gencc-link,         url_env: GF_GENCC_URL,       namespace: gencc,       tags: [gene-disease, curation] }
  - { name: litvar,      repo: berntpopp/litvar-link,        url_env: GF_LITVAR_URL,      namespace: litvar,      tags: [variant, literature] }
  - { name: stringdb,    repo: berntpopp/stringdb-link,      url_env: GF_STRINGDB_URL,    namespace: stringdb,    tags: [ppi, network] }
  - { name: autopvs1,    repo: berntpopp/autopvs1-link,      url_env: GF_AUTOPVS1_URL,    namespace: autopvs1,    tags: [variant, acmg, pvs1] }
  - { name: spliceai,    repo: berntpopp/spliceailookup-link, url_env: GF_SPLICEAI_URL,   namespace: spliceai,    tags: [variant, splicing, prediction] }
  - { name: genereviews, repo: berntpopp/genereviews-link,   url_env: GF_GENEREVIEWS_URL, namespace: genereviews, tags: [literature, gene-disease] }
  - { name: pubtator,    repo: berntpopp/pubtator-link,      url_env: GF_PUBTATOR_URL,    namespace: pubtator,    tags: [literature, entity],
      transform: { strip_prefix: "pubtator_" } }   # remove until pubtator-link#57 lands
```

Each server resolves its `/mcp` URL from the named env var (e.g. `GF_GNOMAD_URL=https://gnomad-link.example.org/mcp`). A backend with a missing/unset URL is skipped with a warning (router still starts). `transform.strip_prefix` / `transform.rename` provide stopgap normalization for non-compliant backends.

## 6. Composition & namespacing

- Per-backend `router.mount(create_proxy(url, ...), namespace=token)` → tools surface as `<token>_<tool>` (e.g. `gnomad_get_variant_details`).
- Namespacing is mandatory for every backend (never rely on `serverInfo.name` or mount precedence).
- Char budget: enforce `len("<token>_<tool>") <= 64`; warn at startup on any violation.
- Prefer `mount(proxy)` over a single `create_proxy({mcpServers})` dict so each backend can carry its own tags/transforms/enable flag.

## 7. Normalization

Two layers:
1. **Source (preferred):** the per-repo Standard-v1 issues fix names/args at the leaf.
2. **Router stopgap:** `ToolTransform` config per backend to `strip_prefix`, rename tools, or remap arg names to the fleet canon (`gene_symbol`, `variant_id`, `pmid`, `hpo_id`, `response_mode`, `limit`, `offset`). As source fixes land, the corresponding `transform` blocks are deleted.

## 8. Tool-overload — BM25 search

Apply `BM25SearchTransform` so the router exposes a small surface: `search_tools` (NL/relevance search over the ~189 tools), `call_tool` (invoke a discovered tool), plus a few `always_visible` essentials (e.g. `resolve_variant_id`, `search_genes`). `max_results` tuned (default 5). Originals remain callable but hidden from the default listing. (Verify exact transform class/API against current FastMCP 3.x docs at build time.)

## 9. Auth (pluggable, OAuth-ready)

`GF_AUTH_MODE = none | jwt | oauth`, assembled via FastMCP `MultiAuth`:
- `none` — open endpoint (PoC; backends are public anyway).
- `jwt` — `JWTVerifier`/`TokenVerifier` (M2M; static issuer/JWKS via env).
- `oauth` — `OAuthProxy` with a provider (GitHub/Google/Azure) for interactive login; env-driven client id/secret/redirect.
- `MultiAuth` lets `jwt` + `oauth` coexist. v1 ships defaulting to `none` or `jwt` (operator choice); `oauth` fully wired and switchable by config — satisfies the OAuth-ready requirement without blocking the PoC.

## 10. Discovery & freshness

Subscribe to each backend's `notifications/tools/list_changed` and re-list to refresh the index + re-run search indexing. Because client/server `list_changed` support is uneven (spec SHOULD), add a **polling fallback** (configurable interval) to re-list backends. Proxy **metadata caching** (TTL `cache_ttl`) absorbs per-backend `list_tools` latency.

## 11. Transport

Streamable HTTP only (`transport="http"`, `--host/--port`); SSE is deprecated and not offered. MCP at `/mcp`, matching the fleet.

## 12. Observability

- `/health` (liveness + per-backend reachability summary).
- `/metrics` (Prometheus: per-backend up/latency, tool-call counts, search hits).
- `structlog` JSON logs with request/correlation IDs (`asgi-correlation-id`).

## 13. Project layout (mirrors `-link`)

```
genefoundry-router/
  genefoundry_router/
    __init__.py
    config.py          # load + validate servers.yaml, resolve .env URLs
    registry.py        # BackendDef model (name, url, namespace, tags, enabled, transform)
    composition.py     # build proxies, apply transforms, mount with namespace
    normalization.py   # ToolTransform builders (strip_prefix, rename, arg remap)
    discovery.py       # list_changed subscription + polling fallback
    tool_search.py     # BM25SearchTransform wiring + always_visible set
    auth.py            # MultiAuth assembly from GF_AUTH_MODE
    observability.py   # health, metrics, logging
    cli.py             # typer app: run/validate/list-tools/doctor
    server.py          # FastMCP("genefoundry") assembly + ASGI app
  servers.yaml
  .env.example
  docker/              # Dockerfile + docker-compose.{yml,prod,dev,npm}
  docs/specs/2026-06-13-genefoundry-router-design.md
  tests/
  pyproject.toml  Makefile  README.md  CLAUDE.md  AGENTS.md  LICENSE
```

## 14. CLI (`typer`)

- `genefoundry-router run --transport http --host 0.0.0.0 --port 8000`
- `genefoundry-router validate` — validate `servers.yaml` + env, report missing URLs.
- `genefoundry-router list-tools [--namespace gnomad]` — enumerate federated tools (post-namespace, post-transform), flag >64-char names.
- `genefoundry-router doctor` — ping each backend `/mcp`, report reachable/auth/tool counts.

## 15. Configuration reference (env)

`GF_AUTH_MODE`, `GF_JWT_ISSUER`/`GF_JWT_JWKS_URL`, `GF_OAUTH_PROVIDER`/`GF_OAUTH_CLIENT_ID`/`GF_OAUTH_CLIENT_SECRET`/`GF_OAUTH_BASE_URL`, `GF_<NAME>_URL` (per backend), `GF_POLL_INTERVAL`, `GF_LOG_LEVEL`, `GF_PORT`.

## 16. Testing strategy

- **Unit:** config parsing/validation; namespace + 64-char enforcement; transform builders (strip_prefix/rename/arg remap); auth assembly per mode.
- **Integration:** spin up ≥2 fake FastMCP backends (in-process) with colliding tool names → assert namespacing, search surface (`search_tools`/`call_tool`), and a proxied call round-trips.
- **Contract:** `doctor`/`list-tools` against fakes; `list_changed` triggers re-index; polling fallback re-lists.
- Coverage ≥70 (fleet parity), ruff + mypy clean.

## 17. Deployment

Docker image + compose mirroring `gnomad-link/docker/` (base, `prod`, `dev`, `npm`). `EXPOSE 8000`, `CMD genefoundry-router run --transport http --host 0.0.0.0 --port 8000`, `/health` probe, public `/mcp` behind nginx-proxy-manager. Secrets/URLs via env.

## 18. Milestones

- **v0.1 (PoC):** config + mount/proxy of 2–3 enabled backends, namespacing, Streamable HTTP, `/health`, `doctor`. Auth `none`. Add to Claude, confirm tools work.
- **v0.2:** BM25 search surface; all reachable backends; `pubtator` strip_prefix transform; metrics + structured logs.
- **v0.3:** auth `jwt` + `oauth` wired; `list-tools`/`validate`; polling fallback; Dockerized deploy.
- **v1.0:** all backends green (incl. hgnc once deployment fixed), source normalization landed (transforms removed), docs + landing-page "Add to Claude" button.

## 19. Open questions

1. Production URL pattern for backends (`https://<name>-link.<domain>/mcp`?) — fills `.env`.
2. Standard **v1.1 verb canon** — extend with `predict`/`annotate`/`submit`/`export` (vs documented exceptions) for action/compute servers (spliceai, stringdb, pubtator).
3. Default `GF_AUTH_MODE` for the first public deploy (`none` vs `jwt`).
4. ~~`always_visible` essentials set for `search_tools` (which 3–6 tools stay pinned).~~
   **Resolved (2026-06-16):** pin the fleet's entry-point resolvers
   `gnomad_resolve_variant_id` + `gnomad_search_genes` (variant-ID normalization, symbol→gene)
   — the most common first step, so pinning saves a `search_tools` round-trip. All other tools
   route via `search_tools` → `call_tool`. See
   [`2026-06-16-router-agentic-ergonomics-design.md`](2026-06-16-router-agentic-ergonomics-design.md) §5.

## 20. References

MCP tools spec; SEP-986 (charset/length); SEP-993 (namespaces, draft); FastMCP docs — composition (`mount`/`namespace`), providers/proxy (`create_proxy`), tool transformation (`ToolTransform`/`ArgTransform`), tool search (`BM25SearchTransform`), auth (`MultiAuth`/`OAuthProxy`/`JWTVerifier`). *(FastMCP 3.x APIs are fast-moving — verify exact symbols against current docs at implementation time.)*
