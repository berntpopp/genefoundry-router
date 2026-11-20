# Negative or no-finding report checks

A negative report is mostly a statement about scope. Most of the review is Passes 1, 8, 9.

1. **Wording.** "No clinically significant (pathogenic or likely pathogenic) variant was
   identified that explains the phenotype" is acceptable. "Normal result", "genetic cause
   excluded", "negative for disease X" overstate what any test can show (Deans 2022,
   PMID 35361922). Overstating = Critical, because it stops further work-up.
2. **Scope.** The exact genes (panel name and version, gene list) or the exome/genome
   analysed, the transcript set, and the build. A named panel must contain the genes the
   indication needs (`panelapp_search_panels`, `panelapp_get_panel_genes`). An expected gene
   missing from the panel is Major.
3. **Method matches conclusion.** A targeted panel cannot be concluded as "exome
   sequencing normal", and a phenotype-filtered exome analysis depends on the HPO terms
   supplied: the report should say so.
4. **Limitations.** Coverage threshold and the share of target covered; regions below it
   in the key genes; what was not assessed (CNVs/SVs, repeat expansions, deep-intronic,
   mtDNA, mosaicism, pseudogene regions). Missing = Major (Matthijs 2016, PMID 26508566).
5. **No benign lists.** Benign and likely benign variants are not listed in the result
   (Deans 2022). Listing them is Minor; listing them in a way that reads as a finding is
   Major.
6. **VUS handling.** A VUS related to the question may be reported with a clear statement
   that it is not used for decisions; a VUS unrelated to the question follows the lab's
   stated policy.
7. **Secondary findings.** Present only if the documented consent allows them and the
   lab's policy covers them. A secondary finding reported against a documented refusal is
   Critical. Absence of secondary findings must not be worded as "no actionable variants".
8. **What next.** Reanalysis or recontact statement (Deignan 2019, PMID 31015575); other
   tests that fit the expectation from Pass 0 (CNV analysis, repeat testing, methylation,
   trio, genome, RNA).
