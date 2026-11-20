# Cancer/somatic resource assessment A: CIViC, VICC MetaKB, Cancer Genome Interpreter (+ BoostDM, Horak 2022 SOP)

Assessor: cancer-genomics informatician (agent). Probe date: 2026-11-20. Every HTTP result below was observed live with `curl -s -m 20..60`.
No repo was modified. Fleet today: 26 backends, no cancer/somatic coverage. No `civic|metakb|cgi` mention in router docs or `servers.yaml`.

---

## 1. CIViC (Clinical Interpretation of Variants in Cancer), civicdb.org

### 1.1 What it is
- An open, community-curated, expert-moderated knowledgebase. Its evidence items are each tied to one publication or ASCO abstract. Each item records a type (Predictive, Diagnostic, Prognostic, Predisposing, Oncogenic, Functional), a direction, a significance, a level A–E and a 1–5 trust rating. On top of the evidence items sit **Assertions**: summary calls that carry an **AMP/ASCO/CAP tier/level** (e.g. `TIER_I_LEVEL_A`), an ACMG/AMP code (for predisposing calls), a ClinGen/CGC/VICC code (for oncogenic calls), NCCN guideline references and an FDA companion-test flag.
- **Molecular Profile model (CIViC v2, 2022+):** evidence now attaches to a *Molecular Profile* (MP), not to a variant. An MP is a boolean expression over variants, e.g. `BRAF V600E AND MSI Low` or `BRAF Amplification AND (BRAF V600E OR BRAF V600K)`. Simple MPs wrap exactly one variant. Variants are typed (`GeneVariant`, `FusionVariant`, `FactorVariant` for things like TMB/MSI). This matters for tool design: a "variant lookup" has to return the simple MP plus the complex MPs that contain the variant, and say which is which.
- Live counts (GraphQL, 2026-11-20):

  | Item | Count |
  |---|---|
  | Molecular profiles | 5,807 |
  | Variants | 5,190 |
  | Genes (features) | 755 |
  | Sources | 5,720 |
  | Evidence items, all statuses | 11,787 |
  | Evidence items, accepted | 5,003 |
  | Assertions, all | 332 |
  | Assertions, accepted | 186 |
  | Oncogenic-type assertions | 122 |
  | Accepted oncogenic evidence items | 160 |
- Update cadence: edits go live immediately. Nightly TSV/VCF dumps (`nightly-*.tsv` had `Last-Modified: 2026-11-20 00:00 GMT`). Monthly frozen releases are listed by `dataReleases` (`01-Nov-2026`, `01-Oct-2026`, … back to at least 2025). The **monthly release name works as a natural `data_version`.**

### 1.2 Access (probed)
- **GraphQL** `POST https://civicdb.org/api/graphql`. No auth for reads, no rate-limit headers seen, about 0.6 s per query. (`HEAD` on the endpoint returns 404; only POST works.)
- BRAF V600E probe: HTTP 200. Response shape, trimmed:
  ```
  molecularProfiles(name:"BRAF V600E") totalCount=41   # substring match: 1 simple + 40 complex MPs
   node id=12 "BRAF V600E" molecularProfileScore=1531.5 evidenceCountsByStatus{accepted:96, submitted:119}
     assertions totalCount=11: AID10 PREDICTIVE SUPPORTS SENSITIVITYRESPONSE TIER_I_LEVEL_A Melanoma(DOID:1909) [Vemurafenib C64768, Cobimetinib]
                               AID23 PREDICTIVE ... Colorectal Cancer [Cetuximab, Encorafenib, Binimetinib] ampLevel=null
                               AID20 PROGNOSTIC POOR_OUTCOME TIER_I_LEVEL_A Colorectal
     evidenceItems totalCount=218: EID79 B DIAGNOSTIC POSITIVE Thyroid ca PMID:21594703 rating 3; EID95 B PREDICTIVE dabrafenib+trametinib melanoma rating 5
  evidenceItems(molecularProfileId:12, status:ACCEPTED) totalCount=97
  ```
