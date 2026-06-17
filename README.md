# GeneFoundry Router

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP 3.x](https://img.shields.io/badge/FastMCP-3.x-6E40C9)](https://github.com/jlowin/fastmcp)
[![Packaged with uv](https://img.shields.io/badge/packaged%20with-uv-DE5FE9?logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![Lint: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Typed: mypy strict](https://img.shields.io/badge/typed-mypy%20strict-2A6DB2)](https://mypy-lang.org/)
[![Tests: 106 passing](https://img.shields.io/badge/tests-106%20passing-brightgreen)](#develop--test)
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
**17 backends, 218 tools**, each surfaced namespaced — e.g. `gnomad_search_genes`.

| Namespace | Domain | Data source | Tools | Repo |
|-----------|--------|-------------|------:|------|
| `pubtator` | Literature & entity annotation | [PubTator3](https://www.ncbi.nlm.nih.gov/research/pubtator3/) | 43 | [pubtator-link](https://github.com/berntpopp/pubtator-link) |
| `gnomad` | Variant / gene / population frequency | [gnomAD](https://gnomad.broadinstitute.org/) | 22 | [gnomad-link](https://github.com/berntpopp/gnomad-link) |
| `clingen` | Gene–disease curation | [ClinGen](https://clinicalgenome.org/) | 17 | [clingen-link](https://github.com/berntpopp/clingen-link) |
| `uniprot` | Protein function | [UniProt](https://www.uniprot.org/) | 15 | [uniprot-link](https://github.com/berntpopp/uniprot-link) |
| `mgi` | Mouse phenotype & models | [MGI](https://www.informatics.jax.org/) | 13 | [mgi-link](https://github.com/berntpopp/mgi-link) |
| `genereviews` | Gene–disease literature | [GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1116/) | 13 | [genereviews-link](https://github.com/berntpopp/genereviews-link) |
| `mondo` | Disease ontology / cross-references | [Mondo](https://mondo.monarchinitiative.org/) | 13 | [mondo-link](https://github.com/berntpopp/mondo-link) |
| `gencc` | Gene–disease curation | [GenCC](https://thegencc.org/) | 12 | [gencc-link](https://github.com/berntpopp/gencc-link) |
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

- **`search_tools`** — BM25 relevance search over the *entire* federated catalog.
- **`call_tool`** — invoke a hit by its `<namespace>_<tool>` name.
- two pinned gnomAD resolvers (`gnomad_resolve_variant_id`, `gnomad_search_genes`) for the
  common first step.

**Everything else is reached via `search_tools` → `call_tool`** (and is also directly callable by
full name once known). A typical flow:

```text
search_tools(query="splicing prediction")        # → hit: name="spliceai_predict_splicing", inputSchema, returns
call_tool(name="spliceai_predict_splicing", arguments={...})
```

The model is oriented on this two-layer model via the MCP **`instructions`** field (set on the
server) plus the `search_tools`/`call_tool` descriptions. Federated names are also valid for Gemini
Remote MCP (snake_case, `[a-z0-9_]`, ≤64 chars).

> **Two traps to avoid** (see [#3](https://github.com/berntpopp/genefoundry-router/issues/3)).
> A capability missing from your **host/client-side tool list is not missing** — the host only
> sees the three entry points above; call `search_tools` before concluding a tool doesn't exist.
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
| `GF_ALLOWED_ORIGINS` | _(empty)_ | CSV `Origin` allowlist (DNS-rebinding defense) |
| `GF_<NAME>_URL` | _(unset)_ | Per-backend `/mcp` URL (e.g. `GF_GNOMAD_URL`) |

A backend with an unset URL or `enabled: false` is skipped with a warning; the router still starts.
**No token passthrough:** the caller is authenticated at the edge and their token is never
forwarded to a backend (confused-deputy defense).

## CLI

```bash
genefoundry-router run        --host 0.0.0.0 --port 8000   # serve over Streamable HTTP
genefoundry-router validate                                # check servers.yaml + env, report missing URLs
genefoundry-router list-tools [--namespace gnomad]         # enumerate federated tools, flag >64-char names
genefoundry-router doctor     [--strict-naming]            # ping backends; audit leaf names vs Tool-Naming v1
```

## Develop & test

```bash
make ci-local   # format-check, lint, 600-LOC budget, mypy, unit + integration tests
make test       # unit + integration only
make run        # serve against the live fleet (exports .env)
```

An offline fake fleet (`make dev-fleet` + `make run-dev`, or one-shot `make test-e2e`) runs the real
router against impersonated backends over real Streamable-HTTP — no Docker, no network.

## Docs

- Design spec — [`docs/specs/2026-06-13-genefoundry-router-design.md`](docs/specs/2026-06-13-genefoundry-router-design.md)
- Agentic-ergonomics spec — [`docs/specs/2026-06-16-router-agentic-ergonomics-design.md`](docs/specs/2026-06-16-router-agentic-ergonomics-design.md)
- Tool-Naming Standard v1 — [`docs/TOOL-NAMING-STANDARD-v1.md`](docs/TOOL-NAMING-STANDARD-v1.md)
- Contributor guide — [`AGENTS.md`](AGENTS.md)

## License

[MIT](LICENSE) © Bernt Popp
