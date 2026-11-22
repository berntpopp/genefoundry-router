# Mitochondrial DNA variants

Source: McCormick et al. 2020, ClinGen mtDNA specifications of ACMG/AMP (PMID 32906214).
Those specifications address maternal inheritance, heteroplasmy, threshold effects, the absence
of splicing, haplogroup context, mtDNA databases and computational tools. Where the specification
gives a code-level rule, use it; this file only lists what to gather.

## Gather (2–3 calls)

- `mitomap_get_variant` (`variant`, e.g. `m.3243A>G`, rCRS NC_012920.1): MITOMAP status
  ("Reported", "Cfrm" = confirmed), disease, homoplasmy/heteroplasmy as reported, GenBank
  frequency, reference count. `found=false` means MITOMAP does not list it, not "benign".
- `gnomad_get_mitochondrial_variant` (`variant_id` = `M-POS-REF-ALT`): homoplasmic and
  heteroplasmic allele counts and the heteroplasmy distribution.
- `clinvar_get_variant` for the ClinVar record and stars.

## Rules for the report

- Always state heteroplasmy level (from the patient's test if given) and tissue tested.
- Population frequency: report homoplasmic and heteroplasmic counts separately.
- Haplogroup background can explain a variant's frequency; if the variant is a haplogroup
  marker, say so (MITOMAP polymorphism record) rather than calling it rare.
- No splicing codes for mtDNA. PVS1 does not apply the nuclear way; follow the specification.
- If the specification's code rules are not available in this session, report codes as
  "not evaluated" rather than applying nuclear thresholds.
