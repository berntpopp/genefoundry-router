---
name: literature-evidence-review
description: Use when asked to review, summarize or synthesize the published evidence on a gene, variant or gene–disease link, to find what a set of papers actually says, to check whether cited PMIDs support a claim, or to report what is new since a date — e.g. "what is the evidence that KMT2D causes Kabuki syndrome", "literature on BRCA2 c.8167G>A", "new papers on SCN2A epilepsy since 2024", "do these PMIDs really describe this variant?".
---

# Literature evidence review

Answer only from text a tool returned in this session. A PMID is cited only after a tool
showed that it is about this exact gene, variant or claim. Names are `<namespace>_<tool>`.

## Routing (pick the row, make its calls, compare)

| Request | Call | Compare |
|---|---|---|
| does cited PMID X support a claim | `pubtator_get_publication_passages`(`pmids=["X"]`, `mode="abstracts"`, `max_chars=4000`) | the claim against the abstract (rules below) |
| … and is X a review or retracted | `pubtator_search_literature`(`text="X"`; several: `"X OR Y OR Z"`) | the record's `publication_types` |
| papers on a variant | `litvar_get_variant_literature`(`variant_id`= rsID) | `resolved_variant` is this allele; `_meta.pagination.total_count` |
| synthesis or surveillance (what is known / new since a date) | read `references/synthesis.md` | |

Call facts:
- `pmids` is a list of strings (`["15118073"]`); integers or a bare string fail validation.
- `max_chars` is the budget for the whole call: an abstract that does not fit is dropped and
  only the title comes back. Use `max_chars` = 2500 × number of PMIDs, at most 5 PMIDs per
  call. If `coverage_by_pmid` shows `title_only` for a PMID, fetch that PMID alone.
- The passages tool returns no publication type; the search record does (default
  `metadata`). Leave `filters`, `metadata` and `include_citations` at their defaults
  (`metadata` and `include_citations` take strings, not booleans).
- LitVar takes an rsID; HGVS without one returns `not_found`. An rsID covers each ALT at the
  position, so check that a paper names this change.

## Claim check: decide from the two results, then stop

Curated sources (HGNC, ClinGen, GenCC, GeneReviews) are not needed to judge a cited PMID; skip
them unless the claim is about curated facts.

- `publication_types` has `Retracted Publication` → **unsupported** (a retracted paper supports
  no claim).
- The claim calls the paper primary, original, first or new data and `publication_types` has
  `Review` → **unsupported**.
- The abstract states the claim's allele, gene, numbers (counts, cohort size, percentages,
  ratios, p-values) and direction → **supported**.
- The abstract gives another number, the reverse direction or comparison, another allele or
  gene, or a different population → **unsupported** (contradicted; quote the abstract).
- A claim about wording ("the abstract names V599E", "codon 664") is compared character for
  character; a different numbering is unsupported even if it is the same residue.
- The abstract is silent on the point → **not confirmable**; if the answer format allows only
  supported/unsupported, it is unsupported.
- Several user-supplied PMIDs: judge each; do not search for replacements unless asked.

Budget: at most 6 calls. Done when each claim has a state backed by a quoted abstract
sentence or a `publication_types` value. A failed or `title_only` fetch that a re-fetch does
not fix → not confirmable.

## Output

If the request specifies an answer format, use exactly that. Otherwise, for a claim check:
one line per PMID: PMID, state, the abstract sentence or publication type that decides it.
For synthesis or surveillance use the template in `references/synthesis.md`. End with the
tools used and data versions. Research use only; not clinical decision support.

## Tool calls

<!-- tool-calls:start -->
- `clingen_get_gene_validity`(gene_symbol, classification, moi) — direct
- `clingen_get_variant_interpretation`(variant_id, refresh) — via call_tool
- `clinvar_get_variant`(identifier, id_type) — direct
- `gencc_get_gene_curations`(gene_symbol, limit) — direct
- `genereviews_search_passages`(q, query, gene, nbk_id, sections, heading_path_contains, mode, limit, exclude, include, snippet_chars, rerank) — direct
- `hgnc_resolve_symbol`(query) — direct
- `litvar_get_variant_literature`(variant_id, limit) — direct
- `mondo_search_diseases`(query; optional: limit, include_obsolete) — direct
- `pubtator_get_publication_passages`(pmids, sections, mode, full, max_passages_per_pmid, max_chars, include_tables, include_references, dry_run, verbosity) — via call_tool
- `pubtator_search_biomedical_entities`(query, concept, limit) — via call_tool
- `pubtator_search_literature`(text, page, sort, filters, publication_types, year_min, year_max, sections, include_citations, text_hl_format, limit, entity_ids, guideline_boost, coverage, metadata, include_meta) — direct
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
