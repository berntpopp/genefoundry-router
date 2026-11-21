---
name: somatic-variant-interpretation
description: Use when asked whether a somatic (tumour, cancer) variant is oncogenic or actionable, or how often a somatic variant occurs in a tumour type — e.g. "is BRAF V600E actionable in melanoma", "is TP53 R175H a hotspot", "KRAS G12C prevalence in lung adenocarcinoma". Research use only.
---

# Somatic variant interpretation

Research use only. Not clinical decision support. Do not use for diagnosis, treatment,
triage, or patient management. The output is **not a clinical actionability report** and never
a clinical tier for a patient.

Reach every tool below through `search_tools` → `call_tool` (only pinned entry points are
listed up front). Tool names are `<namespace>_<tool>`.

## Three axes — never merge them

| Axis | Question | Sources |
|---|---|---|
| Oncogenicity (ClinGen/CGC/VICC, Horak 2022) | Does the variant drive cancer? | ClinVar `oncogenicity`, CIViC oncogenic assertions, hotspots, population frequency, functional/predictor evidence |
| Actionability (AMP/ASCO/CAP 2017 tier) | Which therapy, at what evidence level, in which tumour type? | ClinVar `somatic_clinical_impact`, CIViC assertions/evidence, MOAlmanac approvals, Open Targets drugs |
| Germline pathogenicity (ACMG/AMP) | Is it a heritable disease variant? | ClinVar germline classification (other skills) |

Report each axis separately with its source. ClinVar's germline "Conflicting" for BRAF V600E
says nothing about its somatic Tier I status.

## Workflow

1. **Normalize the variant.** `vep_annotate_variant` (MANE transcript HGVS, GRCh38) and
   `gnomad_resolve_variant_id`. Keep the gene symbol + protein change (e.g. `BRAF` `V600E`)
   for the cancer sources. Normalize the tumour type: `mondo_search_diseases`, and
   `mondo_map_cross_ontology` with `prefixes: ["ONCOTREE"]` to get the OncoTree code
   (e.g. ONCOTREE:SKCM ↔ MONDO:0005012) that cBioPortal uses.
2. **ClinVar somatic.** `clinvar_get_variant` returns `somatic_clinical_impact` (tier) and
   `oncogenicity` as their own blocks beside the germline classification;
   `clinvar_search_somatic_classifications` lists a gene's tiered / oncogenic variants
   (filters `gene_symbol`, `tier`, `oncogenicity`, `tumour_type`).
3. **CIViC evidence.** `civic_search_molecular_profiles` (gene + variant, HGVS, CAID or
   ClinVar id) → use the `simple` profile; complex profiles (combinations) are listed
   separately — never fold them into the single-variant answer. Then
   `civic_search_assertions` / `civic_search_evidence_items` with `molecular_profile_id`
   and `disease` (accepted evidence by default). CIViC diseases are DOID; crosswalk to MONDO
   with `mondo_resolve_xref` when joining. CIViC coordinates are GRCh37-first: read the
   `assembly` label.
4. **Regulatory approvals.** `moalmanac_search_approvals` (`gene`, `biomarker`, `disease`,
   `therapy`, `agency`) — FDA/EMA/Health Canada/HSE/HPRA label versions and dates. It fills
   CIViC gaps (e.g. KRAS G12C NSCLC). HER2 and ERBB2 are expanded to each other.
5. **Prevalence.** `cbioportal_get_gene_alteration_frequency` (`gene`, `cancer_type`,
   `protein_change`) and `cbioportal_get_gene_mutations`. Always quote numerator,
   **profiled** denominator, study id and cohort; restricted-licence studies are excluded
   unless asked for, and stay flagged. Hotspot status: `cbioportal_get_mutation_hotspots`
   (Cancer Hotspots v2 + 3D snapshot; `residue`, `assembly`).
6. **Gene-level biology and drugs.** `opentargets_resolve_target` →
   `opentargets_get_target_tractability`, `opentargets_get_drug_candidates` (`disease`
   narrows), `opentargets_get_target_disease_evidence`. Drug stages lag regulators — prefer
   MOAlmanac for approval status.
7. **Oncogenicity evidence (Horak 2022 criteria), assembled:** hotspot recurrence
   (cBioPortal hotspots), population frequency (`gnomad_get_variant_frequencies`; common in
   gnomAD argues against a somatic driver), null variant in a tumour suppressor
   (`autopvs1_get_variant_pvs1_data`, VEP consequence), functional data
   (`mavedb_search_score_sets`), predictors (`spliceai_predict_splicing`,
   `metadome_get_tolerance_landscape`), plus ClinVar `oncogenicity` and CIViC oncogenic
   assertions. List the criteria you can support and the ones you cannot; do not compute a
   final classification.
