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
| `GF_REWRITE_HINTS` | `true` | Rewrite bare tool refs in responses to namespaced form |
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

**Compact discovery payloads.** `search_tools(query, detail="compact")` (the default) returns,
per hit, the tool name, description, full **`inputSchema`** (the argument contract you need to
construct a correct call), a one-line **`returns`** summary, and tags — but *not* the full
nested `outputSchema` or the per-tool `_meta` block. That nested schema dump was the dominant
token cost of using the fleet (discovery, not data). Pass `detail="full"` to get the complete
output schema for a hit when you actually need it.

**Pinned essentials.** The default listing keeps two gnomAD tools first-class —
`gnomad_resolve_variant_id` and `gnomad_search_genes`. These are the fleet's **entry-point
resolvers** (variant-ID normalization and symbol→gene lookup) that most workflows hit first;
pinning them saves a `search_tools` round-trip on the common first step. Every other tool,
gnomAD's included, is reached via `search_tools` → `call_tool`.

**Self-healing hints work through the gateway.** Backends embed recovery hints in their
responses (`fallback_tool`, `next_commands[].tool`). The router rewrites those references to
the same namespaced form it gives the tools (`search_genes` → `clingen_search_genes`), so a
hint you follow via `call_tool` resolves instead of failing. Toggle with `GF_REWRITE_HINTS`.

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
make docker-up         # start the stack (host port 8010 by default)
make docker-rebuild    # rebuild image + (re)start; reads ../.env
make docker-restart    # recreate the container to re-read ../.env (no rebuild)
make docker-down       # stop the stack
make docker-logs       # follow logs
```

## Local testing (offline fake fleet)

Run the real router against impersonated backends over real Streamable-HTTP — no Docker, no network:

```bash
make dev-fleet   # terminal 1: fakes on :9100 (driven by tests/fixtures/fleet_manifest.json)
make run-dev     # terminal 2: router on :8000 against the fakes (exports .env.dev)
make test-e2e    # one-shot: boot fleet in-process, assert federation, tear down
```

Refresh the manifest from the live fleet when tool surfaces change (online):

```bash
make snapshot-fleet
```

## Local testing against the live fleet

Point the router at the deployed VPS backends (`https://<repo>.genefoundry.org/mcp`) with auth
disabled, then register it with your MCP host.

**1. Configure `.env`** (gitignored). Copy the template and set each `GF_*_URL` to its live
domain; keep `GF_AUTH_MODE=none` for local testing. Note `GF_SPLICEAI_URL` uses the
`spliceailookup-link` domain, not the `spliceai` namespace:

```bash
cp .env.example .env
# GF_GNOMAD_URL=https://gnomad-link.genefoundry.org/mcp
# ...
# GF_SPLICEAI_URL=https://spliceailookup-link.genefoundry.org/mcp
# GF_AUTH_MODE=none
```

**2. Run it** — pick one:

```bash
# A) Host process on :8000 — `make run` now exports .env automatically
make run

# B) Docker on :8010 — base compose reads ../.env; mirrors production
make docker-rebuild    # build + (re)start
make docker-restart    # recreate to re-read .env after editing it (no rebuild)
make docker-down       # stop
```

**3. Verify** all 13 backends federate:

```bash
make doctor                                          # host path: "OK <ns>: N tools" x13
curl -s localhost:8010/health | python -m json.tool  # docker path: 13 enabled, all reachable
```

**4. Add to Claude Code** (auth none → no headers needed). Use the port for the path you ran:

```bash
claude mcp add --transport http genefoundry http://127.0.0.1:8010/mcp   # docker (:8010)
# or                              ...        http://127.0.0.1:8000/mcp   # host (:8000)
```

Then `claude mcp list` should show `genefoundry ✓ connected`, and `/mcp` lists it in-session.
The catalog presents as `search_tools` + `call_tool` + the pinned essentials by design — the
other ~180 tools are reached via `search_tools` then `call_tool`. Add `--scope user` to make it
available in every project.

## Status caveats

- **hgnc** is deployed (`hgnc-link.genefoundry.org`) and `enabled: true` as of 2026-06-16; all
  13 backends are live. (The earlier "serves mgi-link binary" note was an obsolete deploy incident.)
- **pubtator** no longer needs a transform: `pubtator-link` adopted Tool-Naming Standard v1
  ([pubtator-link#57](https://github.com/berntpopp/pubtator-link/pull/64)), dropping its
  `pubtator_` self-prefix at the source, so the stopgap `strip_prefix` block was removed.

### Backend-conformance gaps (upstream, not router)

The router is a **thin aggregator**: it namespaces and shapes the surface, but it must not
fabricate provenance a backend never emitted or rewrite a backend's response envelope. These
items are tracked in the source `-link` repos, not papered over here:

- **`stringdb` envelope.** `stringdb-link` returns a bare `{partners, total_count}` — no
  `success` flag, no `_meta`, no `recommended_citation`, no `unsafe_for_clinical_use` stamp.
  Every other backend carries provenance + a research-use disclaimer. Fix is in
  `stringdb-link` (adopt the fleet envelope); the router will **not** synthesize a citation,
  since inventing provenance in a research-safety tool is worse than its absence.
- **Envelope heterogeneity.** Four response shapes exist across the fleet (`success`+`_meta`;
  `ok`/`data`/`error`/`meta`; result-wrapped; bare typed dicts). This is an inherent cost of
  federation; the router does not normalize envelopes (lossy, and outside the thin-aggregator
  boundary). A generic consumer should detect success across shapes; convergence is tracked via
  the fleet response standard.
- **`spliceai` latency.** `spliceai.predict_splicing` can take ~60 s even warm. The router
  passes through the backend's own `cost_tier` / `expected_cold_latency_ms` / `taskSupport`
  signals; agents should prefer background tasks for compute-tier tools rather than blocking.

See `docs/specs/2026-06-13-genefoundry-router-design.md`,
`docs/specs/2026-06-16-router-agentic-ergonomics-design.md`, and
`docs/plans/2026-06-13-genefoundry-router-implementation.md`.
