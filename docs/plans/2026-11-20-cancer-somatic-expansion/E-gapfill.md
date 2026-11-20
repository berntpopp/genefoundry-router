# E — Gap-fill: cancer/somatic resources the user did not list

Agent E, 2026-11-20. All probes ran live with `curl -s -m 20` unless marked otherwise. Out of scope here (other agents cover them): CIViC, MetaKB, CGI, OncoKB, COSMIC, HSMD, CKB, PMKB, cBioPortal, GENIE, OncoTree, Cancer Hotspots, GDC/TCGA/ICGC/PCAWG.

## 0. Fleet baseline: does the fleet cover cancer today?

- `grep -ril somatic /home/bernt-popp/development/*-link/*/ --include=*.py` matches nothing in any `-link` repo. Every hit is under `apa-manuscript/`, `apa-sequencing/`, or `apa-p0-worktrees/`, which are unrelated analysis projects. **The fleet has no somatic or cancer code.**
- `clinvar-link` is the one exception, and its gap is latent. The data is already on disk but nobody reads it (see §1).

---

## 1. ClinVar somatic classifications (EXTEND clinvar-link) — **P0, effort S–M**

**What it is.** Since 2024, ClinVar stores three separate aggregate classifications per VCV:
- germline classification;
- **somatic clinical impact** (AMP/ASCO/CAP Tier I–IV, with an assertion type of therapeutic, diagnostic or prognostic, a significance such as "sensitivity/response", a drug, and a tumour type);
- **oncogenicity** (ClinGen/CGC/VICC 2022 SOP: Oncogenic, Likely oncogenic, VUS, Likely benign, Benign).

Citation: Landrum MJ et al., *ClinVar: updates to support classifications of both germline and somatic variants*, NAR 2025;53(D1):D1313, **PMID 39578691**.

**What clinvar-link does today (verified in source).**
- The local `/home/bernt-popp/development/clinvar-link/data/variant_summary.txt.gz` already carries the columns: 35 `SomaticClinicalImpact`, 36 `…LastEvaluated`, 37 `ReviewStatusClinicalImpact`, 38 `Oncogenicity`, 39 `OncogenicityLastEvaluated`, 40 `ReviewStatusOncogenicity`, and 41–43 `SCVsForAggregate{Germline,SomaticClinicalImpact,Oncogenicity}`.
- `clinvar_link/ingest/parsing.py` (~L539–600) reads only `ClinicalSignificance` (the germline column) plus `Origin`. **The somatic columns are dropped at ingest.**
- No MCP tool, model or service mentions somatic, oncogenicity or tier. The only trace is `models/variant_models.py:65`, where `origin` is documented as "e.g. 'germline'".
- So for any cancer hotspot, `clinvar_get_variant` today returns only the germline call. BRAF V600E comes back as "Conflicting classifications of pathogenicity", which actively misleads an oncology question.

**Measured somatic coverage** (GRCh38 rows of the local file, July 2026 snapshot):

| Field | Real classifications | "no classification for the single variant" |
|---|---|---|
| SomaticClinicalImpact | 1,307: Tier I 751, Tier II 423, Tier III 92, Tier IV 41 | 685 |
| Oncogenicity | 1,883: Likely oncogenic 979, Oncogenic 389, VUS 504, LB/B 8, conflicting 2 | 685 |

The set is small but high-value; it is essentially the CIViC/ClinGen somatic submissions.

**Live probe, BRAF V600E (VCV 13961):**
- Flat file: `Tier I - Strong | criteria provided, multiple submitters | Oncogenic | single submitter`.
- KRAS G12D (12582): Tier I / Oncogenic. TP53 R175H (12374): Tier I / Oncogenic.
- `esummary.fcgi?db=clinvar&id=13961&retmode=json` returned HTTP 200 with three objects: `germline_classification`, `clinical_impact_classification` (`description: "Tier I - Strong"`, `trait_set: [Melanoma, Diffuse midline glioma…, Ganglioglioma, …]`) and `oncogenicity_classification` (`Oncogenic`, `trait_set: [Neoplasm]`).
- `efetch.fcgi?db=clinvar&rettype=vcv&id=13961` **returns an empty `<ClinVarResult-Set><set/>`**. You must add `&is_variationid&from_esearch=true` to get the full 483 KB VCV XML.
- That XML has per-SCV detail: `<SomaticClinicalImpact ClinicalImpactAssertionType="therapeutic" ClinicalImpactClinicalSignificance="sensitivity/response" DrugForTherapeuticAssertion="Dabrafenib;Trametinib">Tier I - Strong`, plus diagnostic and prognostic ones, the aggregate `<SomaticClinicalImpact NumberOfSubmissions="21" NumberOfSubmitters="3">` and `<OncogenicityClassification …>`.

