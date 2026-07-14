# GeneFoundry Repository Metadata Standard v1

> Canonical reference for the `genefoundry-router` aggregator and the 21 `-link` MCP
> servers. Adopted 2026-07-14. Sibling of the [README](README-STANDARD-v1.md),
> [Tool-Naming](TOOL-NAMING-STANDARD-v1.md), [Response-Envelope](RESPONSE-ENVELOPE-STANDARD-v1.md),
> [Container-Hardening](CONTAINER-HARDENING-STANDARD-v1.md) and [Versioning](VERSIONING-STANDARD-v1.md)
> standards. Rationale and the audit that produced it:
> [`specs/2026-07-14-fleet-repo-metadata-design.md`](specs/2026-07-14-fleet-repo-metadata-design.md).

The README is the **conversion** surface — what a visitor reads once they arrive. The
GitHub **About box is the acquisition surface** — the only thing that gets them there.
README Standard v1 perfected the first and left the second empty.

## Why this standard exists

> **"When you omit this qualifier, only the repository name, description, and topics are
> searched."**
> — [Searching for repositories](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories)

**GitHub search does not read your README.** `in:readme` is opt-in and nobody types it. The
name, the description and the topics are the entire default search surface. Google's view
is the same story: GitHub server-renders the description into both the page title and the
meta description, so

- `<title>` = `GitHub - {owner}/{repo}: {description}`
- `<meta name="description">` = `{description}. Contribute to {owner}/{repo}…`

The repo description **is** the blue link and the grey snippet in a Google result.

The 2026-07-14 audit found the fleet shipping **seven empty descriptions** — including
`gnomad-link` and `pubtator-link` — and **sixteen repos with no topics at all**. The
router, the fleet's front door, had zero topics. No repo set its website, though
`genefoundry.org` is live and links *to* the router. The fleet scored **2.7/10** on the
acquisition surface while scoring 8/10 on the README.

One live description already contained a hand-typed count that had drifted (`gencc-link`:
"10 MCP tools"), which is why the rules below are machine-checked rather than merely
written down.

## Verified constraints

Verified against GitHub's REST validator and served HTML on 2026-07-14 — not inferred.
Where the docs are silent, the API is authoritative.

| Constraint | Value |
|---|---|
| Description max length | **350 chars**, hard-rejected by the API (documented nowhere) |
| Topic charset | must **start with a lowercase letter or digit**; lowercase letters, digits, hyphens. **Underscores are rejected, not normalised** |
| Topic max length | **50 chars**, hard |
| Topics per repo | ≤ **20** (GitHub guidance) |
| `topic:` matching | **exact** — the only exact-match lever in GitHub search |
| Google title / snippet truncation | ~60 / ~155 chars |

## Rules

### 1. Every repo has a description. It follows one formula.

```
MCP server for <SOURCE, expanded + acronym>: <3–5 concrete capabilities>. <differentiator>.
```

