---
name: genetic-report-review
description: Use when asked to review, second-check, audit or sign out a genetic test report (germline panel, exome or genome, CNV/array, somatic tumour, pharmacogenomic, predictive or segregation report), or to find errors in a lab report, e.g. "review this exome report", "second look at this BRCA1 report", "is this report ready to sign out".
---

# Genetic report review (second look)

Research use only. Not clinical decision support. A structured second look that helps a
qualified reviewer; it does not replace the signing geneticist.

## 1. Before you start

- De-identified input only: a real name, full date of birth, address or record number →
  stop and ask for a de-identified copy.
- Note the report date (a re-issue date counts as the report date). Read the ONE reference
  for the report type (if it is already in your context, use that copy); a mixed report may
  need two:

  | Type | Reference |
  |---|---|
  | Germline SNV/indel (panel, exome, genome) | `references/germline.md` |
  | CNV / array | `references/cnv.md` |
  | Negative / no finding | `references/negative.md` |
  | Somatic / tumour | `references/somatic.md` |
  | Pharmacogenomic | `references/pgx.md` |
  | Predictive, cascade, segregation | `references/predictive-segregation.md` |

## 2. Read first (no tools)

Before reading the result, note what you would expect from the indication (genes,
mechanism, inheritance). Then read the whole report and list every problem visible from the
text: conclusions stronger than the result ("ruled out", "causal" for a VUS, "confirms"),
method vs conclusion (panel called exome), zygosity × inheritance × sex, recurrence risks,
age vs year of birth and dates, read counts vs stated fraction, "de novo"/"inherited"
without parental results, somatic VAF far from half the tumour content, a gene, pronoun or
paragraph from another case, sentences that contradict each other, missing limitations,
recommendations that do not follow from the class (predictive testing for a VUS, relatives
excluded by sex). Most findings come from here. Tool checks add to this list, never drop
from it.

## 3. Check with tools (one call per claim, then compare)

