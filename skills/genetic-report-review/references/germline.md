# Germline SNV/indel report: per-variant checks

Use with Passes 3 to 6 of `SKILL.md`. One block per reported variant.

## 1. Identity of the variant

- `vep_annotate_variant` with the report's HGVS and the stated `assembly`: does the tool's
  `hgvsc`/`hgvsp` match the report? Is the transcript MANE Select (PMID 35388217)? If the
  report uses another transcript, it should say why.
- Genomic coordinate: `gnomad_resolve_variant_id` (or ClinVar `coordinates`) gives the
  position per build. A GRCh37 coordinate labelled GRCh38 (or the reverse) is a Major error.
- rsID: resolve once; use it for LitVar.
- Mechanism vs variant type: "truncating" or "loss of function" said of a missense or
  in-frame variant is contradicted by the VEP consequence.

## 2. Current classification

- `clinvar_get_variant` (`response_mode` compact): report the exact aggregate class, stars
  and review status, plus the release date. A 3–4 star (expert panel / practice guideline)
  record that disagrees with the report is a Major finding at least; 0–1 star disagreement
  is a note.
- `clingen_get_gene_summary` shows whether a ClinGen VCEP exists for the gene. If it does,
  the VCEP's gene-specific rules (thresholds, code strengths, codes not applicable) apply
  instead of the generic ones below.

## 3. ACMG/AMP codes (Richards 2015, PMID 25741868)

Check each asserted code is met at the stated strength. Typical checks:

| Code | Check | Tool |
|---|---|---|
| PVS1 | LoF is a known disease mechanism for this gene; NMD predicted (not last exon or last 50 bp of the penultimate exon); the exon is in the biologically relevant transcript. Strength per the ClinGen PVS1 decision tree (PMID 30192042). | `autopvs1_get_variant_pvs1_data`, `clingen_get_gene_dosage` (haploinsufficiency), `gnomad_get_gene_summary` (constraint is context, not proof) |
| PS1 / PM5 | Same amino-acid change (PS1) or a different pathogenic missense at the same residue (PM5) exists and is itself well classified. | `clinvar_get_variant` |
| PS2 / PM6 | De novo confirmed with both parents tested (PS2) or only assumed (PM6). The report must say which. | report text |
| PS3 / BS3 | Functional assay is validated and calibrated for this gene (PMID 31892348). | `mavedb_search_score_sets`, the cited PMID |
| PS4 | Case counts or odds ratio are about this exact allele. | `litvar_get_variant_literature` |
| PM1 | Hotspot or critical functional domain without benign variation. | `metadome_get_tolerance_landscape` |
| PM2 | Absent or extremely rare for the disease model. ClinGen SVI recommends supporting strength by default. | `gnomad_get_variant_frequencies` |
| PM3 | In trans with a pathogenic variant (recessive); phase shown, not assumed. | report text |
| PM4 | In-frame length change in a non-repeat region; never together with PVS1. | `vep_annotate_variant` |
| PP3 / BP4 | Calibrated predictor thresholds (PMID 36413997); PP3 plus PM1 together capped at strong. | `vep_annotate_variant` (REVEL, AlphaMissense), `spliceai_predict_splicing` |
| Splicing codes | Per ClinGen SVI splicing (PMID 37352859): PVS1 for a canonical splice site, PP3 for predicted splice impact; do not count both for the same effect. | `spliceai_predict_splicing` |
| BA1 / BS1 | BA1 = allele frequency above 5% (or the VCEP threshold); BS1 = above the disease-specific threshold (prevalence, penetrance, heterogeneity). | `gnomad_get_variant_frequencies` (use the group maximum filtering allele frequency) |

Double counting to flag: PVS1 with PM4; PVS1 with PP3 for the same splice effect; PS3 and
PP3 resting on the same data; PM1 with PP3 above strong; PP5/BP6 used as evidence
(ClinGen SVI advises against them; use the underlying data instead).

## 4. Points (Tavtigian 2020, PMID 32720330)

Add step by step: very strong 8, strong 4, moderate 2, supporting 1; benign codes count
negative the same way. BA1 is not added: it is stand-alone, so BA1 alone means Benign unless
a VCEP lists an exception for the variant (Ghosh 2018, PMID 30311383).
Totals: pathogenic ≥ 10; likely pathogenic 6 to 9; VUS 0 to 5; likely benign −1 to −6;
benign ≤ −7. Show the sum. If the stated codes do not reach the stated class, that is a
Major finding; if the class would change the clinical message (e.g. LP → VUS), Critical.

## 5. Literature and GeneReviews

- For each PMID: `pubtator_get_publication_passages` (mode `full_abstract`) and, if the
  variant has an rsID, `litvar_get_variant_literature`. Is this allele in the paper? Is the
  paper's claim the one the report makes (case report vs functional study vs review)?
- Every clinical percentage, penetrance figure, de novo rate, inheritance and OMIM number:
  `genereviews_search_passages` with `gene` set and `sections` such as
  `["clinical_features"]`, `["genetic_counseling"]` or `["molecular_genetics"]`. Quote the
  passage id. A number that the chapter states differently is contradicted; a number the
  chapter does not state is not confirmable.

## 6. Gene–disease and zygosity

- `clingen_get_gene_validity` and `gencc_get_gene_curations`: classification and mode of
  inheritance for the disease named in the report. Disputed or refuted = Critical when it
  carries the diagnosis.
- Zygosity × inheritance: AD heterozygous fits; AR needs two alleles in trans or a
  homozygous call (consider a deletion or uniparental disomy for apparent homozygosity);
  X-linked: hemizygous in males, heterozygous in females with variable expression.
- Recurrence risk: AD 50% per child; AR 25% for a couple who are both carriers; X-linked
  depends on the transmitting parent's sex. A stated risk must follow from these unless the
  report states why not (de novo with possible parental germline mosaicism, reduced
  penetrance, mosaicism, imprinting); an unexplained departure is a finding.
