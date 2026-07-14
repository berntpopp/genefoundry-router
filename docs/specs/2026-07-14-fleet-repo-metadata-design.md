# Fleet Repository Metadata & Findability — Design

> Design spec for **GeneFoundry Repository Metadata Standard v1**. Written 2026-07-14,
> the day after [README Standard v1](../README-STANDARD-v1.md) shipped. Sibling of the
> [Tool-Naming](../TOOL-NAMING-STANDARD-v1.md), [Response-Envelope](../RESPONSE-ENVELOPE-STANDARD-v1.md),
> [Container-Hardening](../CONTAINER-HARDENING-STANDARD-v1.md), [Versioning](../VERSIONING-STANDARD-v1.md)
> and [README](../README-STANDARD-v1.md) standards.

## The problem, stated precisely

README Standard v1 made all 22 fleet READMEs excellent. It did not make one of them
findable, because of a fact nobody checked:

> **GitHub search, by default, searches only the repository *name*, *description*, and
> *topics*.** README content is searched only under the opt-in `in:readme` qualifier.
> — [Searching for repositories](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories)

The README is the *conversion* surface — what a visitor reads once they arrive. The
**About box is the entire acquisition surface**: it is the only thing GitHub search matches
on, and it is what Google shows. GitHub server-renders:

- `<title>` = `GitHub - {owner}/{repo}: {description}`
- `<meta name="description">` = `{description}. Contribute to {owner}/{repo}…`

So the repo description **is** the blue link and the grey snippet in a Google result. The
fleet ships **seven empty ones**.

### Audit, 2026-07-14 (22 repos)

