# ACMG/AMP derivation (Steps 1–5)

Read this only when Step 0 found no 3- or 4-star (expert panel or practice guideline)
class, or when the user asks for a code-by-code derivation.

The hard limit in `SKILL.md` (8 calls after Step 0, then stop and answer with what you have)
wins over the step budgets below.

## Step 1: Normalize (2–3 calls)

1. Ask for or confirm the genome build. `vep_annotate_variant` (`assembly`) → MANE Select
   HGVS c./p., consequence, gene, REVEL/AlphaMissense, gnomAD-style id.
2. An rsID can cover several ALT alleles at one position (VEP returns one entry per ALT).
   Keep only the ALT that matches the stated change; pass `allele` to `vep_annotate_variant`.
3. Use the MANE Select transcript for every later step and say so.
   VEP's default `most_severe_consequence` / representative transcript can come from another isoform: take the consequence, c. and p. from the transcript named in the input or the MANE Select entry in the transcript list.
4. Write the variant in the form each tool takes, or the call fails:
   - VEP, ClinVar: one plain HGVS (`NM_000059.4:c.3901_3903del`), no gene name or protein
     around it; write repeat notation (`c.3901ACT[1]`) as del/dup first.
   - gnomAD: `CHROM-POS-REF-ALT` with both alleles (from VEP's output) or an rsID. Never pass
     "GENE c.change" to `gnomad_resolve_variant_id`; recode it with VEP first. "Not found"
     in gnomAD is a valid result (absent, relevant to PM2), not a failed call.
   - LitVar: an rsID; an HGVS without rsID or a coordinate usually returns not_found.

## Step 2: Gene–disease and mechanism (1–2 calls)

- `clingen_get_gene_validity` (and `gencc_get_gene_curations` if ClinGen has no curation).
- If validity for the disease is Limited, Disputed or Refuted: say so; a variant cannot be
  more than VUS for that disease (ClinGen advice). Continue only on request. If the gene has
  no curation at all, say so and state that the gene–disease link is unassessed; do not cap
  the class on that basis alone.
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
| Population | `gnomad_get_variant_frequencies` | BA1, BS1, BS2, PM2 | Compare `summary.faf95…grpmax`, not raw max AF; no faf95 (very low AC) → use AC/AN as the VCEP spec says. BA1: allele frequency > 0.05 unless the VCEP sets a lower value (Ghosh 2018, PMID 30311383). BS1: only with a disease-specific threshold (VCEP or stated calculation); otherwise "not evaluated". Check BA1/BS1 against the spec's thresholds before PM2. PM2: Supporting strength only (ClinGen SVI 2020), and only if gnomAD answered and the variant is absent or below the spec's PM2 threshold. A failed or unresolved gnomAD call gives "not evaluated", never "absent". Never PM2 together with BS1 or BA1. |
| Null variant | `autopvs1_get_variant_pvs1_data` (`genome_build`, `variant_id`) | PVS1 (any strength) | Read `references/pvs1.md`. Only if LoF is a disease mechanism. |
| Splicing | `spliceai_predict_splicing` | PP3, BP4, BP7, PVS1 (RNA) | Read `references/splicing.md`. SpliceAI answers only the splicing question; it never gives PP3/BP4 for a missense effect. |
| Missense predictor | `vep_annotate_variant` (REVEL) | PP3, BP4 | One tool chosen before seeing the score: the spec's named predictor, else REVEL with the thresholds below. One PP3 or BP4 per variant. |
| Functional | `mavedb_find_variant` (`variant`, `gene_symbol`) | PS3, BS3 | Read `references/functional.md`. Only calibrated assays. |
| Same residue | `clinvar_search_variants` (gene + residue), `clinvar_get_variant` | PS1, PM5 | Name the comparator variant, its class and stars; a comparator that is not itself P/LP gives nothing. |
| Hotspot/domain | VCEP spec; `metadome_get_tolerance_landscape` as context | PM1 | If the spec defines PM1 residues or domains (`clingen_get_cspec_criterion`, `code="PM1"`), check the variant's residue against them. Without a VCEP definition, apply PM1 only with a stated, cited domain/hotspot rationale. |
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
benign codes count negative. BA1 is stand-alone and never added: BA1 met means Benign unless
the variant is on the ClinGen SVI BA1 exception list (Ghosh 2018, PMID 30311383) or a VCEP
exempts it. Classes (same paper, Table 3):

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
