# G — Can OncoKB / AACR GENIE be "unlocked" for a public research MCP gateway?

Researched 2026-11-20. No emails sent, no forms submitted, no logins. Evidence = public pages, the
OncoKB website source (github.com/oncokb/oncokb-public, master @ 2026-11-06), Synapse REST API,
live curl probes, and a read-only Chrome view of the GENIE Terms-of-Use Google Form.

---

## Q1 OncoKB — VERDICT: NO for therapeutic/level/variant content without a signed MSK license. The only defensible things are (a) deep links to oncokb.org and (b) nothing else; even the tokenless gene lists carry no separate license.

### Terms of Use (oncokb.org/terms)
oncokb.org is a React SPA; the terms text lives in
`oncokb-public/src/main/webapp/app/pages/apiAccessGroup/TermsPage.tsx` (last content change
2023-11-14 "Update Terms"; still current on master). Decisive clauses:

- "You may view the Content solely for your own personal reference or use for research in an
  academic setting ... outside of scientific publication, you may not otherwise redistribute or
  share the Content with any third party, in part or in whole, for any purpose, without the
  express permission of MSK."
- "Unless you have signed a license agreement with MSK, you may not use any part of the Content
  for any other purpose, including: use or incorporation into a commercial product or towards
  performance of a commercial service; research use in a commercial setting; use for patient
  services; or generation of reports in a hospital or other patient care setting."
- "You may not copy, transfer, reproduce, modify or create derivative works of OncoKB for any
  commercial purpose without the express permission of MSK."

"Content" = everything on oncokb.org. A public MCP endpoint serving anonymous hosts/LLM users is
by definition sharing with third parties -> prohibited without express MSK permission.