- Variant coordinates (`variants(name:"V600E", geneId:5)`, i.e. a `GeneVariant`): `alleleRegistryId=CA123643`, `clinvarIds=[13961, 376069]`. HGVS includes `NM_004333.4:c.1799T>A`, `NC_000007.14:g.140753336A>T` and the MANE transcript `ENST00000646891.2:c.1799T>A`. **The primary `coordinates` block is GRCh37** (7:140453136 A>T). GRCh38 is only available inside the HGVS list.
- KRAS G12D: MP 79 has 21 accepted evidence items and **0 assertions**. So many hotspot variants carry only evidence items, and a tool must handle "no assertion" gracefully.
- **Quirks a backend must handle:**
  1. **GraphQL errors come back as HTTP 200** with an `errors[]` array, e.g. `Field 'evidenceItems' doesn't accept argument 'status'` on the nested connection. The backend must map these to `isError`.
  2. **Page size is silently capped at 100**: `first:500` returns 100 nodes with no error. Paging is cursor-based (`pageInfo.endCursor`). This conflicts with honest `truncated` unless the backend clamps and reports it.
  3. Name search is a substring match, so "BRAF V600E" pulls in 40 complex MPs.
  4. Unknown ID returns `{"molecularProfile":null}` with HTTP 200. Unknown name returns `totalCount:0`.
  5. Small count drift between `evidenceCountsByStatus` (96) and a filtered count (97). The filtered count is authoritative.
- **Bulk:** `https://civicdb.org/downloads/nightly/nightly-{ClinicalEvidenceSummaries,MolecularProfileSummaries,AssertionSummaries,VariantSummaries,...}.tsv` and `nightly-civic_accepted.vcf`. These return 206 on range requests. Evidence TSV is about 4.2 MB with 25 columns (molecular_profile, doid, therapies, evidence_type/level/significance, citation_id, rating, evidence_status, variant_origin, …). Monthly snapshots live at `/downloads/<DD-Mon-YYYY>/`.

### 1.3 License
- Data is **CC0 1.0**; code is MIT. Decisive clause, from civic.readthedocs.io FAQ: *"The content of CIViC, hosted by Washington University School of Medicine is released under the Creative Commons Public Domain Dedication (CC0 1.0 Universal) and the source code for the CIViC application is licensed under the MIT License."*
- Verdict: **fully compatible**. No registration and no restriction on redistribution, re-exposure or commercial use. The best license on the somatic landscape (OncoKB, by comparison, is commercial and token-gated).

### 1.4 Fleet fit
- **Pattern: hybrid.** The live GraphQL proxy is the primary path because the data is curated continuously. `data_version` = the latest monthly `dataReleases` name plus a `live:<ts>` stamp, following the genereviews precedent. Snapshot fallback = the monthly TSVs, small enough (MBs) to bake into the image, as in clingen-link. Recommendation: **live GraphQL, with the monthly release stamped in `data_version`**. The outbound allowlist needs only `civicdb.org`.
- Template: `pgscatalog-link` / `ucsc-link` (live API). Use one fixed persisted query per tool and never accept free GraphQL from the caller (keeps the surface budget and the security posture safe).
- Proposed tools (v1.1 names):

  | Tool | Purpose |
  |---|---|
  | `search_molecular_profiles` | Entry resolver: free text / gene+variant / HGVS / CAID → MP ids, separating `simple` from `complex` MPs |
  | `get_molecular_profile` | MP summary: score, evidence counts by type/status, assertion list, component variants |
  | `get_variant` | Variant coordinates (GRCh37 plus GRCh38 HGVS), CAID, ClinVar IDs, MANE HGVS; `resolve` accepts a ClinVar VCV or CAID |
  | `search_evidence_items` | Filter by MP / gene / disease (DOID) / therapy / evidence type / level / status (default `ACCEPTED`); honest total and truncated |
  | `get_evidence_item` | One EID with its fenced evidence statement, source PMID and NCT ids |
  | `search_assertions` | Assertions by MP / gene / disease / type, with AMP tier, ACMG or ClinGen-CGC-VICC codes, NCCN reference, FDA companion-test flag |
  | `get_assertion` | One AID with its full summary and linked EIDs |
  | `get_gene` | Gene summary and the variants curated for it (by HGNC symbol or Entrez ID) |
  | `list_data_releases` (optional) | Monthly release names, for provenance |

  That is 8–9 tools, roughly 6–8k tokens, within budget.
- Join keys to the existing fleet:
  - HGNC symbol / Entrez ID → `hgnc`
  - ClinVar IDs → `clinvar`
  - ClinGen Allele Registry CAID → `clingen` / `vep`
  - HGVS / MANE transcript → `vep`
  - GRCh38 g. HGVS → `gnomad` chr-pos-ref-alt
  - PMIDs → `pubtator` / `litvar`
  - Diseases are **DOID**, not MONDO, so a DOID→MONDO crosswalk via `mondo` xrefs is needed.
  - Therapies are NCIt.
