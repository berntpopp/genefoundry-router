# GeneFoundry Router

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP 3.x](https://img.shields.io/badge/FastMCP-3.x-6E40C9)](https://github.com/jlowin/fastmcp)
[![Packaged with uv](https://img.shields.io/badge/packaged%20with-uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![Lint: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Typed: mypy strict](https://img.shields.io/badge/typed-mypy%20strict-2A6DB2)](https://mypy-lang.org/)
[![Tests: 126 passing](https://img.shields.io/badge/tests-126%20passing-brightgreen)](#develop--test)
[![Discoverability: 9.8/10](https://img.shields.io/badge/discoverability-9.8%2F10-brightgreen)](#how-discovery-works)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A thin **FastMCP 3.x aggregator** that federates the GeneFoundry `*-link` MCP fleet behind a
single Streamable-HTTP endpoint. A host (Claude, Cursor, Gemini, …) adds **one** server —
`genefoundry` — and transparently gets every backend with collision-free `<namespace>_<tool>`
naming and search-based discovery instead of a ~200-tool wall. The router is a *client* to each
backend and a *server* to hosts; it namespaces and shapes the surface but never rewrites a
backend's data.

> ⚕️ **Research use only. Not clinical decision support.** Mirrors each backend's disclaimers.

**Hosted endpoint:** the live router is served at **`https://genefoundry.org/mcp`** (health check at
[`/health`](https://genefoundry.org/health)). Add it to any MCP host — no install required:

```bash
claude mcp add --transport http genefoundry https://genefoundry.org/mcp
```

## MCP services

Backends are declared in committed [`servers.yaml`](servers.yaml) (URLs in gitignored `.env`).
**21 backends, 280 tools**, each surfaced namespaced — e.g. `gnomad_search_genes`.

| Namespace | Domain | Data source | Tools | Repo |
|-----------|--------|-------------|------:|------|
| `pubtator` | Literature & entity annotation | [PubTator3](https://www.ncbi.nlm.nih.gov/research/pubtator3/) | 43 | [pubtator-link](https://github.com/berntpopp/pubtator-link) |
| `gnomad` | Variant / gene / population frequency | [gnomAD](https://gnomad.broadinstitute.org/) | 22 | [gnomad-link](https://github.com/berntpopp/gnomad-link) |
| `orphanet` | Rare disease ontology & associations | [Orphadata](https://www.orphadata.com/) | 19 | [orphanet-link](https://github.com/berntpopp/orphanet-link) |
| `clingen` | Gene–disease curation | [ClinGen](https://clinicalgenome.org/) | 17 | [clingen-link](https://github.com/berntpopp/clingen-link) |
| `hpo` | Phenotype ontology & associations | [Human Phenotype Ontology](https://hpo.jax.org/) | 17 | [hpo-link](https://github.com/berntpopp/hpo-link) |
| `mavedb` | Variant-effect assay scores | [MaveDB](https://www.mavedb.org/) | 15 | [mavedb-link](https://github.com/berntpopp/mavedb-link) |
| `uniprot` | Protein function | [UniProt](https://www.uniprot.org/) | 15 | [uniprot-link](https://github.com/berntpopp/uniprot-link) |
| `mgi` | Mouse phenotype & models | [MGI](https://www.informatics.jax.org/) | 13 | [mgi-link](https://github.com/berntpopp/mgi-link) |
| `genereviews` | Gene–disease literature | [GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1116/) | 13 | [genereviews-link](https://github.com/berntpopp/genereviews-link) |
| `mondo` | Disease ontology / cross-references | [Mondo](https://mondo.monarchinitiative.org/) | 13 | [mondo-link](https://github.com/berntpopp/mondo-link) |
| `gencc` | Gene–disease curation | [GenCC](https://thegencc.org/) | 12 | [gencc-link](https://github.com/berntpopp/gencc-link) |
| `metadome` | Protein tolerance landscapes | [MetaDome](https://stuart.radboudumc.nl/metadome/) | 11 | [metadome-link](https://github.com/berntpopp/metadome-link) |
| `stringdb` | Protein–protein interaction networks | [STRING](https://string-db.org/) | 10 | [stringdb-link](https://github.com/berntpopp/stringdb-link) |
| `gtex` | Tissue expression | [GTEx Portal](https://gtexportal.org/) | 9 | [gtex-link](https://github.com/berntpopp/gtex-link) |
| `hgnc` | Gene nomenclature | [HGNC](https://www.genenames.org/) | 9 | [hgnc-link](https://github.com/berntpopp/hgnc-link) |
| `panelapp` | Diagnostic gene panels & curation | [PanelApp](https://panelapp.genomicsengland.co.uk/) | 9 | [panelapp-link](https://github.com/berntpopp/panelapp-link) |
| `autopvs1` | Variant ACMG PVS1 | [AutoPVS1](https://autopvs1.bgi.com/) | 7 | [autopvs1-link](https://github.com/berntpopp/autopvs1-link) |
| `spliceai` | Splicing prediction | [SpliceAI Lookup](https://spliceailookup.broadinstitute.org/) | 7 | [spliceailookup-link](https://github.com/berntpopp/spliceailookup-link) |
| `vep` | Variant annotation / consequence | [Ensembl VEP](https://rest.ensembl.org/) | 7 | [vep-link](https://github.com/berntpopp/vep-link) |
| `clinvar` | Variant clinical significance | [ClinVar](https://www.ncbi.nlm.nih.gov/clinvar/) | 6 | [clinvar-link](https://github.com/berntpopp/clinvar-link) |
| `litvar` | Variant literature | [LitVar2](https://www.ncbi.nlm.nih.gov/research/litvar2/) | 6 | [litvar-link](https://github.com/berntpopp/litvar-link) |

## Quick start

```bash
# 1. Install (Python 3.12+, uv)
uv sync --group dev

# 2. Configure: copy the template, set GF_*_URL backend URLs and GF_AUTH_MODE
cp .env.example .env

# 3. Run over Streamable HTTP
uv run genefoundry-router run --host 127.0.0.1 --port 8000

# 4. Verify backend reachability
curl -s localhost:8000/health | python -m json.tool
```

Then point your MCP host at the local instance:

```bash
claude mcp add --transport http genefoundry http://127.0.0.1:8000/mcp
```

## How discovery works

`genefoundry` is a **meta-router**, not a flat tool server. Listing ~200 tools to a model is
unworkable, so the router exposes a **search surface** instead of the full catalog:

- **`search_tools`** — relevance search over the *entire* federated catalog.
- **`call_tool`** — invoke a hit by its `<namespace>_<tool>` name.
- a small set of pinned **canonical entry points** — each backend's front-door tool (a
  free-text→ID resolver and/or the primary query), declared per-backend via `entrypoints:` in
  `servers.yaml` and generated into both the pinned `always_visible` set *and* the server
  `instructions` map. Pinning makes each domain's canonical tool discoverable **deterministically**
  rather than by relevance luck — the fix for FastMCP's flat BM25 index (no field weighting, no
  stemming), which let a terse canonical tool lose to verbose tools that merely repeat a keyword.

**Everything else is reached via `search_tools` → `call_tool`** (and is also directly callable by
full name once known). A typical flow:

```text
search_tools(query="splicing prediction")        # → hit: name="spliceai_predict_splicing", inputSchema, returns
call_tool(name="spliceai_predict_splicing", arguments={...})
```

The model is oriented on this two-layer model via the MCP **`instructions`** field (set on the
server) plus the `search_tools`/`call_tool` descriptions. The router also **improves the search
itself**: its `CompactBM25SearchTransform` folds the tool name/leaf and tags into the index and
stems both documents and queries, so word-form mismatches (`expressed`↔`expression`) and
keyword-stuffed prose no longer hide the right tool. Federated names are also valid for Gemini
Remote MCP (snake_case, `[a-z0-9_]`, ≤64 chars).

Discoverability is **measured, not assumed**: `make bench-discoverability` scores how reliably ~50
realistic intents reach their canonical tool through this exact surface over a snapshot of the real
catalog (currently **9.79/10**, 100% reachable in the served top-5). The bar is enforced in CI
(`tests/discoverability/`), so tuning pins, search, or descriptions stays evidence-driven.

> **Two traps to avoid** (see [#3](https://github.com/berntpopp/genefoundry-router/issues/3)).
> A capability missing from your **host/client-side tool list is not missing** — the host only
> sees `search_tools`, `call_tool`, and the pinned entry points; call `search_tools` before
> concluding a tool doesn't exist.
> And `search_tools` returns *data*, so you **don't re-run a host tool search to invoke a hit** —
> just call `call_tool`. An `Unknown tool: call_tool` means your client evicted it; re-run
> `search_tools` to rediscover and continue (recoverable, not a router fault).

## Configuration

Structure lives in committed `servers.yaml`; URLs/secrets in gitignored `.env` (copy
`.env.example`). Key environment variables (prefix `GF_`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `GF_HOST` / `GF_PORT` | `127.0.0.1` / `8000` | Bind address |
| `GF_MCP_PATH` | `/mcp` | MCP mount path |
| `GF_SERVERS_FILE` | `servers.yaml` | Backend registry |
| `GF_AUTH_MODE` | `none` | `none` \| `jwt` \| `oauth` (use jwt/oauth in production) |
| `GF_DEPLOYMENT_MODE` | `development` | `development` \| `production`; explicit reachability policy, because a loopback listener can still be published by a reverse proxy |
| `GF_ALLOW_INSECURE` | `false` | Opt-in to serve `auth=none` on a non-loopback bind (PoC only; it never weakens production observability controls) |
| `GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY` | `false` | Explicit, warning-emitting acknowledgement for an authenticated local development router without the production rate-limit and/or metrics-token controls; ignored in production |
| `GF_PUBLIC_BASE_URL` | _(unset)_ | Router's canonical public URL — OAuth resource URI + Protected-Resource-Metadata |
| `GF_ALLOWED_HOSTS` | _(empty)_ | CSV Host allowlist; required for every non-loopback bind |
| `GF_JWT_ISSUER` | _(unset)_ | jwt/oauth: token issuer URL (e.g. `https://auth.example.org/realms/genefoundry`) |
| `GF_JWT_JWKS_URL` | _(unset)_ | jwt/oauth: issuer JWKS endpoint (signature verification keys) |
| `GF_JWT_AUDIENCE` | _(unset)_ | jwt/oauth: required token `aud` (MUST match; audience binding) |
| `GF_OAUTH_CLIENT_ID` / `GF_OAUTH_CLIENT_SECRET` | _(unset)_ | oauth: upstream provider client credentials |
| `GF_OAUTH_AUTHORIZE_URL` / `GF_OAUTH_TOKEN_URL` | _(unset)_ | oauth: upstream provider authorize/token endpoints |
| `GF_ALLOWED_ORIGINS` | _(empty)_ | CSV `Origin` allowlist (DNS-rebinding defense) |
| `GF_RATE_LIMIT_RPM` | `0` | Per-client requests/min (429 over). An authenticated `GF_DEPLOYMENT_MODE=production` router **refuses to start** with `0`, even on loopback behind a proxy |
| `GF_METRICS_TOKEN` | _(unset)_ | Bearer token for `GET /metrics`. An authenticated `GF_DEPLOYMENT_MODE=production` router **refuses to start** without it, even on loopback behind a proxy |
| `GF_DRIFT_MODE` | `warn` | Runtime catalog policy: `off` \| `warn` \| `enforce` |
| `GF_DRIFT_BASELINE` | _(packaged)_ | Optional path override for the reviewed packaged baseline |
| `GF_<NAME>_URL` | _(unset)_ | Per-backend `/mcp` URL (e.g. `GF_GNOMAD_URL`) |

A backend with an unset URL or `enabled: false` is skipped with a warning; the router still starts.
**No token passthrough:** the caller is authenticated at the edge and their token is never
forwarded to a backend (confused-deputy defense).

### Authentication

The router refuses to start `auth=none` on a non-loopback bind unless `GF_ALLOW_INSECURE=true`
(the explicit, logged escape hatch for a deliberately-public PoC). It likewise **refuses to start an
authenticated `GF_DEPLOYMENT_MODE=production` router that has no positive `GF_RATE_LIMIT_RPM`, or that
would serve `GET /metrics` without `GF_METRICS_TOKEN`** — including a loopback listener published by a
reverse proxy. `GF_ALLOW_INSECURE` only controls the unauthenticated public-bind guard; it cannot weaken
production observability controls. A local authenticated development router may set the separately named
`GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY=true`; this emits a warning and is ignored in production.
For production, enable one of
two **resource-server** modes — the router *validates* tokens against an identity provider, it does
not mint them, so an IdP (e.g. self-hosted Keycloak) is required:

- **`oauth`** — OAuth 2.1 for interactive/browser MCP clients (claude.ai, Cursor). The router serves
  Protected-Resource-Metadata (RFC 9728) + `WWW-Authenticate`, and proxies an upstream provider so
  clients can complete the login flow; access tokens are verified against `GF_JWT_JWKS_URL` with
  audience binding (`GF_JWT_AUDIENCE`). The router's OAuthProxy **is** the Dynamic-Client-Registration
  facade — it serves `/register` itself, so Keycloak's DCR stays closed; on the Keycloak client,
  whitelist the router's callback `https://genefoundry.org/auth/callback` as a Valid Redirect URI.
- **`jwt`** — machine-to-machine: verify bearer JWTs from `GF_JWT_ISSUER` (JWKS + audience), no
  interactive-login facade.

Example (self-hosted Keycloak at `auth.example.org`, realm `genefoundry`):

```bash
GF_AUTH_MODE=oauth
GF_OAUTH_CLIENT_ID=genefoundry-router
GF_OAUTH_CLIENT_SECRET=…                 # secret; set in the server env, never commit
GF_OAUTH_AUTHORIZE_URL=https://auth.example.org/realms/genefoundry/protocol/openid-connect/auth
GF_OAUTH_TOKEN_URL=https://auth.example.org/realms/genefoundry/protocol/openid-connect/token
GF_JWT_ISSUER=https://auth.example.org/realms/genefoundry
GF_JWT_JWKS_URL=https://auth.example.org/realms/genefoundry/protocol/openid-connect/certs
GF_JWT_AUDIENCE=https://genefoundry.org/mcp   # Keycloak must stamp this into the token `aud`
GF_PUBLIC_BASE_URL=https://genefoundry.org     # ROOT origin, NO path — OAuth routes live at root
```

`GF_PUBLIC_BASE_URL` is the bare public origin: the OAuth endpoints (`/authorize`, `/token`,
`/register`, `/.well-known/*`) are served at the root, and the MCP endpoint is
`GF_PUBLIC_BASE_URL` + `GF_MCP_PATH` (→ `https://genefoundry.org/mcp`), which is also the OAuth
resource / token audience. Putting a path here (`…/mcp`) mis-advertises OAuth endpoints as
`…/mcp/authorize` and doubles the protected-resource-metadata URL.

Verify: an unauthenticated `POST /mcp` returns `401` + `WWW-Authenticate`; a request bearing a valid
issuer-signed, correctly-audienced token returns `200`. See `.env.docker.example` for all three modes.

## CLI

```bash
genefoundry-router run        --host 0.0.0.0 --port 8000   # serve over Streamable HTTP
genefoundry-router validate                                # check servers.yaml + env, report missing URLs
genefoundry-router list-tools [--namespace gnomad]         # enumerate federated tools, flag >64-char names
genefoundry-router doctor     [--strict-naming]            # ping backends; audit leaf names vs Tool-Naming v1
genefoundry-router drift      [--manifest PATH]  # defaults to packaged pin; exit 0/1/2
```

## Drift detection (scheduled CI)

A backend can serve a clean tool at review time and later change its definition — the channel
for a *rug pull* / *tool poisoning*. `genefoundry-router drift` fingerprints each normalized
tool's name, description, input/output schemas, annotations, and execution metadata (SHA-256),
then diffs the **live** fleet against a pinned baseline. `.github/workflows/drift.yml` runs it
every 6 h (opt-in) and alerts via a
deduplicated `tool-drift` GitHub issue + a healthchecks.io dead-man's-switch.

**Exit codes:** `0` no drift, all reachable · `1` drift among reachable backends (alert) ·
`2` no drift but ≥1 backend unreachable (availability warning, **not** an alert). Security
beats availability: drift + an outage still exits `1`.

**Two committed (non-secret) files:**

| File | What | Keep in sync |
|------|------|--------------|
| `ci/fleet-urls.env` | Public `GF_*_URL=https://<name>-link.genefoundry.org/mcp` for every enabled backend — the URLs CI probes | `make` test `test_ci_fleet_urls.py` asserts it matches `servers.yaml` exactly |
| `genefoundry_router/data/fleet-baseline.json` | The packaged reviewed-release tool-definition pin | `make snapshot-baseline RELEASE_CANDIDATE=<reviewed identity>` only after reviewing candidate definitions |

**Runtime response:** in `enforce` mode, a changed startup failure means operators must review the
live definition before the router accepts traffic. Poll-time changes and additions are
quarantined; additions/removals mark health degraded without killing unaffected tools.

**Re-pin discipline:** `make snapshot-baseline RELEASE_CANDIDATE=<reviewed identity>` is allowed only after code review of the complete
definition diff. Treat it as security-relevant and never re-pin merely to restore green status.
Never auto-refresh in CI; that would silently bless a rug pull.

**Enabling it (one-time, on the default branch).** All three settings are configured in
GitHub → *Settings → Secrets and variables → Actions*:

| Setting | Kind | Required? | Value / where to get it |
|---------|------|-----------|-------------------------|
| `DRIFT_ENABLED` | repo **variable** | to enable scheduled runs | `true`. Unset/`false` ⇒ scheduled runs are a no-op (forks stay off); `workflow_dispatch` always runs |
| `DRIFT_HEARTBEAT_URL` | repo **secret** | optional (heartbeat) | The ping URL of a [healthchecks.io](https://healthchecks.io) check (create one: period **6 h**, grace **~45 min**). Unset ⇒ heartbeat step skipped |
| `DRIFT_OPEN_ISSUE` | repo **variable** | optional | `false` to rely only on the red run + owner email instead of the auto-issue (default on) |

```bash
gh variable set DRIFT_ENABLED --body true
gh secret   set DRIFT_HEARTBEAT_URL --body 'https://hc-ping.com/<your-uuid>'
gh workflow run drift.yml        # smoke-test; expect exit 0 and the healthchecks.io check green
```

The `drift` CLI is independently runnable for your own cron/CI:
`genefoundry-router drift --servers-file servers.yaml`
(reads `GF_*_URL` from the environment — `set -a; . ./.env; set +a` locally, or load
`ci/fleet-urls.env`).

## Develop & test

```bash
make ci-local              # format-check, lint, 600-LOC budget, mypy, unit + integration tests
make test                  # unit + integration only
make bench-discoverability # score tool discoverability over the catalog snapshot (offline)
make run                   # serve against the live fleet (exports .env)
```

An offline fake fleet (`make dev-fleet` + `make run-dev`, or one-shot `make test-e2e`) runs the real
router against impersonated backends over real Streamable-HTTP — no Docker, no network.

## Docs

- Design spec — [`docs/specs/2026-06-13-genefoundry-router-design.md`](docs/specs/2026-06-13-genefoundry-router-design.md)
- Agentic-ergonomics spec — [`docs/specs/2026-06-16-router-agentic-ergonomics-design.md`](docs/specs/2026-06-16-router-agentic-ergonomics-design.md)
- Tool-Naming Standard v1 — [`docs/TOOL-NAMING-STANDARD-v1.md`](docs/TOOL-NAMING-STANDARD-v1.md)
- Response-Envelope Standard v1 — [`docs/RESPONSE-ENVELOPE-STANDARD-v1.md`](docs/RESPONSE-ENVELOPE-STANDARD-v1.md)
- Container & Deployment Hardening Standard v1 — [`docs/CONTAINER-HARDENING-STANDARD-v1.md`](docs/CONTAINER-HARDENING-STANDARD-v1.md)
- Contributor guide — [`AGENTS.md`](AGENTS.md)

## License

[MIT](LICENSE) © Bernt Popp
