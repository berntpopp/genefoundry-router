# C — cBioPortal, AACR GENIE, OncoTree, Cancer Hotspots

Assessor: cancer-genomics informatics pass, 2026-11-20. All probes are live `curl -s -m 20` from this host on 2026-11-20.
No repos were modified. (Raw probe outputs are in the same scratchpad dir: `studies.json`, `braf*.json`, `mg.json`,
`pc.json`, `oa3.json`, `ot.json`, `hs_all.json`.)

---

## 1. cBioPortal public instance (www.cbioportal.org)

### 1.1 What it is
- Open-source cancer-genomics portal hosted by MSKCC. It holds harmonised somatic mutation, CNA, SV, expression and clinical
  data for public studies: TCGA PanCancer Atlas (32 studies `*_tcga_pan_can_atlas_2018`), TCGA Firehose Legacy, MSK-IMPACT,
  MSK-CHORD, CPTAC, cell lines, and others.
- Live size: **553 studies, 415,070 samples** (`GET /api/studies?projection=SUMMARY`, 351 kB).
- `/api/info`: `portalVersion v7.2.3`, `dbVersion 3.1.0`, `geneTableVersion hgnc_v7_2025.10.7`, `genesetVersion msigdb_v2025.1.Hs`.
  The backend now reports `goRewriteVersion v0.1.3`: the API is being re-implemented in Go (`go.cbioportal.org`, also 200),
  with Java-parity tests.
- Curation model: the cBioPortal team curates and harmonises data into the "datahub". Studies are added continuously, with
  no versioned release of the corpus. Each study has an `importDate` (e.g. msk_chord_2024 `2026-07-30`).
  => **There is no immutable data version.** The only usable `data_version` stamps are `/api/info` plus the per-study `importDate`.

### 1.2 Access (probed)
- REST + OpenAPI **3.1.0** at `/api/v3/api-docs` (203 kB, 142 paths). Swagger 2 is at `/api/v2/api-docs`. The spec title says
  "Public API [Alpha]… beta and subject to change". Spec license field: Apache-2.0, which covers the API spec, not the data.
- No auth on the public instance. Cloudflare fronts it (`server: cloudflare`, `cf-ray`). `robots.txt` = `Disallow: /`
  (it targets crawlers, but signals that they do not want automated sweeping). **No rate limit is documented.**
  15 back-to-back requests all returned 200 with no 429. The upstream commit message in `/api/info` says Cloudflare added a
  bot-detection challenge script to `go.cbioportal.org` Swagger HTML on 2026-11-14. The JSON API is unaffected today, but the
  risk is rising.
- Page size: `pageSize` max = **10,000,000** (`pageSize=10000001` → 400 `"pageSize must be less than or equal to 10000000"`).
  In practice there is no server-side cap, so **the link must cap pages itself**.
- `projection=SUMMARY|DETAILED|META`. META returns only the `total-count` header, which suits honest `total` reporting.
- Unknown IDs return a clean 404 JSON (`{"message":"Study not found: NOPE_xyz"}`, `{"message":"Gene not found: NOTAGENE1"}`).

