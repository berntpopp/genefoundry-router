# Fleet README Standard — design spec

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> Status: **approved** 2026-07-14, after an adversarial review pass (Codex GPT-5.5 xhigh)
> that rejected the first draft. Produces [`docs/README-STANDARD-v1.md`](../README-STANDARD-v1.md)
> (the normative rules) and a 22-repo refactor.

## Problem

The `genefoundry-router` and its 21 `-link` backends have grown 22 independent READMEs with
no shared shape. They range from 82 to 513 lines (median 267). Several have quietly become
operator runbooks — deployment procedures, exhaustive environment tables, OAuth setup,
release/rollback discipline — pushed above the fold, so a first-time reader must scroll
past them to learn what the server *is*. Two of them assert facts that are already false.

## Evidence

Twenty-two parallel auditors scored every README 1–10 on ten dimensions (harsh calibration:
10 = best-in-class template; 5 = adequate; most real-world READMEs land 4–6). Four parallel
researchers gathered the community standards the scoring was checked against: the
`standard-readme` specification, GitHub's community-profile checklist, Make a README,
Diátaxis, shields.io conventions and the badge-fatigue critique, the official MCP servers
repo, and JOSS's review checklist.

Line counts and badge counts below are measured from the working tree, not self-reported.

**Legend** — Orient(ation) · Why · Quick(start) · Tools · Scan(nability) · Badge(s) ·
Prov(enance/licence) · Links (out to docs) · Fleet (consistency) · S/N (signal-to-noise).

