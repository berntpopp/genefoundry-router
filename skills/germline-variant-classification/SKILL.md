---
name: germline-variant-classification
description: Use when asked to classify a germline sequence variant (SNV or small indel) as pathogenic, likely pathogenic, VUS, likely benign or benign under ACMG/AMP, to re-derive or check an existing classification, or to say which ACMG criteria a variant meets — e.g. "classify NM_000492.4:c.1521_1523del in CFTR", "is this MYH7 missense a VUS?", "which ACMG codes apply to rs80357906?". Also mtDNA variants and repeat-expansion sizing via its references. Not for somatic/tumour variants or CNVs.
---

# Germline variant classification (ACMG/AMP)

Re-derive the class from evidence unless Step 0 finds an expert-panel class; never copy a
ClinVar aggregate or a report's class. Every criterion
is decided from a tool result, weighted by the rules below, and summed as points step by step.

Use the tools as listed below (direct, or through `call_tool`); never search for them. A tool
that is missing (not in your list, or "unknown tool") is unavailable for this session: never call it again under any name;
its codes are "not evaluated".

## Tool calls

<!-- tool-calls:start -->
- `autopvs1_get_variant_pvs1_data`(genome_build, variant_id, meta_mode, include_unmet) — direct
- `clingen_get_cspec`(gn_id) — via call_tool
- `clingen_get_cspec_criterion`(gn_id, code, rule_set_id) — via call_tool
- `clingen_get_gene_validity`(gene_symbol, classification, moi) — direct
- `clingen_get_variant_interpretation`(variant_id, refresh) — via call_tool
- `clingen_list_cspecs`(gene_symbol, affiliation, status, page, size) — via call_tool
- `clinvar_get_variant`(identifier, id_type) — direct
- `clinvar_search_variants`(query, gene_symbol, classification, min_stars, assembly, match_mode, count_mode, limit) — via call_tool
- `gencc_get_gene_curations`(gene_symbol, limit) — direct
- `genereviews_search_passages`(q, query, gene, nbk_id, sections, heading_path_contains, mode, limit, exclude, include, snippet_chars, rerank) — direct
- `gnomad_get_clinvar_variant_details`(variant_id, reference_genome, submissions_limit) — via call_tool
- `gnomad_get_gene_summary`(gene, dataset, clinvar_limit, include_expression, include_clinvar, include_constraint) — via call_tool
- `gnomad_get_mitochondrial_variant`(variant_id, dataset, include_heteroplasmy_zeros) — via call_tool
- `gnomad_get_variant_frequencies`(variant_id, dataset, populations, include_subcohorts, include_sex_split, exclude_zero_populations) — direct
- `gnomad_resolve_variant_id`(query, dataset, limit, enrich) — direct
- `litvar_get_variant_literature`(variant_id, limit) — direct
- `mavedb_find_variant`(variant; optional: gene_symbol, only_current, enrich, limit) — via call_tool
- `mavedb_get_variant_score`(urn; optional: hgvs) — via call_tool
- `mavedb_search_score_sets`(; optional: text, targets, target_organism_names, target_types, authors, facet_mode, published, limit) — direct
- `metadome_get_tolerance_landscape`(transcript_id, position_start, position_stop, limit) — direct
- `mitomap_get_variant`(variant) — via call_tool
- `pubtator_get_publication_passages`(pmids, sections, mode, full, max_passages_per_pmid, max_chars, include_tables, include_references, dry_run, verbosity) — via call_tool
- `spliceai_predict_splicing`(variant_id, genome_build, max_distance, mask, gene_set, transcripts, cross_build_check, include_hints, include_see_also, correlation_id) — direct
- `strchive_get_locus`(locus) — via call_tool
- `strchive_search_loci`(; optional: query, limit) — via call_tool
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

## Scope

- Germline SNV/indel against ONE gene–disease pair. If the user names no disease, use the
  curated disease(s) for the gene and classify per disease.
- mtDNA variant: also read `references/mitochondrial.md`. Repeat expansion: read
  `references/repeat-expansion.md` (sizing, not ACMG points).
- Somatic variants: use the `somatic-variant-interpretation` skill instead. CNVs: out of scope.
- ACMG/AMP v4 was not published as of 2026-11; do not apply v4 rules.

## Step 0: Is there already an expert-panel class? (1–2 calls)

