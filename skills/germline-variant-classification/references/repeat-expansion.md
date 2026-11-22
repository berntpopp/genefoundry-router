# Repeat expansions (STR sizing)

ACMG/AMP points are not used for repeat sizes. The question is which published range the
measured repeat count falls into, for this locus.

## Gather (1–2 calls)

- `strchive_search_loci` (`query` = gene, disease or motif) if the locus is unclear.
- `strchive_get_locus` (`locus`, e.g. `HTT`, `HD_HTT`, `RFC1`): benign / intermediate /
  pathogenic ranges as the source states them (closed `[min, max]` in repeat units; a range the
  source does not state is `null` with `not_stated_by_source`), pathogenic motifs and
  interruptions, inheritance, coordinates per build, PMIDs.
- `genereviews_search_passages` (`q`, `gene`, `sections: ["molecular_genetics"]`) for allele
  classes and penetrance wording when GeneReviews covers the disease.

## Rules

- Compare the measured size, in the same unit (repeat units, not base pairs), to each range.
  Show the comparison: `42 CAG in [40, ∞) → full-penetrance range (STRchive)`.
- A range the source does not state is "not stated", never inferred from neighbours.
- Motif matters: a pathogenic size with a non-pathogenic motif (e.g. RFC1) is not pathogenic.
  Say whether the motif was determined by the test.
- Intermediate and reduced-penetrance ranges are reported as such, never rounded up.
- Sizing accuracy and the method's upper limit come from the lab; report them as given.
