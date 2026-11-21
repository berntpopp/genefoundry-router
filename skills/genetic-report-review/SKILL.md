---
name: genetic-report-review
description: Use when asked to review, second-check, audit or sign out a genetic test report (germline panel, exome or genome, CNV/array, somatic tumour, pharmacogenomic, predictive or segregation report), or to find errors in a lab report before it is released or acted on, e.g. "review this exome report", "second look at this BRCA1 report", "is this report ready to sign out".
---

# Genetic report review (second look)

Research use only. Not clinical decision support. This is a structured second look that
helps a qualified reviewer. It does not replace the signing geneticist or the lab's own
release process.

Reach every tool through `search_tools` → `call_tool` (only pinned entry points are listed
up front). Tool names are `<namespace>_<tool>`.

## Before you start

1. **De-identified input only.** If the report contains a real name, full date of birth,
   address or record number, stop and ask for a de-identified copy. Age, sex and dates
   relative to the report are enough.
2. **Report type.** Identify it and read the matching reference before the passes below:

   | Report type | Reference |
   |---|---|
   | Germline SNV/indel (panel, exome, genome) | `references/germline.md` |
   | CNV, array, exome/genome CNV calls | `references/cnv.md` |
   | Negative or no-finding report (any type) | `references/negative.md` |
   | Somatic / tumour | `references/somatic.md` |
   | Pharmacogenomic | `references/pgx.md` |
   | Predictive, carrier-cascade or segregation test | `references/predictive-segregation.md` |

   A report can need more than one (e.g. an exome with a CNV and a negative SNV part).
3. **Report date.** Note it. Evidence newer than the report date is a reclassification
   flag, not a reporting error.

## Passes (in this order)

**Pass 0: Form your own expectation first (anti-anchoring).** Read the report only up to
the result section: the indication,
phenotype, age, sex and family history. Write down, before reading the result: the genes or
mechanisms you would expect (SNV, CNV, repeat expansion, imprinting/methylation, mtDNA,
mosaicism), the expected mode of inheritance, and what a good answer to this question would
look like. Then read the result. Disagreement between your expectation and the report is
where to look hardest; it is not itself an error.

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

**Pass 4: Classification (each variant).** Re-derive, do not re-read. First look for an
expert-panel (VCEP) classification of the variant: `clingen_get_variant_interpretations`
(`gene_symbol`), or `clingen_get_variant_interpretation` for one variant. A VCEP class published
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
ordering physician (§11), prenatal reports contain no adult-onset findings (§15).

**Pass 10: Freshness.** If ClinVar, ClinGen or the literature changed after the report
date, say what changed and whether it would change the class. That is a reclassification
flag, not an error of the original report.

**Pass 11: Communication.** A VUS is never worded as a cause. Uncertainty is stated.
There is one clear sentence for the referrer that answers the clinical question.

**Stop rule.** One pass per item; do not repeat a check that already has a state. When a
tool fails, mark the claim "not confirmable" and move on. Stop when every pass has a state.

## Severity

- **Critical**: would change the diagnosis, the management, or family testing if released
  (wrong inheritance or risk, VUS treated as causal, AR single het called diagnostic,
  consent violated, overstated negative, wrong patient). An identity mismatch that could mean
  a wrong patient or sample (age vs year of birth, a name or variant from another case) is
  Critical until a human rules it out.
- **Major**: a required element is wrong or missing, or a classification code is wrong
  without changing the final class (HGVS or build error, citation not about the variant,
  missing limitations, method/conclusion mismatch, sex/pronoun error, a correctly classified
  benign variant listed in the result section).
- **Minor**: wording, formatting, a non-decisive omission.

Only Critical and Major findings need tool evidence or a quoted contradiction; a finding
you cannot back is a question, not an accusation. Major needs either an error or a missing
element that reporting standards require (method, limitations, nomenclature, class,
recommendations). Information that is merely absent and not required (ACMG codes not listed
when the class is confirmed, QC details, sample-identity procedure) is Minor or goes under
"Not checked". Do not raise findings that only follow from another finding; fold them in.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

**Expectation (Pass 0):** one or two lines, written before reading the result.