`clinvar_get_variant` (`identifier` = the HGVS, rsID or VCV). If it returns 3 or 4 stars
(expert panel or practice guideline), that class is the class of record. It is not an ACMG
code: it replaces your own derivation as the final class, even when your points add up to
something else. "Classify this variant" or "classify with ACMG/AMP" is not a request for a
derivation; only an explicit ask for the codes ("derive", "show each criterion") is. Otherwise:
confirm the variant identity with one `vep_annotate_variant`
call, report the panel class with its date and review status, list what would change it,
and stop. This is the fast path: no code table, no gnomAD, REVEL, AutoPVS1 or SpliceAI. A 2-star "likely
pathogenic" is not a panel class (and may hide a "Pathogenic/Likely pathogenic" aggregate:
check with `gnomad_get_clinvar_variant_details`). Otherwise, or if ClinVar is unavailable or has no record, go on with Steps 1–5 below.

## Steps 1–5: Derive (only when Step 0 did not settle it)

No 3- or 4-star class, or the user asks for a derivation: read `references/derivation.md`
and follow it. In short:

| Evidence | Call | Codes |
|---|---|---|
| identity, MANE c./p., REVEL | `vep_annotate_variant`(`variant`= plain HGVS, `assembly`) | PP3/BP4 (REVEL thresholds in the reference) |
| gene–disease, mechanism | `clingen_get_gene_validity`(`gene_symbol`) | validity caps the class |
| gene-specific rules | `clingen_list_cspecs`(`gene_symbol`) → `clingen_get_cspec_criterion`(`gn_id`, `code`) | spec thresholds replace generic ones |
| frequency | `gnomad_get_variant_frequencies`(`variant_id`=`CHROM-POS-REF-ALT`) | BA1/BS1 before PM2; a failed call is "not evaluated" |
| null variant | `autopvs1_get_variant_pvs1_data`(`variant_id`, `genome_build`) | PVS1 strength |
| splicing | `spliceai_predict_splicing`(`variant_id`, `genome_build`) | splicing only |

Hard limit: 8 calls after Step 0, in this order, skipping rows that do not apply: VEP →
gnomAD (BA1 met: stop, the variant is benign; BS1 is −4 points in the sum, not a class) → AutoPVS1 only for a
null variant → SpliceAI only for an intronic, synonymous or splice-region variant → ClinGen
validity → cspecs only if ClinGen names a VCEP for the gene. At the limit, or when the
remaining codes need data no tool gives (segregation, de novo, functional, case counts):
stop. Every code you did not check is "not evaluated" and adds 0 points; never keep calling
to fill rows. Points by the reference's table, step by step; BA1 stands alone. A VUS for
lack of evidence is a valid answer. Ten calls in total, or two errors in a row ("not found" is a result, not an error): stop
calling now and write the answer, ending with the Class line.

## Step 6: Compare and stop

- `clinvar_get_variant`: report ClinVar classification, stars and review status.
  A 2-star "likely pathogenic" may stand for a "Pathogenic/Likely pathogenic" aggregate:
  confirm with `gnomad_get_clinvar_variant_details` before treating it as a disagreement.
- `clingen_get_variant_interpretation` (`variant_id` = ClinVar VariationID, CAID or HGVS): the
  expert panel's own classification and met/not-met codes, if one exists. Look up the one
  variant; do not list a gene's interpretations.
- If an expert panel (VCEP, ClinVar 3 or 4 stars) has classified this variant, its class is
  the class of record: report it as the final class, with your own derivation beside it and
  the codes that explain any difference (a panel class older than newer evidence you found
  is a reclassification flag for a human, not something you override).
- Otherwise the final class is your own. If ClinVar's aggregate differs, list which codes
  explain the difference.
- Stop at the limit above (Step 6 adds at most 2 calls). Do not page literature.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

**Variant:** gene, MANE transcript, HGVS c./p., build, consequence.
**Gene–disease:** disease (MONDO), validity, inheritance, LoF mechanism yes/no, VCEP spec used.

| Code | Strength | Points | Evidence (tool, record id, value) | State |
|---|---|---|---|---|

States: met / not met / not evaluated (why) / not confirmable.

**Sum:** the points line. **Class:** one of the five; when an expert-panel class exists, the
final class is the panel class (fast path: no table and no sum; if you derived, show your sum
beside it as "own derivation").

**ClinVar comparison:** class, stars, review status; reason for any difference.
**What would change the class:** the one or two missing data points with the largest effect
(e.g. parental testing for PS2, an RNA study).
**Tools used and data versions.** Research use only; not clinical decision support.

Unless another format is requested, the last line is exactly
`Class: <Pathogenic | Likely pathogenic | Uncertain significance | Likely benign | Benign>`.