**What the extension needs** (no new upstream; the license is unchanged and public domain):
1. **Ingest (S).** Read columns 35–40 into new `variants` columns, e.g. `somatic_clinical_impact`, `somatic_impact_review_status`, `somatic_impact_last_evaluated`, `oncogenicity`, `oncogenicity_review_status`, `oncogenicity_last_evaluated`. Normalise the literal "no classification for the single variant" and "-" to null. Bump `data_version`.
2. **Per-SCV tumour type and drug (M).** These are not in the flat file. clinvar-link already streams the monthly `ClinVarVCVRelease_00-latest.xml.gz` in `ingest/submissions.py` (iterparse on `VariationArchive`). In that same pass, extract `ClinicalAssertion` elements of `SomaticClinicalImpact` type: assertion type, significance, drug(s), tumour trait (MedGen/MONDO xref), submitter, and SCV. Store them in a `somatic_assertion` table. A live esummary call is the fallback for one-off lookups, but the snapshot pattern is better.
3. **Tool surface.** Add `somatic_clinical_impact` and `oncogenicity` blocks to the existing `get_variant` output, gated by `response_mode` so compact stays small. Add one new tool, `search_somatic_classifications` (filter by gene, tier, oncogenicity, assertion type, drug, tumour), so the agent can ask "which ClinVar variants in EGFR are Tier I therapeutic". Also update `get_gene_summary`-style counts if they exist. That is +1 tool, well within budget.
4. **Contract fix.** Today's germline-only answer is a Contract-Truth hazard for oncology prompts. Even before step 2, labelling the existing field `germline_classification` would make it honest.

**Verdict.** EXTEND clinvar-link: **P0, S for flat columns, M for per-SCV tumour/drug**. This is the cheapest real cancer capability the fleet can ship, and it uses a pipeline it already has. Risks: coverage is small (~1.3k tiered variants); CIViC remains the deeper source.

---

## 2. DGIdb 5 (drug–gene interactions) — **BUILD `dgidb-link` (live proxy), P1, effort S**

**What it is.** Aggregates drug–gene interactions and druggable-gene categories from about 40 sources. GraphQL at `https://dgidb.org/api/graphql`, with no auth and no documented rate limit. The code is MIT (`dgidb/dgidb-v5`, pushed 2026-11-19). Source versions shown in the API are current, e.g. CIViC "01-September-2026" and Drugs@FDA 20260831. Citation: Cannon M et al., *DGIdb 5.0*, NAR 2024 (PMID 37953380 is recalled, not re-verified; PMC10767982 was verified via search).

**Probe.**
- `genes(names:["BRAF"])` returns HTTP 200: `conceptId hgnc:1097`, categories `[DRUGGABLE GENOME, KINASE, …, CLINICALLY ACTIONABLE, DRUG RESISTANCE]`, **255 interactions**. Top hits by `interactionScore`: vemurafenib 1.11 (inhibitor; 22 sources), dabrafenib 1.03, panitumumab, trametinib, cetuximab.
- Quirks: `interactions` is a plain list and takes no `first` argument, so you cannot paginate inside a gene; the backend must truncate itself and report total/truncated honestly. An unknown gene returns `{"nodes":[]}` with HTTP 200, so the link must turn that into a not-found that names the parameter.
- HGNC CURIEs (`hgnc:1097`) are the join key to `hgnc-link`.

