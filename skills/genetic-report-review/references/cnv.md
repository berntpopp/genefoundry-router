# CNV report checks

Standard: ACMG/ClinGen CNV technical standards (Riggs 2020, PMID 31690835), points-based.

1. **Description.** Gain or loss, genomic coordinates with build, size, cytoband, and the
   method's resolution. Cytogenomic notation per ISCN where the lab uses it. Coordinates
   from one build must not be compared with tools on another.
2. **Gene content.** List genes in the interval: `gnomad_get_region` (≤100 kb) or
   `ucsc_get_region_annotations` with track `mane` (≤2 Mb). The report's gene count or gene
   list must match.
3. **Dosage sensitivity.** `clingen_search_dosage` (region or cytoband; recurrent ISCA
   regions) and `clingen_get_gene_dosage` for genes inside. A loss over a gene with
   haploinsufficiency score 3, or a gain over a triplosensitivity score 3, supports
   pathogenicity; score 30 means "autosomal recessive" and a single-copy loss is then a
   carrier state, not a diagnosis.
4. **Points.** Riggs sections 1 to 5 (genomic content, overlap with established regions,
   gene number, case evidence, inheritance). Check the stated total reaches the stated class
   (pathogenic ≥ 0.99, likely pathogenic 0.90 to 0.98, VUS −0.89 to 0.89, likely benign
   −0.90 to −0.98, benign ≤ −0.99). Show the sum.
5. **Partial gene overlap and breakpoints.** A deletion of part of a gene: is the
   consequence (frameshift, exon loss) stated? A gain inside a gene: tandem orientation is
   usually unknown; the report should say so.
6. **Recessive unmasking.** A deletion over an AR gene: was the other allele looked at?
7. **Limitations.** Single-exon CNVs in NGS data, balanced rearrangements, low-level
   mosaicism and segmental duplication regions are hard to detect; the report must say what
   applies (check segdups with `ucsc_get_region_annotations`, track `genomicSuperDups`).
8. **Recommendations.** Parental testing for inheritance; orthogonal confirmation where
   the lab's policy needs it.
