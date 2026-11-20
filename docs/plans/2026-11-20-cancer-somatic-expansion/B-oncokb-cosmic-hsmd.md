# B — License-encumbered cancer knowledgebases: OncoKB, COSMIC, HSMD, JAX-CKB, PMKB

Assessed 2026-11-20 against the shared RUBRIC. Every probe was run live with `curl -s -m 20`. The licence text below is quoted from primary sources: the OncoKB public-site source code on GitHub, the COSMIC T&Cs at cosmickb.org (last amended 30 Oct 2025), and the Genomenon CKB EULA (effective 24 Jun 2024).

**Bottom line.** None of the five can be legally federated behind a publicly listed, unauthenticated router. All five are **PARK**. Each one also has a legally clean partial substitute (§6).

---

## 1. OncoKB (MSK) — PARK (P1 to revisit if MSK grants written permission)

### What it is
- MSK's expert-curated precision-oncology knowledgebase. It covers oncogenicity and mutation effect for somatic and germline variants, and it assigns therapeutic (1/2/3A/3B/4/R1/R2), diagnostic (Dx1–3), prognostic (Px1–3) and FDA (Fda1–3) levels.
- Current state from live `/api/v1/info`: data **v7.7 (10/29/2026)**, app v6.13.1, API v1.7.0, OncoTree `oncotree_2025_10_03`. There are **1,151 curated genes**, and the cancer gene list has **1,247 genes**.
- Releases are roughly monthly.
- **FDA recognition:** on 7 Oct 2021 the FDA partially recognised OncoKB as a Public Human Genetic Variant Database, the first tumour-mutation DB so recognised. The recognition covers Level 2 (clinical significance) and Level 3 (potential clinical significance) biomarkers in the "FDA recognized alterations" tab (FDA announcement: fda.gov/drugs/resources-information-approved-drugs/fda-recognizes-memorial-sloan-kettering-database-molecular-tumor-marker-information).
- Germline therapeutic levels were added in 04/2026 (the news file says 50 Level-1 and 7 Level-2 germline associations).
- Citations:
  - Chakravarty D et al., *JCO Precis Oncol* 2017, PMID **28890946**.
  - Suehnholz SP et al., *Cancer Discov* 2024;14:49-65, PMID **37849038** (latest major paper, on the actionability landscape).

### Access (probed live)
| Endpoint | Host | Status | Notes |
|---|---|---|---|
| `/api/v1/info` | www.oncokb.org | **200 no token** | versions plus level definitions |
| `/api/v1/levels` (and therapeutic/diagnostic/prognostic-levels) | www | **200 no token** | level → description map |
| `/api/v1/utils/allCuratedGenes[.txt/.json]` | www | **200 no token**, 1.96 MB | per gene: isoforms, `geneType`, **`highestSensitiveLevel`/`highestResistanceLevel`**, free-text `summary` and `background`. BRAF=1, KRAS=1/R1, TP53=3A |
| `/api/v1/utils/cancerGeneList[.txt/.json]` | www | **200 no token**, 467 KB | 1,247 genes with flags MSK-IMPACT, MSK-HEME, FoundationOne(+Heme), Vogelstein, **COSMIC CGC (v99)**, oncokbAnnotated. `robots.txt` **disallows** `cancerGeneList.txt` |
| `/api/v1/annotate/mutations/byProteinChange?hugoSymbol=BRAF&alteration=V600E&tumorType=MEL` | www | **401** "Full authentication is required" | needs API/service-account authority |
| `/api/v1/genes`, `/variants/lookup`, `/utils/allActionableVariants` | www | **401** | premium / data-download roles |
| `/api/v1/annotate/...BRAF V600E MEL` | **public.api.oncokb.org** | **200 no token** | `publicInstance: true`. Returns `oncogenic`, `mutationEffect.knownEffect`, `hotspot`, gene and variant summaries. **`treatments: []`, all `highest*Level: null`** |
| `api.oncokb.org/api/v1/info` | api.oncokb.org | 404 (docs site only) | the real API host is www.oncokb.org |

