# genefoundry-router

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/genefoundry-router/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/genefoundry-router/actions/workflows/ci.yml)
[![Security](https://github.com/berntpopp/genefoundry-router/actions/workflows/security.yml/badge.svg)](https://github.com/berntpopp/genefoundry-router/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A thin **FastMCP 3.x aggregator** that federates the GeneFoundry `*-link` MCP fleet behind a
single Streamable-HTTP endpoint. A host adds **one** server — `genefoundry` — and gets every
biomedical backend with collision-free `<namespace>_<tool>` naming and search-based discovery.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

An MCP host that mounted all 21 backends directly would face a wall of several hundred
tools — more than a model can reason over, and a guarantee of name collisions. The router
collapses that into one endpoint and replaces the flat catalog with a **search surface**, so
a model finds the right tool by intent instead of by scrolling.

It is a *client* to each backend and a *server* to hosts: it namespaces and shapes the
surface, but never rewrites a backend's data. The caller's token is never forwarded
upstream.

## Quick start

The fleet is hosted — no install required:

```bash
claude mcp add --transport http genefoundry https://genefoundry.org/mcp
```

Health check: [`genefoundry.org/health`](https://genefoundry.org/health).

To run your own against the live fleet (Python 3.12+, [uv](https://github.com/astral-sh/uv)):

```bash
uv sync --group dev
cp .env.example .env                    # set GF_*_URL backend URLs and GF_AUTH_MODE
uv run genefoundry-router run --host 127.0.0.1 --port 8000
curl -s localhost:8000/health | python -m json.tool
```

An offline fake fleet (`make dev-fleet` + `make run-dev`, or one-shot `make test-e2e`) runs
the real router against impersonated backends over real Streamable-HTTP — no Docker, no
network.

## Tools

The router does **not** surface the federated catalog flat. A model sees three things:

| Tool | Purpose |
|------|---------|
| `search_tools` | Relevance search over the entire federated catalog |
| `call_tool` | Invoke a hit by its `<namespace>_<tool>` name |
| *pinned entry points* | Each backend's front-door tool, always visible — declared per-backend as `entrypoints:` in [`servers.yaml`](servers.yaml) |

```text
search_tools(query="splicing prediction")   # → spliceai_predict_splicing (+ schema)
call_tool(name="spliceai_predict_splicing", arguments={...})
```

Pinning makes each domain's canonical tool reachable deterministically rather than by
relevance luck. See [How discovery works](docs/discovery.md) — including the two traps that
bite MCP clients.

### Federated backends

<!-- BEGIN GENERATED: fleet-inventory -->
**21 backends, 272 tools**, each surfaced namespaced — e.g. `gnomad_search_genes`.

| Namespace | Domain | Data source | Tools | Repo |
|-----------|--------|-------------|------:|------|
| `pubtator` | Literature & entity annotation | [PubTator3](https://www.ncbi.nlm.nih.gov/research/pubtator3/) | 35 | [pubtator-link](https://github.com/berntpopp/pubtator-link) |
| `gnomad` | Variant / gene / population frequency | [gnomAD](https://gnomad.broadinstitute.org/) | 22 | [gnomad-link](https://github.com/berntpopp/gnomad-link) |
| `orphanet` | Rare disease ontology & associations | [Orphadata](https://www.orphadata.com/) | 19 | [orphanet-link](https://github.com/berntpopp/orphanet-link) |
| `clingen` | Gene–disease curation | [ClinGen](https://clinicalgenome.org/) | 17 | [clingen-link](https://github.com/berntpopp/clingen-link) |
| `hpo` | Phenotype ontology & associations | [Human Phenotype Ontology](https://hpo.jax.org/) | 17 | [hpo-link](https://github.com/berntpopp/hpo-link) |
| `mavedb` | Variant-effect assay scores | [MaveDB](https://www.mavedb.org/) | 15 | [mavedb-link](https://github.com/berntpopp/mavedb-link) |
| `uniprot` | Protein function | [UniProt](https://www.uniprot.org/) | 15 | [uniprot-link](https://github.com/berntpopp/uniprot-link) |
| `genereviews` | Gene–disease literature | [GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1116/) | 13 | [genereviews-link](https://github.com/berntpopp/genereviews-link) |
| `mgi` | Mouse phenotype & models | [MGI](https://www.informatics.jax.org/) | 13 | [mgi-link](https://github.com/berntpopp/mgi-link) |
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
<!-- END GENERATED: fleet-inventory -->

## Data & provenance

The router serves **no data of its own**; each backend owns its sources, licences and
citation guidance, and the router mirrors their disclaimers.

What it does own is **integrity of the tool surface**. A backend can serve a clean tool at
review time and later change its definition — the channel for a rug pull. The router
fingerprints every normalized tool definition and diffs the live fleet against a reviewed,
packaged baseline (`genefoundry_router/data/fleet-baseline.json`), enforced at startup and
on a schedule. See [Deployment → drift detection](docs/deployment.md).

## Documentation

- [Configuration & authentication](docs/configuration.md) — every `GF_*` variable, the OAuth/JWT resource-server modes, and the startup guards.
- [Deployment](docs/deployment.md) — container release, digest pinning, rollback, and drift detection.
- [How discovery works](docs/discovery.md) — the search surface, entry-point pinning, and how discoverability is measured.
- [Design spec](docs/specs/2026-06-13-genefoundry-router-design.md) — the architecture and why it is shaped this way.
- Fleet standards — [Tool-Naming](docs/TOOL-NAMING-STANDARD-v1.md) · [Response-Envelope](docs/RESPONSE-ENVELOPE-STANDARD-v1.md) · [MCP-Behaviour](docs/MCP-BEHAVIOUR-STANDARD-v1.md) · [Tool-Surface-Budget](docs/TOOL-SURFACE-BUDGET-STANDARD-v1.md) · [Tool-Schema-Documentation](docs/TOOL-SCHEMA-DOCUMENTATION-STANDARD-v1.md) · [Container-Hardening](docs/CONTAINER-HARDENING-STANDARD-v1.md) · [Versioning](docs/VERSIONING-STANDARD-v1.md) · [README](docs/README-STANDARD-v1.md).

## Contributing

See [`AGENTS.md`](AGENTS.md) for engineering conventions. `make ci-local` is the
definition-of-done gate: format, lint, line budget, README standard, mypy, and tests.

## License

[MIT](LICENSE) © Bernt Popp. Each federated backend carries the licence and citation terms
of its upstream data source; see that backend's repository.