**Verdict:** concur / concur with comments / do not sign out yet — one sentence why.

**Findings** (most severe first; one row per finding; quote the report verbatim):

| Severity | Quote from report | Problem | Fix | Tool evidence | State |
|---|---|---|---|---|---|
| Critical | "…exact sentence or field…" | … | … | `tool` → record id / value | contradicted |

**Checks out:** short list of what was verified and is correct (with tool and record id).

**Not checked / needs a human:** items no tool could confirm, and items that need clinical
or lab judgement (sample identity, coverage files, consent records).

Do not rewrite the report. Never change HGVS, gene names, ACMG class or OMIM numbers
yourself; propose the fix in the table. Be concise.

## Tool calls

<!-- tool-calls:start -->
- `autopvs1_get_variant_pvs1_data`(genome_build, variant_id, meta_mode, include_unmet) — direct
- `civic_search_assertions`(; optional: molecular_profile_id, disease, therapy, assertion_type, amp_level, status, limit) — via call_tool
- `clingen_get_gene_dosage`(gene_symbol) — via call_tool
- `clingen_get_gene_summary`(gene_symbol) — via call_tool
- `clingen_get_gene_validity`(gene_symbol, classification, moi) — direct
- `clingen_get_variant_interpretation`(variant_id, refresh) — via call_tool
- `clingen_get_variant_interpretations`(gene_symbol, disease, expert_panel, classification, page, size) — via call_tool
- `clingen_search_dosage`(query, region, cytoband, haplo_score, triplo_score, record_type, page, size) — via call_tool
- `clinpgx_get_website_data`(operation, path_parameters, query_parameters, pointer, pointers, limit) — via call_tool
- `clinpgx_search_dataset`(dataset_id, member, query, filters, match, limit, include_fields, parent_fields) — via call_tool
- `clinvar_get_variant`(identifier, id_type) — direct
- `gencc_get_gene_curations`(gene_symbol, limit) — direct
- `genereviews_search_passages`(q, query, gene, nbk_id, sections, heading_path_contains, mode, limit, exclude, include, snippet_chars, rerank) — direct
- `gnomad_get_gene_summary`(gene, dataset, clinvar_limit, include_expression, include_clinvar, include_constraint) — via call_tool
- `gnomad_get_region`(region, dataset, include_clinvar, include_genes, max_clinvar_variants, max_genes, compact_rows) — via call_tool
- `gnomad_get_variant_frequencies`(variant_id, dataset, populations, include_subcohorts, include_sex_split, exclude_zero_populations) — direct
- `gnomad_resolve_variant_id`(query, dataset, limit, enrich) — direct
- `hgnc_get_gene_cross_references`(query; optional: databases) — via call_tool
- `hgnc_resolve_symbols_batch`(queries) — direct
- `hpo_get_genes_for_disease`(disease_id; optional: limit) — via call_tool
- `hpo_get_phenotypes_for_gene`(gene; optional: limit) — direct
- `litvar_get_variant_literature`(variant_id, limit) — direct
- `mavedb_search_score_sets`(; optional: text, targets, target_organism_names, target_types, authors, facet_mode, published, limit) — direct
- `metadome_get_tolerance_landscape`(transcript_id, position_start, position_stop, limit) — direct
- `moalmanac_search_approvals`(; optional: gene, biomarker, disease, therapy, agency, biomarker_type, limit) — via call_tool
- `mondo_resolve_xref`(xref_id; optional: limit) — via call_tool
- `panelapp_get_panel_genes`(panel_id, region, entity_type, min_confidence, limit) — via call_tool
- `panelapp_search_panels`(query, region, limit) — direct
- `pubtator_get_publication_passages`(pmids, sections, mode, full, max_passages_per_pmid, max_chars, include_tables, include_references, dry_run, verbosity) — via call_tool
- `spliceai_predict_splicing`(variant_id, genome_build, max_distance, mask, gene_set, transcripts, cross_build_check, include_hints, include_see_also, correlation_id) — direct
- `ucsc_get_region_annotations`(assembly, chrom, start, end, track; optional: limit) — via call_tool
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