| Probe | Result |
|---|---|
| `GET /api/genes/BRAF` | 200 `{"entrezGeneId":673,"hugoGeneSymbol":"BRAF","type":"protein-coding"}` |
| `GET /api/molecular-profiles/skcm_tcga_pan_can_atlas_2018_mutations/mutations?sampleListId=skcm_tcga_pan_can_atlas_2018_all&entrezGeneId=673` | 200, 0.25 s, **245 mutations**; top: V600E 158, V600K 35, K601E 5, V600R 4. Record has chr/start/ref/alt (**GRCh37**), `proteinChange`, `mutationType`, `refseqMrnaId NM_004333.4`, tumor/normal alt/ref counts, `keyword` |
| `POST /api/molecular-profiles/{id}/mutations/fetch?projection=DETAILED` `{"sampleListId":…,"entrezGeneIds":[673]}` | 200, same 245, adds nested `gene` |
| `POST /api/mutations/fetch` across all 32 PanCan profiles, BRAF | 200, 0.37 s, 603 kB, **777 mutations** (V600E 510); `projection=META` → `total-count: 777` |
| `POST /api/mutated-genes/fetch` `{"studyIds":["skcm_tcga_pan_can_atlas_2018"]}` | 200, 0.8 s, **4.7 MB, 18,252 genes**. BRAF: `numberOfAlteredCases 233 / numberOfProfiledCases 440` (53%); NRAS 125/440. The endpoint has no gene filter in the body, so it is a **heavy** call |
| `POST /api/mutation-counts-by-position/fetch` `[{"entrezGeneId":673,"proteinPosStart":600,"proteinPosEnd":600}]` | 200 `count: 9070` (portal-wide, with overlapping studies double-counted) |
| `GET /api/studies/skcm_tcga_pan_can_atlas_2018/significantly-mutated-genes` | 200 `[]` (MutSig not loaded for PanCan, so it cannot be relied on) |
| `POST /api/alteration-counts/fetch` | **404**: removed from this version. Alteration frequency now comes from `mutated-genes/fetch`, `cna-genes/fetch`, `structuralvariant-genes/fetch` (StudyView filter endpoints) |
| `GET /api/cancer-types/mel` | 200 `{"cancerTypeId":"mel","name":"Melanoma","parent":"skin"}` (OncoTree-derived lowercase codes) |

**Heavy and POST-filter endpoints.** Almost all analytic endpoints are `POST …/fetch` with a JSON filter body:
`mutations/fetch`, `mutated-genes/fetch`, `cna-genes/fetch`, `clinical-data/fetch`, `molecular-data/fetch`,
`alteration-enrichments/fetch`, `survival-data/fetch`, `copy-number-segments/fetch`.
- StudyView `*-genes/fetch` calls return the whole gene table (MBs). A link must post-filter and cap.
- `molecular-data` (expression matrices), `copy-number-segments` and `clinical-data` without a filter are unbounded. Do not expose them.
- `data-access-tokens` endpoints exist, but only for authenticated instances.

### 1.3 License / Terms
- **Software:** AGPL-3.0, which does not matter for an HTTP client.
- **Data (FAQ, docs.cbioportal.org/user-guide/faq):** "Unless otherwise noted, data in cBioPortal are available under the
  ODC Open Database License … with no restrictions on the use of the data, as long as you properly give attribution to the
  original studies." The FAQ also says some studies restrict commercial use, and that those restrictions are stated in the
  study information.
- **Per-study exceptions are real and machine-findable only as free text.** 9/553 study descriptions mention licence or
  commercial restrictions: asclc_msk_2024, hcc_msk_2024, msk_ctdna_vte_2024, msk_met_2021, pancreas_msk_2024,
  panet_msk_2018, prostate_msk_2024, msk_chord_2024, msk_impact_50k_2026. Example from `/api/studies/msk_chord_2024`:
  *"This data is available under the Creative Commons BY-NC-ND 4.0 license. For commercial use, please contact
  datarequests@mskcc.org"*.
- **ToS on programmatic load:** none published. The API docs list recommended clients (cBioPortalData, cbioportalR,
  bravado, gget), which implies programmatic use is expected. A mailing-list maintainer statement says the team "only
  re-distribute[s] the data" and per-dataset licences must be checked.
- **Verdict: COMPATIBLE with conditions.** ODbL plus attribution permits re-exposure through a public research-use router.
  The link must:
  - (a) surface each study's `citation`/`pmid` and the licence text extracted from `description` in every response;
  - (b) mark or optionally deny NC/ND studies with a config denylist, defaulting to "flag, don't block", because the fleet is
    research-use only;
  - (c) be polite: cache, single-flight, low concurrency, honest UA. The ODbL share-alike clause applies only if the link
    builds a derived *database*; a live proxy returning query results is a "Produced Work" (attribution only).

