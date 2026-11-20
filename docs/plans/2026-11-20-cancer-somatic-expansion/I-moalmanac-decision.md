# I: MOAlmanac decision: own link (A), fold into civic-link (B), or skip (C)

Date 2026-11-20. Live probes against `api.moalmanac.org`, `civicdb.org/api/graphql` and `api.fda.gov`, plus the `gh` API. Raw dumps are in `scratchpad/cancer/moa/`. Helper scripts are `scratchpad/cancer/moasum.py` and `scratchpad/cancer/civ.py`.

## Recommendation: **A, a small `moalmanac-link` (P2, effort S), served from a release snapshot and not the live API.** Do not do B.

---

## 1. MOAlmanac status

### The API

- `api.moalmanac.org` is FastAPI with Swagger at `/` and the OpenAPI spec at `/openapi.json` (`/docs` returns 404). OpenAPI 3.1, `info.version: "draft"`.
- It has 22 GET endpoints: `/about /agents /alleles /biomarkers /biomarker_criteria /codings /contributions /copy_changes /diseases /documents /function_consequences /genes /indications /mappings /propositions /search /sequence_locations /sequence_references /statements /strengths /therapies /therapy_groups`.
- Filters are `gene, biomarker, biomarker_type, disease, therapy, therapy_type, document, agent_id, indication, contribution, include_deprecated`. Different filters combine with AND. A repeated filter combines with OR.
- `/search` returns propositions with per-proposition `aggregates` (counts by agent, document and strength). It is the compact view.
- The model is GA4GH VA-Spec, VRS and Cat-VRS. `VariantTherapeuticResponseProposition` objects carry VRS allele digests, NCIt, OncoTree, RxNorm and OMOP codings, and MANE RefSeq.

### API problems that matter for a link

- **No pagination.** Unfiltered `/statements` is 2,000 records and 27.8 MB, about 14 KB per statement. `statements?gene=EGFR&disease=NSCLC` is 5.2 MB. `/search` for the same query is 1.2 MB.
- **Unknown filters are silently ignored.** `?bogus=1` returns all 2,000 rows. This is exactly the "silently-empty / ignored filter" failure that `behaviour.py` gates, so a link must validate parameters itself.
- **`/about` is stale or misleading.** It reports `release: "draft"`, `last_updated: 2026-10-08` and `license: "GPL-2.0"`. The repo was relicensed on 2026-10-24/26, and there are db commits up to 2026-11-20. The API serves the draft main branch, not a tagged release.
- The legacy v1 API (`moalmanac.org/api/*`) now returns 302 redirects.
- Latency is 0.9–1.8 s.

### Data release and cadence

- `vanallenlab/moalmanac-db` tags are `v.YYYY-MM-DD`. There have been 30+ releases since 2023, roughly monthly: v.2026-02-05, 03-05, 04-09, 05-07, then v.2026-08-02 (published 2026-10-31, the "2.0.0" launch after a 6-month rebuild), then v.2026-11-05.
- Zenodo DOIs are minted per release since 2025-09-04.
- Releases have no binary assets. The data is the repo tree at the tag: `referenced/*.json` totals about 7.5 MB, and there is a dereferenced `molecular-oncology-almanac.json`.

### GitHub activity and maintainers

| repo | licence (GitHub) | last push | open issues | commits |
|---|---|---|---|---|
| moalmanac-db | CC-BY-4.0 (`LICENSE`) + GPL-2 (`LICENSE-code`) | 2026-11-20 | 18 | brendanreardon 181, ehahn25 1 |
| moalmanac-api | CC-BY-4.0 + GPL-2 | 2026-10-26 | 6 | brendanreardon 59, dependabot 3 |
| moalmanac (algorithm) | CC-BY-4.0 + GPL-2 | 2026-11-16 | 6 | 0.8.5 (2025-10) |
| moalmanac-browser | CC-BY-4.0 + GPL-2 | 2026-10-26 | 10 | v1.3.0 |

- The project is very active: 5 db PRs in the last week, covering EMA, HPRA and European Commission revisions.
- **Bus factor is 1.** Essentially one curator and maintainer (B. Reardon, Van Allen lab / DFCI).
- Open curation backlog includes FDA gedatolisib, selpercatinib traditional approval, datopotamab deruxtecan, "resistance variants associated with HER2 inhibition", "resistance variants from GENIE" and "re-check missing FDA approvals".

### Licence

