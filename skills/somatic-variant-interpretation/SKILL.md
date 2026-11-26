---
name: somatic-variant-interpretation
description: Use when asked whether a somatic (tumour, cancer) variant is oncogenic or actionable, or how often a somatic variant occurs in a tumour type — e.g. "is BRAF V600E actionable in melanoma", "is TP53 R175H a hotspot", "KRAS G12C prevalence in lung adenocarcinoma". Research use only.
---

# Somatic variant interpretation

Research use only. Not clinical decision support. The output is never a clinical tier for a patient.

## Three axes — report each separately, never merge

- **Oncogenicity** (does it drive cancer): ClinVar `oncogenicity`, CIViC, hotspots.
- **Actionability** (AMP/ASCO/CAP 2017 tier): ClinVar `somatic_clinical_impact`, MOAlmanac approvals, CIViC.
- **Germline pathogenicity** (ACMG): not this skill. ClinVar's germline class says nothing about the somatic tier.

## Calls, in this order (at most 7)

VEP and ClinVar `get_variant` are in your tool list; the others exist only through `call_tool`, exactly as written.

1. `vep_annotate_variant`(`variant`= HGVS or `GENE:p.X` with a colon). Use the allele whose `hgvsp` is the asked change.
2. `call_tool(name="clinvar_search_variants", arguments={"query": "GENE X123Y"})` → `clinvar_get_variant`(`identifier`= its VCV): read `result.somatic_clinical_impact` and `result.oncogenicity`.
3. `call_tool(name="moalmanac_search_approvals", arguments={"gene": "GENE"})`: read `summary.biomarkers` and `summary.diseases`. A row counts only for this alteration type (RET fusion ≠ RET M918T; HER2 amplification ≠ ERBB2 S310F). A class "<GENE> activating/oncogenic variants" covers an oncogenic or hotspot variant; for its diseases call again with `biomarker`= that class name.
4. `call_tool(name="civic_search_molecular_profiles", arguments={"query": "GENE X123Y"})` → `call_tool(name="civic_search_assertions", arguments={"molecular_profile_id": <integer>})`.
5. `call_tool(name="cbioportal_get_mutation_hotspots", arguments={"gene": "GENE", "residue": "V600"})`.

"Not found" from a search is an answer (no record), not an error: do not search that source again with other words; go on to the next call. An empty search never lowers a tier that another source already supports.

## Tier

- Approval or level A/B evidence for this variant or its class in this tumour type, an "Any solid tumor" approval for a solid tumour, or resistance to an approved therapy here (KRAS/NRAS-mutant colorectal: no anti-EGFR antibody) → **Tier I**.
- The same only in another tumour type, or level C/D evidence anywhere → **Tier II**.
- None of that → **Tier III** (unknown clinical significance), even when oncogenic or a hotspot. Benign or likely benign, or common in the population → **Tier IV**.

Stop when the tier is settled. Prevalence, biology or literature only if asked: read `references/full-workflow.md`.

## Report

- Use the requested format exactly, if one is given.
- Cite record ids (ClinVar VCV, CIViC AID/EID, MOAlmanac id). Every coordinate carries its build.
- Empty means "no approval/evidence found in these open sources", never "not actionable".
- OncoKB, COSMIC, GENIE and CKB are not available here; never present their content.

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
- `clinvar_search_variants`(query, gene_symbol, classification, min_stars, assembly, match_mode, count_mode, limit) — via call_tool
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