| Dimension | State | Score |
|---|---|:--:|
| Description | **7/22 empty**; the other 15 range 75–221 chars with no shared formula, three different fleet suffixes, vanity adjectives (`Production`, `Unified`, `Deterministic`), and one hand-typed count (`gencc`: "10 MCP tools") | 3/10 |
| Topics | **16/22 have zero.** No taxonomy: only `bioinformatics` / `mcp` / `model-context-protocol` recur (7× each). **`mcp-server` — 20,660 repos — appears zero times fleet-wide** | 2/10 |
| Website (About) | **0/22 set**, although `genefoundry.org` is live and links *to* the router | 1/10 |
| Social preview | 0/22 custom (GitHub's auto-card is the fallback); a fleet logo exists but is unused | 2/10 |
| README | Excellent and machine-gated — but the hook sits at line 8, below the badge row | 8/10 |
| Community health | LICENSE 22/22; SECURITY 6/22; CONTRIBUTING / CoC / issue templates 0/22 | 4/10 |
| Citability | **0/22 `CITATION.cff`** → no "Cite this repository" button, no DOI | 1/10 |
| MCP ecosystem | 0/22 `server.json` → absent from the official MCP Registry | 1/10 |
| PyPI | Unpublished; **4 repos' `pyproject` URLs point at non-existent orgs**; `genereviews-link` ships as `genereview-link` | 2/10 |
| Cross-linking | Site → router only; no repo → site; no fleet topic | 3/10 |

**Overall 2.7/10.** The three cheapest fields are the whole acquisition surface.

## Verified constraints

Everything below was verified against GitHub's API validator or its served HTML, not
inferred. Where GitHub's docs are silent, the API is authoritative.

| Constraint | Value | Source |
|---|---|---|
| Description max length | **350 chars**, hard-rejected by the API | REST validator: `description cannot be more than 350 characters` (stated nowhere in the docs) |
| Google title truncation | ~60 chars | Front-load accordingly |
| Google snippet truncation | ~155 chars | Front-load accordingly |
| Topic charset | must **start with a lowercase letter or number**; lowercase letters, digits, hyphens. **Underscores are rejected** | REST validator on `PUT /repos/{o}/{r}/topics` |
| Topic max length | **50 chars**, hard | REST validator |
| Topics per repo | ≤ **20** (documented as guidance; the hard cap is unverified — we stay well under) | [Classifying with topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) |
| GitHub default search fields | name + description + topics **only** | [Searching for repositories](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories) |
| `topic:` matching | **exact**, against a fixed vocabulary — the only exact-match lever in GitHub search | ditto |
| Social preview | PNG/JPG/GIF < 1 MB, 1280×640 (2:1) | [Customizing the social media preview](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview) |
| `CITATION.cff` | exact filename, repo **root** of the **default branch** → "Cite this repository" (APA + BibTeX) | [About CITATION files](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files) |

Topic-volume measurements (GitHub search API, 2026-07-14) — these drive the taxonomy:

| Tier | Topic | Repos | Read |
|---|---|---:|---|
| Protocol | `mcp` | 49,719 | the pool everyone browses |
| Protocol | **`mcp-server`** | **20,660** | **fleet uses it zero times** |
| Protocol | `model-context-protocol` | 17,193 | canonical long form |
| Protocol | `claude` | 37,314 | primary client ecosystem |
| Protocol | `llm-tools` | 3,049 | |
| Protocol | `fastmcp` | 1,490 | the framework |
| Domain | `bioinformatics` | 14,552 | the field's front door |
| Domain | `genomics` | 4,001 | |
| Domain | `rare-disease` | 113 | |
| Domain | `clinical-genetics` | **9** | own the niche outright |
| Source | `uniprot` 152 · `hpo` 87 · `clinvar` 80 · `ensembl` 78 · `vep` 78 · `gtex` 49 · `mondo` 28 · **`gnomad` 21** · `string-db` 9 · `orphanet` 6 | — | near-empty ponds; exact-intent traffic |
| Brand | **`genefoundry`** | **0** | the fleet can own it |

The strategic read: **high-volume topics buy reach; near-empty source topics buy
ownership.** A geneticist who opens `github.com/topics/gnomad` sees 21 repos. Being one of
them is worth more than being one of 49,719 under `mcp`. Do both.

### A hypothesis this design started with, and had to drop

The initial thesis was that topics are the **crawl trigger** — that MCP aggregators index
GitHub by the `mcp` / `mcp-server` topic, making topics the whole ballgame. **That is
false**, and it is worth recording so nobody re-derives it:

- **Glama** is submission-driven (GitHub OAuth, verifies you have write access, then syncs).
- **Smithery** takes a CLI publish of a live endpoint; it is not even GitHub-connected.
- **awesome-mcp-servers** (90k★) is a pull request.
- `modelcontextprotocol/servers` — **88k stars — carries no topics at all** and dominates
  anyway.

The actual hub is the **official MCP Registry** (live, API v0.1), which its own docs say
aggregators are expected to scrape hourly. Publish once there; propagate everywhere.

This does not weaken the case for topics — it *relocates* it. Topics remain the only
exact-match lever in GitHub search, and (with the name and description) the entire
default-indexed surface. They make the fleet **searchable**; they will not get it
**listed**. Both jobs are real; they are different jobs, and this standard does the first.

## Design

### Principle: each signal goes in the channel that carries it cheapest

The current descriptions spend up to 36 of their ~160 useful characters on a fleet suffix
("Part of the GeneFoundry `-link` fleet") — 22% of the budget, on a brand a stranger has
never heard of. That is bad copy *and* bad information architecture.

**The `genefoundry` topic carries fleet membership for free**, and carries it better: it
makes all 22 repos enumerable from one page. So the description spends **none** of its
budget on the fleet, and all of it on the two tokens that actually get searched: the **data
source's proper noun** and **"MCP server"**.

### Rule 1 — the description formula

```
MCP server for <SOURCE, expanded + acronym>: <3–5 concrete capabilities>. <differentiator>.
```

- **≤ 350 chars** (hard, GitHub). **Target ≤ 200.** The first ~100 chars must contain the
  source proper noun *and* the token `MCP server` — Google truncates the title at ~60 and
  the snippet at ~155.
- **Forbidden:** vanity adjectives (`Production`, `Unified`, `Deterministic`, `thin`,
  `powerful`, `comprehensive`); hand-typed counts (`10 MCP tools`) — these drift, exactly
  as README Standard Rule 9 says; the research-use disclaimer (it is the README's
  `[!IMPORTANT]` callout, and it is not a search keyword); the fleet suffix (the topic
  carries it).
- **Required:** the literal token `MCP server` (or `MCP gateway`, router only), and the
  upstream source's name as a searchable noun.

The router's description is the one place a count earns its keep — *"federating 21
biomedical MCP servers"* is the whole pitch. It is therefore **generated** from
`servers.yaml`, never typed, honouring README Standard Rule 9's actual requirement
(derived numbers must be machine-owned, not banned).

### Rule 2 — the topic taxonomy

Four tiers. Every repo carries the universal set; the rest is per-repo. Budget ≤ 20.

| Tier | Applies to | Topics |
|---|---|---|
| **A. Protocol** | all 22, identical | `mcp`, `mcp-server`, `model-context-protocol`, `fastmcp`, `llm-tools`, `claude` |
| **B. Brand** | all 22, identical | `genefoundry` |
| **C. Domain** | all 22 carry `bioinformatics`; the rest drawn from a controlled vocabulary | `genomics`, `genetics`, `clinical-genetics`, `rare-disease`, `variant-interpretation`, `proteomics`, `gene-expression`, `ontology`, `literature-mining`, `computational-biology`, `precision-medicine` |
| **D. Source** | per repo | the upstream's proper noun(s): `gnomad`, `clinvar`, `gtex`, `hgnc`, `mgi`, `uniprot`, `clingen`, `gencc`, `litvar`, `string-db`, `autopvs1`, `spliceai`, `pangolin`, `genereviews`, `pubtator`, `ensembl`, `vep`, `panelapp`, `mondo`, `mavedb`, `hpo`, `human-phenotype-ontology`, `metadome`, `orphanet` |

Tier C is a **closed vocabulary**: a new domain topic must be added to the standard, so the
fleet cannot drift into 22 private taxonomies again (which is precisely how it got here).

### Rule 3 — the website field

All 22 point at **`https://genefoundry.org`** — the live fleet hub ("Every biomedical MCP,
one endpoint"), which already links out to the router. Setting the field closes the
hub-and-spoke loop in the direction that currently does not exist. Backend roots serve
`404` (only `/health` responds), so a per-backend URL would be a worse answer than the hub.

### Rule 4 — the source of truth is a file, and a machine owns the sync

This is the fleet's settled pattern (`servers.yaml` → `gen_readme_inventory.py` →
`check_readme.py`) and the reason README v1 has held for a day without rotting:

> A written convention with no gate decays — this fleet has watched it happen.
> — README Standard v1, *Enforcement*

**`fleet-metadata.yaml`** (router repo) is the single source of truth for all 22 repos'
description, topics, and homepage. It is deliberately **not** `servers.yaml`: that file is
the router's *runtime* registry, parsed at boot, and it does not (and should not) contain
the router itself. Runtime config and publication surface are different concerns.

Two gates, mirroring the fleet's existing offline/online split:

1. **`scripts/check_fleet_metadata.py`** — **offline**, in `make ci-local`. Validates the
   *declared* metadata against this standard: description length, required tokens,
   forbidden patterns; topic charset/length against GitHub's real validator rules; topics
   drawn from the closed vocabulary; universal tiers present; homepage set; and **coverage
   parity with `servers.yaml`** (every enabled backend has an entry; no orphans).
2. **`scripts/sync_fleet_metadata.py`** — **online**, *not* in `ci-local`. `--check`
   reports drift between the file and live GitHub (exit 1); `--apply` pushes it. This is
   the same CI≠prod lesson the fleet already learned with `fleet-probe`: a check that needs
   the network belongs on a schedule, not in the local loop.

Crucially, **the 21 backends need no commits.** The About box is pure API metadata. One
router PR plus one API sweep fixes the entire acquisition surface — which is why this
phase ships first.

## Scope

**In scope (v1, ships now):** description, topics, website — for all 22 repos — plus the
source-of-truth file, both gates, tests, and the live sweep.

**Out of scope (v1), tracked as follow-ups** — each is real, none blocks the acquisition
surface:

| Follow-up | Why deferred |
|---|---|
| **Publish the router to the official MCP Registry** — a `remotes` entry via `mcp-publisher`, GitHub-OIDC auth from CI | **Worth more than this entire standard.** Deferred only because it is a distinct workstream, not because it is lower value. Two constraints found in research: the registry caps `description` at **100 chars** (shorter than this standard's 350, so it needs its own copy), and **only the router is publishable** — a `remotes` entry must be publicly reachable, and the 21 backends are unauthenticated-by-design behind the router (AGENTS.md). Listing them would require `packages` (PyPI/OCI) entries instead. That is a deliberate decision, not a detail. |
| `CITATION.cff` × 22 → "Cite this repository" + Zenodo DOI | Needs 22 repo commits + a DOI decision (Zenodo mints on *release*, not on push). Highest-value follow-up for an academic audience. |
| Social-preview cards (1280×640) from the existing logo | Branding, not discoverability — GitHub's auto-card is adequate. |
| PyPI publish + fix the **4 dead `pyproject` homepage URLs** and the `genereview-link` name typo | Real defects, but a separate packaging concern. |
| Community-health files (CONTRIBUTING, CoC, issue templates) | `AGENTS.md` already serves contributors; low acquisition value. |
| Tool annotations (`title`, `readOnlyHint`) fleet-wide | Prerequisite for the Anthropic Connectors Directory (which additionally needs a Team/Enterprise plan). Improves every client's UX regardless. |

## Success criteria

1. `fleet-metadata.yaml` covers exactly the 21 enabled backends + the router.
2. `make lint-metadata` passes; `make ci-local` stays green.
3. Every repo's live description is non-empty, ≤ 350 chars, and contains its source's
   proper noun and `MCP server`/`MCP gateway`.
4. Every repo carries the universal topic tiers, including `mcp-server` and `genefoundry`.
5. Every repo's website is `https://genefoundry.org`.
6. `make metadata-check` reports **zero drift** against live GitHub.
7. `github.com/topics/genefoundry` enumerates all 22 repos.

## Boundary

Research use only. Not clinical decision support. Descriptions state capability, never
clinical utility; the research-use disclaimer remains the README's `[!IMPORTANT]` callout.
