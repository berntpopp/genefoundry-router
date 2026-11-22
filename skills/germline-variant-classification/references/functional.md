# Functional evidence (PS3 / BS3), including MAVE data

Source: Brnich et al. 2019, ClinGen SVI PS3/BS3 recommendations (PMID 31892348).

## Four steps (from the paper)

1. Define the disease mechanism.
2. Decide whether the general class of assay reflects that mechanism.
3. Judge the specific assay instance (controls, replicates, validation).
4. Apply the evidence to the variant at a strength the validation supports.

Without rigorous statistical calibration, an assay needs at least 11 total pathogenic and benign
variant controls to reach moderate-level evidence. With fewer controls: supporting at most.

## Tools

- `mavedb_find_variant` (`variant` = HGVS, `gene_symbol`): every MaveDB score set that measured
  this variant, with score and calibrated classification when the set carries one.
- `mavedb_get_variant_score` (`urn`, `hgvs`): one score set's row for the variant.
- `mavedb_search_score_sets` (`targets: [gene]`): which assays exist for the gene.

## Rules

- Use only the strength the score set's calibration states (its `calibrations` /
  `classifications`). A raw score without calibration is reported as context, state
  "not evaluated: uncalibrated assay".
- Literature functional data: a PMID counts only after its passage shows this exact variant was
  assayed. Verify with `pubtator_get_publication_passages`.
- RNA splicing assays are not PS3/BS3: see `splicing.md`.
