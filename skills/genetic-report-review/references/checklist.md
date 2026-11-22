# Full checklist (optional)

Read this only for a full audit, or when the tool budget in SKILL.md is not used up.
The routing table in SKILL.md already covers the checks that change results most often.

**Pass 1: Does the test answer the question?** Was the test able to detect the suspected
mechanism? Does the panel or analysed gene list contain the expected genes (verify a named
panel with `panelapp_search_panels` / `panelapp_get_panel_genes` when the report names one)?
Does the method described match the test that the conclusion talks about (a targeted panel
must not be concluded as "exome")? A panel without a name, version or gene list cannot be
checked: Major, state "not confirmable".

**Pass 2: Identity and internal consistency.** Check, quoting the report:
- sex vs every pronoun, every sex-specific statement and X-linked reasoning;
- age or date of birth vs age-dependent claims (onset, penetrance, screening advice);
- the same gene, transcript, HGVS, zygosity and class everywhere (summary, table, text);
- no paragraph that belongs to another case (a gene, variant or name not in this case);
- no duplicated or contradicting paragraphs; verdict vs findings (a report with a P/LP or
  VUS finding is not worded as "no finding", and a "no finding" report lists no primary
  finding).

**Pass 3: Nomenclature (each variant).** Genome build and reference transcript stated
(MANE Select preferred, PMID 35388217). A "MANE Select" label is checked with
`hgnc_get_gene_cross_references` (`databases=["mane"]`); a non-MANE transcript labelled
MANE is Major. c. and p. agree with each other and with the
transcript; the genomic coordinate belongs to the stated build. Use
`vep_annotate_variant` (HGVS, MANE, consequence; `assembly` matters) and
`gnomad_resolve_variant_id`. Gene symbols current: `hgnc_resolve_symbols_batch`.
HGVS rules: PMID 39702242. Flag, never rewrite: give the corrected form as the fix.
VEP's default `most_severe_consequence` / representative transcript can come from another isoform: take the consequence, c. and p. from the transcript named in the input or the MANE Select entry in the transcript list.
One `vep_annotate_variant` call per variant already returns what to compare, so compare
before moving on: the report's transcript against the MANE Select transcript VEP flags
(a different transcript labelled "MANE Select" is Major), and the report's c. and p.
against VEP's `hgvsc` / `hgvsp` character by character, including the frameshift length
(`fs*2` vs `fs*12`).

**Pass 4: Classification (each variant).** Re-derive, do not re-read. First look for an
expert-panel (VCEP) classification of the variant: `clingen_get_variant_interpretations`
(`gene_symbol`), or `clingen_get_variant_interpretation` for one variant; a ClinVar record
with 3 stars (reviewed by expert panel) already is the panel class, no extra call. A VCEP class published
before the report date that differs from the report is Major (Critical if it changes P/LP vs VUS). Follow
`references/germline.md`: current ClinVar class, stars and review status
(`clinvar_get_variant`); frequency (`gnomad_get_variant_frequencies`); each asserted ACMG
code met at the stated strength; points added step by step; double counting; gene-specific
(VCEP) rules over generic ones. Report over-called and under-called codes and whether the
code combination yields the stated class. If the stated codes do not reach the stated class
but the class itself is confirmed (e.g. an expert-panel ClinVar record), that is Major
("code support"), with the class listed under Checks out. An expert-panel (VCEP) ClinVar
classification outranks a generic predictor such as `autopvs1_get_variant_pvs1_data`
(arguments `variant_id`, `genome_build`); report the disagreement, don't let the predictor
overrule the panel.

**Pass 5: Literature and numbers.** Every cited PMID must describe this variant (or the
stated claim), checked with `litvar_get_variant_literature` (rsID) or
`pubtator_get_publication_passages` (the PMID itself). A PMID that cannot be tied to the
allele is flagged "not confirmable", a PMID about something else is "contradicted". Every
clinical number, mechanism, inheritance mode, de novo rate and OMIM number is checked
against the source chapter with `genereviews_search_passages`. An OMIM number given for a disease must be a phenotype entry: check it with
`mondo_resolve_xref` (`xref_id="OMIM:<n>"`) or `hpo_get_genes_for_disease`
(`disease_id="OMIM:<n>"`). A gene entry (as in `hgnc_get_gene_cross_references` with
`databases=["omim"]`) cited as the disease number is an error, even if ClinVar shows it.

**Pass 6: Gene–disease and inheritance math.** Gene–disease validity for the disease
named (`clingen_get_gene_validity`, `gencc_get_gene_curations`; flag disputed or refuted).
The stated inheritance matches the curated one. Then the math: zygosity × inheritance × sex
must explain the conclusion. Classic misses: a single heterozygous variant in an autosomal
recessive gene called diagnostic; two variants called compound heterozygous without phase;
a hemizygous call in a female; mosaic allele fraction ignored; recurrence risks that do not
follow from the inheritance mode.

**Pass 7: Phenotype fit.** Does the disease spectrum, onset and penetrance fit the patient
(`hpo_get_phenotypes_for_gene`, GeneReviews)? Features left unexplained suggest a second
diagnosis; say so as a suggestion.

**Pass 8: What was not tested.** Limitations must name what the method cannot see:
coverage gaps in reported genes, CNVs/SVs, repeat expansions, deep-intronic, mtDNA,
mosaicism below the detection limit, pseudogene or homology regions (check a region with
`ucsc_get_region_annotations`, tracks `genomicSuperDups` / `alllowmapandsegdupregions`).
A "detects all relevant variants" claim is an error.

**Pass 9: Recommendations.** They follow from the class and inheritance: counselling
referral; cascade testing only for P/LP (never predictive testing of relatives for a VUS);
segregation or parental testing where it would change the class; RNA or functional
follow-up where relevant; reanalysis or recontact statement (PMID 31015575). Secondary
findings are reported only in line with the documented consent and the lab's stated
policy. In Germany: a genetic counselling offer (GenDG §10), the report goes to the
ordering physician (§11), prenatal reports contain no adult-onset findings (§15). Cite no other legal clause numbers.

**Pass 10: Freshness.** If ClinVar, ClinGen or the literature changed after the report
date, say what changed and whether it would change the class. That is a reclassification
flag, not an error of the original report.

**Pass 11: Communication.** A VUS is never worded as a cause. Uncertainty is stated.
There is one clear sentence for the referrer that answers the clinical question.