**License: the decisive point.** DGIdb itself states no data license; it aggregates, and each source keeps its own terms. The API exposes this per source (`sources{sourceDbName license}`):
- **Permissive:** CIViC CC0, Drugs@FDA CC0, Wikidata CC0, NCI/FDA public domain, NCIt CC BY 4.0, DoCM CC BY 4.0, GO CC BY 4.0, Drug Repurposing Hub CC BY 4.0, ChEMBL CC BY-SA 3.0, GtoPdb/ClinPGx/Pharos/IDG CC BY-SA 4.0, MOAlmanac ODbL, HemOnc CC BY 3.0.
- **Restrictive:** OncoKB ("Restrictive, non-commercial"), MyCancerGenome (restrictive), COSMIC (non-commercial), CKB-CORE (CC BY-NC-SA), CGI (CC BY-NC), DTC (CC BY-NC-SA), DrugBank (non-commercial), Oncomine ("All rights reserved"), CancerCommons, several "Unknown" sources, and publisher-copyright supplementary tables.

**Verdict on license.** The fleet is public and research-only, so re-exposing NC sources is the same OncoKB/COSMIC question other agents are judging. The fix is cheap: **filter interactions server-side to an allowlist of permissively licensed sources** and stamp each interaction with its source and license. This is defensible. An unfiltered proxy is grey. Recommend allowlist by default.

**Fleet fit (5 tools).**
- `get_gene_interactions`: drugs for a gene, filtered by source license.
- `get_drug_interactions`: genes for a drug.
- `get_gene_categories`: druggable-genome categories.
- `resolve_drug`: name to ChEMBL/NCIt/RxNorm concept.
- `list_sources`: sources with version and license, a provenance resource.

Overlap: clinpgx-link (pharmacogenomics) is complementary; Open Targets tractability (below) is the alternative. An official `dgidb/dgidb-mcp-server` exists (a Cloudflare Worker, license NOASSERTION, 3 stars, citing Bioinformatics btag632). Do not federate it: no license, wrong runtime, and no fleet standards. Build the fleet link instead.

**Value.** "What approved drugs target this gene, and from which evidence source?" This is the druggability layer that a pure germline fleet lacks.

---

## 3. IntOGen and NCG (driver-gene compendia)

### IntOGen — **BUILD a small snapshot link or fold into a "cancer-genes" link, P1, effort S**
- Bulk downloads only. The website returns 303/403 to bots (WebFetch got 403), but `curl` works. `https://www.intogen.org/api/gene/BRAF` returns 404, so **there is no API**.
- `https://www.intogen.org/download?file=IntOGen-Drivers-20240920.zip` returns HTTP 200, 965 KB. It contains `Compendium_Cancer_Genes.tsv` (4,478 rows, **633 driver genes across 86 tumour types**, per-cohort, with ROLE Act/LoF/amb, methods, q-value, dN/dS excess, 2D/3D clusters, domains) and `Unfiltered_drivers.tsv`.
- **LICENSE.txt is CC0 1.0** (also shown on the download page). Quirk: the zip's README still says "RELEASE 20230531", so stamp `data_version` from the zip filename. Releases so far: 2020.02, 2023.05, 2024.09; cadence is roughly 1.5 years.
- BRAF rows show per-cohort evidence (e.g. HARTWIG_WGS_BLCA_2023, combination q=1.4e-5).
- Canonical citation: Martínez-Jiménez F et al., *A compendium of mutational cancer driver genes*, Nat Rev Cancer 2020 (PMID 32778778 is recalled, not re-verified). Method papers are ongoing, e.g. Oncodrive3D, NAR 2025, PMID 40808301.
- **License verdict: fully compatible (CC0).** Tiny, immutable snapshot, which is ideal for the clingen/hpo snapshot pattern.

### NCG (Network of Cancer Genes & Healthy Drivers) — **PARK, P3**
- NCG7: 3,347 cancer genes and 95 healthy-tissue drivers. Dressler L et al., Genome Biol 2022, **PMID 35078504**.
- Live: `http://network-cancer-genes.org/` returns HTTP 200 but the body is "Site down for maintenance". HTTPS refuses connections, and `/download.php` returns 404.
- No data license found; only the bioRxiv preprint text is CC BY-NC-ND.
- **Verdict:** PARK until the site is back and a data license is confirmed. If it returns with a permissive license, fold it into the IntOGen link as a second source. It adds literature-curated canonical and candidate genes, which complements IntOGen's statistical calls.

