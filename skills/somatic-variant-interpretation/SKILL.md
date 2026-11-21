---
name: somatic-variant-interpretation
description: Use when asked whether a somatic (tumour, cancer) variant is oncogenic or actionable, or how often a somatic variant occurs in a tumour type — e.g. "is BRAF V600E actionable in melanoma", "is TP53 R175H a hotspot", "KRAS G12C prevalence in lung adenocarcinoma". Research use only.
---

# Somatic variant interpretation

Research use only. Not clinical decision support. Do not use for diagnosis, treatment,
triage, or patient management. The output is **not a clinical actionability report** and never
a clinical tier for a patient.

## Three axes — never merge them

| Axis | Question | Sources |
|---|---|---|
| Oncogenicity (ClinGen/CGC/VICC, Horak 2022) | Does the variant drive cancer? | ClinVar `oncogenicity`, CIViC oncogenic assertions, hotspots, population frequency, functional/predictor evidence |
| Actionability (AMP/ASCO/CAP 2017 tier) | Which therapy, at what evidence level, in which tumour type? | ClinVar `somatic_clinical_impact`, CIViC assertions/evidence, MOAlmanac approvals, Open Targets drugs |
| Germline pathogenicity (ACMG/AMP) | Is it a heritable disease variant? | ClinVar germline classification (other skills) |

Report each axis separately with its source. ClinVar's germline "Conflicting" for BRAF V600E
says nothing about its somatic Tier I status.

## Routing (one call per question, then answer)

| Question | Call | Notes |
|---|---|---|
| variant identity, GRCh38 coordinate | `vep_annotate_variant`(`variant`= plain HGVS or `GENE p.X`) | take c./p. from the MANE transcript; frequency later via `gnomad_get_variant_frequencies`(`variant_id`=`CHROM-POS-REF-ALT` from VEP). Never send HGVS to `gnomad_resolve_variant_id` |
| ClinVar tier and oncogenicity | `clinvar_get_variant`(`identifier`= HGVS, VCV or rsID) | `somatic_clinical_impact` and `oncogenicity` blocks; `clinvar_search_somatic_classifications` takes a VCV/rsID or `gene_symbol`, never a protein string |
| approval in THIS tumour type | `moalmanac_search_approvals`(`gene`, `disease`= a word of the tumour type, `agency`= lowercase `fda`/`ema`) | leave `biomarker` out: labels are class names ("ERBB2 activating variants"); read the returned `biomarkers` and keep rows that cover this variant. `moalmanac_list_biomarkers`(`gene`) lists the names |
| tumour-agnostic approval | `moalmanac_search_approvals`(`gene`, `disease="solid"`) | "Any solid tumor" rows count for every solid tumour; FDA-only rows stay FDA-only |
| curated evidence | `civic_search_molecular_profiles`(gene + variant) → `civic_search_assertions`(`molecular_profile_id`, `disease`) | use the simple profile; level A/B in this tumour type |
| hotspot | `cbioportal_get_mutation_hotspots`(`gene`, `residue`= string such as `"V600"`) | `is_hotspot` |

Order and budget: identity → ClinVar → approvals (this tumour type, then "solid") →
CIViC → hotspot. At most 8 calls. Stop as soon as the tier is settled:
- an approval or level A/B evidence for this variant (or its class) in this tumour type →
  Tier I; the same only in another tumour type → Tier II;
- no approval and no curated evidence: oncogenic driver or hotspot → Tier III; benign or
  common in gnomAD → Tier IV.
Prevalence, biology, literature and a full oncogenicity work-up only if asked or still
open: read `references/full-workflow.md`.

## Reporting rules

- If the request specifies an answer format, use exactly that.
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
- `moalmanac_list_biomarkers`(; optional: gene, biomarker_type, limit) — via call_tool
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