### 1.4 Upstream MCP efforts (do not duplicate blindly)
- **cBioPortal/cbioportal-mcp** (official, Apache-2.0, 17★, pushed 2026-11-19) wraps `mcp-clickhouse`. It does free-form SQL
  against a *cloned, LLM-prepped ClickHouse copy* of the cBioPortal DB and needs DB credentials. It is **hosted at
  `https://mcp.cbioportal.org/db/mcp`**, Streamable HTTP, and requires **Google sign-in OAuth**. The probe returned 401 with
  `resource_metadata=…/.well-known/oauth-protected-resource/db/mcp`.
  => It cannot be federated by the router: per-user Google OAuth, and the fleet forbids per-user token passthrough.
  It also cannot be self-hosted without a ClickHouse clone of the DB.
- **cBioPortal/cbioportal-navigator** (TypeScript, no licence, 4★) turns natural language into cBioPortal URLs. It returns
  navigation, not data.
- **cBioPortal/cbioportal-mcp-qa** is a public chat benchmark. **It is directly reusable as an eval set for a fleet
  cbioportal-link**, which is a strong reason to build against it.
- Community servers: pickleton89/cbioportal-mcp (MIT, 6★, stale since 2025-07), openpharma-org, jjgao, QuentinCody,
  pipeworx. None follows fleet standards.
- **Conclusion:** none of these can sit behind the router. A fleet-standard `cbioportal-link` over the public REST API is
  justified. Keep it to a narrow curated surface (frequency, mutations, studies), not a SQL agent. Point users who want deep
  ad-hoc analysis to the official MCP in the link's docs and resources.

### 1.5 Fleet fit — live-proxy (template: pgscatalog-link / ucsc-link)
The corpus is too big and too fluid to snapshot. Proposed tools (v1.1 names):

| Tool | Purpose |
|---|---|
| `search_studies` | Keyword/cancer-type (OncoTree code) search over the 553 studies → studyId, name, sample count, citation, licence flag |
| `get_study` | One study: samples, molecular profiles, citation/PMID, extracted licence text, importDate |
| `get_gene_alteration_frequency` | Gene(s) × study(ies) → altered/profiled cases (mutation, + CNA/SV), via `mutated-genes`/`cna-genes` filtered server-side or post-filtered and capped |
| `get_gene_mutations` | Mutations for gene(s) in a study or study set (default: PanCan Atlas). Counts by proteinChange, plus capped records with genomic coords (GRCh37 flagged) |
| `get_mutation_position_counts` | Gene + protein position → recurrence count (hotspot-style check, e.g. BRAF 600) |
| `list_cancer_types` | cBioPortal cancer-type tree (OncoTree-derived), optional parent filter |
| (optional) `get_study_clinical_summary` | Bounded clinical attribute counts for a study (no patient-level rows) |

- **Entry-point resolver:** `search_studies`, with gene resolution via HGNC symbol → Entrez. `/api/genes/{symbol}` returns the
  Entrez ID, but HGNC-link should be the canonical resolver.
- **Join keys:** HGNC symbol and Entrez (hgnc); protein change (vep HGVS p.); GRCh37 coords (needs liftover note against
  the GRCh38-centric gnomad/vep); OncoTree code (oncotree); PMID (pubtator/litvar).
- **Guards:** server-side `pageSize` cap (the upstream allows 10M); never expose `molecular-data`/`segments`/unfiltered
  `clinical-data`; no patient-level clinical rows. These are public de-identified data, but they are row-level, and the
  token budget alone rules them out.
- **Overlap:** GENIE (same API shape, inaccessible); Cancer Hotspots (`mutation-counts-by-position` is a weak substitute);
  COSMIC (licensed, not public).

### 1.6 Scientific value
It is the only fleet source for **somatic prevalence by cancer type**. Example questions it answers:
- "How often is BRAF mutated in TCGA melanoma?": 233/440 = 53%; V600E 158/245 mutations.
- "Which tumour types carry KRAS G12D most often?"
- "Is this VUS seen recurrently in tumours?"
- "Which studies have IDH1-mutant gliomas with survival data?"