**Suggested combined link.** A `cancergenes-link` (snapshot) from IntOGen (CC0), optionally NCG later, and DepMap gene-level summaries (§5):
- `get_gene_driver_status`: is the gene a driver; in which tumour types; role Act or LoF.
- `list_drivers_for_tumor_type`.
- `search_driver_genes`.
- `get_driver_cohort_evidence`.

Join on HGNC symbol and Ensembl transcript (→ vep). Tumour types are IntOGen/TCGA codes, so map them to OncoTree (another agent covers that) and MONDO.

---

## 4. ClinicalTrials.gov API v2 — **BUILD `trials-link` (live proxy), P2, effort S–M**

**Access.**
- `https://clinicaltrials.gov/api/v2/studies?query.cond=melanoma&query.term=BRAF V600E&filter.overallStatus=RECRUITING&pageSize=3&countTotal=true&fields=…` returned HTTP 200, `totalCount 4`, with a `nextPageToken`. Example hit: NCT06194929 (defactinib + avutometinib ± encorafenib).
- `/api/v2/version` returns `apiVersion 2.0.5`, `dataTimestamp 2026-11-19T10:00:07`, so data refreshes daily.
- Quirks: an unknown NCT gives **HTTP 400**, not 404. Paging is token-based. `pageSize` is at most 1000. No auth is needed; NLM asks for about ≤50 requests/min (recalled).
- Biomarker matching is free-text only (`query.term`), with no structured biomarker field. Recall is therefore noisy: e.g. two of three hits were liquid-biopsy diagnostic studies, not therapy trials. The tool must say this.

**License.** ClinicalTrials.gov data is US-government and generally public domain, under NLM terms. Some sponsor-submitted text may carry copyright; NLM only asks for attribution and an accuracy disclaimer. **Compatible.**

**In scope for a genetics fleet?** Yes, but secondary. The fleet's user is a research geneticist, and trials are the actionable end of an oncology or rare-disease query. Rare-disease gene-therapy and ASO trials help the existing germline users too, so this is not oncology-only. It is not core variant interpretation, though, and the router's research-use framing must stay prominent.

**Overlap.** `cyanheads/clinicaltrialsgov-mcp-server` (Apache-2.0, 101 stars, active 2026-11-13) and BioMCP's trial module both exist. Federation is technically possible, since both licenses are permissive, but neither follows the fleet standards (naming v1.1, envelope, `response_mode`, container hardening), so a thin first-party link is likely cheaper than adapting either.

**Tools.**
- `search_trials`: condition, gene/biomarker term, status, phase, location.
- `get_trial`: by NCT ID.
- `list_trial_locations`.
- `get_trial_eligibility`: fenced untrusted text.

Join keys: MONDO disease to condition text, HGNC symbol to term.

---

## 5. DepMap (CRISPR dependencies) — **BUILD as snapshot (inside `cancergenes-link` or its own), P2, effort M**

- **Access has changed: the Broad portal API is now behind a Cloudflare Turnstile challenge.** `depmap.org/portal/api/download/files` and `/portal/api/` return HTTP 200 with an HTML page titled "DepMap — Verification" (turnstile/challenge), even with a browser User-Agent. **A live proxy is infeasible.**
- Releases are also deposited on figshare (e.g. the 23Q4 public release is labelled CC BY 4.0; "DepMap NextGen" was deposited 2026-02-25). Use figshare as the snapshot source, with a pinned release, sha256 and `data_version` (e.g. `25Q4`).
- **License.** DepMap-generated data is CC BY 4.0 (DepMap forum; figshare deposit labels). Some CCLE-legacy files (e.g. metabolomics) are under the older 2018 CCLE terms, so take only `CRISPRGeneEffect`/`CRISPRGeneDependency` and model metadata. The **Sanger** Cancer Dependency Map / Cell Model Passports API is a different source: it forbids commercial use without consent, so avoid it. **Verdict: compatible if built from the Broad public release files.**
- **Fleet fit.** The matrix is about 1,100+ cell lines × 18k genes, a few hundred MB. Precompute gene-level summaries rather than serving the matrix: common-essential flag, strongly-selective flag, fraction of lines dependent, mean Chronos effect by OncoTree lineage, and top dependent lineages.
  - `get_gene_dependency`
  - `list_dependent_lineages`
  - `search_selective_dependencies`: e.g. by lineage.
  - Optionally `get_cell_line`: ACH ID / Cellosaurus to lineage and mutations.
