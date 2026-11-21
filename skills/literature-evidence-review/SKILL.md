---
name: literature-evidence-review
description: Use when asked to review, summarize or synthesize the published evidence on a gene, variant or gene–disease link, to find what a set of papers actually says, to check whether cited PMIDs support a claim, or to report what is new since a date — e.g. "what is the evidence that KMT2D causes Kabuki syndrome", "literature on BRCA2 c.8167G>A", "new papers on SCN2A epilepsy since 2024", "do these PMIDs really describe this variant?".
---

# Literature evidence review

Answer from curated sources first, then from papers whose text a tool returned in this
session. A PMID is cited only after a tool showed that it is about this exact gene, variant
or claim.

Reach tools through `search_tools` → `call_tool`; names are `<namespace>_<tool>`.

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

## Step 1: Pin down the question (1–2 calls)

- Gene: `hgnc_resolve_symbol` → approved symbol + HGNC id (old symbols find old papers; keep
  them as search terms).
- Variant: keep the MANE HGVS and the rsID; `vep_annotate_variant` if only one is known.
- Disease: `mondo_search_diseases` if a disease is named.
- Mode: **synthesis** (what is known) or **surveillance** (what is new since a date). If a date
  or "new" / "recent" / "since" is in the request, it is surveillance.

## Step 2: Curated sources first (2–3 calls)

Use these for facts (mechanism, inheritance, penetrance, frequencies, management). They are
reviewed; a single paper is not.

- `genereviews_search_passages` (`q`, `gene`; `sections` e.g. `["clinical_features"]`,
  `["genetic_counseling"]`, `["molecular_genetics"]`). Quote numbers from the passage only.
- `clingen_get_gene_validity` (`gene_symbol`) and `gencc_get_gene_curations`: classification,
  inheritance, date.
- Variant: `clinvar_get_variant` (`identifier`): classification, stars, and the PMIDs its
  submissions cite (`citations`).

## Step 3: Find papers (1–3 calls)

- Variant: start with the PMIDs curated records cite (`clinvar_get_variant` `citations`,
  `clingen_get_variant_interpretation`); they were chosen for this allele. Then
  `litvar_get_variant_literature` (`variant_id` = rsID preferred). Note `resolved_variant`;
  an rsID covers every ALT at the position, so LitVar papers may describe a different change.
  Its top hits are often reviews that mention the variant only in the full text.
  `_meta.pagination.total_count` is the true count.
- Gene or topic: `pubtator_search_literature` (`text`; optional `entity_ids` from
  `pubtator_search_biomedical_entities`, `publication_types`, `year_min`).
  Surveillance: `sort: "date desc"` and `year_min` = the year of the cut-off date; then drop
  hits published before the exact date.
- Do not page beyond the first page unless the user asks for completeness; report the total
  and how many you read.

## Step 4: Verify each PMID before using it (1–2 calls, batched)

`pubtator_get_publication_passages` (`pmids`: up to 25 at once, `mode: "full_abstract"`;
open-access full text with `full: true` and `sections`). For each PMID decide:

- **confirmed**: the returned text names this allele (HGVS, protein change or rsID) or this
  gene–disease link and supports the claim.
- **contradicted**: the text says the opposite, or describes a different allele, gene or
  disease.
- **not confirmable**: abstract only and silent on the point, or the text could not be fetched.

A paper that mentions the gene but not the variant does not support a variant claim. A
legacy variant name must be mapped to HGVS before it counts; say how you mapped it.

When checking PMIDs the user supplied, verify each one; do not search for replacements unless
asked.

## Step 5: Stop

Stop when each claim in the question has its sources and states. Budget: about 10 tool calls.
More papers do not make a stronger answer; contradictions and gaps do.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

**Question** as understood (gene, variant, disease, mode, cut-off date).

**Curated summary**: 3–6 sentences from GeneReviews / ClinGen / GenCC / ClinVar, each with its
record id (NBK id + section, ClinGen permalink, VCV).

**Evidence table**

| PMID | Year | Claim it is used for | What the text shows (short quote or paraphrase) | State |
|---|---|---|---|---|

**New since <date>** (surveillance mode): only papers on or after the date; "none found in
PubTator for <query>" if empty, with the query.

**Conflicts and gaps**: contradictions between sources; questions no source answered.

**Coverage**: queries run, total hits per source, how many papers were read.
**Tools used and data versions.** Research use only; not clinical decision support.
