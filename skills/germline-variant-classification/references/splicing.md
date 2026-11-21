# Splicing evidence

Source: Walker et al. 2023, ClinGen SVI Splicing Subgroup (PMID 37352859).

## Prediction (`spliceai_predict_splicing`)

Call with `variant_id` and `genome_build`; read the SpliceAI maximum Δ score from the headline.
For variant interpretation the paper's calibration uses SpliceAI; Pangolin agreement is context.

| SpliceAI max Δ | Meaning (Walker 2023, Table 1) | Code |
|---|---|---|
| ≥ 0.2 | predicted spliceogenic | PP3 (supporting) |
| > 0.1 and < 0.2 | uninformative | none |
| ≤ 0.1 | predicted not spliceogenic | BP4 (supporting); for a missense or in-frame variant only if the missense predictor is also in its BP4 range. SpliceAI ≤ 0.1 never cancels a REVEL PP3. |

- PP3 is used once per variant. If PP3 is already used for a splicing prediction, do not
  add it again for a missense predictor, and vice versa (pick the stronger, state why).
  PP3 may still be applied for a missense or in-frame indel with computational evidence for a
  protein effect when there is no splicing prediction.
- Canonical ±1,2 donor/acceptor variants go through the PVS1 tree (`pvs1.md`), not PP3.

## BP7

- BP7 applies to synonymous variants and to intronic variants at or beyond positions +7/−21,
  after BP4 is met (no predicted splice impact).
- Never apply BP7 inside a designated splice region, or for synonymous changes at the first
  base or the last 3 bases of an exon.

## RNA (observed) evidence

- Splicing assay showing an LoF transcript: use PVS1 (RNA) with the strength from the PVS1 tree,
  and remove PP3/BP4 for that variant. PS3/BS3 are not used for RNA splicing results.
- Splicing assay showing no aberrant transcript: BP7_Strong (RNA). For a missense or in-frame
  variant, this alone does not make it benign; the protein effect still needs evidence.
- Results from tumour tissue or complex readouts (two or more alternative transcripts from one
  allele) carry less weight; say so.