| Repo | Lines | Orient | Why | Quick | Tools | Scan | Badge | Prov | Links | Fleet | S/N | **Overall** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mondo-link` | 95 | 8 | 6 | 7 | 9 | 9 | 1 | 9 | 9 | 8 | 9 | **7.5** |
| `hgnc-link` | 110 | 8 | 9 | 7 | 9 | 8 | 2 | 8 | 6 | 9 | 8 | **7.4** |
| `mgi-link` | 82 | 8 | 9 | 6 | 8 | 9 | 2 | 7 | 6 | 8 | 9 | **7.2** |
| `mavedb-link` | 161 | 9 | 7 | 8 | 9 | 6 | 6 | 9 | 5 | 8 | 5 | **7.2** |
| `hpo-link` | 156 | 8 | 5 | 8 | 9 | 7 | 2 | 9 | 8 | 8 | 7 | **7.1** |
| `gencc-link` | 299 | 8 | 6 | 9 | 9 | 4 | 2 | 10 | 7 | 9 | 4 | **6.8** |
| `uniprot-link` | 115 | 8 | 6 | 8 | 6 | 9 | 2 | 6 | 6 | 8 | 8 | **6.7** |
| `metadome-link` | 262 | 8 | 9 | 9 | 9 | 4 | 2 | 10 | 3 | 8 | 3 | **6.5** |
| `genefoundry-router` | 314 | 9 | 7 | 8 | 8 | 4 | 4 | 7 | 6 | 7 | 3 | **6.3** |
| `spliceailookup-link` | 181 | 8 | 5 | 7 | 9 | 5 | 1 | 9 | 2 | 8 | 4 | **5.8** |
| `clingen-link` | 254 | 8 | 4 | 5 | 7 | 4 | 2 | 9 | 6 | 9 | 4 | **5.8** |
| `vep-link` | 247 | 8 | 5 | 7 | 9 | 4 | 2 | 6 | 5 | 8 | 4 | **5.8** |
| `clinvar-link` | 348 | 8 | 9 | 7 | 7 | 3 | 1 | 7 | 4 | 8 | 3 | **5.7** |
| `gnomad-link` | 276 | 7 | 4 | 6 | 8 | 4 | 1 | 9 | 6 | 7 | 4 | **5.6** |
| `autopvs1-link` | 240 | 7 | 3 | 7 | 5 | 4 | 5 | 6 | 8 | 7 | 4 | **5.6** |
| `panelapp-link` | 272 | 7 | 4 | 7 | 4 | 4 | 1 | 9 | 7 | 8 | 4 | **5.5** |
| `orphanet-link` | 287 | 8 | 3 | 6 | 9 | 5 | 2 | 8 | 2 | 7 | 5 | **5.5** |
| `pubtator-link` | 513 | 5 | 3 | 6 | 7 | 2 | 1 | 6 | 5 | 5 | 2 | **4.2** |
| `litvar-link` | 492 | 5 | 2 | 5 | 8 | 2 | 1 | 6 | 4 | 3 | 2 | **3.8** |
| `stringdb-link` | 289 | 5 | 2 | 3 | 6 | 3 | 3 | 3 | 3 | 4 | 3 | **3.5** |
| `genereviews-link` | 417 | 6 | 2 | 5 | 4 | 2 | 1 | 2 | 3 | 4 | 2 | **3.1** |
| `gtex-link` | 366 | 5 | 2 | 4 | 6 | 2 | 1 | 3 | 1 | 5 | 2 | **3.1** |
| **Fleet (median lines / mean scores)** | **267** | **7.3** | **5.1** | **6.6** | **7.5** | **4.7** | **2.0** | **7.2** | **5.1** | **7.1** | **4.5** | **5.7** |

### What the numbers say

**Length is the disease, not a symptom.** Overall score correlates inversely with line
count (Spearman ρ = −0.79). The five shortest READMEs average **7.2/10**; the five longest
average **4.0/10**. No README over 300 lines scores above 6.3. Nothing else in the data
predicts quality this strongly — not domain, not age, not tool count.

**The fleet fails on three dimensions and only three:**

- **Badges 2.0/10.** Eighteen of 22 repos carry **zero** badges. The four that do
  (`genefoundry-router` 8, `mavedb` 5, `autopvs1` 4, `stringdb` 3) carry mostly static
  vanity claims, two of which have already rotted: the router's `tests: 126 passing`
  links to a heading rather than a run, and its `Discoverability: 9.8/10` contradicts the
  9.79 printed in its own body.
- **Signal-to-noise 4.5/10.** Roughly half the median README is `docs/` content.
- **Scannability 4.7/10.** A direct consequence of the other two.

**The fleet is already strong where it counts most** — orientation 7.3, tool surface 7.5,
provenance 7.2. The refactor must *preserve* that, not restart from zero.

**The weakest content dimension is `Why` (5.1/10).** Every `-link` server wraps a resource
that already exists on the public web, so a reader's first real question is what the
wrapper adds. Only `mgi`, `hgnc`, `clinvar` and `metadome` answer it. `mgi-link` answers it
best, in one sentence: *"the MGI gene page is a rich JS app with no clean JSON API."*

### Two facts that were already false

These are why the load-bearing rules are machine-checked rather than merely written down.

1. The router advertised **`21 backends, 280 tools`** and `pubtator = 43`. The authoritative
   `fleet-baseline.json` says **272** and `pubtator = 35`. A hand-typed aggregate drifted the
   moment the fleet changed.
2. **`clinvar`, `vep`, `mondo`, `hpo` and `orphanet` declare `license = MIT` in
   `pyproject.toml` but ship no `LICENSE` file.** They assert a grant they never made. That
   is a legal defect, not a documentation one.

## Design

Normative rules: [`docs/README-STANDARD-v1.md`](../README-STANDARD-v1.md). In summary — a
fixed 11-part skeleton identical across all 22 repos; ≤150 lines target, 200 hard ceiling;
exactly four badges; everything else relocated to `docs/`, `AGENTS.md` or `CHANGELOG.md`.

### Why these four badges

The owner asked for "stack, build/CI, tests". Research on badge fatigue and static-vs-
dynamic shields converges on one rule: **a badge whose truth is not maintained by a machine
will eventually lie.** The router's rotted `tests: 126 passing` is the proof.

A literal "tests" badge needs a live source. Codecov is wired into **none** of the 22 repos
(the owner uses it on other projects), so adopting it means installing a third-party app
and token across 22 repos that were *just* supply-chain hardened. Rejected as
disproportionate.

The third badge instead reports **`conformance.yml`** — the existing MCP Transport &
Session contract gate, present and `push`-triggered on all 21 backends. This is strictly
*more* informative than a test badge: `ci.yml` already runs the suite, so a green CI badge
**already attests that tests pass**, while Conformance attests something no generic repo
can claim — that this server still satisfies the fleet's wire contract.

The router has no `conformance.yml`, so its slot 3 is **`security.yml`** (push-triggered;
the router is the fleet's trust boundary, so a security gate is the apt assurance signal
there).

## Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Slot-3 badge = `conformance.yml` (backends) / `security.yml` (router). Not Codecov. | No fleet repo has Codecov; CI already attests tests; conformance is higher-signal, zero-infra and cannot rot. **Owner-approved.** |
| D2 | Add the 5 missing `LICENSE` files; point `Contributing` at `AGENTS.md` | Fixes a real legal defect. `AGENTS.md` exists in all 22 and already *is* the contributor guide, so no new file is invented and every link resolves. **Owner-approved.** |
| D3 | The router uses the identical skeleton, with a normative `## Tools` exception | Owner asked for one layout. But "list every exposed tool" would mean 272 rows; the router instead lists its model-visible tools (`search_tools`, `call_tool`, pinned entrypoints) plus a **generated** backend inventory. |
| D4 | Enforce with `scripts/check_readme.py` in `make ci-local` | Mirrors the existing `check_file_size.py` / `lint-loc` precedent. Python, no new toolchain. |
| D5 | The Tools table is machine-verified by `tests/unit/test_readme_tools.py` | Resolves the Rule 6 / Rule 9 tension (below). Every backend already enumerates its tools via the fixture its `test_tool_names.py` uses; the new test reuses it. |
| D6 | Relocation must create its destination file in the same PR | Most repos lack `docs/configuration.md`, `deployment.md`, `architecture.md`, `data.md`. The checker fails any relative link whose target does not exist. |

