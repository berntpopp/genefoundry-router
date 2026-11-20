# H — Drug/driver-gene layer: Open Targets vs DGIdb + IntOGen

Date: 2026-11-20. Live probes against the Open Targets Platform API (26.09), the DGIdb 5 GraphQL API, and the IntOGen download.
Probe scripts and raw JSON are in `scratchpad/cancer/probe/`. The IntOGen zip is extracted in `scratchpad/cancer/intogen_dl/`.

## Recommendation: **A — build one `opentargets-link`. Do not build `dgidb-link` or `intogen-link`.**

The decisive facts:

1. **OT keeps IntOGen intact.** For BRAF, TP53, ARID1A, KMT2D and RNF43, every OT `intogen` evidence row matches a compendium row. The cohort ID matches, and `resourceScore` equals `QVALUE_COMBINATION` exactly (436/436 rows). The `significantDriverMethods`, sample counts and role all carry through. The role is stored as an SO term: Act → `gain_of_function_variant`, LoF → `loss_of_function_variant`. OT 26.09 uses the same IntOGen release (2024.06) as the download. OT covers 92% of compendium cohort rows (436/471). Every missing row is a `HARTWIG_WGS_*_2023` cohort.
2. **After a license allowlist, DGIdb has little left to add.** On BRAF, 68% of interactions come only from NC, copyright or unknown sources: CKB-CORE NC-SA, PRISM (copyright), OncoKB, CGI NC, TTD, MyCancerGenome and others. Removing what OT already covers, and what civic-link and clinpgx-link will serve, leaves 27 BRAF / 38 KRAS / 104 EGFR rows. Of these, only 4 / 10 / 7 are approved drugs. Most are GtoPdb tool compounds and off-target kinase inhibitors, plus ChEMBL noise. Example of the noise: 9 HCV protease inhibitors (boceprevir, telaprevir, glecaprevir, …) listed as KRAS "inhibitors".
3. **Cost and standards favour A.** One link with about 6 tools fits about 4–5k tokens and uses one CC0 API with one cadence (quarterly). B needs two repos (about 6–7k tokens). One of them must carry a 25-source license filter that is easy to get wrong and changes whenever DGIdb adds a source. C pays for both and adds almost no new rows.

## 1. Open Targets Platform

**Version and cadence.** `meta.apiVersion` = 26.9.0 and `dataVersion` = 26.09. The 26.09 release notes are dated 24 Sep 2026, and the previous release was 26.06, so releases are quarterly. It ships ChEMBL 37, COSMIC 2025-06-24, IntOGen 2024.06, EVA/ClinVar 2026-07-23 and AACT 2026-08-25.

**Schema change.** `Target.knownDrugs` no longer exists. The field is now `Target.drugAndClinicalCandidates`, of type `clinicalTargets {count, rows: ClinicalTargetFromTarget}`. Each row has `maxClinicalStage`, `drug`, `diseases[] {diseaseFromSource, disease}` and `clinicalReports[]`. A `ClinicalReport` carries trial or label data (source, phase, status, sponsor, whyStopped, …). Stages are enum strings: `APPROVAL`, `PHASE_4`, … `PHASE_1`.

### Live results

| Query | BRAF | KRAS |
|---|---|---|
| drugAndClinicalCandidates (drug rows) | 18 (3 APPROVAL, 5 PH4, …) | 3 (sotorasib, adagrasib APPROVAL; salirasib PH2) |
| clinicalReports nested | 1,433 (1,381 CT.gov) → **220 KB** | 99 → 18 KB |
| same query without reports | 38 KB | — |
| tractability | 13 true buckets (SM approved drug, HQ pocket; AB loc; PROTAC) | 10 true |
| associatedDiseases count | 2,928 | 2,262 |

Notes on the table:
- **The mechanism layer is thin for KRAS.** Only 3 drugs, because it is ChEMBL mechanism-of-action only. Drugs aimed at KRAS-mutant tumours appear instead as `cancer_biomarkers` evidence: 38 distinct drugs for KRAS, 24 for BRAF.
- **Stage lag.** Avutometinib (FDA approval 2025) is shown as PHASE_3 with MEK1/2 mechanism only.

### Cancer-relevant evidence per datasource

Measured with `size:50`. Approximate bytes per row with all fields selected:

| datasource | BRAF count | KRAS count | ~B/row | Key fields |
|---|---|---|---|---|
| intogen | 54 | 77 | 1.07 k | cohortId/ShortName/Description, significantDriverMethods[], resourceScore (= q-value), mutatedSamples{SO role, tested, mutated}, literature |
| cancer_biomarkers (CGI-derived, CC BY 4.0 per OT) | 84 | 59 | 0.93 k | biomarkerName, geneticVariation[] (GRCh37 `7_140453136_A_T` = V600E), drug (ChEMBL), drugResponse (sensitivity/resistance EFO), confidence (e.g. "NCCN guidelines"), literature |
| cancer_gene_census (COSMIC) | ≥200 (paged) | ≥200 | 1.4–1.5 k | mutatedSamples per SO consequence, literature |
| eva_somatic (ClinVar somatic) | 54 | 96 | 0.88 k | RCV studyId, clinicalSignificances, review-status confidence, variant id, rsId |
| gene_burden | 0 | 3 (leukaemia, OR 12.27, p 5.7e-10, UKB/AoU/MGB) | 1.0 k | OR/beta, CI, p, statisticalMethod, cohort |

### Pagination

- `evidences(efoIds!, datasourceIds, size, cursor)` uses an opaque cursor and returns an honest `count`. **`efoIds` is required.** For "all evidence for a target" the wrapper must first collect EFO IDs from `associatedDiseases` (a page of 3,000 works), then query with all of them.
- `associatedDiseases` uses index/size pages.
- **`clinicalReports` is not paginated.** The wrapper must never select it by default. Return the report count plus the top N reports.

### Rate limits and usage policy

- No rate-limit headers (`nginx`, nothing else).
- The docs ask users to download datasets instead of querying one entity at a time. Trimmed queries return in about 0.16 s.
- The official MCP defaults to client-side limiting at 3 rps with a burst of 100. Use the same in the link.

### License (platform-docs/licence)

- **"Open Targets Platform is marked with CC0 1.0"**. The per-source table adds detail:
  - ChEMBL is CC BY-SA 3.0.
  - Cancer Gene Census is listed as **"Commercial use for Open Targets"**. The page also states that every listed source, including those labelled "commercial use for Open Targets", "agreed for their data to be used without restriction by all Open Targets users".
  - CGI is CC-BY 4.0. Europe PMC is mixed (CC-BY / CC-BY-NC / CC0). ClinVar via EVA falls under the EMBL-EBI terms.
- **Gateway position:**
  - CC0 and CC-BY sources: fine.
  - ChEMBL: share-alike, which needs attribution on drug and mechanism rows.
  - `cancer_gene_census`: it is unclear whether a third-party gateway counts as an "Open Targets user". **Keep the plan's rule and exclude `cancer_gene_census`** from the datasource allowlist. COSMIC's own terms are non-commercial. Nothing is lost for drivers: `intogen` and `eva_somatic` cover the same role.
  - `europepmc`: text-mined evidence with mixed licenses. Expose counts only, or exclude it.

### Official Open Targets MCP

- `github.com/opentargets/platform-mcp`: Apache-2.0, FastMCP, last release 2026.7.1, pushed 2026-10-06. A hosted endpoint runs at `https://mcp.platform.opentargets.org/mcp`, and its `tools/list` was probed live.
- **5 tools, about 17.5k characters (about 4.4k tokens):** `get_open_targets_graphql_schema` (5.8k chars, about 1.45k tokens), `get_type_dependencies`, `search_entities`, `query_open_targets_graphql` (4.0k chars), `batch_query_open_targets_graphql` (5.5k chars, about 1.37k tokens).
- **Federating it as-is breaks fleet rules:**
  - Two tools are over the 1.2k per-tool limit.
  - It is raw GraphQL passthrough. The model writes arbitrary queries, so there is no honest pagination, no Response-Envelope, and no untrusted-text fencing.
  - Output is unbounded: one natural BRAF drug query returns 220 KB.
  - Nothing stops `cancer_gene_census` or `europepmc` rows from coming through.
  - Tool names do not follow verb_noun v1. `search_entities` is ambiguous across the fleet.
- **Verdict:** wrap OT per fleet standards, using persisted fixed queries. Treat platform-mcp as a reference for the schema and common pitfalls (`common_mistakes_guide.md`), and as an eval checklist.

### Germline bonus (in favour of A)

- BRAF and KRAS associations already include germline datasources: `eva` (ClinVar germline), `gene2phenotype`, `orphanet`, `clingen`, `genomics_england`, `uniprot_variants`, `gwas_credible_sets` (13 for BRAF) and `gene_burden`.
- The fleet already covers ClinVar, ClinGen, Orphanet, PanelApp and GenCC. It has **no GWAS credible-set / L2G source and no rare-variant burden (UKB/AoU) source**. OT adds both, plus one harmonised association score across all evidence types.
- The same link therefore serves germline research questions, which neither DGIdb nor IntOGen can do.

## 2. DGIdb 5 (live `sources` and `genes.interactions`)

