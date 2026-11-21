# Pharmacogenomic report checks

Standard terms: CPIC (Caudle 2017, PMID 27441996). Sources: ClinPGx/PharmGKB, CPIC.

1. **Alleles tested.** The report lists which star alleles or variants the assay can
   detect. A "*1/*1" (normal) call is only "no tested variant found"; untested alleles must
   be stated as a limitation.
2. **Diplotype → phenotype.** The phenotype term is a CPIC standard term (e.g. "poor
   metabolizer", "intermediate metabolizer", "decreased function"). Check the diplotype's
   phenotype against CPIC data via `clinpgx_search_dataset` (CPIC allele / diplotype tables)
   or `clinpgx_get_website_data` (CPIC routes); find the right dataset id first with
   `search_tools`. If no table is reachable, apply the CPIC standard-term rule itself: two
   no-function alleles = poor metabolizer; one no-function plus one normal-function allele =
   intermediate metabolizer (Caudle 2017). A phenotype that contradicts the diplotype is Critical
   when it changes the drug recommendation.
3. **Activity score.** Where CPIC uses one (e.g. CYP2D6, DPYD), add the allele values step
   by step and compare with the stated phenotype.
4. **Drug recommendations.** Each recommendation cites the CPIC (or DPWG) guideline and
   its level; no dose that the guideline does not state. A recommendation that contradicts
   the guideline is Critical.
5. **Copy number and hybrids.** CYP2D6 deletions, duplications and hybrid alleles: was
   copy number assessed? If not, the report must say so.
6. **Scope.** Germline PGx results apply lifelong; the report should say they are relevant
   for future prescriptions.
