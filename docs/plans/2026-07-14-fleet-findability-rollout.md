# Fleet Findability — Rollout Plan

> Companion to [Repository Metadata Standard v1](../REPO-METADATA-STANDARD-v1.md) and its
> [design spec](../specs/2026-07-14-fleet-repo-metadata-design.md). Written 2026-07-14.

## Phase 1 — the About box — **DONE 2026-07-14**

`fleet-metadata.yaml` + `make lint-metadata` (in `ci-local`) + `make metadata-check/apply`,
applied live to all 22 repos. Verified: 0 empty descriptions (was 7), 0 zero-topic repos
(was 16), 0 repos without a website (was 22), `topic:genefoundry` enumerates **22/22**.

Required **no commits to the 21 backends** — the About box is pure API metadata. That is
why it went first.

## What Phase 1 cannot fix, and why

Measured after the sweep, searching as a stranger would:

| Query | Result |
|---|---|
| `topic:gnomad topic:mcp-server` | **`gnomad-link` ranks 1st** (of 2) |
| `gnomad mcp server` | `gnomad-link` **#6**; `genefoundry-router` #7; `metadome-link` #8 |
| `clinvar mcp server` | fleet absent from the top 5 |

The winners are named **`gnomad-mcp-server`**, **`clinvar-mcp-server`**. GitHub's "best
match" weights the **repository name** heavily, and the name is the one field this standard
cannot touch: ours are `<source>-link`.

**Do not rename the repos to chase this.** The `-link` slug is load-bearing across
deployments, the router's `servers.yaml`, container tags, PyPI names, every doc link and
every existing URL. The cost dwarfs the benefit, and the benefit is capped at a few
positions on one query.

The conclusion is not "try harder at GitHub SEO". It is that **on-GitHub search is a
second-order channel for an MCP server**, and the fleet should win where MCP users actually
look. That is Phase 2.

## Phase 2 — the MCP Registry (highest leverage remaining)

Worth more than all of Phase 1. The official registry (live, API v0.1) is the hub that
directories scrape — its own docs tell aggregators to poll `GET /v0.1/servers` hourly.
Glama, Smithery and awesome-mcp-servers are otherwise **submission-driven**; none of them
crawl GitHub by topic. Publish once, propagate everywhere.

Two constraints found in research, both load-bearing:

1. **Only the router is publishable as a `remotes` entry.** The registry requires a remote
   server to be *publicly reachable*, and the 21 backends are unauthenticated-by-design and
   reachable only behind the router (AGENTS.md, and the security posture the fleet spent
   two sweeps establishing). Listing backends individually would mean `packages` entries —
   i.e. actually publishing to PyPI/OCI first. **Publishing the backends as reachable
   remotes would be a security regression. Do not.**
2. **The registry caps `description` at 100 chars** — less than half this standard's budget.
   It needs its own, shorter copy. Do not reuse the About-box text verbatim.

Steps:

1. Decide the namespace — `io.github.berntpopp/genefoundry` (GitHub OIDC auth) vs a DNS
   namespace on `genefoundry.org` (Ed25519 TXT at the apex). **Decide once**; renaming later
   fragments identity across every downstream directory.
2. Write the ≤100-char registry description for the router.
3. `mcp-publisher init` → `login github-oidc` → `publish`.
4. Automate republish from CI on `v*` tags (`permissions: id-token: write`; no secrets).
5. Extend `fleet-metadata.yaml` with the registry fields so `server.json` is **generated**,
   not hand-maintained — the same pattern as everything else here.

Then, cheap and additive: Smithery (`smithery mcp publish <url>`), Glama (GitHub OAuth
claim), and a PR to `punkpeye/awesome-mcp-servers` (90k★) adding a bioinformatics cluster.

## Phase 3 — academic citability

**0/22 repos ship `CITATION.cff`** — so no repo has GitHub's "Cite this repository" button.
For a fleet whose users are geneticists and whose output is meant to be cited in papers,
this is the largest remaining gap *relative to audience*.

- `CITATION.cff` in each repo root on the default branch → APA + BibTeX, rendered by GitHub.
- Zenodo: enable per repo, then **cut a release** — the DOI is release-triggered, which is
  the standard failure mode. Every fleet repo already cuts releases.
- Feed the DOI back into `CITATION.cff` and the README's `## Data & provenance`.

Generate the CFF files from `fleet-metadata.yaml` + `pyproject.toml`, so authorship and
version stay machine-owned.

## Phase 4 — packaging defects (real bugs, found in the audit)

Not findability polish — these are wrong today:

- **4 repos' `pyproject` URLs point at non-existent GitHub orgs**: `gtex-link/gtex-link`,
  `litvar-link/litvar-link`, `stringdb-link/stringdb-link`, and `ai-assistant/pubtator-link`.
  Dead links on any future PyPI page.
- **`genereviews-link` publishes as `genereview-link`** (singular) — mismatches the repo
  slug, the router namespace, and any future PyPI URL.
- 7 repos carry no `keywords` at all.
- Nothing is on PyPI; `genereviews-link`'s release workflow targets **TestPyPI** only.

## Explicitly not doing

| | Why |
|---|---|
| Renaming repos to `<source>-mcp-server` | Buys a few ranks on one query; breaks deployments, registry, containers, PyPI and every URL. |
| Custom social-preview cards | GitHub's auto-generated card already renders name + description + stats. Branding, not discoverability. |
| Anthropic Connectors Directory | Requires a Team/Enterprise plan, OAuth, a privacy policy and a reviewer test account. Park it. |
| Publishing backends as public MCP remotes | A security regression. See Phase 2. |