- **Value.** "Is this gene essential, or a selective vulnerability in a tumour lineage?" This is target validation that no other candidate gives. It also helps germline triage (a common-essential gene suggests intolerance, alongside gnomAD constraint).
- Citations (recalled, not re-verified): Tsherniak 2017 Cell (PMID 28753430); Chronos, Dempster 2021 Genome Biol.
- Existing MCPs (`QuentinCody/depmap-mcp-server` Apache-2.0, `openpharma-org/depmap-mcp-server`, and others) have 0–1 stars and will hit the same Turnstile wall. Do not federate.

---

## 6. Mutational-signature references (COSMIC-free) — **SKIP**

- COSMIC SBS/DBS/ID reference signatures fall under the COSMIC license (another agent covers it). The Alexandrov 2020 (Nature) PCAWG signature set is downloadable from Synapse (syn11738319), and SigProfiler tools are BSD-2.
- Why skip: signatures are an **analysis method** that needs a sample's whole mutation catalogue (96-channel counts), not a lookup keyed by gene or variant. Neither an agent nor the fleet's join keys (variant/gene/disease) have a useful query against a static matrix of about 80 signatures × 96 channels. Aetiology text ("SBS4 = tobacco") fits better in a router skill.
- **SKIP.** At most, write a ROUTER-SKILL note pointing users to SigProfiler. P3.

---

## 7. CancerVar (somatic AMP/ASCO/CAP auto-interpreter) — **PARK (license), P3**

- Live: `https://cancervar.wglab.org/api_new.php?queryType=position&build=hg19&chr=7&pos=140453136&ref=A&alt=T&tissue=SKCM` returns HTTP 200: `{"Cancervar":"11#Tier_I_strong","Gene":"BRAF","CBP_1..12":…,"OPAI":0.99}`. Note it is **hg19 only** in the query form.
- License: the GitHub `WGLab/CancerVar` has no SPDX license and was last pushed 2025-02-20. Its README says: *"CancerVar and OPAI is free for non-commercial use without warranty. Users need to obtain licenses such as ANNOVAR by themselves. Please contact the authors for commercial use."* It depends on ANNOVAR, which needs a license for commercial use.
- Science caveat: it is an automated rule engine over CIViC/OncoKB/CGI-derived evidence, so its tiers are derivative and can be stale (built on a 2020-era knowledge base). The ClinVar somatic tiers (§1) and CIViC give curated tiers instead.
- **PARK.** It is non-commercial and has no redistribution terms; a public router re-exposing it is grey. If wanted, run it as a disabled-by-default self-host-only backend. The same applies to similar interpreters (PCGR/CPSR is MIT code but bundles restricted KBs).

---

## 8. Existing maintained cancer-genomics MCP servers — federate vs build

| Server | License | Activity | Coverage | Verdict |
|---|---|---|---|---|
| **genomoncology/biomcp** | **MIT** | pushed 2026-11-10, 683 stars | gene (incl. DGIdb, ClinGen), variant (MyVariant, ClinVar, gnomAD, **CIViC, CGI, OncoKB (with `ONCOKB_TOKEN`), cBioPortal**), trials (CT.gov v2 + NCI CTS), drugs, disease, cell lines (DepMap xrefs). Now a single binary with stdio plus a `serve-http` Streamable HTTP `/mcp`. | **Do not federate wholesale.** Its tool surface overlaps the fleet (ClinVar, gnomAD, PubTator, MONDO), breaks naming v1.1 and the surface budget, uses CLI-style verbs, and lets an operator-supplied OncoKB token reach anonymous router users, which is a license breach. **Use it as a design reference and coverage checklist.** A pinned, OncoKB-disabled instance federated for `trial`/`drug` only is possible but would need a transform block and fails the fleet's conformance gates. Not recommended. |
| cBioPortal/cbioportal-mcp | Apache-2.0 | active, 17 stars | Wrapper around mcp-clickhouse; needs ClickHouse credentials; README says "still under construction" | Not federable (DB creds, SQL surface). |
| dgidb/dgidb-mcp-server | NOASSERTION | 3 stars | Cloudflare Worker | Build a fleet `dgidb-link` instead (§2). |
| cyanheads/clinicaltrialsgov-mcp-server | Apache-2.0 | 101 stars, active | CT.gov v2 | Reference for `trials-link`; could be federated, but fails fleet standards. |
| `npstorey/civic-ai-tools` | MIT | 48 stars | CIViC | The CIViC agent covers this. |
| DepMap MCPs (several) | mixed | 0–1 stars | DepMap | Blocked by Turnstile; ignore. |