| Claim in the report | Call | Compare |
|---|---|---|
| variant c./p., coordinate, consequence | `vep_annotate_variant`(`variant`= plain HGVS, no gene or protein around it; repeat notation as del/dup; `assembly`) | `hgvsc`/`hgvsp` of the named or MANE transcript (not the most-severe isoform), character by character incl. `fs*` length; coordinate of the stated build |
| "MANE Select" label | `hgnc_get_gene_cross_references`(`query`=gene, `databases=["mane"]`) | same accession and version |
| class, ClinVar statement, cited VCV | `clinvar_get_variant`(`identifier`= the cited VCV, or the HGVS); if it does not resolve (e.g. repeat notation), `clinvar_search_variants`(`query`= the report's exact string) | 3 stars = expert-panel class; a panel class dated before the report that differs: Major (Critical if P/LP vs VUS/benign) |
| a 2-star "likely pathogenic" against a report's "pathogenic" | `gnomad_get_clinvar_variant_details`(`variant_id`) | the true aggregate may be "Pathogenic/Likely pathogenic"; do not call the report contradicted on the first result alone |
| frequency | `gnomad_get_variant_frequencies`(`variant_id`=`CHROM-POS-REF-ALT`, both alleles) | "not found" = absent, a valid result |
| gene–disease, inheritance | `clingen_get_gene_validity`(`gene_symbol`) | disease, MOI, disputed/refuted |
| OMIM number for a disease | `mondo_resolve_xref`(`xref_id="OMIM:<n>"`) | no disease match = a gene entry cited as the disease (error) |
| cited PMID | `pubtator_get_publication_passages`(`pmids=[...]`, `mode="abstracts"`, `max_chars=4000`) | the paper is about this allele or claim |
| CNV interval and gene content | `clingen_search_dosage`(`cytoband=...`); `ucsc_get_region_annotations`(`assembly`, `chrom`, `start`, `end`, `track="mane"`) | genes named are inside the interval; HI/TS scores |
| penetrance, risks, de novo rates | `genereviews_search_passages`(`q`, `gene`) | the number in the chapter |
| named panel | `panelapp_search_panels`(`query`) → `panelapp_get_panel_genes`(`panel_id`, `min_confidence="green"`) | expected genes present |
| PGx phenotype and drug advice | `clinpgx_get_api_data`(`operation="GET /data/guidelineAnnotation/{id}"`, `path_parameters={"id": "PA..."}`, `pointers=["/data/summaryMarkdown/html"]`) | the guideline's recommendation for the phenotype |
| somatic tier, therapy | `moalmanac_search_approvals`(`gene`, `biomarker` in short form such as `KRAS p.G12C`, `disease`, `agency` lowercase) | approval in this tumour type |

Skip rows that do not apply. Budget: a cap, not a target: at most 8 calls, in this order: identity and class of each reported variant, the
MANE label, gene–disease, each cited PMID and OMIM number, then the rest. What the budget
does not reach is "not checked", not a finding. A failed call: mark "not confirmable",
never repeat it identically. For a full audit with budget left, see
`references/checklist.md`.

## 4. Rules models get wrong

- A VUS is never a cause and never a reason for predictive testing of relatives.
- An expert-panel class outranks a generic predictor and the report's own codes.
- Phase: one variant per parent = trans; one parent carrying both = cis in that parent
  (an unaffected double carrier argues for cis); one parent tested = phase open.
- A single heterozygous variant in an autosomal recessive gene is not diagnostic.
- A negative result never "rules out" or "excludes"; it lowers the probability within the
  stated scope.
- Secondary findings only in line with the documented consent and lab policy. Germany:
  counselling offer (GenDG §10), report to the ordering physician (§11), no adult-onset
  findings in prenatal reports (§15). Cite no other legal clause numbers.
- Evidence newer than the report date is a reclassification flag, not a reporting error.

## 5. Severity

- **Critical**: would change diagnosis, management or family testing (wrong inheritance or
  risk, VUS as cause, consent violated, overstated negative, possible wrong patient or
  sample, a sentence from another case).
- **Major**: a required element is wrong or missing (HGVS, build, MANE label, class, disease
  id, citation not about the variant, missing limitations, method/conclusion mismatch,
  pronoun or sex error, benign variant listed as a result).
- **Minor**: wording, formatting, a non-decisive omission.

The table holds problems only: a statement you checked and found correct goes under Checks
out, never in the table. A stated limitation that is false (it contradicts the method) is an
error; an accurate one is not. A short protein form that matches ClinVar's own name (e.g.
`p.(Gln1756fs)`) is Minor. Test: if fixed, does the result, class, a risk or a recommendation change? No → Minor,
unless a required element is wrong. A limitation the report itself states, a correct
conclusion you would word differently, and procedure details the result does not depend on
are never Major. A wrong recommendation or risk that follows from another error keeps its
own row; a finding that only repeats another is folded in. A finding you cannot back is a
question, not an accusation.

## 6. Output

If the request specifies an answer format, use exactly that; put confirmed checks in one
"Checks out:" line after it, never as table rows. Otherwise:

**Verdict:** concur (no Critical/Major) / concur with comments (Minor to fix) / do not sign
out yet — one sentence why.

| Severity | Quote from report | Problem | Fix | Tool evidence | State |
|---|---|---|---|---|---|

Quotes are copied character for character. One row per finding, most severe first. Then
**Checks out** (verified, with tool and record id) and **Not checked / needs a human**.
Never rewrite HGVS, gene names, class or OMIM numbers yourself; propose the fix.

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
- `clinpgx_get_api_data`(operation, path_parameters, query_parameters, form_parameters, representation, pointer, pointers, limit) — via call_tool
- `clinpgx_get_website_data`(operation, path_parameters, query_parameters, pointer, pointers, limit) — via call_tool
- `clinpgx_list_datasets`(query, include_legacy, limit) — via call_tool
- `clinpgx_search_dataset`(dataset_id, member, query, filters, match, limit, include_fields, parent_fields) — via call_tool
- `clinpgx_search_records`(entity_type, query, filters, source, view, limit, shape) — via call_tool
- `clinvar_get_variant`(identifier, id_type) — direct
- `clinvar_search_variants`(query, gene_symbol, classification, min_stars, assembly, match_mode, count_mode, limit) — via call_tool
- `gencc_get_gene_curations`(gene_symbol, limit) — direct
- `genereviews_search_passages`(q, query, gene, nbk_id, sections, heading_path_contains, mode, limit, exclude, include, snippet_chars, rerank) — direct
- `gnomad_get_clinvar_variant_details`(variant_id, reference_genome, submissions_limit) — via call_tool
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