- Overlap: MetaKB v2 re-serves CIViC (and MOA) in VA-Spec form. CGI's biomarker DB overlaps on therapy/biomarker pairs.

### 1.5 Scientific value
- Answers questions like:
  - "Is BRAF V600E actionable in melanoma, and at what tier?" → AID10, Tier I Level A, vemurafenib+cobimetinib.
  - "Which EGFR variants predict resistance to osimertinib, with evidence level and PMIDs?"
  - "Is variant X oncogenic per ClinGen/CGC/VICC?" → 122 oncogenic assertions.
  - "Diagnostic/prognostic value of a fusion or MSI-combined profile?"
- No other open (CC0), per-publication, structured source for somatic clinical evidence exists. It is the natural first cancer backend.
- Citations: Griffith M et al., *Nat Genet* 2017, **PMID 28138153** (canonical). Krysiak K et al., "CIViCdb 2022", *Nucleic Acids Res* 2023;51:D1230, **PMID 36373660** (latest NAR database paper; introduces the Molecular Profile model).

### 1.6 Verdict
**BUILD `civic-link` (P0, effort M).**
- Risks:
  - The silent 100-node page cap must be clamped and reported, or honest `truncated` is broken.
  - GraphQL errors arrive as HTTP 200.
  - Complex-MP semantics are easy to misrender: "BRAF V600E" must never silently include "V600E AND MSI Low" evidence.
  - Coordinates are GRCh37-first.
  - DOID needs a MONDO bridge.
  - Evidence statements are free text and must be fenced as untrusted.
  - Submitted (unreviewed) evidence must default to excluded.

---

## 2. VICC Meta-Knowledgebase (MetaKB)

### 2.1 Status: is it still maintained?
- **MetaKB v1 is retired.** `search.cancervariants.org` returned **HTTP 504 Gateway Time-out** on `/`, `/api/v2/*` and `/api/v2/docs`. The repo README (commit "hotfix: add historical metakb v1 note (#930)", 2026-11-17) says: *"MetaKB v1 was an Elasticsearch-backed web application ... hosted at search.cancervariants.org. The service has since been retired, but its code and data remain available for reproducing it."*
- **MetaKB v2 is actively developed but alpha.** Repo `cancervariants/metakb` is MIT, last push 2026-11-19, latest release `2.0.0-a2` (2026-11-09). It is a FastAPI + Neo4j stack plus gene/disease/therapy normalizers (DynamoDB), UTA and SeqRepo. Harvesters are **CIViC, MOA, FDA PODA, cBioPortal, NCH MCI** (a Molecular Characterization Initiative pilot).
- The only public v2 instance found is **`https://pediatric.metakb.org`** (repo homepage). Its `/api/service-info` reports:

  | Field | Value |
  |---|---|
  | `version` | 2.0.0a2 |
  | `environment` | prod |
  | VRS | 2.1.1 |
  | cat-VRS | 1.1.1 |
  | **VA-Spec** | **1.1.0-snapshot.2026-02.1** |
  | contact | A. Wagner, Nationwide Children's |

  The hostname suggests a pediatric-focused deployment. There is no stable general-purpose public URL.

### 2.2 Access (probed)
- `GET https://pediatric.metakb.org/api/search/statements?variation=BRAF%20V600E` returns HTTP 200 (application/json) in about 0.2–0.9 s. Params: `variation`, `disease`, `therapy`, `gene`, `statement_id`, `start`, `limit`. There is also a batch endpoint.
  - **Without `limit` the response was 1.54 MB**: 73 therapeutic, 6 prognostic, 3 diagnostic statements (82 total).
  - With `limit=5` it was 91 kB.
  - Provenance: CIViC EIDs/AIDs (1,169 refs), MOA (300 refs), FDA PODA (8 refs).
- Response shape: `{query:{variation:{resolved_id:"ga4gh:VA.j4Xns…"}}, start, limit, therapeutic_response_statements[], prognostic_statements[], diagnostic_statements[]}`. Each statement is a **VA-Spec `Statement`** containing:
  - `proposition`: `VariantTherapeuticResponseProposition` with `predicate: predictsSensitivityTo`, `objectTherapeutic` as an RxCUI concept, `conditionQualifier` as an NCIt disease mapped to OncoTree
  - `direction`
  - `strength`: a VICC evidence code mapped to the CIViC level and the AMP 2017 level
  - `hasEvidenceLines`
