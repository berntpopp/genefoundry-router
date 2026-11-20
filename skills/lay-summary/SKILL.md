---
name: lay-summary
description: Use when asked to explain a genetic result, gene, variant or genetic condition in plain language for a patient, parent or family member, e.g. "explain this VUS to the family", "write a patient-friendly summary of this BRCA2 result", "lay summary of Marfan syndrome", "Erklärung für die Familie in einfacher Sprache".
---

# Lay summary of a genetic result or condition

Research use only. Not clinical decision support. A lay summary supports, and never
replaces, a conversation with a genetic counsellor or physician.

Reach every tool through `search_tools` → `call_tool`. Tool names are
`<namespace>_<tool>`.

## Input

- **Best:** findings that are already verified (the output of `genetic-report-review`,
  `germline-variant-classification`, or a signed report). Keep their class and wording of
  certainty exactly.
- **Otherwise:** a gene, variant or condition name. Then collect only what you need:
  `hgnc_resolve_symbol` (gene), `clinvar_get_variant` (class, stars), `mondo_resolve_disease`
  or `orphanet_search_diseases` (condition), `genereviews_search_passages` with `sections`
  `["summary"]`, `["clinical_features"]` or `["genetic_counseling"]` (what the condition is,
  how it is inherited, what care looks like).
- De-identified input only. Language: the language of the request (German or English) unless
  asked otherwise.

## Rules

1. **No new facts.** Every statement comes from the verified input or a tool result in this
   session. If a number (risk, frequency, penetrance) is not in a source, leave it out.
2. **Keep the certainty.** Pathogenic / likely pathogenic: "explains" or "is very likely to
   explain". VUS: "we do not yet know whether this change matters; it is not used to make
   medical decisions". Never write a VUS as a cause, and never write "negative" as "healthy"
   or "ruled out".
3. **Plain words.** Aim for a grade-8 reading level: short sentences (about 15 words), common
   words, one idea per sentence. Explain each needed term once in brackets ("variant (a
   change in the genetic code)"). No HGVS in the main text; put it in a short "for your
   doctor" line at the end.
4. **Inheritance in pictures of chance.** AD: "each child has a 1 in 2 chance". AR: "when
   both parents carry a change, each child has a 1 in 4 chance". Only state what follows
   from the verified inheritance mode.
5. **Respect and hope without false comfort.** Neutral, non-blaming words ("change" not
   "mutation"; "has the condition" not "suffers from"). Name what can be done, only if a
   source says so.
6. **Stop rule.** At most about 5 tool calls when starting from scratch; if something
   important is missing, say it is not known rather than searching on.

## Output

If the request specifies an answer format, answer in exactly that format; the template below is the default.

**What was found** (2–3 sentences)

**What this means, and what it does not mean**

**What is still uncertain**

**What this may mean for your family** (only if inheritance is verified)

**Questions you may want to ask your doctor or genetic counsellor** (3–5 questions)

**For your doctor:** gene, variant (HGVS), class, sources with record ids.

A closing line: "This summary is for information. Please discuss it with your doctor or
genetic counsellor."

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
