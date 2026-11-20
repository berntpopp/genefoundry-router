---
name: germline-variant-classification
description: Use when asked to classify a germline sequence variant (SNV or small indel) as pathogenic, likely pathogenic, VUS, likely benign or benign under ACMG/AMP, to re-derive or check an existing classification, or to say which ACMG criteria a variant meets — e.g. "classify NM_000492.4:c.1521_1523del in CFTR", "is this MYH7 missense a VUS?", "which ACMG codes apply to rs80357906?". Also mtDNA variants and repeat-expansion sizing via its references. Not for somatic/tumour variants or CNVs.
---

# Germline variant classification (ACMG/AMP)

Re-derive the class from evidence; never copy a class from ClinVar or a report. Every criterion
is decided from a tool result, weighted by the rules below, and summed as points step by step.

Reach tools through `search_tools` → `call_tool`; names are `<namespace>_<tool>`.

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

## Scope

- Germline SNV/indel against ONE gene–disease pair. If the user names no disease, use the
  curated disease(s) for the gene and classify per disease.
- mtDNA variant: also read `references/mitochondrial.md`. Repeat expansion: read
  `references/repeat-expansion.md` (sizing, not ACMG points).
- Somatic variants: use the `somatic-variant-interpretation` skill instead. CNVs: out of scope.
- ACMG/AMP v4 is not yet published; do not apply v4 rules.

## Step 1: Normalize (2–3 calls)

1. Ask for or confirm the genome build. `vep_annotate_variant` (`assembly`) → MANE Select
   HGVS c./p., consequence, gene, REVEL/AlphaMissense, gnomAD-style id.
2. An rsID can cover several ALT alleles at one position (VEP returns one entry per ALT).
   Keep only the ALT that matches the stated change; pass `allele` to `vep_annotate_variant`.
   For loose text, `gnomad_resolve_variant_id`.
3. Use the MANE Select transcript for every later step and say so.

## Step 2: Gene–disease and mechanism (1–2 calls)

- `clingen_get_gene_validity` (and `gencc_get_gene_curations` if ClinGen has no curation).
- If validity for the disease is Limited, Disputed, Refuted or absent: say so; a variant
  cannot be more than VUS for that disease. Continue only on request.
- Note the mode of inheritance and whether loss of function (LoF) is a known mechanism
  (needed for PVS1). `gnomad_get_gene_summary` constraint is context, never a criterion.

## Step 3: Expert-panel rules override generic ones (1–2 calls)

`clingen_list_cspecs` (`gene_symbol`). If a Released specification exists for the gene and
disease, read it with `clingen_get_cspec`, or one code at a time with
`clingen_get_cspec_criterion` (`gn_id`, `code`). Its thresholds, strengths and "not
applicable" codes replace the generic rules below. Name the spec (GN id, version).

## Step 4: Gather evidence (batch; 5–8 calls)

| Evidence | Tool | Codes | Rules |
|---|---|---|---|
| Population | `gnomad_get_variant_frequencies` | BA1, BS1, BS2, PM2 | Compare `summary.faf95…grpmax`, not raw max AF; no faf95 (very low AC) → use AC/AN as the VCEP spec says. BA1: allele frequency > 0.05 unless the VCEP sets a lower value (Ghosh 2018, PMID 30311383). BS1: only with a disease-specific threshold (VCEP or stated calculation); otherwise "not evaluated". PM2: Supporting strength only (ClinGen SVI 2020). |
| Null variant | `autopvs1_get_variant_pvs1_data` (`genome_build`, `variant_id`) | PVS1 (any strength) | Read `references/pvs1.md`. Only if LoF is a disease mechanism. |
| Splicing | `spliceai_predict_splicing` | PP3, BP4, BP7, PVS1 (RNA) | Read `references/splicing.md`. |
| Missense predictor | `vep_annotate_variant` (REVEL) | PP3, BP4 | One tool chosen before seeing the score: REVEL, thresholds below. |
| Functional | `mavedb_find_variant` (`variant`, `gene_symbol`) | PS3, BS3 | Read `references/functional.md`. Only calibrated assays. |
| Same residue | `clinvar_search_variants` (gene + residue), `clinvar_get_variant` | PS1, PM5 | Name the comparator variant, its class and stars; a comparator that is not itself P/LP gives nothing. |
| Hotspot/domain | VCEP spec; `metadome_get_tolerance_landscape` as context | PM1 | Without a VCEP definition, apply PM1 only with a stated, cited domain/hotspot rationale. |
| Case data | PMIDs cited by `clinvar_get_variant` / `clingen_get_variant_interpretation` first, then `litvar_get_variant_literature`; verify with `pubtator_get_publication_passages` | PS2, PM6, PS4, PM3, PP1, PP4 | A PMID counts only after its passage shows this exact allele and the case data; otherwise "not confirmable". LitVar lists any mention under the rsID (other ALTs included); its top hits are often reviews. Case data the user gives (de novo, phase, segregation) is used as stated. |