- **≤ 350 chars** (GitHub's hard ceiling); **target ≤ 220**.
- The literal token **`MCP server`** (or `MCP gateway`, router only) MUST appear **within
  the first 100 characters**, alongside the upstream source's proper noun. Those are the
  two things a human actually searches; Google truncates the title at ~60.
- **Forbidden**, each for a stated reason:
  | Forbidden | Why |
  |---|---|
  | Vanity adjectives — `Production`, `Unified`, `Deterministic`, `thin`, `comprehensive` | They assert nothing and are not search terms. `clinvar-link` opened with "Production"; it bought nothing. |
  | Hand-typed counts — `10 MCP tools` | They drift. `gencc-link`'s already had. README Standard Rule 9. |
  | The research-use disclaimer | It is the README's `[!IMPORTANT]` callout — a safety control, not a keyword. In a 350-char budget it is pure waste. `vep-link` spent ~50 chars on it. |
  | The fleet suffix — "Part of the GeneFoundry `-link` fleet" | 36 chars to say what the `genefoundry` **topic** says for free, and better. |

The router's backend count is the one number that earns its place — *"federating 21
biomedical MCP servers"* is the entire pitch. It is therefore written `{n}` and
**substituted from `servers.yaml`**. Rule 9 does not ban derived numbers; it bans
*hand-typed* ones. A machine owns this one.

### 2. Topics: four tiers, one closed vocabulary

Every repo carries the **universal** set. The rest is per-repo, drawn from a **closed
vocabulary** declared in `fleet-metadata.yaml` — so extending the taxonomy is a deliberate,
reviewed edit. Twenty-two private taxonomies is exactly how the fleet got here.

| Tier | Applies to | Topics |
|---|---|---|
| **Protocol** | all 22 | `mcp` · `mcp-server` · `model-context-protocol` · `claude` · `llm-tools` · `fastmcp` |
| **Domain** | all 22 | `bioinformatics`, plus per-repo terms from the vocabulary |
| **Brand** | all 22 | `genefoundry` |
| **Source** | per repo | the upstream's proper noun — `gnomad`, `clinvar`, `hpo`, … |

Two facts set this design:

- **`mcp-server` — 20,660 repos — appeared zero times fleet-wide.** It is the canonical
  token (`github/github-mcp-server`, `upstash/context7`, `firecrawl` all carry it).
- **Source topics are near-empty ponds**: `topics/gnomad` holds 21 repos, `orphanet` 6,
  `gtex` 49. A geneticist browsing them sees a page you can be one twenty-first of. That is
  worth more than being one of 49,719 under `mcp`. **High-volume topics buy reach; source
  topics buy ownership.** Take both.
- **`genefoundry` is held by no other repo**, so the fleet owns it outright:
  [`github.com/topics/genefoundry`](https://github.com/topics/genefoundry) enumerates all
  22 from one page.

> **Topics are a GitHub-search and Google lever, not an aggregator-crawl trigger.** The
> major MCP directories (Glama, Smithery, awesome-mcp-servers) are *submission*-driven, and
> `modelcontextprotocol/servers` — 88k stars — carries no topics at all. Do not expect
> topics to get you listed anywhere. Expect them to make you *searchable*.

### 3. Website: the fleet hub

All 22 point at **`https://genefoundry.org`**. It is live, it already links out to the
router, and setting the field closes the hub-and-spoke loop in the direction that does not
currently exist. Backend roots serve `404` (only `/health` answers), so a per-backend URL
would be a worse answer than the hub.

### 4. The file is the source of truth; a machine owns the sync

`fleet-metadata.yaml` in the router repo declares the description, topics and website for
all 22 repos. It is deliberately **not** `servers.yaml`: that is the router's *runtime*
registry, parsed at boot, and it does not contain the router itself. Runtime config and
publication surface are different concerns.

**Do not hand-edit metadata in the GitHub UI.** It will be reported as drift and
overwritten. Edit the file.

## Enforcement

Two gates, mirroring the fleet's existing offline/online split:

1. **`scripts/check_fleet_metadata.py`** (`make lint-metadata`, **in `ci-local`**) —
   offline. Validates the *declared* metadata: description length, the required token and
   its position, every forbidden pattern; topic charset, length, budget and closed-vocabulary
   membership; universal tiers present; homepage set; and **coverage parity with
   `servers.yaml`** — every enabled backend has an entry, and there are no orphans. A new
   backend therefore *cannot* ship with an empty About box.
2. **`scripts/sync_fleet_metadata.py`** (`make metadata-check` / `make metadata-apply`) —
   online, and **not** in `ci-local`. `--check` reports drift against live GitHub and exits
   non-zero; `--apply` pushes the file. A check that needs the network belongs on a
   schedule, not in the local loop — the same CI≠prod lesson `fleet-probe` already taught
   this fleet.

`tests/unit/test_fleet_metadata.py` backs both, and includes **negative tests**: the linter
must actually reject an empty description, a vanity adjective, a hand-typed count, the
disclaimer, an uppercase topic, an underscore topic, and an out-of-vocabulary topic. The
first draft of the aggregate-fact rule accepted `"10 MCP tools"` — the exact live defect it
existed to catch. The negative test found it.

## Out of scope for v1

Tracked, not forgotten. None of these block the acquisition surface, and the first is worth
more than everything in this standard combined:

| Follow-up | Note |
|---|---|
| **Publish the router to the official MCP Registry** (`remotes` entry, `mcp-publisher`) | **The highest-leverage action available.** Aggregators scrape the registry hourly; directories are otherwise submission-driven. Note the registry caps `description` at **100 chars** — shorter than this standard's — and that **only the router is publishable**: a `remotes` entry must be publicly reachable, and the backends are unauthenticated-by-design behind the router. |
| `CITATION.cff` × 22 → "Cite this repository" + Zenodo DOI | 0/22 today. The highest-value follow-up for an academic audience. |
| Social-preview cards (1280×640) from the existing logo | Branding, not discoverability — GitHub's auto-card is adequate. |
| PyPI publish; fix the **4 dead `pyproject` homepage URLs** and the `genereview-link` name typo | Real defects, separate concern. |

## Boundary

Research use only. Not clinical decision support. Descriptions state capability, never
clinical utility; the research-use disclaimer remains the README's `[!IMPORTANT]` callout.
