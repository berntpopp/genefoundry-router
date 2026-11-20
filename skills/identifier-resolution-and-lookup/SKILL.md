---
name: identifier-resolution-and-lookup
description: Use when a question needs identifiers resolved or chained across databases before it can be answered — gene symbols, aliases, Ensembl/RefSeq/UniProt/NCBI ids, rsIDs, HGVS or coordinates on GRCh37/GRCh38, disease names or OMIM/Orphanet/MONDO ids, HPO terms — e.g. "which gene is rs6025 in", "Ensembl id of the gene causing Dravet syndrome", "what chromosome band is the gene for OMIM:154700", "convert these 50 ENSG ids to symbols". Research use only.
---

# Identifier resolution and multi-hop lookup

Goal: answer "X → Y → property" questions with the fewest calls, and stop. Most lookup
errors are identifier errors: the wrong id type, the wrong build, or one id resolved many
times. Reach tools through `search_tools` → `call_tool`; pinned entry points
(`hgnc_*`, `mondo_*`, `gnomad_*`, `hpo_*`, `vep_annotate_variant`) are also callable directly.

## 1. Plan the hops before the first call

Write the chain in one line, e.g. `disease name → OMIM id → genes → HGNC record → band`.
Set a budget: hops × 2 calls, at most 10. Each hop uses the first tool in its row below.

| Have | Tool | Notes |
|---|---|---|
| 2+ genes in any id form | `hgnc_resolve_symbols_batch` | ONE call, up to 200; symbols, aliases, ENSG, RefSeq, UniProt, `NCBIGene:<n>` |
| one gene | `hgnc_resolve_symbol` | returns HGNC id, approved symbol and `location` (band); ambiguous alias → candidate list, pick none silently |
| gene → Ensembl / NCBI / OMIM / UniProt / MANE ids | `hgnc_get_gene_cross_references` | the resolve tools do not return external ids |
| other external gene id (OMIM gene, MGI, MANE transcript) | `hgnc_resolve_gene_by_xref` | `source` + `value`; a MANE Select ENST/NM resolves to its gene |
| disease name | `mondo_resolve_disease` | returns MONDO id + Mondo causal genes; `ambiguous_query` → choose by the user's wording or ask |
| disease name not found | `orphanet_search_diseases`, then `mondo_search_diseases` | OMIM titles are often absent as labels; search, don't guess |
| MONDO id → OMIM/ORPHA | `mondo_map_cross_ontology` | `prefixes=["OMIM","ORPHA"]` |
| OMIM / ORPHA id → genes | `hpo_get_genes_for_disease` | needs `OMIM:`/`ORPHA:` CURIE, not MONDO; use when Mondo lists no causal gene |
| Orphanet disorder → genes | `orphanet_get_disease_genes` | group terms return 0 genes: query a subtype |
| rsID / HGVS / loose variant | `gnomad_resolve_variant_id` or `vep_annotate_variant` | gnomAD gives the gene symbol and AF; VEP gives MANE HGVS and consequence |
| many variants | `vep_annotate_variants_batch` | one call, up to 200 |
| coordinate on the other build | `vep_liftover_variant` or `gnomad_compute_variant_liftover` | never compare raw positions across builds |
| small region (≤100 kb) → genes | `gnomad_get_region` | `dataset="gnomad_r4"` is GRCh38, `dataset="gnomad_r2_1"` GRCh37 |
| phenotype text | `hpo_search_terms` | top hit; do not page |
| protein / UniProt | `uniprot_find_proteins` | |

## 2. Rules

- **Batch first.** Resolve every gene once, in one batch, to symbol + HGNC id before any
  other lookup. Reuse those ids for every later hop.
- **Transcript ids are not gene ids.** ENST…/NM_… go through `hgnc_resolve_gene_by_xref`
  (`source="mane_select"`) or VEP, never into gene-only parameters.
- **State the assembly** (GRCh37 or GRCh38) for every coordinate, rsID result and region,
  and say where it came from. If the input build is unknown, say so and use GRCh38.
- **Upstream refusal or not-found:** do not repeat the identical call. Switch source or
  build once (e.g. VEP → gnomAD, GRCh38 → GRCh37 after liftover) and say you did.
- **Rate limit:** continue with the other sources; do not loop on the limited one.
- **Curated before free text.** For facts (band, Ensembl id, inheritance) prefer HGNC,
  Mondo, Orphanet and HPO records over literature search results.
- **Known gaps:** NCBI `LOC` ids and legacy clone-based gene names have no resolver in the
  fleet; report them as not resolvable rather than guessing.

## 3. Stop rule

Answer as soon as **one curated source gives the requested field for the resolved id**, or
**two sources agree** when the first was ambiguous. When the budget is spent, stop and
answer with what you have, naming the missing hop. Do not re-verify an answer that a
curated record already states.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

- **Answer:** the requested value(s)
- **Chain:** each hop as `input → id (tool)`, with the assembly for any coordinate
- **Not found:** each input or hop that failed, as "not found in <source>" (never "does
  not exist")

## Evidence rules (all GeneFoundry skills)

- Research use only. Not clinical decision support. Use de-identified input only; if the input
  contains names, dates of birth or record numbers, stop and ask for a de-identified version.
- Every fact comes from a tool result in this session. Cite the record id the tool returned
  (PMID, VCV, HGNC id, MONDO id, ...). Never cite from memory.
- Give every checked claim one state: **confirmed** (tool agrees), **contradicted** (tool disagrees;
  show the evidence), **not confirmable** (tool returned nothing or failed), **not checked**.
  "Not found in source X" never means "negative" or "benign".
- State genome build and transcript before any variant lookup. Resolve identifiers once, in batch.
- Scores, points and risks are added up step by step by the stated rule, never estimated.
- If a call errors or is rate limited, do not repeat the identical call: use another source or
  report the gap. Stop when the skill's stop rule is met.
- End with: tools used and the data versions they report.