### Licence types and the 2026 paywall/AI language (registration / api-access flow)
- `shared/texts/LicenseExplanation.tsx` (commit 733bddf9, **2026-05-15**, "update paywall language
  related to clinical reporting (#1358)"): "OncoKB is freely accessible for academic research use
  and for manual clinical reference through the OncoKB website. A paid license is required for
  commercial use or for programmatic/API-based use of OncoKB content, including integration into
  clinical reporting workflows."
- `components/newAccountForm/ApiAccessSection.tsx` (commit 9880e694, **2026-05-01**, "Hospitals now
  have to justify API access (#1351)"): API request only offered for ACADEMIC and HOSPITAL licence
  types; hospital: "API access falls under a paid hospital license." Academic: "Should you request
  API access, you must provide a detailed description on how you plan to use OncoKB APIs."
- `config/constants.tsx` CLINICAL_TERMS checkboxes: "I understand that I may not redistribute or
  share the content with any others, in part or in whole, for any purpose." / "I understand that
  API access, automated integrations, and incorporation of OncoKB content into clinical reporting
  systems or workflows require a paid license."
- ACADEMIC_TERMS checkboxes: "I agree that my use of OncoKB is solely for research or educational
  purposes." / "I confirm that I will NOT use OncoKB data for the purpose of creating and training
  AI models." + agree to Terms of Use.
- `config/constants/terms.ts` (commit 109481df, 2024-03-11 "Add term related to AI model usage"):
  "Using OncoKB data for the creation and training of AI models is strictly prohibited. Access may
  be granted for model validation or benchmarking purposes only with explicit authorization."
- `components/newAccountForm/ImportantNotes.tsx`: "use of OncoKB in AI/ML is extremely restricted".
- News April 2026 (`newsPage/markdown/NewsContent042026.md`, v7.1, 2026-04-30): "We recognize that
  our new registration requirement has raised questions ... We are actively refining and clarifying
  our terms of use ..." — i.e. website registration became required in spring 2026.

Net: academic API tokens still exist (free, screened, per-person, no redistribution, no AI
training). Hospital/commercial programmatic use = paid. None of the licence types covers
"public service re-exposing OncoKB to third parties".

### FAQ (https://faq.oncokb.org/licensing, GitBook, "Last updated 2 years ago")
- "A license is required to use OncoKB for commercial and/or clinical purposes, and to access
  OncoKB data programmatically for academic purposes."
- "all users, even without registering, can download The Cancer Gene List, the All Curated Genes
  List, and the Biomarker-Drug Association List from OncoKB." (download permission, NOT a
  redistribution licence — the Terms still govern use.)
- "Can I use OncoKB to train my AI/ML model? No. OncoKB cannot be used to train AI/ML models whether
  for academic or commercial purposes. With explicit permission, OncoKB may be used for
  benchmarking existing AI/ML models."
- No "open-source project" exception exists anywhere in Terms, FAQ, or registration code.

### Is the cancer gene list separately (permissively) licensed? — NO
- Live: `https://www.oncokb.org/api/v1/utils/cancerGeneList.txt` 200 (139,688 B, tokenless);
  `allCuratedGenes.txt` 200 (1.69 MB, tokenless, includes OncoKB-authored gene Summary/Background);
  `allActionableVariants.txt` 401 "Full authentication is required". Same on public.api.oncokb.org.
- Repo licences: oncokb/oncokb = AGPL-3.0, oncokb/oncokb-annotator = AGPL-3.0 — these cover
  *code*. oncokb-public, oncokb-transcript, oncokb-datahub have no LICENSE at all. No data file
  carries CC/ODbL/any open licence. `CancerGenesPage.tsx` has no licence text. So the lists fall
  under the website Terms (no third-party redistribution). The cancer gene list is largely a
  compilation of third-party sets (COSMIC CGC v99, Vogelstein 2013, MSK-IMPACT/F1 panels) plus
  OncoKB's own oncogene/TSG calls — the OncoKB-specific columns are MSK Content.

### GitHub (issues/discussions about LLM/MCP/agents/third parties)
- `gh search issues` over org oncokb: LLM/chatgpt = 0 hits. MCP = oncokb/oncokb#4221-#4224
  (2026-09-25, open): "MCP server for agent curation" — an *internal curation-platform* MCP
  requiring scoped tokens ("MCP calls require a scoped API token"; non-goal "autonomous curation
  without an accountable user"). Not a public/redistributable MCP and not a licensing signal.
- "redistribute": oncokb#1834 (2019) "Add one more checkbox about the redistribution" — i.e. they
  tightened, not loosened.

### Piggy-backing via cBioPortal / Genome Nexus — NO
- Genome Nexus `annotation/7:g.140453136A>T?fields=oncokb` returns `oncokb: null` without a token.
- `www.cbioportal.org/proxy/oncokb/...` -> 404. cBioPortal displays OncoKB under MSK's own
  arrangement; nothing grants downstream re-exposure.

### What IS possible without a contract
1. Deep links (`https://www.oncokb.org/gene/BRAF/V600E`) — a URL is not redistribution of Content.
2. Nothing data-bearing. Even a per-user BYO-token pass-through conflicts with this gateway's
   design (service credentials only, no per-user tokens) and with "No token passthrough".
3. Formal path: email licenses@oncokb.org / contact@oncokb.org for express written permission
   (out of scope here — user's call).

---

## Q2 AACR GENIE — VERDICT: NO public redistribution, raw or aggregate, without express written permission from the GENIE Coordinating Center; since 2025 access is additionally gated by U.S. DOJ bulk-data (28 CFR 202) attestations that an anonymous public gateway cannot satisfy.

### Current Terms of Use (the actual click-through)
genie.cbioportal.org/login: "If you are a new user, you will need to register and agree to the
terms of use before you can use this portal." -> links to Google Form "AACR Project GENIE Terms of
Use" (docs.google.com/forms/d/e/1FAIpQLSfzQBxAbXWTIKNwML1JcxXd6peiTHbGFZNnN9c2aHKEDmqC5A/viewform;
viewed read-only, not submitted). Required "Agree" statements include:
- "By clicking 'agree' to any statement, you are certifying that the entire statement is true and
  correct, subject to any applicable penalties of perjury under the laws of the United States".
- "I declare that I have not been designated a 'Covered Person' by the U.S. Attorney General
  pursuant to 28 CFR 202.211(a)(5) and/or 202.701." + not a foreign person resident in / employed
  by entities of China (incl. HK/Macau), Cuba, Iran, North Korea, Russia, Venezuela.
- "I declare that if any of these attestations become untrue, I will immediately cease access to
  any GENIE data ..."
- "I will not redistribute the data, in whole or in part, without express written permission from
  the AACR Project GENIE Coordinating Center (send email to: info@aacrgenie.org)."
- Publication/presentation: cite Cancer Discov 2017;7(8):818-831 with dataset version, fixed
  acknowledgement, GENIE logo.

genie.synapse.org/Access (2026-11-20): "Access to the AACR Project GENIE Registry has been
restored. To regain access, users must submit a new data access request in compliance with updated
U.S. regulations, agree to the revised terms of use, and provide additional required information."
Synapse: project syn7222066 carries ManagedACTAccessRequirement 9606645 ("GENIE Access
Requirement_external link", modified 2026-02-25); even `AACR_Project_GENIE_Terms of_Use.pdf`
(syn50898929, modified 2026-07-14) returns 403 "There are unmet access requirements".
genie.cbioportal.org/api/studies -> 401.

### Aggregate / derived statistics
- The terms say "the data, in whole or in part" — no carve-out for aggregates/summary statistics.
  Gene x cancer-type mutation frequencies are derived "in part" from the data; publishing them in
  a *scientific publication* is explicitly allowed (with citation), but serving them as a live
  public data service is redistribution in practice. No text anywhere permits it.
- AACR GENIE FAQ (aacrprojectgenie.org/faq, dateModified 2026-08-10):
  "Q. Can I host public AACR Project GENIE® data in another system for external access?
   A. This depends on the use case and system. Please contact us to discuss further."
  -> the sanctioned path is case-by-case written permission, nothing self-serve.
- DOJ 28 CFR 202 (effective 2025-04-08; enforcement from 2025-07-08) restricts access to bulk human
  'omic data by countries of concern / covered persons. GENIE now requires per-user perjury-backed
  attestations; an unauthenticated public gateway cannot attest for its callers, which makes even
  aggregate re-exposure something only AACR can bless.

### Is any GENIE-derived aggregate publicly redistributed?
- www.cbioportal.org (public): `api/studies?keyword=genie` -> only `sarcoma_dfci_genie_2026`
  (48 DFCI cases, Brigham CAMD; not the GENIE registry). keyword `bpc` -> 0. The GENIE registry and
  BPC cohorts are only on genie.cbioportal.org (401/login).
- AACR itself publishes static per-cancer-type summary figures ("11 most common cancer types in the
  AACR Project GENIE registry", aacr.org) — AACR's own outreach, no reuse licence.
- My Cancer Genome historically showed frequencies "derived from the publicly available AACR
  Project GENIE genomic alteration dataset" (presumably under arrangement); www.mycancergenome.org
  currently fails TLS (cert for *.sandbox.plat.ttoworks.net) — not a usable source and not a
  licence for others.
- Nothing found that is an openly licensed GENIE aggregate table.

### What IS possible without permission
- Deep links to genie.cbioportal.org (user logs in themselves) and citation of GENIE publications.
- Formal path: email info@aacrgenie.org describing the gateway (user's decision; no email sent).

---

## Bottom line for the gateway
Neither source can be integrated as data on a public, unauthenticated, service-credential gateway
without a signed/written grant (OncoKB: MSK licence; GENIE: Coordinating Center written
permission). Ship link-outs only, or look to openly licensed somatic sources instead.

## Not checked / limits
- Did not read aacr.org "11 most common cancer types" page body (redirect lost in browser).
- OncoKB FAQ is a GitBook last updated ~2 years ago; the website code (2026) is the fresher source.
- Not legal advice; interpretation of "redistribution" for aggregates is the plain reading.