GitHub search found no maintained OncoKB MCP; OncoKB access would sit inside BioMCP's token path.

**Overall position.** Build fleet-native links. The fleet's value is the standards: envelope, honest totals, fencing, license-safe source filtering, and the conformance gate. None of the third-party servers meet them, and federating them would import license and surface-budget debt.

---

## 9. Extra resources a sharp scientist would add (quick probes)

- **Open Targets Platform** (GraphQL `api.platform.opentargets.org/api/v4/graphql`, HTTP 200; BRAF tractability "Approved Drug SM: true").
  - License **CC0**.
  - Somatic evidence (cancer_gene_census, intogen, cancer biomarkers), tractability, and known drugs, all in one CC0 API.
  - **Strong P1 candidate.** It could replace part of DGIdb and IntOGen, and it joins on Ensembl and EFO/MONDO.
  - Note: its somatic `cancer_gene_census` evidence derives from COSMIC; filter it out by datasource.
- **MOAlmanac** (`api.moalmanac.org`, FastAPI).
  - `/statements?gene=BRAF` returned HTTP 200 with 112 statements.
  - License ODbL: share-alike on the database, which is research-compatible with attribution.
  - Curated FDA/guideline biomarker–therapy relationships, a CIViC-like but FDA-anchored layer.
  - **P1/P2: fold into the clinical-actionability link the CIViC agent proposes.**
- **openFDA drug labels** (`api.fda.gov/drug/label.json?search=indications_and_usage:"BRAF V600E"` returns HTTP 200).
  - CC0.
  - Gives the "is there an FDA-approved companion indication" fact.
  - P2: a tool inside `trials-link` or a small `fda-link`.

---

## Priority roll-up

| # | Resource | Verdict | P | Effort | License |
|---|---|---|---|---|---|
| 1 | ClinVar somatic/oncogenicity | EXTEND clinvar-link | P0 | S–M | Public domain, OK |
| 2 | DGIdb 5 | BUILD dgidb-link (live, source-license allowlist) | P1 | S | Mixed per source; filter to permissive |
| 3a | IntOGen | BUILD (snapshot; cancergenes-link) | P1 | S | CC0, OK |
| 3b | NCG7 | PARK (site down, license unknown) | P3 | — | Unknown |
| 4 | ClinicalTrials.gov v2 | BUILD trials-link (live) | P2 | S–M | Public domain, OK |
| 5 | DepMap | BUILD snapshot from figshare release | P2 | M | CC BY 4.0, OK (avoid Sanger API) |
| 6 | Mutational signatures | SKIP (router-skill note at most) | P3 | — | COSMIC restricted; SigProfiler BSD |
| 7 | CancerVar | PARK | P3 | — | Non-commercial + ANNOVAR |
| 8 | BioMCP and others | Don't federate; use as reference | — | — | MIT, but OncoKB-token risk |
| + | Open Targets | BUILD candidate | P1 | M | CC0 |
| + | MOAlmanac | Fold into actionability link | P1–P2 | S | ODbL |

Sources: [DepMap forum license thread](https://forum.depmap.org/t/license-for-data-found-in-the-depmap-portal/130), [CCLE terms](https://depmap.org/portal/ccle/terms_and_conditions), [Sanger DepMap data-usage policy](https://depmap.sanger.ac.uk/documentation/data-usage-policy/), [DGIdb 5.0 paper (PMC10767982)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10767982/), [NCG Genome Biol (PMC6317252)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6317252), [BioMCP README](https://github.com/genomoncology/biomcp), [CancerVar README](https://github.com/WGLab/CancerVar), [IntOGen downloads](https://www.intogen.org/download), and live API probes as listed.
