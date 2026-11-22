---
name: phenotype-to-candidate-genes
description: Use when a patient's phenotypes (HPO terms or clinical features) must be matched to the most likely causal gene, with or without a candidate gene list from exome or genome variants — e.g. "which of these genes explains seizures, hypotonia and microcephaly", "rank candidate genes for these HPO terms", "pick the causal gene for this case". Research use only.
---

# Phenotype to candidate genes

Goal: name the most likely causal gene for a phenotype profile in about 4–6 tool calls, and
say which evidence decided it. Reach tools through `search_tools` → `call_tool`; the
`hpo_*` and `hgnc_*` entry points below are also callable directly.

## Steps

1. **Phenotypes → HP ids.** Use HP ids exactly as given. Resolve each free-text feature
   with `hpo_search_terms` and take the top hit; do not page. Features stated as absent go
   into `excluded_hpo_ids`, never into `hpo_ids=[…]`.
2. **Candidate genes → symbols, once, in batch.** If candidates are anything other than
   gene symbols or NCBI gene ids (Ensembl ENSG…, `HGNC:` ids, RefSeq, UniProt), translate
   the whole list in ONE `hgnc_resolve_symbols_batch` call (max 200). Never resolve genes
   one at a time. Transcript ids (ENST…, NM_…) are not gene ids: if the batch reports them
   unresolved, try `hgnc_resolve_gene_by_xref` with `source="mane_select"` once per id
   (only MANE Select transcripts map); if that fails, list the id as unresolved. Never guess
   the gene.
3. **One ranking call.** `hpo_score_genes_for_phenotypes` ONCE with all `hpo_ids=[…]`, the
   `excluded_hpo_ids`, and `candidate_genes` (when given). Never score terms one at a time.
   Do not page with `offset`. Without candidates, the default top 20 is the shortlist.
   Report every entry in `ignored_hpo_ids` and `unranked_candidate_genes`.
4. **Close call only.** If `close_call` is false, the answer is rank 1. If it is true,
   compare only the genes in `close_call_genes` (usually 2–3). Never pick rank 1 just
   because it is first. Break the tie in this order and stop at the first that separates:
   1. ONE `gencc_get_genes_curations` call with those genes: a gene with no Definitive /
      Strong / Moderate curation for a disease matching the phenotype loses
      (Disputed/Refuted always lose). Do not page the curations. If every gene has such a
      curation, curation does not separate them: go to 2.
   2. Hallmark fit: the gene whose best disease matches the most specific input term at the
      higher frequency (`f=` in `top_matches`) and matches more input terms
      (`matched_count`). If `top_matches` does not separate them, call
      `hpo_get_phenotypes_for_gene` once per tied gene and count which list covers more of
      the patient's terms.
   3. Inheritance and sex fit, if the patient's sex or family history is given (e.g. an
      X-linked gene that affects mainly females, in a male patient).
   If still tied, say so: name the tied genes in order and what would separate them
   (variant data, segregation, sex, onset age). If the request needs a single gene, give
   the first tied gene after these tie-breaks and say it was a close call. The answer is
   always one of the genes you compared, never a gene outside `close_call_genes`.
5. **Stop.** Answer after step 3 (clear winner) or step 4 (close call). Do not re-rank with
   other term subsets, do not page, do not re-check the winner with more sources.

## Failure handling

- A ranking call that errors or is rate limited: do not repeat it unchanged. Retry once
  with fewer candidates only if the error names the list size; otherwise report the gap.
- A gene that cannot be ranked stays in "Not found / not used" with its reason; it is not
  evidence against that gene.
- An empty ranking means no annotated disease matched these terms in HPO, not that no gene
  explains the case.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

- **Answer:** gene symbol (HGNC id)
- **Deciding evidence:** score and best-matching disease from the ranking; for a close
  call, the curation (source, classification, disease id) that broke the tie
- **Runner-up(s):** gene and why it lost
- **Not found / not used:** ignored or replaced HPO ids, unranked candidates, failed sources

## Tool calls

<!-- tool-calls:start -->
- `gencc_get_genes_curations`(genes, limit_per_gene) — via call_tool
- `hgnc_resolve_gene_by_xref`(source, value) — via call_tool
- `hgnc_resolve_symbols_batch`(queries) — direct
- `hpo_get_phenotypes_for_gene`(gene; optional: limit) — direct
- `hpo_score_genes_for_phenotypes`(hpo_ids; optional: excluded_hpo_ids, candidate_genes, limit) — direct
- `hpo_search_terms`(query; optional: limit, include_obsolete) — direct
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