- Unknown variation returns HTTP 200 with empty arrays (honest).
- **Quirk:** unknown API paths (`/api/v2/...`, `/api/meta/...`) return **HTTP 200 text/html** (SPA fallback), not 404. A client must check the content type.
- **Variation Normalizer** (`normalize.cancervariants.org/variation/`) is live, v0.17.1, VRS 2.0.1, UTA 20241220. `normalize?q=BRAF V600E` returns a VRS Allele `ga4gh:VA.j4XnsLZcdzDIYa5pvvXM7t1wn9OITr0L` (protein-level). The gene, therapy and disease normalizers redirect with 307 to their docs.

### 2.3 License
- Code is MIT. Data is a union of upstream licenses: CIViC CC0, MOA (`vanallenlab/moalmanac-db`) **CC BY 4.0**, FDA PODA public, cBioPortal/MCI pilot data (unchecked). There is no single MetaKB data license statement and no ToS on the pediatric instance.
- Verdict: the aggregated content is **compatible** (CC0 + CC BY with attribution). The problem is **operational, not legal**: no SLA'd public endpoint, an alpha API, and a hostname scoped to pediatrics.

### 2.4 Fleet fit
- Proxying `pediatric.metakb.org` from a public router is not advisable: alpha API, megabyte responses, unknown stability or rate limits. Self-hosting means Neo4j, three normalizers, UTA (Postgres) and SeqRepo (~10s of GB), which is far above the fleet's container/snapshot pattern.
- Better: get the *value* MetaKB adds through other routes. (a) `civic-link` covers the CIViC portion. (b) A small **`moa-link`** (MOAlmanac, CC BY 4.0, has a public API) could be assessed separately. (c) Emit or accept **GA4GH VRS IDs** in civic-link by calling the variation normalizer as an optional join key.
- If built anyway (later), the tool sketch would be: `search_statements` (variation/gene/disease/therapy), `get_statement`, `normalize_variation` (VRS), `search_statements_batch`. Join keys: VRS allele ID, NCIt, RxCUI, OncoTree, HGNC.

### 2.5 Scientific value
- Its distinctive feature is **cross-knowledgebase harmonization** in GA4GH VA-Spec/VRS form, giving one normalized evidence-strength scale across CIViC, MOA and FDA labels. That is valuable for "do the knowledgebases agree on X?" questions. Most of the underlying content is CIViC.
- Citation: Wagner AH et al., "A harmonized meta-knowledgebase of clinical interpretations of somatic genomic variants in cancer", *Nat Genet* 2020;52:448, **PMID 32246132**. No v2 database paper found.

### 2.6 Verdict
**PARK (access/maturity), P3. Re-assess when MetaKB v2 reaches a stable release with a general public endpoint.**
- Meanwhile, use the variation normalizer as an optional VRS join inside civic-link (S effort), and evaluate MOAlmanac as a separate candidate.
- Risks: alpha API churn, 1.5 MB default responses, SPA-200 on bad paths, a pediatric-scoped host, heavy self-host dependencies.

---

## 3. Cancer Genome Interpreter (CGI), cancergenomeinterpreter.org (IRB Barcelona, BBGLab)

### 3.1 What it is and current state
- A tumor-variant interpretation tool. It flags known oncogenic alterations, predicts drivers for VUS (BoostDM, OncodriveMUT; now an "Automatic Learning Platform": IntOGen, BoostDM, RankDM, RulesDM) and matches biomarkers of drug response.
- **The new CGI-Clinics Platform (launched April 2026)** lives at `platform.cgiclinics.eu`. It is a JS SPA with user accounts and tiered plans:
  - "CGI Open": academic, 30 analyses/month, 1 user, 60-day storage
  - "CGI Teams": hospitals, licensed
  - "CGI Industry": commercial, licensed