ClinVar's aggregate classification is a comparator (Step 6), never an evidence code.
Do not use PP5 or BP6 (reputable-source codes; Biesecker 2018, PMID 29543229).

REVEL (Pejaver 2022, PMID 36413997, Table 2), missense only:

| Strength | PP3 (pathogenic) | BP4 (benign) |
|---|---|---|
| Supporting | ≥ 0.644 and < 0.773 | > 0.183 and ≤ 0.290 |
| Moderate | ≥ 0.773 and < 0.932 | > 0.016 and ≤ 0.183 |
| Strong | ≥ 0.932 | > 0.003 and ≤ 0.016 |
| Very strong | not reached | ≤ 0.003 |

Scores between 0.290 and 0.644 are indeterminate: no PP3, no BP4.

## Step 5: Weigh and add up (no tool calls)

Points (Tavtigian 2020, PMID 32720330): Supporting 1, Moderate 2, Strong 4, Very strong 8;
benign codes count negative. Classes (same paper, Table 3):

| Total points | Class |
|---|---|
| ≥ 10 | Pathogenic |
| 6 to 9 | Likely pathogenic |
| 0 to 5 | Uncertain significance |
| −1 to −6 | Likely benign |
| ≤ −7 | Benign |

Before adding, apply the double-count rules:

- PM4 is never applied when PVS1 is applied at any strength (Abou Tayoun 2018, PMID 30192042).
- PP3 is never applied together with PVS1; PP3 is used once per variant, for splicing or
  protein effect, not both (Walker 2023, PMID 37352859).
- PM1 + PP3 together count at most Strong (4 points) (Pejaver 2022, Table 4).
- PS3 from an RNA splicing assay is not used: RNA evidence goes to PVS1 (RNA) or BP7 (Strong)
  per `references/splicing.md`.

Write the sum as a line: `PVS1 (8) + PM2_Supporting (1) = 9 → Likely pathogenic`.

## Step 6: Compare and stop

- `clinvar_get_variant`: report ClinVar classification, stars and review status.
- `clingen_get_variant_interpretation` (`variant_id` = ClinVar VariationID, CAID or HGVS): the
  expert panel's own classification and met/not-met codes, if one exists. Look up the one
  variant; do not list a gene's interpretations.
- If either differs from your class, list which codes explain the difference. An expert-panel
  classification that disagrees is a finding for a human, not something to override.
- Stop when every code row has a state. Do not page literature beyond the first page unless a
  code you would apply depends on it. Budget: about 15 tool calls.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

**Variant:** gene, MANE transcript, HGVS c./p., build, consequence.
**Gene–disease:** disease (MONDO), validity, inheritance, LoF mechanism yes/no, VCEP spec used.

| Code | Strength | Points | Evidence (tool, record id, value) | State |
|---|---|---|---|---|

States: met / not met / not evaluated (why) / not confirmable.

**Sum:** the points line. **Class:** one of the five.
**ClinVar comparison:** class, stars, review status; reason for any difference.
**What would change the class:** the one or two missing data points with the largest effect
(e.g. parental testing for PS2, an RNA study).
**Tools used and data versions.** Research use only; not clinical decision support.
