# PVS1: predicted loss of function

Source: Abou Tayoun et al. 2018, ClinGen SVI PVS1 recommendations (PMID 30192042).

## Preconditions

- PVS1 applies only when loss of function (LoF) is a known disease mechanism for the gene and
  disease. Take this from the VCEP specification or the gene–disease curation.
- If the mechanism is not established, the SVI paper suggests applying PVS1 only if LoF sequence
  and/or copy-number variants make up at least 10% of the reported pathogenic variants in the
  gene and at least three LoF variants are classified pathogenic without using PVS1. Otherwise
  report PVS1 as "not evaluated: LoF mechanism not established".

## Use AutoPVS1, then check it

`autopvs1_get_variant_pvs1_data` (`genome_build`: hg19 or hg38, `variant_id`). Read the
decision path and final strength. Widen to `response_mode: "standard"` only if you need to
explain the tree. Check the tool's path against these rules from the paper:

- **NMD:** nonsense-mediated decay is generally not predicted when the premature stop lies in
  the 3'-most exon or within the 3'-most 50 nucleotides of the penultimate exon.
- **Truncations escaping NMD:** if the removed region is not known to be critical, removing
  more than 10% of the protein supports PVS1_Strong and less than 10% PVS1_Moderate.
- **Variant types** covered by the decision tree: nonsense/frameshift, canonical ±1,2 splice
  sites, initiation codon, single- or multi-exon deletion and duplication. Each has its own
  branch; quote the branch AutoPVS1 used.
- **Biologically relevant transcript:** if the affected exon is absent from relevant
  transcripts, the strength drops. Say which transcript the tool used and whether it is MANE.

## Combination rules

- Never apply PM4 when PVS1 is applied at any strength.
- Never apply PP3 together with PVS1 (Walker 2023, PMID 37352859).
- If RNA data show the splicing outcome, see `splicing.md`: the RNA result sets the PVS1 (RNA)
  strength.

## Report

`PVS1_<strength>`: AutoPVS1 branch, transcript, NMD prediction, % protein removed if relevant,
and any disagreement between the tool's path and the rules above.
