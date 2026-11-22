# Answer key: planted errors in the report-review fixtures

SYNTHETIC. `quote` is the exact planted text in the fixture (a finding is a hit when its
quote contains this text or this text contains the finding's quote of at least 12 characters).
Unplanted Critical/Major findings are adjudicated by hand: a real error is not a false accusation.

| id | fixture | quote | severity | category |
|---|---|---|---|---|
| E1 | report-01-brca1 | p.(Gln1756Glyfs*12) | Major | nomenclature (c./p. disagree; correct p.(Gln1756Profs*74)) |
| E2 | report-01-brca1 | including a functional study demonstrating loss of BRCA1 function (PMID: 26931183) | Major | citation not about the variant (PMID 26931183 is HGVS nomenclature) |
| E3 | report-01-brca1 | which is inherited in an autosomal recessive manner | Critical | wrong inheritance for HBOC (autosomal dominant; BRCA1 also has a definitive AR association, Fanconi anaemia, which is not this disease) |
| E4 | report-01-brca1 | We recommend that he discuss intensified breast cancer surveillance with his gynaecologist | Major | sex/pronoun inconsistency (female patient) |
| E5 | report-01-brca1 | Each child of the patient has a 25% risk of inheriting the variant | Critical | wrong recurrence risk (50% for AD) |
| E6 | report-01-brca1 | This test detects all clinically relevant variants in the genes analysed | Major | overstated sensitivity, no CNV/limitation statement |
| E7 | report-02-cftr | 37-year-old man | Critical | age inconsistent with year of birth 2015 |
| E7 | report-02-cftr | year of birth 2015 | Critical | alternate quote for E7 (the same inconsistency quoted from the patient line) |
| E8 | report-02-cftr | chr7:117199645-117199647 (GRCh38) | Major | build mismatch (GRCh37 coordinate; GRCh38 is chr7:117559592-117559594 after HGVS 3prime shift, VEP; ClinVar's GRCh38 span 117559591-117559593 is equally correct) |
| E9 | report-02-cftr | The variant has an allele frequency of 0.0001 in gnomAD and is therefore rare (PM2) | Major | frequency contradicted (gnomAD v4 AF about 0.012); PM2 misapplied |
| E10 | report-02-cftr | The heterozygous CFTR variant confirms the diagnosis of cystic fibrosis in this patient | Critical | single heterozygous variant in an AR gene called diagnostic |
| E11 | report-02-cftr | No further testing is required | Major | recommendations missing (deletion/duplication and deep-intronic testing, sweat test, partner testing) |
| E12 | report-03-negative | Whole-exome sequencing: normal result | Major | method/conclusion mismatch (panel) and "normal result" wording |
| E12 | report-03-negative | Targeted NGS epilepsy panel of 150 genes | Major | alternate quote for E12 (the same method/conclusion mismatch quoted from the method line) |
| E13 | report-03-negative | A genetic cause of the epilepsy has been excluded | Critical | overstated negative |
| E14 | report-03-negative | Benign variant identified: SCN1A c.3199G>A p.(Ala1067Thr), heterozygous, benign | Minor | benign variant listed in a negative report |
| E15 | report-03-negative | Heterozygous pathogenic variant in MYBPC3 (hypertrophic cardiomyopathy) identified as a secondary finding | Critical | secondary finding reported against documented refusal |
| E16 | report-03-negative | Reanalysis of the data is not recommended | Minor | reanalysis/recontact statement missing or wrong |
| E17 | report-03-negative | Not applicable. | Major | limitations missing (coverage, CNVs, repeats, mosaicism) |