It complements the germline-centric fleet (gnomad/clinvar) for the somatic/germline distinction.

Citations:
- Cerami E et al., Cancer Discov 2012, PMID 22588877 (canonical; PMID from memory, not re-verified here).
- Gao J et al., Sci Signal 2013, PMID 23550210 (verified via pubtator).
- de Bruijn I et al., Cancer Res 2023, PMID 37668528 (verified; latest portal paper, GENIE BPC).

### 1.7 Verdict
**BUILD `cbioportal-link`: P1, effort M.** Live proxy over public REST, 6-7 tools, pgscatalog-link template.

Risks:
1. No immutable data version (stamp `/api/info` + `importDate`).
2. Undocumented load tolerance, Cloudflare in front, and newly added bot detection on go.cbioportal.org. Mitigate with
   cache, low concurrency, and an optional self-hosted cBioPortal base URL.
3. The API is labelled beta and is mid Java→Go rewrite. `alteration-counts/fetch` already 404s, so pin a contract test.
4. GRCh37 coordinates versus the GRCh38 fleet.
5. Per-study NC/ND licences are free text only.

---

## 2. AACR Project GENIE (genie.cbioportal.org + Synapse)

### 2.1 What it is
International real-world clinical-sequencing registry from ~19+ cancer centres, mostly targeted panels, with OncoTree-coded
tumour types. Current public release is **v19.0-public: 271,837 samples / 227,696 patients**, adding cfDNA samples
(aacr.org GENIE data page). Releases are versioned and roughly twice a year. GENIE BPC (Biopharma Collaborative) adds curated
clinical outcomes.

### 2.2 Access (probed)
- `GET https://genie.cbioportal.org/api/studies` → **401**; `/api/info` → **401**; root → **302 → /login**.
- Synapse download (syn7222066 lineage) requires a Synapse account plus per-release terms acceptance. Some releases need a
  consortium membership or an approved concept sheet.

### 2.3 Terms (decisive clauses)
- The aacr.org GENIE page says users "will need to create an account for either site and agree to the terms of access."
- The GENIE Data Guide (11.0/16.0-public) states: "All users of the AACR Project GENIE data must agree to the following
  terms of use; failure to abide by any term herein will result in revocation of access", and:
  **"Users will not redistribute the data without express written permission from the AACR Project GENIE Coordinating
  Center (genieinfo@aacr.org)."** It also contains a clause against attempting to identify or contact participants.
- **Verdict: BLOCKER for a public router.** Access is per-user, behind registration and click-through terms, and
  redistribution is barred without written permission. A shared service credential re-exposing GENIE to anonymous router
  users is exactly third-party redistribution.

### 2.4 Fleet fit / verdict
**PARK, P3.** Pick it up only if:
- (a) AACR grants written permission for aggregate-only re-exposure, e.g. gene × OncoTree frequency tables (worth asking:
  My Cancer Genome publishes GENIE-derived prevalence, so a precedent exists); or
- (b) a self-host, disabled-by-default deployment where each operator holds their own GENIE agreement.

If it ever ships, it should be a `source=genie` configuration of `cbioportal-link` (same API), not a new repo.

Citations:
- AACR Project GENIE Consortium, Cancer Discov 2017, PMID 28572459.
- Pugh TJ et al., "AACR Project GENIE: 100,000 Cases and Beyond", Cancer Discov 2022, PMID 35819403 (both verified via pubtator).

---

## 3. OncoTree (oncotree.mskcc.org = oncotree.info)

### 3.1 What it is
MSK/cBioPortal cancer-type classification tree: tissue → main type → subtypes. **897 nodes** in `oncotree_latest_stable`
(release **2025-10-03**; previous 2025-04-08; `oncotree_candidate_release` and `oncotree_development` also exposed).
It is the cancer-type vocabulary used by cBioPortal, GENIE, OncoKB and MSK-IMPACT, so the codes are the native join key for
everything cancer-side.