### Rejected

- **Codecov fleet-wide** (D1) — disproportionate infra; new third-party supply-chain dependency.
- **Authoring `CONTRIBUTING.md` / `SECURITY.md` / `CITATION.cff` fleet-wide** — real value,
  but roughly doubles the diff and is orthogonal to README shape. Tracked separately.
- **remark-lint + standard-readme preset** — the research's recommendation, but it drags a
  JS toolchain into 22 Python repos for one lint. `check_readme.py` covers the same rules.
- **Badging the router's `drift.yml`** — see below.

## Adversarial review (Codex GPT-5.5 xhigh)

The first draft was reviewed against the live repos and **rejected** ("not safe to roll out
as written"): 3 blockers, 3 majors, 1 minor. All were real; all are fixed above.

| # | Finding | Resolution |
|---|---------|-----------|
| 1 | **BLOCKER** — draft badged the router's `drift.yml` as "Conformance". It is `schedule`/`workflow_dispatch`-only *and* gated behind `vars.DRIFT_ENABLED`, so the badge would report a stale or absent run, mislabelled. | Router slot 3 = `security.yml` (push-triggered). Standard now forbids badging a workflow that does not run on push to the default branch, and forbids a label that misstates what the workflow asserts. |
| 2 | **MAJOR** — "cannot rot" overclaimed for scheduled workflows; `?branch=` guidance needed. | Verified all 21 backends' `ci.yml` + `conformance.yml` are `push`-triggered, and every repo's default branch is `main`. No `?branch=` param needed. Claim narrowed and the push-trigger requirement made explicit. |
| 3 | **BLOCKER** — Rule 6 (list every tool) contradicts Rule 9 (no hand-maintained machine facts): a tool table drifts exactly as `280`/`43` did. | Rule 9 now bans *aggregate/derived* numbers specifically; an enumeration is permitted **because D5 puts a test behind it**. `test_readme_tools.py` asserts the table equals the registered tools. |
| 4 | **BLOCKER** — router `## Tools` underspecified; "every exposed tool" = 272 rows, blowing the 200-line ceiling. | D3: normative router exception written into the standard. |
| 5 | **MAJOR** — relocation targets (`docs/configuration.md` etc.) do not exist in most repos; the standard told implementers to link them. | D6: destinations are created in the same PR; the checker fails links to nonexistent files. |
| 6 | **MAJOR** — spec claimed 15 zero-badge repos; the true figure is 18. | Evidence table regenerated from the working tree. |
| 7 | **MINOR** — stated averages and two line counts were internally inconsistent. | All figures recomputed from `wc -l` and the raw scores; ρ corrected to −0.79. |

Codex independently confirmed the load-bearing claims: all 21 backends do ship
`conformance.yml`; the router ships `drift.yml` and no `conformance.yml`; the five named
repos really do declare MIT without a `LICENSE`; the router README really says `280`/`43`
while the baseline says `272`/`35`.

## Rollout

One PR per repo, 22 total. Each: rewrite `README.md` to the standard, relocate displaced
content into `docs/` (creating the destination files — never delete real information), add
the `LICENSE` file where absent, add `scripts/check_readme.py` +
`tests/unit/test_readme_tools.py`, wire the checker into `make ci-local`, and pass it. The
router additionally lands this standard, this spec, and the inventory generator.

## Boundary

Research use only. Not clinical decision support.