- **The legacy CGI is now `/2021/`**, and on it *"new analysis submissions are disabled ... Existing analysis runs remain available for view-only access."*
- Static legacy datasets, still downloadable without login:

  | Dataset | Last update | File | Size |
  |---|---|---|---|
  | **Cancer Biomarkers database** | **2022-10-17** | `/2021/data/biomarkers/cgi_biomarkers_latest.tsv` (HTTP 200) | 270 kB, 1,169 rows, 20 cols |
  | Catalog of Validated Oncogenic Mutations | 2018-01-30 | | |
  | Catalog of Cancer Genes | 2017-05-01 | | |
  | Cancer Bioactivities | 2016-06-18 | | |

  - Biomarker columns: Biomarker, Gene, Alteration type, Alteration, Drug status, Drug, Association, Evidence level, Source, Curator, Primary Tumor type, …
  - Evidence-level mix: pre-clinical 418, early trials 270, case report 227, FDA 165, late trials 38, NCCN 37, ELN 4, CPIC 3.
  - BRAF appears in 88 rows, e.g. `BRAF (V600E) | Approved | Encorafenib;Cetuximab | Responsive | FDA guidelines | COREAD`.
  - The DB is **frozen since 2022** and its tumor types use CGI's own acronyms (CM, COREAD, NSCLC, TH).

### 3.2 Access (probed)
- `www.cancergenomeinterpreter.org/*` returns the same 1,404-byte SPA shell for every path, including `/api`. There is no public JSON API on the new site.
- The legacy REST API `https://www.cancergenomeinterpreter.org/2021/api/v1` returns **HTTP 400** `{"message":"Missing user email or token. Please sign up ... and request a token"}`. It is a **job-submission** API: POST mutation/CNA/translocation files, poll, fetch results. It is not a per-variant lookup API, and per the legacy page it is now disabled for new jobs.
- The new platform API is behind login with per-user quotas (not probed; needs an account).
- BoostDM: precomputed in-silico saturation-mutagenesis predictions are browsable at `intogen.org/boostdm`. The download is behind a license-acceptance form. IntOGen driver/cohort releases are at `intogen.org/download` (latest `IntOGen-Drivers-20240920`).

### 3.3 License
- **Cancer Biomarkers DB: CC0 1.0.** From the biomarkers page: *"This database is licensed under a Creative Commons Public Domain Dedication (CC0 1.0 Universal)."* Compatible.
- Validated Oncogenic Mutations catalog: mixed. It is *"licensed under ... DoCM license, ClinVar license, **OncoKB license**, IARC license and Cancer Biomarkers database license"*. The OncoKB component makes it non-redistributable. Blocker.
- **CGI Tool / API / platform: blocker.** From the Terms: *"Users will obtain a non-exclusive, non-sublicensable and non-transferable license to use it solely for academic or internal scientific research purposes (in any case, for non-commercial purposes)"* and *"Users are not allowed ... to: Make available, copy, reproduce, disseminate, distribute, publish, sublicense, resell, transfer ... to third parties the use of the software of the CGI Tool"*. Passwords and tokens are *"strictly personal ... non-transferable"*. Re-exposing CGI through a public router with a shared service token violates all three clauses.
- **BoostDM / IntOGen predictions: CC BY-NC 4.0** (the intogen.org license badge; the download needs click-through acceptance). Non-commercial use is acceptable for a research-use fleet, but the click-through and NC terms make it at best self-host/opt-in. It cannot ship as a default public backend without counsel's sign-off.

### 3.4 Fleet fit
- No live-proxy option: the API is job-based, per-user and non-transferable.
- The only clean asset is the **CC0 Biomarkers TSV** (270 kB, frozen 2022). It is too small and stale to justify its own backend. Better: (a) **EXTEND `civic-link`** with an optional `search_biomarkers` tool over the baked-in CGI biomarker TSV, stamped `data_version=cgi-biomarkers-2022-10-17`; or (b) ingest it in a future aggregated `cancer-actionability` backend; or (c) leave it out, since CIViC/MOA cover most of it with fresher evidence.
- BoostDM: a possible future `boostdm-link` (snapshot of per-gene, per-tumor-type predictions, ~hundreds of genes). It would be self-host-only and `enabled:false` by default because of CC BY-NC plus the click-through. It joins on gnomAD-style chr-pos-ref-alt (GRCh38) and HGNC. It overlaps conceptually with fleet `spliceai`/`metadome`/`mavedb`/AlphaMissense-style predictors but is cancer- and tumor-type-specific.