### 3.2 Access (probed)
| Probe | Result |
|---|---|
| `GET /api/tumorTypes` (both hosts) | 200, 240 kB, ~1 s, 897 nodes. Node: `{"code":"MEL","name":"Melanoma","mainType":"Melanoma","tissue":"Skin","parent":"SKIN","level":2,"externalReferences":{"UMLS":["C0025202"],"NCI":["C3224"]},"history":[],"revocations":[],"precursors":[]}` |
| `GET /api/tumorTypes/search/code/SKCM?exactMatch=true` | 200 → SKCM "Cutaneous Melanoma", parent MEL, UMLS C0151779, NCI C3510 |
| `GET /api/tumorTypes/search/name/melanoma?exactMatch=false` | 200 → OM, … (substring name search) |
| unknown code `ZZZZ` | **404, plain text** "No tumor types found matching query" (not JSON) |
| `GET /api/versions` | 200, version list with release dates |
| `GET /api/tumorTypes/tree` | 200, 246 kB nested tree |
| `GET /api/mainTypes` | 200 |
| `/api/crosswalk` | 404 (the older crosswalk/mapping service is not at this path) |

Xref coverage: **691/897 have UMLS, 665/897 have NCIt**. There are no MONDO, DOID or ICD-O xrefs in OncoTree itself.
`history`/`revocations`/`precursors` support code migration across releases. The OncoTree Mapping Tool is in the GitHub repo.

### 3.3 License
**CC BY 4.0** (README: "This work is licensed under a Creative Commons Attribution 4.0 International License"; GitHub licence
`CC-BY-4.0`). **Fully compatible**: snapshot, redistribute and re-expose with attribution.

### 3.4 MONDO already carries OncoTree
Checked `/home/bernt-popp/development/mondo-link`, commit d16dda6, local `data/mondo.sqlite`:
- The `xref` table has **558 `ONCOTREE:` xrefs** (all `equivalentTo`, origin `obo_xref`), e.g. `ONCOTREE:SKCM → MONDO:0005012`,
  `ONCOTREE:MEL → MONDO:0005105`, `ONCOTREE:LUAD → MONDO:0005061`, `ONCOTREE:NSCLC → MONDO:0005233`. The OBO has 1,035
  ONCOTREE mentions in total, including synonyms sourced from ONCOTREE. There are also **14,758 NCIT** and 769 ICDO xrefs.
- `MondoService.resolve_xref` (`mondo_link/services/mondo_service.py:399`) has no prefix whitelist. `normalize_xref` passes
  unknown prefixes through uppercased, so **`resolve_xref("ONCOTREE:SKCM")` should already return MONDO:0005012 today**.
  This was inferred from the code and DB; it was not called live, because the mondo-link MCP was unreachable from this session.
- Gap: `ONCOTREE` is not in the first-class `XrefPrefix` Literal (`mondo_link/constants.py:32`). The `prefixes=[…]` filter
  on the xref/mapping tools therefore rejects `"ONCOTREE"` with invalid_input. `_KNOWN_PREFIX_ALIASES` also lacks it.
  Coverage is about 558/897 = 62% of OncoTree nodes; NCIt bridging covers more.

### 3.5 Where it belongs
- **Not its own link.** 897 nodes is a single ~240 kB table with ~3 useful operations, which is under the cost of a repo plus
  container plus release pipeline.
- **Extend `mondo-link` (P1, effort S)** as the normalization backbone:
  - (a) add `ONCOTREE` (and `ICDO`) to the first-class `XrefPrefix` and aliases. This is a one-line Literal change plus a test,
    which unlocks MONDO → OncoTree forward mapping through the existing xref tool;
  - (b) optionally vendor the OncoTree stable snapshot (CC BY 4.0, versioned by `release_date`, so it fits the immutable
    snapshot rule) to add one tool, `resolve_oncotree_code`: code or name → node, parent chain, UMLS/NCIt, MONDO equivalent
    (direct xref, else via NCIt). This covers the remaining ~38% unmapped codes.
- `cbioportal-link` should accept OncoTree codes as its cancer-type filter, because cBioPortal `cancerTypeId` values are
  lowercase OncoTree codes. It should defer normalization to mondo.