The allowlist comes from OncoKB's own source code (`TokenSecurityConfiguration.java`). Only these are `permitAll`: info, levels, allCuratedGenes, and cancerGeneList. `/annotate/**` requires the `API` or `ROLE_SERVICE_ACCOUNT` authority.

**SAFETY HAZARD (public instance).** For BRAF V600E in melanoma, `public.api.oncokb.org` returns:

> `tumorTypeSummary: "There are no FDA-approved or NCCN-compendium listed treatments specifically for patients with BRAF V600E mutant melanoma."`

That statement is false: dabrafenib+trametinib (and others) are Level 1. The public instance strips treatments and then generates a summary that reads as a confident negative. Wiring it into an agent would produce dangerous false "not actionable" answers. **Never proxy the public instance.**

Other behaviour seen:
- KRAS G12D/PAAD, TP53 R175H and EGFR L858R/LUAD all return `Oncogenic`, `treatments: []`.
- An unknown gene (`FAKEGENE`) returns 200 with `geneExist: false`, not an error.

### License / Terms of Use (decisive clauses)
From the OncoKB Terms page source (`TermsPage.tsx`):

> "You may view the Content solely for your own personal reference or use for research in an academic setting, provided that all academic research use of the Content must credit OncoKB as the source … **outside of scientific publication, you may not otherwise redistribute or share the Content with any third party, in part or in whole, for any purpose, without the express permission of MSK.**"

> "Unless you have signed a license agreement with MSK, you may not use any part of the Content for any other purpose, including: use or incorporation into a commercial product or towards performance of a commercial service; research use in a commercial setting; use for patient services; or generation of reports in a hospital or other patient care setting."

From `LicenseExplanation.tsx` (updated 2026-05-15):

> "OncoKB is freely accessible for academic research use and for manual clinical reference through the OncoKB website. **A paid license is required for commercial use or for programmatic/API-based use of OncoKB content**, including integration into clinical reporting workflows."

From the AI clause shown in the registration form (`terms.ts`):

> "**Using OncoKB data for the creation and training of AI models is strictly prohibited.** Access may be granted for model validation or benchmarking purposes only with explicit authorization."

Two further points:
- **Academic API access** has to be requested with "a detailed description on how you plan to use OncoKB APIs", and users are screened.
- The 04/2026 news adds a **registration requirement** and says the terms are being "refined".

**Can an academic token power a third-party-reachable service? No.**
- The token is personal, and only one token is allowed per account.
- The Content may not be shared "with any third party … for any purpose" outside publication.
- Since 05/2026, programmatic use itself needs a paid licence.

A router listed in the MCP Registry is exactly third-party access. The unauthenticated endpoints are not an open licence either: the gene list and curated-gene summaries are still "Content" under the same Terms, and no CC licence is attached anywhere.

### Fleet fit (only if MSK grants written permission)
This would be a live proxy behind a service credential, disabled by default.

Proposed tools:
- `annotate_variant` (`hugo_symbol`, `alteration`, `oncotree_code`)
- `get_gene` (curated gene summary and highest levels)
- `list_cancer_genes`
- `get_evidence_levels`
- `annotate_copy_number`
- `annotate_structural_variant`
- `resolve_tumor_type` (OncoTree)

Join keys: HGNC symbol / Entrez (hgnc); HGVSg (vep); OncoTree ↔ MONDO needs a crosswalk.

### Scientific value
OncoKB uniquely answers "is BRAF V600E actionable in melanoma, and at what FDA-recognised level". It is the field standard for tumour-board actionability, and FDA recognition gives it an authority that CIViC lacks.

### Verdict
**PARK (licence).**
- Effort if unlocked: M.
- Clean-ish partial today: none without permission. Even the open gene list is Terms-governed.
- Lowest-cost ask: email MSK (contact@oncokb.org) for permission to re-expose `cancerGeneList` and `levels` metadata in a research-only MCP. Until then, write a **router skill** that explains the levels and deep-links users to oncokb.org. Linking is not redistribution.

---

## 2. COSMIC (Wellcome Sanger / GRL; commercial via QIAGEN) — PARK (hard)