Licenses as listed by the `sources` query (`sourceType: INTERACTION`, plus PRISM):

- **Permissive (PD/CC0/CC BY):** CIViC (CC0), FDA (PD), NCI (PD), DoCM (CC BY 4.0), Drug Repurposing Hub (CC BY 4.0).
- **Share-alike / attribution:** ChEMBL 37 (CC BY-SA 3.0), GuideToPharmacology 2026.2 (CC BY-SA 4.0), ClinPGx (CC BY-SA 4.0), MOAlmanac (ODbL).
- **Non-commercial:** CGI (CC BY-NC), CKB-CORE (CC BY-NC-SA), COSMIC, DTC (NC-SA), OncoKB, MyCancerGenome(+CT), CancerCommons, DrugBank (0 claims).
- **Copyright / unknown:** PRISM ("Nature Publishing Group copyright publication"), TALC, TdgClinicalTrial, TEND, TTD ("All Rights Reserved"/unclear), Clearity×2 ("no longer publicly available").

Note that OT lists CGI as CC-BY 4.0 while DGIdb lists it as CC BY-NC 4.0. The two records disagree, so resolve this before relying on CGI-derived rows from either source.

### Interactions under a permissive + share-alike allowlist

| Gene | Interactions | Kept by allowlist | Only from restricted sources (approved) | Kept, not in OT mech/biomarker drugs | …and not from CIViC/ClinPGx (approved) |
|---|---|---|---|---|---|
| BRAF | 255 (366 KB) | 82 (32%) | 173 (54) | 56 | **27 (4: avutometinib, dasatinib, fluorouracil, pazopanib)** |
| KRAS | 133 | 108 (81%) | 25 (11) | 96 | **38 (10: 9 HCV protease inhibitors + avutometinib)** |
| EGFR | 329 | 247 (75%) | 82 (14) | 147 | **104 (7: acalabrutinib, ibrutinib, tucatinib, lidocaine, quercetin, genistein, ethanol)** |

The leftover rows are mostly GuideToPharmacology and Drug Repurposing Hub bioactivity "inhibitor" claims, without the potency, stage or indication context OT gives. The truly new rows are off-target kinase activity, which is research-interesting but low value, plus noise.

On BRAF, CKB-CORE (NC-SA) provides 181 of the 255 interactions. That is DGIdb's real cancer value, and the license makes it unusable here.

**Unique value of DGIdb after the allowlist:** drug-category annotations (druggable-genome categories) and GtoPdb affinity-style claims. The druggable-genome part is largely duplicated by OT `tractability` (SM/AB/PR/OC buckets) and `targetClass`.

## 3. IntOGen (compendium snapshot 2024-06-18 / IntOGen-Drivers-20240920.zip, CC0, 965 KB)

- **Files:** `Compendium_Cancer_Genes.tsv` (4,478 rows, 633 genes, 271 cohorts) and `Unfiltered_drivers.tsv` (23,444 rows: candidate genes with TIER, RANKING, warning flags and a FILTER reason, 18,237 "No driver"). The LICENSE file is CC0 1.0. The TSVs inside the zip are dated 2026-04-16, so the zip was re-cut and **the version must come from the folder name and file dates, not the README.**

| Gene | Compendium rows | OT `intogen` rows | Missing from OT | q-value match |
|---|---|---|---|---|
| BRAF | 57 | 54 | 3 (HARTWIG GIST/PROSTATE/READ 2023) | 54/54 exact |
| TP53 | 212 | 193 | 19 (all HARTWIG_*_2023) | 193/193 |
| ARID1A | 90 | 83 | 7 (HARTWIG) | 83/83 |
| KMT2D | 91 | 86 | 5 (HARTWIG) | 86/86 |
| RNF43 | 21 | 20 | 1 (HARTWIG) | 20/20 |

OT does include most Hartwig cohorts: BRAF has 13 in the compendium and 10 in OT. The gap is a subset of cohorts that OT either did not map to EFO or that were added when the zip was re-cut. Genes that are drivers only in Hartwig cohorts number 633 − 616 = 17 genes compendium-wide.

### What the standalone snapshot adds over OT

- Per-row `DOMAINS`, `2D_CLUSTERS` and `3D_CLUSTERS` (hotspot positions).
- `EXCESS_MIS`, `EXCESS_NON` and `EXCESS_SPL` (dN/dS-style excess).
- Canonical `TRANSCRIPT`, the `MUTATIONS` count, and the `CANCER_TYPE` code.
- The `ambiguous` role (68 rows), which OT folds into an SO term.
- The **Unfiltered candidates file**, with TIER and warning reasons (artifact, germline, expression, signature).
- The roughly 8% of Hartwig rows OT lacks.

