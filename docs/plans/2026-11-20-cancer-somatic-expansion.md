# Cancer / Somatic Expansion Plan — GeneFoundry fleet (2026-11-20)

> Status: **PLAN / research-complete, not started.** Nothing here is built or registered.
> **Owner decisions (2026-11-20):** drug/driver layer = Open Targets only; MOAlmanac = own
> `moalmanac-link`; first wave = Phase 0 + `civic-link` + `cbioportal-link` (with hotspots).
> Evidence: five parallel assessments with live API probes (2026-11-20) in
> [`2026-11-20-cancer-somatic-expansion/`](2026-11-20-cancer-somatic-expansion/) (A–E + the shared
> rubric). Literature anchors via the fleet's own `pubtator_search_literature`.
> Not legal advice: every "license OK" below is a reading of published terms; get written
> confirmation where flagged.

## 1. Why, and the baseline

The fleet (26 backends) is **germline-only**. `grep -ril somatic ../*-link` finds no cancer code.
Today an agent asked "is BRAF V600E actionable in melanoma?" gets ClinVar's *germline*
aggregate ("Conflicting classifications of pathogenicity"), which is actively misleading for
oncology.

The somatic question space has four layers. A complete fleet answers each from a
license-clean source:

| Layer | Question | Chosen source |
|---|---|---|
| **Oncogenicity** | Is this variant oncogenic? (ClinGen/CGC/VICC, Horak 2022, PMID 35101336) | ClinVar somatic + CIViC + hotspots + existing predictors |
| **Actionability** | Which therapy, evidence level, tumour type? (AMP/ASCO/CAP 2017 tiers) | CIViC (CC0), ClinVar somatic clinical-impact, MOAlmanac |
| **Prevalence** | How often is gene/variant X altered in cancer type Z? | cBioPortal (+ Cancer Hotspots), later GDC |
| **Biology / drugs / trials** | Is this gene a driver? Druggable? Open trials? | Open Targets (CC0), ClinicalTrials.gov |
| **Normalization** | Cancer-type IDs that join everything | OncoTree via `mondo-link` xrefs |

## 2. Verdict on every resource assessed

### The six resources the user listed

| Resource | Verdict | Prio | Why (decisive fact) |
|---|---|---|---|
| **CIViC** | **BUILD `civic-link`** | **P0** | CC0 data, MIT code; live GraphQL, no auth, ~0.6 s; BRAF V600E → MP 12, 97 accepted EIDs, 11 assertions (AID10 Tier I Level A). Carries CAID/ClinVar/MANE HGVS → joins hgnc/clinvar/vep/gnomad/pubtator. |
| **cBioPortal** | **BUILD `cbioportal-link`** | **P1** | Only clean source for prevalence-by-cancer-type with correct profiled denominators; 553 studies/415k samples; no auth. Data default ODbL + per-study terms (9 studies restricted, e.g. `msk_chord_2024` CC BY-NC-ND) → must surface/flag per-study license. **Scripted use is sanctioned**: `llms.txt` says use `api.cbioportal.org`, filtered, sequential, cached; 429 on bulk concurrency. |
| **TCGA / NCI GDC** | TCGA: **SKIP** as own link (already in GDC + cBioPortal PanCan Atlas). GDC: **BUILD `gdc-link`** | P2 | Cleanest license ("Somatic MAFs … can be freely distributed"), DR46.0, uniform GRCh38, pediatric/CPTAC. Second source after cBioPortal. Controlled (dbGaP) tier never proxied. |
| **ICGC** | **SKIP** | P3 | dcc.icgc.org retired June 2024 (301 → "retired"); ARGO API indexes files only; open data = frozen Release 28 bucket. Nothing queryable. PCAWG is in cBioPortal. |
| **OncoKB** | **PARK (license)** | — | Terms forbid sharing Content with third parties. Since May 2026, API use needs a paid license, and AI training is prohibited. The tokenless public server answers BRAF V600E/melanoma with "no FDA-approved treatments", which is **false** (Level 1). Never proxy it. |
| **HSMD (QIAGEN)** | **SKIP** | — | Commercial 10-seat subscription, no API, no academic tier. |