- **Database content is CC BY 4.0**, per the README, `LICENSE`, and Terms of Use effective 2026-10-24. Commercial use is allowed with attribution. **There is no share-alike and no ODbL.**
- Code (API, browser, algorithm) is GPL v2. We would not run their code, only consume the data, so GPL does not reach us.
- Files in `datasources/` (NCIt, OncoTree, RefSeq raw files) are excluded from the CC BY grant. A link only needs the `referenced/` and `dereferenced/` outputs.
- **Correction for the plan docs.** `docs/plans/2026-11-20-cancer-somatic-expansion.md` (line 56) and `E-gapfill.md` (lines 63 and 214) call MOAlmanac **ODbL**. That is wrong. `B-oncokb-cosmic-hsmd.md` and `A-civic-metakb-cgi.md` correctly say CC BY 4.0. Before the 2.0 release, the repo and API advertised GPL-2.0 for everything, which is where `/about`'s stale value comes from.
- **Gateway implications.** Returning rows needs attribution only: name, citation, licence link and DOI/release, put in envelope `source`/`license` fields. No share-alike, so the ODbL produced-work vs derivative-database question does not arise. Shipping a snapshot inside a public GHCR image is redistribution, which CC BY allows with attribution. Mirror DFCI's "not validated for clinical use" disclaimer, which already matches the fleet boundary.

## 2. Scope and content: what MOAlmanac 2.0 actually is

MOAlmanac 2.0 is **no longer a general actionability KB. It is a structured multi-agency regulatory-approval KB.**

- 2,000 statements, 886 propositions, 873 indications, 418 documents, 184 biomarkers, 98 diseases, 165 therapies.
- **Every document is `documentType: "Regulatory approval"`.** Documents by agency: HSE 137, FDA 111, EMA 96, Health Canada 71, HPRA 3.
- Statements by agency: FDA 705, EMA 531, HC 387, HSE (Ireland reimbursement) 355, HPRA 22.
- **One strength value only: `Approval` (NCIt C25425). One predicate only: `predictsSensitivityTo`. One direction only: `supports`.** There is no resistance, prognostic, diagnostic, guideline (NCCN/ESMO), clinical-trial or preclinical tier in the current data. The v1 KB, as described in Reardon et al. 2021, had guideline, trial and preclinical levels; that comes from the paper and my background knowledge, not from today's probes. Resistance work is still only open issues (#76, #92).
- Biomarker types are somatic variant 85, protein expression 32, germline 20, fusion 16, wild type 7, CNV 6, rearrangement 5, MSI/MMR/TMB/HRD and others.

What it carries per statement that nobody else in our plan carries in structured form:

- the agency (FDA, EMA, HC, HSE, HPRA)
- a **versioned label document**. For example, `doc:fda:verzenio:2023-03-03` has the label PDF URL, `regulatory_identifier` NDA208716 or EMEA/H/C/xxxx, `publication_date`, company and brand name.
- contribution history with **approval dates and type** (traditional, accelerated, conditional)
- verbatim indication text
- the full combination regimen as a therapy group
- co-biomarker logic: wild-type, NOT, AND, PD-L1 thresholds
- NCIt, OncoTree, RxNorm and OMOP codes, and VRS digests

### Comparison with CIViC (live GraphQL, accepted assertions / accepted evidence items)

| Case | MOAlmanac (`/search`) | CIViC |
|---|---|---|
| BRAF V600E melanoma | 18 propositions (V600E+V600K). Dab, dab+tram, vem, vem+cobi, enco+bini, tram, atezo+cobi+vem; pembro/nivo (HC). Agencies FDA/EMA/HC/HSE per row. | 3 assertions: AID7 dab+tram and AID10 vem+cobi (Tier I-A, `regulatoryApproval=true`, NCCN Melanoma **2.2018**), AID281 oncogenic. 33 accepted EIDs (66 total). |
| EGFR L858R NSCLC | 82 EGFR-NSCLC propositions. L858R: erlotinib, gefitinib, afatinib, dacomitinib, osimertinib (± chemo), lazertinib+amivantamab, amivantamab+chemo, Dato-DXd, aumolertinib (EMA), erlotinib+ramucirumab/bevacizumab. Plus EGFR-WT IO regimens. | 4 assertions: erlotinib, gefitinib, afatinib, dacomitinib (Tier I-A; NCCN 3.2018 / 6.2026). **No osimertinib assertion for L858R.** 33 EIDs. |
| KRAS G12C NSCLC | Sotorasib (EMA/FDA/HC), adagrasib (EMA/FDA). Also adagrasib+cetuximab CRC and avutometinib+defactinib LGSOC. | **0 accepted assertions.** 4 accepted EIDs (22 total). KRAS G12C assertions exist only for CRC anti-EGFR. |
| ERBB2 amp breast | 58 HER2-positive + 14 "ERBB2 amplification" propositions: trastuzumab, pertuzumab regimens, T-DM1, T-DXd, tucatinib, neratinib, lapatinib, margetuximab. | 1 assertion (AID2 trastuzumab, NCCN Breast 3.2017). 32 EIDs. |
| NTRK fusions | 3 pan-tumour propositions per gene (v::NTRK1/2/3): entrectinib, larotrectinib, repotrectinib. | Partner-specific: NTRK1 9 assertions, NTRK3 14 (larotrectinib/entrectinib Tier I-A, oncogenic and diagnostic, e.g. ETV6::NTRK3 in congenital fibrosarcoma, CMN, secretory breast carcinoma). |

