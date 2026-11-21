# Full somatic workflow (optional)

Read only when the routing table in SKILL.md leaves a question open (no approval and no
curated evidence, conflicting sources, or the user asks for prevalence, biology or a full
oncogenicity work-up).

## Steps

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