8. **Literature.** `pubtator_search_literature` / `litvar_get_variant_literature` for
   primary sources behind a claim.

## Reporting rules

- Every prevalence number carries source, numerator / profiled denominator, cohort/study
  and genome build.
- Every coordinate carries its assembly (GRCh37 vs GRCh38); join on MANE HGVS or VEP
  liftover, never on raw positions across builds.
- An empty result means "no CIViC evidence found" / "no MOAlmanac approval found" — never
  "not actionable" or "not approved". Coverage is limited to these open sources.
- Cite the source record ids (CIViC EID/AID, ClinVar VCV, MOAlmanac indication id, study id).
- OncoKB, COSMIC, AACR GENIE and JAX CKB are not in the fleet (licence). You may suggest the
  user check them directly; never present their content.

## Tool calls

<!-- tool-calls:start -->
- `autopvs1_get_variant_pvs1_data`(genome_build, variant_id, meta_mode, include_unmet) — direct
- `cbioportal_get_gene_alteration_frequency`(gene; optional: cancer_type, study_ids, protein_change, include_restricted, max_studies) — via call_tool
- `cbioportal_get_gene_mutations`(gene, study_id; optional: protein_change, include_restricted, limit) — via call_tool
- `cbioportal_get_mutation_hotspots`(gene; optional: residue, assembly) — via call_tool
- `civic_search_assertions`(; optional: molecular_profile_id, disease, therapy, assertion_type, amp_level, status, limit) — via call_tool
- `civic_search_evidence_items`(; optional: molecular_profile_id, disease, therapy, evidence_type, evidence_level, significance, status, limit) — via call_tool
- `civic_search_molecular_profiles`(query; optional: limit) — via call_tool
- `clinvar_get_variant`(identifier, id_type) — direct
- `clinvar_search_somatic_classifications`(gene_symbol, tier, oncogenicity, tumour_type, identifier, limit) — via call_tool
- `gnomad_get_variant_frequencies`(variant_id, dataset, populations, include_subcohorts, include_sex_split, exclude_zero_populations) — direct
- `gnomad_resolve_variant_id`(query, dataset, limit, enrich) — direct
- `litvar_get_variant_literature`(variant_id, limit) — direct
- `mavedb_search_score_sets`(; optional: text, targets, target_organism_names, target_types, authors, facet_mode, published, limit) — direct
- `metadome_get_tolerance_landscape`(transcript_id, position_start, position_stop, limit) — direct
- `moalmanac_search_approvals`(; optional: gene, biomarker, disease, therapy, agency, biomarker_type, limit) — via call_tool
- `mondo_map_cross_ontology`(term; optional: prefixes) — via call_tool
- `mondo_resolve_xref`(xref_id; optional: limit) — via call_tool
- `mondo_search_diseases`(query; optional: limit, include_obsolete) — direct
- `opentargets_get_drug_candidates`(target; optional: disease, limit) — via call_tool
- `opentargets_get_target_disease_evidence`(target; optional: disease, datasource, limit) — via call_tool
- `opentargets_get_target_tractability`(target) — via call_tool
- `opentargets_resolve_target`(query) — via call_tool
- `pubtator_search_literature`(text, page, sort, filters, publication_types, year_min, year_max, sections, include_citations, text_hl_format, limit, entity_ids, guideline_boost, coverage, metadata, include_meta) — direct
- `spliceai_predict_splicing`(variant_id, genome_build, max_distance, mask, gene_set, transcripts, cross_build_check, include_hints, include_see_also, correlation_id) — direct
- `vep_annotate_variant`(variant, assembly, transcripts, allele, vep_options) — direct
<!-- tool-calls:end -->

## Evidence rules (all GeneFoundry skills)

- Research use only. Not clinical decision support. Use de-identified input only; if the input
  contains names, dates of birth or record numbers, stop and ask for a de-identified version.
- Every fact comes from a tool result in this session. Cite the record id the tool returned
  (PMID, VCV, HGNC id, MONDO id, ...). Never cite from memory.
- Give every checked claim one state: **confirmed** (tool agrees), **contradicted** (tool disagrees;
  show the evidence), **not confirmable** (tool returned nothing or failed), **not checked**.
  "Not found in source X" never means "negative" or "benign".
- State genome build and transcript before any variant lookup. Resolve identifiers once, in batch.
- Tools marked "via call_tool" below are not in your tool list: call them as
  `call_tool(name="<tool>", arguments={...})` straight away, with the argument names given,
  without trying the bare name or searching for them first.
- Scores, points and risks are added up step by step by the stated rule, never estimated.
- If a call errors or is rate limited, do not repeat the identical call: use another source or
  report the gap. Stop when the skill's stop rule is met.
- End with: tools used and the data versions their results already report (never make a call
  only to get a version).