### What is unique and what is redundant

- **MOAlmanac uniquely adds:**
  - multi-agency approval coverage: EMA, Health Canada, Ireland HSE reimbursement, HPRA
  - label version and date anchoring
  - accelerated vs approved status
  - full regimens and line-of-therapy text
  - coverage of recent approvals that CIViC never turned into assertions (KRAS G12C NSCLC, osimertinib, T-DXd, amivantamab and others)
- **CIViC uniquely adds:**
  - literature evidence items (5,003 accepted)
  - resistance, prognostic, diagnostic, predisposing and oncogenic types
  - fusion-partner and exon granularity
  - AMP/ASCO/CAP tiers and ACMG/ClinGen codes
  - (stale) NCCN references
- **Redundancy is low in kind and moderate in pairs.** Every CIViC Tier I-A "approved" predictive assertion in these 5 cases has a MOAlmanac counterpart. CIViC covers about 10–25% of MOAlmanac's approval pairs for the same cases. They answer different questions: MOAlmanac answers "is it approved, where, since when, by which label"; CIViC answers "what is the evidence and its level".
- **Neither has NCCN or ESMO guidelines in a current form.** CIViC's are mostly 2017/2018 versions; MOAlmanac has none.

### Data-quality defects found in MOAlmanac (worth upstream issues)

1. **Wrong therapy binding.** `stmt:ema:pemazyre:0:*` and `stmt:hse:00889:a:*` describe pemigatinib in FGFR2-fusion cholangiocarcinoma, but `/statements?therapy=Elacestrant` returns them. `/search?biomarker_type=Rearrangement` shows "Cholangiocarcinoma | FGFR2 rearrangements | Elacestrant | ema,hse".
2. **Coverage lag.** FDA sotorasib + panitumumab for KRAS G12C mCRC (approved Jan 2025, present in the openFDA Lumakras label effective 2026-11-03) is absent. `ind:fda:lumakras` has only the NSCLC indication.
3. **Biomarker duplication.** "ERBB2 amplification" and "HER2-positive" are separate biomarkers for the same EMA/HSE trastuzumab indications, so a `gene=ERBB2` query misses the FDA HER2-positive rows. The link needs alias expansion or should document this.

## 3. VICC MetaKB and FDA labels in CIViC

- **MetaKB v2 does include MOA.** `cancervariants/metakb` (MIT, pushed 2026-11-19, release `2.0.0-a2`) has harvesters for `civic`, `moa`, `fda_poda`, `cbioportal` and `mci`.
- **However, its `moa.py` harvester still calls the retired v1 endpoints** (`https://moalmanac.org/api/genes`, `/api/assertions`, `/api/features`), which now 302. So MetaKB's MOA content is a frozen v1-era snapshot (the pediatric instance cites 300 MOA references) and does not follow MOAlmanac 2.0.
- MetaKB v1 (`search.cancervariants.org`) returns 504 and is retired. The plan's "PARK MetaKB" stands. MetaKB is not a route to current MOAlmanac.
- `fda_poda` is a separate, tiny curated FDA *pediatric* oncology approvals set (GenomicMedLab).
- **CIViC does not import FDA labels.**
  - CIViC source types are `PUBMED, ASCO, ASH, BIORXIV, MEDRXIV` only.
  - Assertions carry curator-set booleans `regulatoryApproval` and `fdaCompanionTest` with a `*LastUpdated` timestamp, which is usually null.
  - They have no label URL or version, no approval date, and no agency other than FDA.
  - The `Assertion.approvals` field is organisational endorsement of the assertion (for ClinVar submission), not regulatory approval.
  - There are 186 accepted assertions in total.

## 4. Effort and tool sketch

### (A) `moalmanac-link`: recommended

- **Data plan.** Snapshot the tagged release (`referenced/*.json`, about 7.5 MB) from GitHub at build or boot, using the "committed/volume snapshot + `data_version = v.YYYY-MM-DD`" pattern. Do not proxy `api.moalmanac.org` live, for these reasons:
  - it is a draft branch
  - it has no pagination and returns 1–28 MB payloads
  - it silently ignores unknown filters
  - its licence metadata is stale
  - there is one maintainer and no SLA

  In-process joins over the referenced tables are trivial. A monthly Dependabot-style re-pin, or the existing data-refresh workflow, follows the release cadence.