### 3.5 Scientific value
- BoostDM answers "is this VUS in a driver gene likely a cancer driver *in this tumor type*?". Tumor-type-specific, explainable (SHAP) driver prediction is something the fleet lacks entirely.
- The biomarkers DB covers drug-response biomarkers including drug-family and pre-clinical evidence.
- Citations:
  - Tamborero D et al., *Genome Med* 2018;10:25, **PMID 29592813** (canonical CGI)
  - Muiños F et al., "In silico saturation mutagenesis of cancer genes", *Nature* 2021;596:428, **PMID 34321661**, doi 10.1038/s41586-021-03771-1 (BoostDM)
  - Tamborero D et al., MTB Portal, *Nat Cancer* 2022, PMID 35221333 (related)

### 3.6 Verdict
- **CGI tool / API: PARK (license). Never proxy.**
- **CGI Biomarkers DB: EXTEND civic-link (optional tool), P2, effort S.**
- **BoostDM: PARK, P3.** Revisit only as a self-host / disabled-by-default snapshot after the CC BY-NC click-through terms are reviewed.
- Risks: personal non-transferable tokens; quota-limited accounts; frozen 2022 data; the OncoKB-tainted mutation catalog.

---

## 4. ClinGen/CGC/VICC somatic oncogenicity SOP (Horak et al. 2022)

- **Horak P et al.**, *Genet Med* 2022;24(5):986–998, **PMID 35101336**. Erratum PMID 36063163; correspondence PMID 35779071.
- It defines a points-based **oncogenicity** classification (Oncogenic / Likely oncogenic / VUS / Likely benign / Benign) with codes OVS1, OS1–3, OM1–4, OP1–4, SBVS1, SBS1–2, SBP1. This parallels ACMG/AMP for germline but is separate from AMP/ASCO/CAP 2017 **clinical-significance** tiering (actionability).
- Relevance to the fleet:
  1. **CIViC already implements it.** Since 2023, CIViC Oncogenic evidence items and Assertions carry Horak codes. Live: 122 oncogenic assertions and 160 accepted oncogenic EIDs. So civic-link should expose `assertion_type=ONCOGENIC` plus the codes, giving the fleet expert-curated oncogenicity calls.
  2. Many Horak criteria can be **assembled from existing backends**:

     | Horak criterion | Existing source |
     |---|---|
     | OVS1 null variant in a tumor suppressor | autopvs1 / vep |
     | SBVS1 / SBS1 population frequency | gnomad |
     | OS2/OM functional studies | mavedb |
     | OP computational predictors | vep, spliceai, metadome |
     | OS3/OM3 hotspot counts | needs a cancer hotspot source (cancerhotspots.org / COSMIC / GENIE): **the current gap** |

     This argues for a **ROUTER-SKILL** ("somatic oncogenicity worksheet") that orchestrates these, analogous to the germline ACMG orchestration layer on the roadmap. It is not a backend.
  3. Keep the two axes separate in tool output: **oncogenicity (Horak 2022)** versus **actionability tier (AMP/ASCO/CAP 2017, Li et al.)**. CIViC carries both, in `assertionType` and `ampLevel`.
- Verdict: **ROUTER-SKILL, P1, effort M**, after civic-link exists. Missing input: a hotspot-frequency source (assessed elsewhere).

---

## 5. Summary table

| Resource | Verdict | Pri | Effort | License verdict |
|---|---|---|---|---|
| CIViC | BUILD `civic-link` (live GraphQL + monthly release stamp) | P0 | M | CC0 data, MIT code: compatible |
| MetaKB v2 | PARK (alpha, only pediatric.metakb.org; v1 retired, 504) | P3 | (L if self-hosted) | MIT + CC0/CC BY sources: compatible, operationally unready |
| VICC variation normalizer | Optional VRS join inside civic-link | P2 | S | MIT, live |
| CGI tool/API | PARK, never proxy | — | — | Non-transferable, non-commercial, no third-party redistribution: blocker |
| CGI Biomarkers DB | EXTEND civic-link (optional tool) | P2 | S | CC0 (frozen 2022-10-17): compatible |
| CGI Validated Oncogenic Mutations | SKIP | — | — | Includes OncoKB license: blocker |
| BoostDM | PARK (self-host, disabled by default) | P3 | M | CC BY-NC 4.0 + click-through |
| Horak 2022 SOP | ROUTER-SKILL somatic-oncogenicity worksheet | P1 | M | n/a (guideline) |

Not checked: the login-gated CGI-Clinics platform API docs; MOAlmanac API (worth its own assessment); cBioPortal/MCI data terms inside MetaKB; CIViC server-side rate limits under load (none advertised).