This is niche, mechanism-level detail. It does not justify its own link plus a second release cadence for a source that has been frozen since 2024. If it is wanted later, fold the cluster and excess columns into `opentargets-link` as a small bundled CC0 TSV join, keyed on (symbol, cohortId), instead of building a new link.

## 4. Cost and overlap

Estimates use fleet-typical tool definitions of about 700–900 tokens each.

| Option | Links | Tools | Est. surface tokens | Upstreams / cadence | License work |
|---|---|---|---|---|---|
| **A** opentargets-link | 1 | 6: `resolve_target`, `get_target_tractability`, `get_target_drugs`, `search_disease_targets`, `get_target_disease_evidence` (allowlist intogen, cancer_biomarkers, eva_somatic, eva, gene_burden, gwas_credible_sets, clingen, orphanet, gene2phenotype, genomics_england; **exclude cancer_gene_census, europepmc**), `get_target_associations` | ~4.5–5.5k | 1 GraphQL, quarterly | 1 datasource denylist plus ChEMBL BY-SA attribution |
| **B** dgidb-link + intogen-link | 2 | dgidb 4 (`resolve_gene`, `get_gene_interactions`, `get_drug_interactions`, `list_sources`) + intogen 3 (`get_gene_drivers`, `list_cohort_drivers`, `list_cohorts`) | ~3k + ~2.3k = ~5.3k | live GraphQL + frozen zip snapshot | 25-source allowlist that changes upstream; snapshot build/versioning |
| **C** all three | 3 | ~13 | ~10k (fleet-wide) | 3 | both |

### Overlap with planned links

- **civic-link (actionability):**
  - OT `cancer_biomarkers` (CGI-derived) partly overlaps CIViC actionability. Keep it in OT as a second, guideline-anchored source. Label it "CGI biomarker, frozen", with drugResponse sensitivity/resistance. Never present it as an AMP tier.
  - DGIdb's largest permissive cancer source is CIViC itself: 20 of BRAF's 56 non-OT rows and 40 of KRAS's 96. civic-link serves this directly with evidence levels.
- **clinpgx-link (pharmacogenomics):**
  - clinpgx-link serves curated gene-drug interactions, CPIC guidelines, dosing and variant annotations (13 tools).
  - DGIdb ClinPGx rows (13 BRAF, 19 KRAS, 30 EGFR) duplicate it.
  - OT `Drug.pharmacogenomics` also duplicates it. **Do not expose that field in opentargets-link.**
- **MOAlmanac:** DGIdb's MOAlmanac rows (ODbL) are already planned as their own link or as a second source behind civic-link.

## Residual risks

1. **COSMIC/CGC terms through OT.** OT's "used without restriction by all Open Targets users" wording may not cover a third-party gateway. Mitigation: hard-exclude `cancer_gene_census` and add a guard test. Also check that `hallmarks` (Target.hallmarks, COSMIC hallmarks v103) is not exposed.
2. **Schema churn.** `knownDrugs` was renamed to `drugAndClinicalCandidates` in 2025–26, and 26.09 refactored the target dataset. Pin persisted queries, add a CI schema-introspection contract test, and expect per-release maintenance.
3. **Unbounded nesting.** `clinicalReports` is not paginated (220 KB for BRAF). The `evidences` field requires `efoIds`, so "all evidence for a gene" takes two calls. The wrapper must cap and report `truncated` honestly.
4. **Lag and coverage.**
   - Drug stages trail regulators: avutometinib still shows PHASE_3.
   - The KRAS mechanism layer has only 3 drugs.
   - IntOGen is frozen at 2024.06, and OT is missing about 8% of rows (Hartwig).
   - Agents must not read "no OT drug" as "no drug". Use the empty-result wording rule from the plan.
5. **Genome build.** `cancer_biomarkers` variant IDs are GRCh37 (`7_140453136_A_T`), while `eva_somatic` uses GRCh38 (`7_140753338_…`). Label the assembly on every variant.
6. **Third-party license inconsistency.** OT says CGI is CC-BY 4.0, DGIdb says CC BY-NC 4.0. Confirm before relying on `cancer_biomarkers` in a public gateway. If it is NC, drop that datasource too. Actionability then rests on civic-link and MOAlmanac.
7. **No published OT rate limit.** Self-throttle to about 3 rps and cache per release (`dataVersion` as `data_version`).

Not checked: OT `Disease.associatedTargets` filtered to `intogen`, for listing drivers per cancer type (likely works through `datasources` / `facetFilters`). The intogen and eva_somatic evidence query only scanned EFO IDs from the top 500 associations, so counts in the §1 table are lower bounds. The §3 counts are complete.
