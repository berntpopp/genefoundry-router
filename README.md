# GeneFoundry Router

`genefoundry-router` — a thin **FastMCP 3.x aggregator** that federates the GeneFoundry
`*-link` MCP fleet behind a single Streamable-HTTP endpoint (deployed MCP server name:
`genefoundry`). A host (Claude, Cursor, Gemini, …) adds **one** server and transparently
gets every backend (gnomAD, GTEx, HGNC, MGI, UniProt, ClinGen, GenCC, LitVar, STRING,
AutoPVS1, SpliceAI, GeneReviews, PubTator) with collision-free namespacing, normalized
tool names, and search-based tool discovery.

> Research use only. Not for clinical decision support. Mirrors the disclaimers of the
> underlying `-link` backends.

## Core Purpose

The fleet is ~13 FastMCP `-link` servers (~189 tools). Exposing all of them to a model at
once is unworkable. The router federates them behind one endpoint, namespaces every tool as
`<token>_<tool>` (e.g. `gnomad_get_variant_details`) so names never collide, and presents a
small **search surface** so the model is never shown the full catalog at once.

## Key Features

- **One endpoint** federating all `-link` backends; config-driven registry (`servers.yaml`).
- **Collision-free namespacing** (`<token>_<tool>`), with a 64-char MCP-name guard.
- **Tool-overload control** via FastMCP `BM25SearchTransform` — exposes `search_tools` +
  `call_tool` + a few pinned essentials instead of ~189 raw tools.
- **Pluggable, OAuth-ready auth** (`none | jwt | oauth`) on the router endpoint.
- **Per-backend normalization** transforms (e.g. strip a self-prefix) until source repos
  adopt Tool-Naming Standard v1.
- **Observability**: `/health` (per-backend reachability), `/metrics` (Prometheus),
  structlog JSON logs with correlation IDs.

## Quick Start

```bash
# 1. Install (Python 3.12+, uv)
uv sync --group dev

# 2. Configure: copy the template and fill in backend URLs
cp .env.example .env        # then edit GF_*_URL values

# 3. Run the router over Streamable HTTP
uv run genefoundry-router run --host 127.0.0.1 --port 8000

# 4. Verify
curl -s localhost:8000/health | python -m json.tool
```

Add the server to your MCP host using the `/mcp` URL (e.g. `http://localhost:8000/mcp`).

## Configuration

Structure lives in committed `servers.yaml`; secrets/URLs live in gitignored `.env`
(copy `.env.example`). Key environment variables (prefix `GF_`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `GF_HOST` / `GF_PORT` | `127.0.0.1` / `8000` | Bind address |
| `GF_MCP_PATH` | `/mcp` | MCP mount path |
| `GF_SERVERS_FILE` | `servers.yaml` | Backend registry |
| `GF_SEARCH_MAX_RESULTS` | `5` | BM25 `search_tools` result cap |
| `GF_POLL_INTERVAL` | `0` | Polling re-list interval (s); `0` disables |
| `GF_AUTH_MODE` | `none` | `none` \| `jwt` \| `oauth` |
| `GF_ALLOWED_ORIGINS` | _(empty)_ | CSV Origin allowlist (DNS-rebinding defense) |
| `GF_PUBLIC_BASE_URL` | _(unset)_ | Public URL behind the proxy (OAuth resource URI) |
| `GF_JWT_ISSUER` / `GF_JWT_JWKS_URL` / `GF_JWT_AUDIENCE` | _(unset)_ | JWT verification |
| `GF_<NAME>_URL` | _(unset)_ | Per-backend `/mcp` URL (e.g. `GF_GNOMAD_URL`) |

A backend with a missing/unset URL is skipped with a warning (the router still starts).

## CLI

```bash
genefoundry-router run        --host 0.0.0.0 --port 8000   # serve over Streamable HTTP
genefoundry-router validate                                # check servers.yaml + env, report missing URLs
genefoundry-router list-tools [--namespace gnomad]         # enumerate federated tools, flag >64-char names
genefoundry-router doctor     [--strict-naming]            # ping backends; optionally audit leaf names vs Standard v1
```

## MCP Integration & Tool Discovery

The router speaks **Streamable HTTP** at `/mcp`. Instead of listing ~189 tools, it exposes a
synthetic search surface: **`search_tools`** (relevance search over the federated catalog) and
**`call_tool`** (invoke a discovered tool), plus a few pinned `always_visible` essentials.
Originals remain callable but hidden from the default listing.

> This server-side `search_tools`/`call_tool` surface is client-agnostic and independent from
> Anthropic's API-level tool-search. The federated names are also valid for **Gemini** Remote
> MCP (snake_case, `[a-z0-9_]`, ≤64 chars, no dots/dashes) — the gateway already emits these.

## Architecture

```
host → Streamable HTTP + auth → genefoundry-router (FastMCP "genefoundry")
         • MultiAuth (none|jwt|oauth)   • BM25SearchTransform (search_tools/call_tool)
         • per-backend ToolTransform    • mount(create_proxy(url), namespace=token)
         • /health, /metrics, structlog
       → 13 remote -link MCP backends (metadata-cached proxies)
```

## Security

- **Origin validation** (`GF_ALLOWED_ORIGINS`): per MCP transport spec, a request that sends a
  disallowed `Origin` is rejected with 403 (DNS-rebinding defense). Requests with no `Origin`
  (non-browser MCP clients) pass through.
- **Auth modes**: `GF_AUTH_MODE=none` is **local/PoC only**; use `jwt` or `oauth` for public
  deployments. In `jwt`/`oauth` mode the router serves MCP Protected-Resource-Metadata and
  returns `401` + `WWW-Authenticate` to unauthenticated callers.
- **No token passthrough**: the gateway authenticates the *caller* at the edge and never
  forwards the caller's token to the 13 backends (confused-deputy defense).

## Deployment

Docker image + compose overlays under `docker/` (base / `prod` / `dev` / `npm`), mirroring the
fleet. Runs behind nginx-proxy-manager; set `GF_PUBLIC_BASE_URL` and ensure forwarded headers
(`X-Forwarded-Proto`/`-Host`) reach the app so generated URLs use the public host.

```bash
make docker-build      # build the image
make docker-up         # start the dev stack
```

## Status caveats

- **hgnc** is `enabled: false` until its live deployment is fixed (currently serves the
  `mgi-link` binary).
- **pubtator** carries a `strip_prefix: "pubtator_"` transform until `pubtator-link` drops its
  self-prefix at the source.

See `docs/specs/2026-06-13-genefoundry-router-design.md` and
`docs/plans/2026-06-13-genefoundry-router-implementation.md`.
