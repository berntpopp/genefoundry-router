# D — NCI GDC, TCGA, ICGC (DCC / ARGO / 25K), PCAWG

Assessor: cancer-genomics informatics pass, 2026-11-20. All probes run live with `curl -s -m 20` the same day.

---

## 1. NCI Genomic Data Commons (GDC)

### 1.1 What it is
- NCI's harmonized cancer genomics repository. All submitted data is re-aligned to GRCh38 and re-called with
  uniform pipelines (MuSE, MuTect2, VarScan2, Pindel, CaVEMan/Sanger; ensemble masked MAF), then annotated with VEP
  (GENCODE v36). Clinical, biospecimen, mutation, CNV, expression, methylation, proteome, SV.
- **Live release: Data Release 46.0, 2026-08-10** (`/status`: `"data_release":"Data Release 46.0 - August 10, 2026"`, API tag 8.5.0).
- **93 projects** (`/projects` pagination.total=93): TCGA (33), TARGET (pediatric), CPTAC-2/3, CCDI-MCI (pediatric),
  HCMI, MMRF-CoMMpass, BEATAML, MP2PRT-ALL, ALCHEMIST, CGCI, FM-AD (Foundation Medicine), etc.
- **3,605,978 distinct SSMs** in the open somatic tier (GraphQL `explore.ssms.hits.total`).
- Curation: centrally processed (not literature curation). Release cadence: ~3-4 data releases/year, numbered.
- Canonical citations (verified via NCBI esummary):
  - Grossman RL et al. "Toward a Shared Vision for Cancer Genomic Data." *N Engl J Med* 2016. PMID 27653561.
  - Heath AP et al. "The NCI Genomic Data Commons." *Nat Genet* 2021. PMID 33619384.
  - Zhang Z et al. "Uniform genomic data analysis in the NCI Genomic Data Commons." *Nat Commun* 2021. PMID 33619257 (pipelines).

### 1.2 Access (live probes)
Base URL `https://api.gdc.cancer.gov` (REST, JSON; plus GraphQL at `/v0/graphql`, used by the portal). No auth for
open tier; controlled tier needs a GDC token (eRA Commons + dbGaP). No published rate limit; no rate-limit headers
observed. `size=10000` accepted (`/genes?size=10000` -> HTTP 200, 469 KB).

| Probe | HTTP | Result (trimmed) |
|---|---|---|
| `GET /status` | 200 | `{"data_release":"Data Release 46.0 - August 10, 2026","status":"OK","tag":"8.5.0"}` |
| `GET /projects?size=2` | 200 | `TCGA-LUAD` (585 cases), `ALCHEMIST-ALCH`; `pagination.total=93` |
| `GET /genes?filters=symbol=BRAF` | 200 | `ENSG00000157764, BRAF, 7q34, is_cancer_gene_census:true` |
| `GET /ssms?filters=gene=BRAF & aa_change=V600E` | 200 | `ssm_id 84aef48f-31e6-52e4-8e05-7d5b9ab15087`, `chr7:g.140753336A>T`, `cosmic_id ["COSM476"]`, `clinical_annotations.civic.variant_id "12"`, ncbi_build GRCh38 |
| `GET /ssms?filters=gene=BRAF&size=0` | 200 | total 308 distinct BRAF SSMs |
| `GET /ssm_occurrences` (BRAF V600E, TCGA-SKCM) | 200 | total 200; hits carry `case.submitter_id` (TCGA barcode), `disease_type` |
| `GET /ssm_occurrences?ssm.ssm_id=<V600E>&facets=case.project.project_id` | 200 | 868 occurrences: TCGA-THCA 283, TCGA-SKCM 200, CCDI-MCI 143, CPTAC-3 91, TCGA-COAD 50 |
| KRAS G12D facet by project | 200 | 491 occ.: CPTAC-3 94, MP2PRT-ALL 55, TCGA-COAD 54, TCGA-PAAD 45, HCMI-CMDC 42, TCGA-UCEC 32 |
| `GET /analysis/top_mutated_genes_by_project` (TCGA-SKCM, size 3) | 200 | TTN 388, MUC16 377, ANK3 375 (raw counts; 20,489 genes) |
| GraphQL `cases` SKCM with `available_variation_data=ssm` | 200 | 470 (denominator) |

**Worked frequency, "how often is BRAF mutated in TCGA melanoma":** denominator 470 SSM-profiled SKCM cases;
BRAF any SSM = 236 distinct cases (dedup of `/ssm_occurrences` case_ids) -> **50.2%**; BRAF V600E = 200 cases ->
**42.6%**. Matches the literature (~50% BRAF, ~40-45% V600E in TCGA SKCM).