### What it is
- The world's largest curated somatic-mutation catalogue. The current release is **v105** (cosmickb.org homepage).
- Modules: Cancer Gene Census (CGC), Cancer Mutation Census (CMC), Actionability, mutational signatures (**v3.6**), and Cell Lines.
- Citations:
  - Sondka Z et al., *NAR* 2024, "COSMIC: a curated database of somatic variants and clinical data for cancer", PMID **38183204**.
  - CGC: Sondka 2018 *Nat Rev Cancer*, PMID **30293088**.
  - Signatures: Alexandrov 2020 *Nature*, PMID **32025018**.

### Access (probed live)
- `cancer.sanger.ac.uk/cosmic` → **302 to `/cosmic/login`**. `/census` → login. `/cosmic/license` → `cosmickb.org/licensing`. **The whole web platform now requires login**, and "All users must be registered".
- There is **no public REST API**. `cancer.sanger.ac.uk/api/v1/` returns 404. Scripted downloads need an authenticated account.
- Signatures (`cancer.sanger.ac.uk/signatures/`) are **200 without login** and offer v1–v3.6 SBS/DBS/ID/CN/SV/RNA-SBS reference matrices. The page states: "While you do not need to log in to access this module, **use of the data is still subject to the COSMIC Terms and Conditions**."
- A third-party mirror exists at NLM Clinical Tables (`clinicaltables.nlm.nih.gov/api/cosmic/v4/search?terms=BRAF V600E` → 200, 708 hits, `[["BRAF","p.V600E","c.?","skin"],…]`). Its own docs say: "**Due to the source data's terms of use, this API does not support pagination**". This confirms COSMIC's terms constrain even NLM, and it is not a clean route.

### License (COSMIC Licence T&Cs, "Version last amended 30 October 2025")
- **Scope reaches identifiers.** 1.15 defines "Use COSMIC" as access/use/download "…including without limitation **any COSMIC identifiers such as a mutation ID (COSV/COSM)**."
- **2.1.2**: "Except for Publication Enablement, Licensee **may not transfer, grant access to, display, share or otherwise distribute COSMIC to any third party or on any free-to-access platform (including without limitation any public-facing website)** in any form or manner without GRL's express written consent."
- **4.6**: "Licensee **must not use data from COSMIC to train any Artificial Intelligence models** without express written consent."
- **4.7**: "…shall not distribute, sub-license, sell, lend, or **provide access (including without limitation via a public-access internet site)** to the whole or any part of COSMIC to any third parties…"
- **Part C (h)**: "Users **cannot expose COSMIC data on a free to access platform**."
- **Part C (e)(ii)**: not-for-profits that "offer bioinformatic services using COSMIC data" need a **commercial** licence. A GeneFoundry MCP serving COSMIC is arguably such a service even when free.
- Academic Use is defined as "internal academic research" by a not-for-profit only.

**Verdict on the fleet model:** incompatible on four separate grounds: third-party access, free-to-access platform, bioinformatic service, and AI. This applies to every module, including CGC, CMC, Actionability and signatures, and even to COSV/COSM IDs.

### What is free and redistributable
- Free: academic, registered, internal use only.
- Redistributable: **nothing** without GRL's written consent. The only exception is disclosure needed to publish a paper.

### Clean partial alternatives
- **Cancer-gene list / role (CGC substitute):** IntOGen Compendium of Cancer Drivers. The intOGen FAQ states "available free of restrictions under the **Creative Commons Zero** Public Domain Dedication". It gives driver genes per cancer type with mode of action (Act/LoF), which is about 80% of the CGC use case.
- **Hotspot / recurrence (CMC substitute):** cancerhotspots.org (MSK; licence unverified, check it) or AACR Project GENIE public releases (registration and terms apply; check them). Both belong to another agent's scope.
- **Mutational signatures:** the reference signature matrices published with Alexandrov et al. 2020 (*Nature*, open-access; the supplementary data licence must be checked per file). SigProfiler tools are BSD-2, but the COSMIC v3.x matrices bundled in them remain COSMIC-T&C-governed, so do not assume they are clean.