**COSMIC itself (the baseline the user wants alternatives to):** PARK (hard). Login-walled, no API.
The 2025-10-30 terms bar access "on any free-to-access platform" (C(h)), cover even COSV/COSM IDs,
and ban AI training. **Fleet hygiene action:** make sure `vep-link` never emits COSV/COSM
co-located IDs.

### Resources a domain expert adds (not in the user's list)

| Resource | Verdict | Prio | Why |
|---|---|---|---|
| **ClinVar somatic** (clinical-impact tier + oncogenicity, since 2024) | **EXTEND `clinvar-link`** | **P0** | Already downloaded: `variant_summary.txt.gz` cols 35–43 are dropped at ingest (`clinvar_link/ingest/parsing.py` ~L539–600). ~1,307 tiered + ~1,883 oncogenicity calls (GRCh38). Public domain. Cheapest win in the plan. |
| **OncoTree** | **EXTEND `mondo-link`** | **P0** | CC BY 4.0, 897 nodes. MONDO already holds 558 `ONCOTREE:` xrefs, but `ONCOTREE` is missing from the prefix allowlist (`mondo_link/constants.py:32`). One line + test. |
| **Cancer Hotspots** (+3D via Genome Nexus) | **Fold into `cbioportal-link`** (`get_mutation_hotspots`, snapshot) | P1 | Frozen v2 (~2018), 1,165 hotspots; BRAF V600 n=897. **Data ODbL 1.0** (cancerhotspots.org, commit `cBioPortal/cancerhotspots@b257eeaf`; code AGPL, don't copy it). Source: Genome Nexus importer `hotspots_v2_v3_and_3d.txt` (GRCh37+38). Ship derived table under ODbL + attribution. |
| **Open Targets Platform** | **BUILD `opentargets-link`** | P1 | CC0 GraphQL; tractability + known drugs (ChEMBL) + somatic evidence (IntOGen, cancer biomarkers) in one API; joins Ensembl/EFO/MONDO; also serves germline. **Filter out the `cancer_gene_census` datasource (COSMIC-derived).** |
| **IntOGen** | **SKIP standalone**: Open Targets carries 436/471 rows (92%) with cohort, q-value, methods and Act/LoF role identical | — | CC0; 633 drivers × 86 tumour types; no API, a 965 KB zip; version from the filename, not the README. |
| **DGIdb 5** | **SKIP** | — | 68% of BRAF interactions come only from restricted sources (CKB alone 181/255). After the allowlist, and minus OT/civic/clinpgx, only 4–10 mostly noisy drugs per gene remain. |
| **MOAlmanac 2.0** | **BUILD `moalmanac-link`** (snapshot of a tagged release) | P2 | **CC BY 4.0** (since 2.0, 2026-10-31; not ODbL). Regulatory approvals only: FDA/EMA/Health Canada/HSE/HPRA, 2,000 statements, label version + date. Fills CIViC holes (KRAS G12C NSCLC: CIViC 0 assertions). Live API unsafe (no paging, 1–28 MB, ignores unknown filters). |
| **ClinicalTrials.gov v2** | **BUILD `trials-link`** | P2 | Public domain, live, daily refresh; biomarker search is free text (noisy). Also serves rare-disease trials. |
| **DepMap** | BUILD snapshot `depmap-link` | P2 | CC BY 4.0 (Broad's own data); portal API is behind Cloudflare Turnstile → figshare release files. Avoid the Sanger API (non-commercial). |
| **CGI** | Tool/API **PARK** (personal tokens, non-transferable, non-commercial). CC0 biomarker TSV (2022-10-17, 1,169 rows): optional fold-in | P3 | Frozen and mostly covered by CIViC + MOAlmanac. |
| **VICC MetaKB** | **PARK** | P3 | v1 retired (504s); v2 is alpha (2.0.0-a2), only `pediatric.metakb.org`. Revisit at v2 GA. The **variation normalizer** (VRS IDs, live) is a possible optional join key. |
| **AACR GENIE** | **PARK (final)** | — | Current ToU form: "will not redistribute the data, in whole or in part, without express written permission". No aggregate carve-out. Adds 28 CFR 202 per-user attestations that a public gateway cannot make. Deep links only. |
| **JAX CKB** | **PARK (hard)** | — | Now Genomenon; EULA bans LLM use and externally facing re-use. |
| **PMKB** | **PARK (unreachable)** | P3 | Host timed out; license reportedly CC BY 4.0 (secondary source). |
| **CancerVar** | **PARK** | — | Non-commercial only, needs an ANNOVAR license, hg19 only. |
| **NCG7** | **PARK** | — | Site down, no data license found. |
| **Mutational signatures** | **SKIP** | — | They need a whole-sample catalogue, not a gene/variant lookup; the COSMIC reference set is restricted. At most a skill note → SigProfiler (BSD). |
| **BioMCP / cbioportal-mcp / dgidb-mcp** | **Don't federate** | — | BioMCP (MIT) breaks naming/budget and would expose an operator's OncoKB token to anonymous users. The cBioPortal MCP needs per-user Google login or DB creds. Use them as **coverage checklists**; use the `cbioportal-mcp-qa` benchmark as an eval set. |

## 3. Phased roadmap

Each phase is independently shippable. Order = value ÷ effort, with license risk weighed first.

### Phase 0: extensions to existing repos (days, no new repo)

1. **`clinvar-link` somatic** (S–M). Ingest TSV cols 35–43 (somatic clinical-impact, its
   review status/date, oncogenicity, its review status/date). Pull per-submission tumour type +
   therapy from the VCV XML in the existing `ingest/submissions.py` stream (E-utilities gotcha:
   `efetch rettype=vcv` needs `&is_variationid&from_esearch=true`). Surface:
   - add `somatic_clinical_impact` + `oncogenicity` blocks to `get_variant` (keep them
     **separate** from the germline significance, never merged);
   - +1 tool `search_somatic_classifications` (gene / tier / oncogenicity / tumour type).
   - Acceptance: BRAF V600E (VCV 13961) returns Tier I + Oncogenic alongside the germline
     "Conflicting"; budget test still < 10k tokens.
2. **`mondo-link` OncoTree** (S). Add `ONCOTREE` to the prefix allowlist; a test that
   `ONCOTREE:SKCM → MONDO:0005012` resolves and that prefix filtering works. Optional later: an
   OncoTree snapshot + `resolve_oncotree_code` for the ~38% of codes without a MONDO mapping.
3. **`vep-link` hygiene** (S). Assert no COSV/COSM IDs in co-located-variant output (guard test).

### Phase 1: the two core new backends

4. **`civic-link`** (M, P0). Live GraphQL, `data_version` = latest monthly release + `live:<ts>`
   (genereviews precedent). Outbound allowlist: `civicdb.org` only. **One fixed persisted query
   per tool; never accept caller GraphQL.** Tools (≈8, ~6–8k tokens):
   `search_molecular_profiles` (entry resolver: gene+variant / HGVS / CAID / ClinVar → MP IDs,
   `simple` vs `complex` split), `get_molecular_profile`, `get_variant`, `search_evidence_items`
   (default status `ACCEPTED`), `get_evidence_item`, `search_assertions`, `get_assertion`, `get_gene`.
   Must handle:
   - GraphQL errors arrive as HTTP 200 + `errors[]` → map them to an envelope error naming the parameter.
   - **Silent 100-item page cap** → honest `total` / `truncated` (behaviour-probe hazard).
   - Name search for "BRAF V600E" also matches 40 complex MPs → label them, and never fold them
     into the simple-variant answer.
   - GRCh37-first coordinates → emit GRCh38 HGVS where CIViC provides it, and label the build.
   - Diseases are DOID → return the DOID, and crosswalk to MONDO via `mondo-link` xrefs (router skill).
   - Keep oncogenicity (Horak) and actionability (AMP tier) as distinct fields.
5. **`cbioportal-link`** (M, P1). Live proxy. Outbound: **`api.cbioportal.org`** (the scripted-access host per cBioPortal's `llms.txt`, 2026-10-08). Tools (≈7):
   `search_studies`, `get_study`, `get_gene_alteration_frequency`, `get_gene_mutations`,
   `get_mutation_position_counts`, `list_cancer_types`, `get_mutation_hotspots` (Cancer Hotspots
   snapshot). Must handle:
   - Server-side page cap (upstream allows 10M); filter `mutated-genes/fetch` (4.7 MB unfiltered).
   - Never expose `molecular-data` / `segments` / patient-level clinical rows.
   - **Per-study license + citation in every row, restricted studies flagged** (NC/ND studies
     excluded by default).
   - GRCh37 coordinates labelled.
   - No upstream data version → stamp `/api/info` + study `importDate`.
   - Policy (`llms.txt`): filtered requests only, **sequential** (concurrency 1), cache keyed on
     `/api/info` `dbVersion`, back off on 429, never loop the API for bulk data. The 2026-11-14
     Cloudflare challenge covers Swagger HTML only (`cbioportal-go@bb36f33`); the JSON API is unaffected.
   - The official cBioPortal MCP (`mcp.cbioportal.org`) needs per-user OAuth → can't be federated; our
     link's value = fleet joins + standards. Use their `cbioportal-mcp-qa` set in the agent UX eval.
   - `alteration-counts/fetch` is 404 mid-Go-rewrite → integration test against live, and a drift alarm.

### Phase 2: breadth

6. **`opentargets-link`** (M, P1). CC0 GraphQL, release 26.09, ~6 tools (~5k tokens):
   `resolve_target`, `get_target_tractability`, `get_drug_candidates` (upstream
   `drugAndClinicalCandidates`; `knownDrugs` is gone; nested trial reports are omitted by default, 220 KB for BRAF),
   `search_disease_targets`, `get_target_disease_evidence` (two-step: associations → `efoIds`).
   **Datasource allowlist excludes `cancer_gene_census` and COSMIC hallmarks** (COSMIC's grant
   covers "Open Targets users" only) **and `europepmc`**; `cancer_biomarkers` (CGI) is held until its
   CC BY vs BY-NC conflict is resolved. Guard test on the allowlist; schema contract test per release;
   ~3 req/s throttle. Germline bonus: GWAS credible sets + gene-burden evidence. Don't federate
   `opentargets/platform-mcp` (raw GraphQL passthrough, over budget).
7. **`trials-link`** (S–M, P2). ClinicalTrials.gov v2 live: `search_trials`, `get_trial`,
   `get_trial_eligibility` (fenced untrusted text). Unknown NCT → HTTP 400 → `not_found`;
   token paging → honest `truncated`.
8. **`gdc-link`** (M, P2). Tools: `list_projects`, `get_gene_mutation_frequency`,
   `get_variant_mutation_frequency`, `search_somatic_mutations`, `get_top_mutated_genes`,
   `get_gene_cnv_frequency`. Must handle:
   - **A misspelled filter field returns 200/total 0** → validate against `/_mapping`
     (the fleet's "no silently-empty filter" rule).
   - GDC canonical transcript gives BRAF **V640E**, not V600E → join on genomic coordinate / MANE.
   - Facet buckets are capped → count cases by paging.
   - A `genes.symbol` case filter includes CNV hits → wrong numerator.
9. **`depmap-link`** (M, P2). Snapshot from figshare; precomputed per-gene dependency summaries.
10. **`moalmanac-link`** (S, P2, 2–3 days). Snapshot of the tagged data release (`referenced/`, ~7.5 MB,
    `data_version = v.YYYY-MM-DD`). ~3 tools: `search_approvals` (ERBB2↔HER2 alias expansion, strict
    params), `get_indication`, optional `list_biomarkers`. Schema guard (2.0 was a full rebuild); bus
    factor 1 upstream. Known upstream defects (pemigatinib↔elacestrant links, missing sotorasib+panitumumab
    CRC, HER2 vs ERBB2 split) → handle in tests and file GitHub issues upstream.

### Router layer (parallel to Phase 1–2)

11. `servers.yaml` entries `enabled: false` per new backend (skill `backend-registry-change`), flipped after
    deploy, then regenerate snapshot-catalog / expansions / baseline / provenance / readme-inventory
    + control-ledger rows (as router PRs #282/#289). Pin entry points: `civic_search_molecular_profiles`,
    `cbioportal_get_gene_alteration_frequency`.
12. **Research skill `skills/somatic-variant-interpretation/`**:
    - Normalize: vep MANE HGVS → CAID / gnomAD ID.
    - Oncogenicity evidence: Horak 2022 criteria assembled from clinvar-somatic + civic + cbioportal
      hotspots + gnomad (population frequency) + autopvs1/vep (null variants) + mavedb (functional) +
      spliceai/metadome (predictors).
    - Actionability: civic assertions/EIDs + clinvar somatic tier, then trials.
    - Always state source, denominator, cohort and genome build for any prevalence number.
    - Never present the output as a clinical tier; research use only.
13. BM25 doc-expansion entries for cancer vocabulary (actionable, oncogenic, hotspot, tier, OncoTree
    codes) so `search_tools` routes somatic intents (skill `tool-search-change`).

### Access questions: resolved from public evidence (no outreach needed)

| Question | Answer | Evidence |
|---|---|---|
| May a gateway script the cBioPortal API? | **Yes, with conditions** | `cbioportal.org/llms.txt` (`cbioportal-go@708f329f`); team declined app-level rate limits (`cbioportal#11980`) |
| Cancer Hotspots data license? | **ODbL 1.0** | `cBioPortal/cancerhotspots@b257eeaf`; Genome Nexus `HotspotAnnotation.java` |
| OncoKB via a public gateway? | **No** (signed MSK license only) | Terms: no sharing with any third party; 2026-05-15 paid license for API use (commit 733bddf9); FAQ bans AI training |
| GENIE aggregates? | **No** | ToU form, no aggregate carve-out; 28 CFR 202 attestations |

OncoKB and GENIE get **deep links** in the research skill (linking isn't redistribution), never data.

## 4. Per-new-repo Definition of Done (fleet standards, strictly)

Applies to civic / cbioportal / opentargets / trials / gdc / depmap. Template: **clone
`pgscatalog-link`** (newest live-API backend; snapshot ones use `clingen-link` / `hpo-link` patterns).

- [ ] Superpowers flow: spec in `docs/superpowers/specs/` → plan → TDD, one atomic commit per change; tracking issue on genefoundry-router.
- [ ] **Tool-Naming v1.1**: unprefixed `verb_noun`, ≤ 50 chars, canonical verbs; **no router `transform` block**.
- [ ] **Tool-Surface Budget**: ≤ 1,200 tokens/tool, ≤ 10,000/server; regression test; `make lint-surface`.
- [ ] **Tool-Schema Documentation** standard; `response_mode` minimal|compact|standard|full (default compact).
- [ ] **Response-Envelope v1.1**: honest `total`/`truncated`, untrusted-text fencing (CIViC statements, trial eligibility, study descriptions), errors name the parameter, `isError` on every error envelope.
- [ ] **MCP Transport v1**: single `/mcp`, stateless Streamable HTTP + JSON, no SSE; vendored `docs/conformance/` byte-identical (`conformance.py` + `behaviour.py` green).
- [ ] **HTTP Policy v1**: URL predicate on every request (host allowlist), ≤ 5 redirects, decoded-byte caps, host-free error messages; staged adoption ledger row.
- [ ] Not-found reflection guard + error-message sanitation (fleet canon).
- [ ] **Versioning v1**: single-source version; `serverInfo.version` guard test; `data_version` on every payload.
- [ ] **Container Hardening v1**: non-root numeric UID, read-only rootfs, `cap_drop: ALL`, no-new-privileges, limits, digest-pinned base, CI Trivy + SBOM.
- [ ] **Deploy Contract v1**: one deployed `docker-compose.npm.yml`, `GF_IMAGE`, named volumes, subnet from the registry (C6), numeric `user:` only there.
- [ ] **Logging & CLI v1** (mgi-link canon); README Standard v1 (`check_readme.py`); Repo Metadata Standard (fleet-metadata.yaml topics); CITATION.cff; LICENSE/NOTICE carrying the **upstream license + attribution** (CC0 / ODbL / CC BY 4.0).
- [ ] Research-use-only disclaimer mirrored, plus an explicit "not a clinical actionability report" line.
- [ ] GitHub: **enable `immutable-releases` before the first tag**; Dependabot config; CodeQL; secret scanning.
- [ ] `make ci-local` green; release via tag → approve `release` environment (double gate); **never `gh release create`**.
- [ ] Agent UX eval (pgscatalog harness: claude/codex/agy, gate > 85; run 3 sessions at a time), with somatic task sets (e.g. "BRAF V600E actionability in melanoma", "KRAS G12C prevalence in LUAD", "is TP53 R175H a hotspot").

## 5. Risks and open decisions

1. **cBioPortal programmatic tolerance**: bot detection added 2026-11-14 is the biggest operational risk. Mitigation: cache, limiter, ask first; GDC is the fallback prevalence source.
2. **Genome builds**: CIViC and cBioPortal are GRCh37-first while the fleet is GRCh38. Every coordinate carries `assembly`; joins go through vep liftover or MANE HGVS, never raw positions.
3. **Two classification axes**: oncogenicity vs AMP tier vs germline ACMG must never be collapsed into one "significance" field anywhere (clinvar extension, civic, skill).
4. **Safety**: no source here is a clinical report. OncoKB's public server demonstrates how a degraded upstream silently gives a dangerous "no treatment" answer, so empty-actionability answers must say "no CIViC evidence found", never "not actionable".
5. **Decided by owner 2026-11-20:** Open Targets as the drug/driver layer; no DGIdb/IntOGen links. See `H-*.md`.
6. **Decided by owner 2026-11-20:** MOAlmanac as its own snapshot link (one source per link; two data models would not merge cleanly). See `I-*.md`.
7. **CGI license conflict** (OT says CC BY 4.0, DGIdb says CC BY-NC 4.0) gates OT `cancer_biomarkers` and the CGI biomarker TSV.

## 6. Key citations (PMIDs, verified via pubtator unless noted)

CIViC 28138153, 36373660 · MetaKB 32246132 · Horak oncogenicity SOP 35101336 · CIViC SOP 31779674 ·
KB review (Borchert 2021) 33971666 · cBioPortal 23550210, 37668528 · GENIE 28572459, 35819403 ·
OncoTree 33625877 · 3D hotspots 28115009 · GDC 27653561, 33619384 · PCAWG 32025007 · CGI 29592813 ·
BoostDM 34321661 · OncoKB 28890946, 37849038 · COSMIC 38183204 · CGC 30293088 · CancerVar 35544644 ·
PMKB 27789569. Unverified (from agent memory): Cerami 2012 22588877, Chang 2016 26619011, Chang 2018 29247016,
IntOGen 2020 / DGIdb 5 / DepMap PMIDs.