- Agent questions: "map 'cutaneous melanoma' to the OncoTree code used by cBioPortal/OncoKB"; "what is the MONDO ID for
  OncoTree LUAD"; "list subtypes under NSCLC".

Citation: Kundra R et al., "OncoTree: A Cancer Classification System for Precision Oncology", JCO Clin Cancer Inform 2021,
PMID 33625877 (verified via pubtator).

### 3.6 Verdict
**EXTEND `mondo-link`: P1, effort S.** Add ONCOTREE as a first-class xref prefix now (near-zero effort). Optionally add an
OncoTree snapshot plus `resolve_oncotree_code`.

Risks:
- MONDO↔OncoTree coverage is incomplete (62%).
- OncoTree codes get revoked or renamed across releases. Use `history`/`revocations`, stamp `oncotree_version`.
- The 404 for an unknown code is plain text (irrelevant if snapshotted).

---

## 4. Cancer Hotspots (cancerhotspots.org / 3dhotspots.org)

### 4.1 What it is
Statistically significant recurrent somatic mutation positions:
- **Single-residue + in-frame indel hotspots v2**: 24,592 tumours (Chang et al. 2018). Live API returns **1,165 hotspots**
  (1,110 single residue + 55 in-frame indel).
- **3D hotspots**: mutation clusters in protein structures (Gao et al. 2017).

The dataset is **static** (v2, ~2017/2018; a "v3" with 164 extra hotspots is referenced by third parties). There is no
update cadence. It is widely used: OncoKB, Genome Nexus, cBioPortal annotations, Illumina Nirvana.

### 4.2 Access (probed)
| Probe | Result |
|---|---|
| `GET https://www.cancerhotspots.org/api/hotspots/single` | 200, 478 kB, 1,165 records |
| `GET /api/hotspots/single/byGene/KRAS` | 200 → `[{"hugoSymbol":"KRAS","residue":"Q61","tumorCount":190,"transcriptId":"ENST00000256078","qValue":"0","qValueCancerType":"4.66e-165","type":"single residue","variantAminoAcid":{"R":37,"H":…},"tumorTypeComposition":{…}}, …]` |
| BRAF V600 (from full list) | `tumorCount 897`, `variantAminoAcid {E:833, M:29, K:24, R:4, V:4, G:3}`, `tumorTypeComposition {skin:357, thyroid:316, bowel:113, lung:33, …}`, `qValue 0`, transcript ENST00000288602 |
| `/api/hotspots/single/byGene/NOTAGENE1` | **200 `[]`** (an unknown gene is indistinguishable from "no hotspots", so the link must validate the gene via HGNC first) |
| `/api/hotspots/single/BRAF`, `/api/variants/KRAS` | 404 (wrong paths) |
| **`/api/hotspots/3d`, `/api/hotspots/3d/byGene/KRAS`** | **500 `IllegalStateException: Input not found`**: 3D is broken on cancerhotspots.org |
| `https://www.3dhotspots.org/api/hotspots/3d` | 200 (3D lives here) |
| Genome Nexus `GET https://www.genomenexus.org/cancer_hotspots/hgvs/7:g.140453136A>T` | 200 → BRAF V600 `"type":"single residue","tumorCount":897` **and** `"type":"3d","tumorCount":545`, `version v2` |

No auth, no documented rate limit, no OpenAPI (`/v2/api-docs` 404; `swagger-ui.html` 200). No `robots.txt`.

### 4.3 License
- Code: **AGPL-3.0** (cBioPortal/cancerhotspots; irrelevant for an HTTP client or a data snapshot).
- **Data: no explicit licence found** on the site, the repo or in search. The data are a published supplement of
  Chang 2018 (AACR journal) and are freely downloadable as `hotspots_v2.xls`. Commercial annotators (Illumina Nirvana,
  Genomics England) redistribute them.