### Fleet hygiene note
No current fleet backend exposes COSMIC IDs (grep of `vep-link` and the other `*/src` found none). However, Ensembl VEP's `colocated_variants` can return COSV IDs. If vep-link ever exposes `colocated_variants`, filter COSV/COSM IDs out, because Part C(c)/(h) treats public display of COSMIC IDs as licensed use.

### Verdict
**PARK (hard licence block).** No negotiation path is realistic for a free public platform: (e)(ii) makes it commercial. Use IntOGen (CC0) for driver genes.

---

## 3. QIAGEN HSMD (Human Somatic Mutation Database) — PARK (commercial-only; SKIP)

- **What it is:** a QIAGEN Digital Insights web app that combines QIAGEN's real-world oncology dataset with about 20 years of manual curation. Marketing claims are inconsistent:
  - more than 300k clinical oncology cases and more than 1.3M mutations;
  - another page says about 70k new cases and 60k unique variants per year.

  Content is updated weekly. QIAGEN says it is "not intended for the diagnosis, prevention, or treatment of a disease".
- **Access:** annual subscription, sold as a **10-user online licence**. "QIAGEN HSMD Online" is listed as a hosted offering in the QDI EULA SaaS exhibit (Jan 2025). There is **no public API, no academic free tier** (unlike HGMD Public), and no download.
- **License:** the QDI standard terms grant API use, where offered, as "limited, nonexclusive, non-transferable, **non-sublicensable**". The exhibit PDF could not be fetched (it renders as an empty HTML shell). There was nothing to probe; nothing is unauthenticated.
- **Verdict:** **SKIP/PARK.** Named-user commercial SaaS is fundamentally incompatible with a router that third parties can reach. There is no partial alternative from QIAGEN. Use IntOGen + CIViC + ClinVar somatic instead.

---

## 4. JAX Clinical Knowledgebase (CKB CORE) — PARK (hard; explicit anti-AI clause)

- **Ownership change:** `ckbhome.jax.org` and `ckb.jax.org` now **301 → `ckbhome.genomenon.com`**. CKB is operated by **Genomenon** under the Genomenon EULA (effective 24 Jun 2024).
- **Tiers:**
  - CORE: free registration, **50 hotspot-panel genes**, web only.
  - BOOST: 2,300+ genes, web.
  - FLEX: paid, flat file or JSON.

  The site says: "CKB CORE and BOOST provide web-based access only, **with no option to download content**."
- **Access probe:** gene pages and `/api/*` → **302 to `/login/auth`**. There is no anonymous access.
- **License (Genomenon EULA):**
  - §3.1.1 limits use to "personal use or … Entity's internal research or internal business purposes".
  - §3.1.3 API: "**prohibited from importing the Results … into any externally facing database or data deliverable** and from … providing access to such Results".
  - §4.1: no right to use the Licensed Materials for "training, pre-training, downstream fine-tuning, or **evaluating and deploying a machine learning large language model (LLM), generative artificial intelligence, or any other model or practice utilizing AI technologies**, without the express written permission".
  - §4.1 also prohibits: "Use, reproduce, or **scrape** the Licensed Materials for any purpose".
  - The claim in the VICC docs that CKB is "CC-BY-NC-SA" is **outdated**. It reflects a historical subset distributed via the VICC meta-KB, not the current terms.
- **Citation:** Patterson SE et al., *Hum Genomics* 2016, PMID **26772741**.
- **Verdict:** **PARK (hard).** An LLM-serving MCP is exactly the "deploying an LLM … utilizing AI technologies" use that §4.1 names. There is no clean partial route. The CC-BY-NC-SA subset frozen in the VICC meta-KB is old, and NC is still incompatible with the fleet.

---

## 5. PMKB (Weill Cornell Precision Medicine Knowledgebase) — PARK (unreachable), licence probably the most permissive of the five

