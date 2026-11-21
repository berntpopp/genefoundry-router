# Synthesis and surveillance procedure

Load this only for a synthesis ("what is the evidence that ...") or surveillance ("new since
<date>") request. A cited-PMID claim check never needs it. Budget: about 10 calls.

## 1. Pin down the question (1–2 calls)

- Gene: `hgnc_resolve_symbol` → approved symbol + HGNC id; keep old symbols as search terms
  (old symbols find old papers).
- Variant: keep the MANE HGVS and the rsID; `vep_annotate_variant` if only one is known.
- Disease: `mondo_search_diseases` if a disease is named.
- A date or "new" / "recent" / "since" in the request = surveillance; otherwise synthesis.

## 2. Curated sources (2–3 calls)

Facts on mechanism, inheritance, penetrance, frequencies and management come from reviewed
sources, not a single paper.

- `genereviews_search_passages` (`q`, `gene`; `sections` e.g. `["clinical_features"]`,
  `["genetic_counseling"]`, `["molecular_genetics"]`). Quote numbers from the passage only.
- `clingen_get_gene_validity` (`gene_symbol`) or `gencc_get_gene_curations`: classification,
  inheritance, date. Both curate germline gene–disease links; skip them for somatic or
  drug-response questions.
- Variant: `clinvar_get_variant` (`identifier`): classification, stars, and the PMIDs its
  submissions cite (`citations`).

## 3. Find papers (1–3 calls)

- Variant: start with the PMIDs curated records cite (`clinvar_get_variant` `citations`,
  `clingen_get_variant_interpretation`); they were chosen for this allele. Then
  `litvar_get_variant_literature` (`variant_id` = rsID; HGVS without an rsID returns
  `not_found`). Note `resolved_variant`: an rsID covers each ALT at the position, so a LitVar
  paper may describe a different change. Top hits are often reviews that mention the variant
  only in the full text. `_meta.pagination.total_count` is the true count.
- Gene or topic: `pubtator_search_literature` (`text`; optional `entity_ids` from
  `pubtator_search_biomedical_entities`, `publication_types` as a list such as
  `["Review"]`). Leave `filters` unset.
- Surveillance: one call per calendar year, `year_min` = `year_max` = that year,
  `sort="date desc"`; then drop hits before the exact date. A year range (or `year_min`
  alone) is filtered over one page only and can return an empty success.
- For a landmark or first-description paper, search with the discovery terms (author, year,
  original variant name) or take the PMID GeneReviews / ClinGen cite, rather than the top hits
  of a broad query.
- Read the first page only unless the user asks for completeness; report the total and how
  many you read.

## 4. Verify the PMIDs you will cite (1–2 calls, batched)

Same two calls as the claim check in `SKILL.md`: abstracts via
`pubtator_get_publication_passages` (`pmids` as strings, at most 5 per call,
`max_chars` = 2500 × number of PMIDs, re-fetch any `title_only` PMID alone) and
`publication_types` via `pubtator_search_literature` (`text="<pmid> OR <pmid>"`).
Open-access full text: `full: true` with `sections`.

- **confirmed**: the text names this allele (HGVS, protein change or rsID) or this
  gene–disease link and supports the claim.
- **contradicted**: the text says the opposite, or describes a different allele, gene or
  disease.
- **not confirmable**: abstract silent on the point, or the text could not be fetched.

A paper that mentions the gene but not the variant does not support a variant claim. A legacy
variant name counts only after you map it to HGVS; say how you mapped it.

## 5. Stop

Stop when each claim in the question has its sources and states. More papers do not make a
stronger answer; contradictions and gaps do.

## Default output (when the request gives no format)

**Question** as understood (gene, variant, disease, mode, cut-off date).

**Curated summary**: 3–6 sentences from GeneReviews / ClinGen / GenCC / ClinVar, each with its
record id (NBK id + section, ClinGen permalink, VCV).

**Evidence table**

| PMID | Year | Claim it is used for | What the text shows (short quote or paraphrase) | State |
|---|---|---|---|---|

**New since <date>** (surveillance): only papers on or after the date; "none found in PubTator
for <query>" if empty, with the query.

**Conflicts and gaps**: contradictions between sources; questions no source answered.

**Coverage**: queries run, total hits per source, how many papers were read.
**Tools used and data versions.** Research use only; not clinical decision support.