- **Verdict: LIKELY COMPATIBLE but unstated.** A static fact table of positions and counts from a public paper is low risk
  for research use. Attribute it, and record "licence not stated" in the resource metadata. Ask the cBioPortal/MSK team
  for an explicit statement before GA.

### 4.4 Fleet fit
1,165 rows (~0.5 MB), static. **Snapshot pattern, not live**, because the live 3D endpoint is already broken: vendor the
single+indel table plus the 3D table (from 3dhotspots.org or Genome Nexus), stamped `hotspots_v2`.

It is too small for its own link. Options, in order of preference:
- **(a) Fold into `cbioportal-link`** as `get_mutation_hotspots` (gene and/or residue → hotspot calls, q-values,
  tumour-type composition, 3D cluster membership). It is the same MSK/cBioPortal family, with the same join keys and the
  same agent workflow ("is this somatic variant recurrent?").
- (b) Ship it as part of a future OncoKB/somatic-annotation link if one is built (OncoKB already consumes hotspots).
- Genome Nexus (`/cancer_hotspots/hgvs/…`) gives variant-keyed lookup, but adds a third live dependency. Snapshotting is cleaner.

Join keys: HGNC symbol, Ensembl transcript (ENST, matches VEP canonical/MANE mostly; BRAF ENST00000288602 = MANE),
protein residue (VEP HGVS p.).

Agent question it uniquely answers: "Is KRAS Q61H / BRAF V600 / a novel residue a statistically significant cancer
hotspot, in which tumour types, and is it part of a 3D cluster?" This feeds somatic oncogenicity (AMP/ASCO/CAP, and the
ClinGen/CGC/VICC oncogenicity SOP OS3/OM3 "located in a hotspot"). There is no fleet equivalent today.

Citations:
- Chang MT et al., Nat Biotechnol 2016, PMID 26619011 (from memory, not re-verified).
- **Chang MT et al., "Accelerating Discovery of Functional Mutant Alleles in Cancer", Cancer Discov 2018, PMID 29247016**
  (from memory; the pubtator title search did not surface it).
- Gao J et al., "3D clusters of somatic mutations in cancer…", Genome Med 2017, PMID 28115009 (verified via pubtator).

### 4.5 Verdict
**EXTEND (fold into the new `cbioportal-link`) as one snapshot-backed tool: P1, effort S** (on top of cbioportal-link).

Risks:
- The data are frozen at v2 (2017/18).
- The data licence is unstated.
- The 3D endpoint on cancerhotspots.org returns 500. Source 3D from 3dhotspots.org or Genome Nexus.
- An unknown gene returns 200 `[]` (validate input).
- Transcript-relative residues need VEP/MANE alignment.

---

## Summary table

| Resource | Verdict | Pri | Effort | License verdict |
|---|---|---|---|---|
| cBioPortal public | BUILD `cbioportal-link` (live proxy, 6-7 tools) | P1 | M | ODbL + attribution: OK; flag 9 NC/ND studies (e.g. msk_chord_2024 CC BY-NC-ND) |
| AACR GENIE | PARK (or a future `source=genie` mode of cbioportal-link with written AACR permission / self-host) | P3 | — | BLOCKER: registration + "will not redistribute without express written permission" |
| OncoTree | EXTEND `mondo-link` (ONCOTREE first-class xref prefix; optional snapshot + `resolve_oncotree_code`) | P1 | S | CC BY 4.0: OK |
| Cancer Hotspots | EXTEND: fold into `cbioportal-link` as a snapshot-backed `get_mutation_hotspots` | P1 | S | Data licence unstated (code AGPL); likely OK for research, confirm with MSK |

Sources: docs.cbioportal.org/user-guide/faq/, docs.cbioportal.org/web-api-and-clients/, aacr.org GENIE data page,
GENIE data guides (aacr.org/wp-content/uploads/2022/03/GENIE_data_guide_11.0-public.pdf), github.com/cBioPortal/{cbioportal-mcp,
cbioportal-navigator, cbioportal-mcp-qa, oncotree, cancerhotspots}, groups.google.com/g/cbioportal/c/wf3x52voarY.