- **What it is:** Englander Institute (WCM) curated clinical interpretations of cancer variants. It is tier-based, written by board-certified pathologists, and covers about 600+ interpretations (per the paper). It has a REST API (HGVS protein → interpretations) and CSV/Excel bulk downloads of interpretations and variants (per the About page in search snippets).
- **Citation:** Huang L et al., *JAMIA* 2017, PMID **27789569**.
- **Access probe:** `pmkb.weill.cornell.edu` (157.139.68.125) **TCP-times-out on 443 and 80** from this host, and WebFetch gets **ECONNREFUSED**. The service is down or network-restricted as of 2026-11-20, so the API could not be probed.
- **License:**
  - The VICC meta-KB licence FAQ lists PMKB as **CC BY 4.0** (secondary source; PMKB's own statement could not be retrieved).
  - The About page carries a no-warranty disclaimer.
  - If CC BY 4.0 is confirmed, it is the only one of the five that is compatible: snapshot plus attribution.
- **Concerns:** the curation appears low-cadence. Interpretations are clinical-report prose, which needs untrusted-text fencing. It overlaps heavily with CIViC (CC0), which is larger and actively curated.
- **Verdict:** **PARK (access).**
  - Re-probe later. If it is reachable and CC BY 4.0 is confirmed, it is at most a **P3 EXTEND** (a snapshot folded into a future CIViC-based somatic link), not a standalone link.
  - Effort: S.

---

## 6. Recommended legally clean substitutes (for the synthesis)

| Need | Encumbered source | Clean substitute | Licence |
|---|---|---|---|
| Clinical actionability (variant → drug → evidence level) | OncoKB, CKB, HSMD | **CIViC** (civicdb.org GraphQL, 200 live) | data CC0, code MIT |
| FDA/guideline-level relationships | OncoKB L1/L2 | **MOAlmanac** (`vanallenlab/moalmanac-db`) | **CC-BY-4.0** (GitHub licence API) |
| Cancer-gene list and role (oncogene/TSG) | COSMIC CGC, OncoKB cancerGeneList | **IntOGen Compendium of Cancer Drivers** | **CC0** (intOGen FAQ) |
| Somatic recurrence / hotspots | COSMIC CMC | cancerhotspots.org, GENIE public, ClinVar somatic (fleet's clinvar-link) | verify each |
| Mutational signatures | COSMIC Signatures v3.6 | Alexandrov 2020 supplementary matrices | check per file |
| Evidence-level vocabulary | OncoKB levels | AMP/ASCO/CAP 2017 tiers (published guideline), router skill text | paraphrase |

**Router-skill option (zero licence risk):** a `skills/` workflow that tells agents how to reason about OncoKB and COSMIC evidence and **deep-links** to oncokb.org / cosmickb.org for the human user to view under their own account. No content passes through GeneFoundry.

## Sources
- OncoKB public source: github.com/oncokb/oncokb-public — `TermsPage.tsx`, `shared/texts/LicenseExplanation.tsx`, `config/constants/terms.ts`, `TokenSecurityConfiguration.java`, `NewsContent042026.md`
- FDA recognition: https://www.fda.gov/drugs/resources-information-approved-drugs/fda-recognizes-memorial-sloan-kettering-database-molecular-tumor-marker-information
- COSMIC licensing: https://www.cosmickb.org/licensing/ ; T&Cs: https://www.cosmickb.org/terms/ (amended 30 Oct 2025)
- COSMIC signatures: https://cancer.sanger.ac.uk/signatures/downloads/
- NLM COSMIC mirror: https://clinicaltables.nlm.nih.gov/apidoc/cosmic/v4/doc.html
- QIAGEN HSMD: https://www.qiagen.com/us/products/discovery-and-translational-research/next-generation-sequencing/informatics-and-data/interpretation-content-databases/hsmd ; QDI SaaS exhibit (Jan 2025)
- CKB: https://ckbhome.genomenon.com/ ; EULA https://ckbhome.genomenon.com/legal/eula
- PMKB: https://pmkb.weill.cornell.edu/about (unreachable 2026-11-20); VICC licence FAQ https://docs.cancervariants.org/en/stable/license.html
- IntOGen licence: https://www.intogen.org/faq
