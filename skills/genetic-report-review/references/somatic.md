# Somatic (tumour) report checks

Standards: AMP/ASCO/CAP tiers (Li 2017, PMID 27993330); ClinGen/CGC/VICC oncogenicity
(Horak 2022, PMID 35101336). For the evidence chain itself, follow the
`somatic-variant-interpretation` skill (`skill://somatic-variant-interpretation/SKILL.md`).

1. **Three axes kept apart.** Oncogenicity (Horak), clinical actionability (AMP tier) and
   germline pathogenicity (ACMG) are separate statements. A report that uses one for the
   other (e.g. "Tier I, therefore pathogenic germline") is Major.
2. **Pre-analytics.** Tumour type, tumour cell content, sample type and, for liquid
   biopsy, the amount of cell-free DNA; variant allele fraction (VAF) per variant;
   the limit of detection. Missing VAF or tumour content is Major.
3. **Tier vs evidence.** Each tier claim is backed by a regulatory approval or guideline
   for this tumour type (`moalmanac_search_approvals`), or by curated evidence
   (`civic_search_assertions`, `clinvar_get_variant` somatic blocks). Tier I in one tumour
   type does not carry over to another.
4. **Negative wording.** "No evidence found in source X" is never "not actionable".
   A wild-type result for a biomarker must state the detection limit.
5. **Possible germline origin.** A VAF near 50% or 100% in a cancer-predisposition gene
   (`clinvar_get_variant` germline class, `gnomad_get_variant_frequencies`) should prompt a
   recommendation for germline testing with counselling.
6. **Nomenclature** as for germline (Pass 3), with the build stated; many cancer sources
   are GRCh37-first.