- **Tools.** Three tools, about 2.5–3.5k tokens, well under the 10k (B2) and 1,200 per tool (B1) budgets:
  1. `moalmanac_search_approvals(gene?, biomarker?, disease?, therapy?, agency?[fda|ema|hc|hse|hpra], approval_status?, limit)` returns compact rows: biomarker set (with NOT/wild-type), disease (NCIt/OncoTree), regimen, agency, indication id, approval date and type, label doc id and version. Results are grouped per proposition with agencies collapsed, reusing `/search`'s aggregate idea. It should do **strict parameter validation** and HGNC/alias expansion (HER2 to ERBB2).
  2. `moalmanac_get_indication(indication_id)` returns verbatim indication text, label URL/version/publication date, regulatory identifier, contribution history and derived statements.
  3. Optional `moalmanac_list_biomarkers(gene?|type?)` lets a model map its variant onto MOAlmanac biomarker vocabulary (e.g. "EGFR activating variants" vs p.L858R). Alternatively fold this into tool 1's resolver.
- **Envelope.** `source=MOAlmanac`, `license=CC BY 4.0`, citation (Reardon 2021 Nat Cancer) plus release DOI, `data_version=v.2026-11-05`. Use the untrusted-content fencing for verbatim label text.
- **Effort.** S: about 2–3 days following the ucsc-link or pgscatalog-link template. Joins to fleet keys: HGNC symbol, VRS digest, MANE RefSeq, NCIt/OncoTree, RxNorm.

### (B) Fold into civic-link

- This adds a second upstream client, a second data-version stamp, a second licence (CC0 plus CC BY attribution per row) and a second release cadence to one repo.
- It either adds 2–3 tools to civic-link's surface (CIViC alone will likely need 5–7 tools) or forces a merged "actionability" tool. A merged tool means harmonising two incompatible models: CIViC MP/EID/AID with AMP tiers vs VA-Spec propositions with a single Approval strength. That harmonisation is MetaKB's job, and MetaKB is parked for good reason.
- It breaks the fleet's one-source-per-link convention. That convention exists so that drift detection, conformance probes, data-freshness, deploy and kill-switches stay per upstream.
- Effort is no lower than A, roughly S–M, and the coupling cost lasts for the life of the repo. The router already federates and BM25-searches across links, so cross-source answers come from the agent calling both tools. **Reject B.**

### (C) Skip

- Defensible only if the cancer track stays US-only and CIViC-only.
- It leaves real gaps: KRAS G12C NSCLC has no CIViC assertion, there is no EMA, HC or HSE data anywhere in the fleet, and there are no label versions or dates. Those are exactly the "is X approved for Y, in the EU, since when" questions agents get asked.
- Given CC BY, low effort and active upstream, skipping is not justified. P2 timing (after civic-link) is fine.

### Fleet-convention judgment

- A follows the convention: one source, its own `data_version`, its own licence row.
- B violates it for no saving.
- MOAlmanac is complementary to CIViC (approvals vs evidence), not a second view of the same data, which is the case where folding would be tempting.

## 5. openFDA drug label API as an alternative

- `api.fda.gov/drug/label.json` returns 200. openFDA data is public domain / CC0. Results include `effective_time` and SPL `version`; for example, Lumakras is effective 20261103, version 20.
- **It is unstructured.** `indications_and_usage` is free text. There is no biomarker → disease → regimen structure, so we would need NLP to get approval pairs. It is US-only, and it has no approval history or dates, only the current SPL plus a `recent_major_changes` section.
- **MOAlmanac mostly subsumes openFDA for this use.** MOAlmanac is the curated structured layer over FDA labels, links the exact label PDF version, and adds EMA, HC and HSE.
- openFDA is fresher. It already has sotorasib+panitumumab CRC, which MOAlmanac lacks.
- Verdict: no openFDA link. An optional later enhancement is a `label_current_as_of` freshness check inside moalmanac-link (one CC0 call per indication). That should be a footnote, not a source.

## 6. Risks

- **Bus factor 1** (one curator) and an occasional multi-month release gap (May to Oct 2026 during the 2.0 rebuild). Mitigation: snapshot pinned per release, so if upstream stalls we serve the last release honestly via `data_version`.
- **Schema churn.** 2.0 was a full rebuild (v1 API retired, MetaKB's harvester broken by it). Pin to release tags and add a schema guard test on the snapshot.
- **Data-quality defects** (misbound therapy, coverage lag, HER2/ERBB2 duplication). Surface them in the README, file upstream issues, and add alias expansion.
- **Scope drift.** MOAlmanac may re-add resistance or trial tiers (issues #76, #92). The tool must expose `strength` and `predicate` as returned values, not assume them.
- **Clinical-use framing.** These are regulatory facts, which tempts clinical use. Mirror the DFCI disclaimer and the fleet research-only boundary.