**Quirks a wrapper must handle (these are contract-truth hazards):**
1. **Unknown filter field silently returns empty** (`/ssms?filters={"field":"bogus"}` -> HTTP 200, total 0,
   `warnings:{}`). Unknown *facet* names do produce `warnings.facets`, unknown filter fields do not. A link must validate field names against
   `/_mapping` (ssms has 282 fields) or it violates the fleet's "no silently-empty filter" behaviour gate.
2. **Unknown gene = 200 + empty hits** (not 404). Need explicit not-found in the envelope.
3. **Canonical transcript != MANE.** For BRAF, GDC `is_canonical:true` is ENST00000644969 -> **V640E**;
   V600E sits on ENST00000646891 (MANE Select) and ENST00000496384. Any "canonical" protein change from GDC
   disagrees with the fleet's vep/MANE canon. Join on genomic coordinate (`chr7:g.140753336A>T` -> gnomAD id
   `7-140753336-A-T`) or on the MANE transcript; never on GDC's `is_canonical` aa_change.
4. **Facet buckets are capped** (case_id facet returned 200 buckets where dedup gave 236). Distinct-case
   counts must page `/ssm_occurrences` (size<=10000) or use GraphQL `cases` counts; do not trust bucket length.
5. GraphQL `cases` with `genes.symbol=BRAF` returns 330 for SKCM (includes CNV-affected cases), not 236. The
   portal's "SSM-affected cases" is a specific filter combination; easy to get a wrong numerator.
6. `top_mutated_genes_by_project` is raw counts dominated by long genes (TTN, MUC16): no length/background
   correction, so it is not a driver ranking. Pair with Cancer Gene Census flag (`is_cancer_gene_census`).

### 1.3 License / Terms
- GDC is a US-federal (NIH/NCI) resource; no copyright licence is stated for open data. Governing terms are the
  **GDC Data Access Policies + NIH Genomic Data Sharing (GDS) Policy**.
- Decisive clause, GDC MAF docs (https://docs.gdc.cancer.gov/Data/File_Formats/MAF_Format/):
  > "Somatic MAFs are publicly available and can be freely distributed" (within the GDC Data Access Policies).
- Open-access definition (gdc.cancer.gov/access-data/data-access-processes-and-tools): "no authentication or
  authorization is necessary ... No login required for access." Open data = high-level, not individually
  identifiable genomic data, most clinical, all biospecimen.
- Obligations (gdc.cancer.gov/access-data/data-access-policies): users must "Not attempt to identify individual
  human research participants" and "Acknowledge in all oral or written presentations, disclosures, or
  publications the specific dataset(s)" and the NIH repository. No redistribution prohibition; no embargo
  for TCGA (all moratoria long expired). Some newer program datasets (e.g. CCDI, CPTAC) may carry project-level
  publication guidelines; surface `project_id` so callers can cite.
- Controlled tier (protected MAFs, germline, BAMs) = dbGaP: **must never be proxied** (would need per-user
  tokens -> forbidden by fleet constraint).
- **Verdict: COMPATIBLE** with a public, unauthenticated-backend-behind-router deployment for the open tier.
  Recommend: aggregate counts by default, emit per-case TCGA barcodes only in `full` mode, add GDC + dataset
  citation in every envelope `_meta`.

### 1.4 Fleet fit
- **Live-proxy** (like ucsc-link / pgscatalog-link). Snapshot not warranted: API is fast, versioned
  (`data_release` -> envelope `data_version: "GDC DR46.0"`), and full open MAF set is large.
- Outbound allowlist: `api.gdc.cancer.gov` only.
- Proposed tools (v1.1 names):
  1. `resolve_gene` — symbol/Ensembl -> GDC gene record (CGC flag, cytoband). Entry-point resolver.
  2. `list_projects` — programs/projects with disease type, primary site, case counts, SSM-profiled counts.
  3. `get_gene_mutation_frequency` — gene x project(s): SSM-affected cases / SSM-profiled cases, per project (the core question).
  4. `get_variant_mutation_frequency` — variant (genomic `chr-pos-ref-alt` or gene+protein change, MANE-aware) x project(s): cases/denominator; returns ssm_id, COSMIC id, CIViC variant id.
  5. `search_somatic_mutations` — mutations in a gene (hotspot table: protein change, consequence, VEP impact, case count), honest total.
  6. `get_top_mutated_genes` — top genes in a project, with CGC flag and caveat that it is length-unadjusted.
  7. `get_gene_cnv_frequency` — gain/loss case fractions per project (GDC has `/analysis/top_cases_counts_by_genes` and CNV occurrences).
  (Optional 8. `get_project_summary` — clinical/demographic breakdown.) ~7 tools fits the 10k-token budget.
- Join keys: HGNC symbol & Ensembl gene ID (hgnc), GRCh38 `chr-pos-ref-alt` (gnomad, vep, clinvar), CIViC
  variant id and COSMIC id (future civic/oncokb links), `disease_type`/`primary_site` -> mondo (needs a mapping
  table: TCGA project codes -> MONDO, ~33 rows for TCGA; others free-text).
- Overlap: heavy with cBioPortal (TCGA PanCancer Atlas). See section 5.

### 1.5 Scientific value
- "How frequently is KRAS G12D seen across cancer projects?" — harmonized single-pipeline counts across 93
  projects including **pediatric (TARGET, CCDI-MCI, MP2PRT-ALL)**, CPTAC, HCMI, MMRF, BEATAML — cohorts cBioPortal
  has only partially or under different processing.
- Somatic-vs-germline sanity in variant interpretation: "is this ClinVar germline VUS a recurrent somatic
  hotspot?" (gnomAD id -> GDC SSM count by project).
- Not answered by GDC: actionability / evidence levels (that is OncoKB/CIViC), clinical-grade panel cohorts
  (MSK-IMPACT, AACR GENIE — cBioPortal).

### 1.6 Verdict
**BUILD `gdc-link` — P2, effort M**, as a *second* somatic-frequency backend after cBioPortal. Key risks: the
silent-empty-filter behaviour (must validate fields), canonical-transcript mismatch (V640E vs V600E),
facet caps / numerator definition (wrong frequencies look plausible), per-case barcodes in output (keep
behind `full`). An unofficial community GDC MCP server exists (quentincody/nci-gdc-mcp-server, GraphQL-based,
listed "dormant") — confirms demand, not reusable for the fleet.

---

## 2. TCGA as a dataset

- TCGA (33 cancer types, ~11k cases) lives in GDC as 33 `TCGA-*` projects (harmonized GRCh38 calls,
  DR46), and in cBioPortal as **32 PanCancer Atlas studies totalling 10,967 samples** (live
  `/api/studies?keyword=pan_can_atlas`), based on MC3 calls + curated clinical endpoints (TCGA-CDR).
- Canonical PanCanAtlas citation: Hoadley KA et al. *Cell* 2018, PMID 29625048.
- Licence: open tier inherits GDC/NIH GDS terms (section 1.3); no extra restrictions; moratoria expired.
- **Verdict: SKIP a dedicated TCGA link.** TCGA needs nothing beyond GDC (harmonized, current) + cBioPortal
  PanCanAtlas (curated, published-analysis-consistent). Note the two give slightly different numbers (MC3 vs
  GDC ensemble masked MAF); a skill should state which source a frequency came from.

---

## 3. ICGC (DCC / 25K / ARGO)

### 3.1 Status
- **ICGC Data Portal (dcc.icgc.org) retired June 2024** (confirmed: daco.icgc.org and docs.icgc.org banner
  "The ICGC Data Portal officially closed in June, 2024"; live `https://dcc.icgc.org/api/v1/genes/...` -> HTTP 301 to
  `index.html` whose meta reads "The ICGC Data Portal is now retired. Please visit
  https://docs.icgc-argo.org/docs/data-access/icgc-25k-data"). **No mutation-level query API exists today.**
- Last data release = **Release 28 (2019-11-26 per ARGO docs; README dated March 27, 2019)**: 86 projects,
  22 primary sites, 22,330 donors with molecular data, 81,782,588 SSMs, 57,905 mutated genes.
- Citation: Zhang J et al. "The International Cancer Genome Consortium Data Portal." *Nat Biotechnol* 2019, PMID 30877282.

### 3.2 What replaced it
1. **ICGC 25K legacy open bucket** — S3-compatible, not AWS: `https://object.genomeinformatics.org`, bucket
   `icgc25k-open`, `--no-sign-request`. Live: HTTP 200 listing with `release_28/{data,headers,projects_files,summary_files}`,
   `/PCAWG`, `/Supplemental`. Key file: `release_28/summary_files/simple_somatic_mutation.aggregated.vcf.gz`
   (**1.53 GB**). Static, frozen since 2019.
2. **Controlled legacy tier** — SFTP `icgc-legacy-sftp.platform.icgc-argo.org:2222`, DACO-approved users only;
   raw data at EGA (DAC EGAC00001000010); TCGA portion only via dbGaP phs000178.
3. **ICGC ARGO platform** (platform.icgc-argo.org, HTTP 200) — new 100k-patient programme. Public GraphQL at
   `https://api.platform.icgc-argo.org/graphql` (live HTTP 200). It is a **file-metadata** index: 179,956 files,
   only **4,412 open** (all `Filtered SNV Calls` / `Filtered InDel Calls` per-donor VCFs, restricted to GENCODE
   "open-access regions" to limit germline leak); 175,544 controlled. No variant/gene-level aggregate endpoint.
   Quirk: aggregation filters use `field:` not `fieldName:` (the latter 500s with an internal JS error).

### 3.3 License
- No licence named for open-tier files; docs say open data "is available to everyone" and point to ICGC ARGO
  publication policy (E3) and data-use policies. README for Release 28 carries ICGC publication/embargo policy;
  PCAWG embargo ended (July 2019 / marker paper Feb 2020). Historical ICGC open tier was explicitly
  usable without restriction other than publication policy + no re-identification.
- **Verdict: probably compatible for open tier, but unverified** (no explicit redistribution clause found);
  irrelevant in practice because there is no queryable API.

### 3.4 Verdict
**PARK / SKIP — P3.** No live query API; the only route is a 1.5 GB frozen 2019 aggregated VCF (snapshot
pattern, effort M-L) whose non-TCGA content (~16k donors: PCAWG, Asian/European ICGC projects like LIRI-JP,
ESAD-UK, PACA-AU) is the sole unique value. ARGO open tier is per-donor VCF files, not something to proxy.
Revisit only if a "non-US cohort somatic frequency" gap is shown with real agent questions.

---

## 4. PCAWG (brief)
- 2,658 whole cancer genomes (ICGC/TCGA), 38 tumour types; *Nature* 2020, PMID 32025007 (ICGC/TCGA PCAWG Consortium).
- Open consensus SNV/indel calls in `icgc25k-open/PCAWG/consensus_snv_indel`; **cBioPortal already hosts
  `pancan_pcawg_2020` (2,922 samples)** (live probe).
- **Verdict: SKIP** — reachable through cBioPortal; its unique value (non-coding drivers, SV, signatures) is
  analysis-level, not a lookup API.

---

## 5. GDC vs cBioPortal — which to wrap for "how frequently is gene X / variant Y mutated in cancer Z"

| Criterion | cBioPortal | GDC |
|---|---|---|
| Cohort breadth | ~400+ studies incl. MSK-IMPACT, AACR GENIE, MSK-CHORD, PanCanAtlas, PCAWG, many published cohorts | 93 projects, TCGA/TARGET/CPTAC/CCDI/HCMI/MMRF etc. |
| Denominator correctness | first-class "profiled samples" incl. gene-panel coverage awareness | must construct (SSM-profiled cases); facet caps; easy to get wrong |
| Protein change notation | HGVSp_Short on canonical (MSK/OncoKB-isoform = V600E) | GDC canonical can be wrong isoform (BRAF V640E) |
| Processing | heterogeneous (per-study pipelines) | uniform GRCh38 harmonized pipeline |
| Clinical/cancer-type ontology | OncoTree codes per sample | project + disease_type (coarse) |
| Pediatric | some studies | strong (TARGET, CCDI-MCI, MP2PRT) |
| Licence | per-study terms + GENIE has its own terms (other agent) | NIH open, "freely distributed" |

**Recommendation:** cBioPortal is the **primary** wrap for frequency questions (bigger and clinically relevant
cohorts, correct denominators, OncoTree, isoform matching clinical convention), provided the other agent's licence
check clears it (GENIE/MSK terms are the risk). **gdc-link is worth adding as P2**: it is the licence-cleanest
source, the uniform harmonized pan-project denominator, the pediatric/CPTAC coverage, and it exposes CIViC and COSMIC
IDs that bridge to actionability backends. If cBioPortal's licence turns out to block re-serving, **promote gdc-link
to P1** as the fallback primary.

Router-skill angle (no build): a `somatic-frequency` skill that tells the agent to report source + denominator +
cohort, and to resolve the variant through vep (MANE) -> genomic coordinate before querying either backend.

---

## Sources
- GDC API live: https://api.gdc.cancer.gov/status, /projects, /genes, /ssms, /ssm_occurrences, /analysis/top_mutated_genes_by_project, /v0/graphql
- GDC MAF format (open vs protected): https://docs.gdc.cancer.gov/Data/File_Formats/MAF_Format/
- GDC data access processes: https://gdc.cancer.gov/access-data/data-access-processes-and-tools
- GDC data access policies: https://gdc.cancer.gov/access-data/data-access-policies
- ICGC 25K data: https://docs.icgc-argo.org/docs/data-access/icgc-25k-data
- ICGC portal closure: https://daco.icgc.org/ , https://docs.icgc.org
- ARGO open-access filtering: https://docs.icgc-argo.org/docs/analysis-workflows/dna-open-access-filtering
- ARGO publication policy: https://www.icgc-argo.org/page/77/e3-publication-policy
- Live: https://object.genomeinformatics.org/icgc25k-open , https://api.platform.icgc-argo.org/graphql , https://www.cbioportal.org/api/studies
- Unofficial GDC MCP: https://lobehub.com/mcp/quentincody-nci-gdc-mcp-server
- PMIDs verified via NCBI esummary: 27653561, 33619384, 33619257, 29625048, 30877282, 32025007
