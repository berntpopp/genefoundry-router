---
name: lay-summary
description: Use when asked to explain a genetic result, gene, variant or genetic condition in plain language for a patient, parent or family member, e.g. "explain this VUS to the family", "write a patient-friendly summary of this BRCA2 result", "lay summary of Marfan syndrome", "Erklärung für die Familie in einfacher Sprache".
---

# Lay summary of a genetic result or condition

Research use only. Not clinical decision support. A lay summary supports, and never
replaces, a conversation with a genetic counsellor or physician.

## Input

- **Verified findings** (a signed report, or the output of `genetic-report-review` or
  `germline-variant-classification`): write from them alone, with no tool calls, no record
  ids beyond those given, and no "tools used" line. Keep their class and certainty.
- **Only a name** (gene, variant, condition): at most 5 calls through `search_tools` →
  `call_tool`: `hgnc_resolve_symbol`, `clinvar_get_variant`, `mondo_resolve_disease` or
  `orphanet_search_diseases`, `genereviews_search_passages` with `sections` `["summary"]` or
  `["genetic_counseling"]`. Anything still missing: say it is not known.
- De-identified input only. Write in the language of the request.

## Gate: check the draft before you send it

1. **No new facts.** Each sentence restates a verified fact. Add nothing about symptoms,
   course, treatment, tests or chances that the facts do not state.
2. **Number rule.** Use only the figures in the facts. The same figure in another form is
   fine (25% = 1 in 4). No other percentage, "N in M", fraction, "twice" or "times as
   likely", and no dose.
3. **Reading level: grade 8.** Sentences under 15 words; everyday words. Replace the facts'
   jargon, do not copy it: "autosomal dominant" → "one changed copy is enough"; "autosomal
   recessive" → "a child needs a changed copy from each parent"; heterozygous → "one copy";
   pathogenic → "harmful". A term you keep (variant, carrier) gets a plain meaning in
   brackets right after it, the first time.
4. **Technical names** (c./p. names, NM_ or rs numbers) appear only in the last line,
   "For your doctor:".
5. **Length** 120 to 300 words of prose, the "For your doctor:" line not counted.
6. **Chances as chances.** Write "a 1 in 4 chance of a child with the condition", never
   "the child will have it".
7. **Who to ask** comes from the facts: the genetics team, or for a medicine result the
   doctor or pharmacist.

## Required and forbidden, by result type

| Result | Must say | Never say |
|---|---|---|
| Pathogenic | the change is harmful and explains the problem or a raised (higher) chance; not a certainty; relatives' chance as given; the next step in the facts | "you will get (another) cancer", "no risk", "test your children now" |
| VUS | we do not yet know whether this change matters; it does not explain the problem and is not a diagnosis; it is not used to test relatives or change care; it may be reviewed later | cause, "explains", "linked to your illness", "relatives should be tested" |
| Negative, no family variant known | lowers the chance but does not rule out an inherited cause; screening follows the family history | "healthy", "all clear", "ruled out", "no risk" |
| Negative for the known family variant | not inherited; her chance is not raised by it (the same as other people); usual screening for everyone | that other genes or causes were checked |
| Carrier | does not have the condition, carriers are usually healthy; the chances only if the partner is a carrier too; the partner test or options in the facts | "you have / will get it", "your child will have it" |
| CNV | explains the problem; effects differ, the course cannot be predicted; parents' test | how the child will develop |
| Medicine (PGx) | which medicine it concerns, and only that; not a disease, no effect on health by itself; the doctor or pharmacist chooses the medicine | any dose; the patient starting, stopping or lowering a medicine |
| Predictive | exactly the certainty the facts give (will develop / raised chance); when it starts is unknown if so stated; children are tested as adults | "test your children now", a date or age not in the facts |
| Secondary finding | it has nothing to do with the reason for the test; what it means as stated; the next check | that it explains the original problem; illness as certain |

## Output

If the request specifies an answer format, use exactly that. Then add no headings, lists,
closing disclaimer or "tools used" line; put "ask your genetics team" in the prose.

Default: three or four short paragraphs (what was found; what it means and does not mean;
what it means for the family; next step and who to ask), then one line "For your doctor:"
with gene, variant, class and source (record ids, or "as given in the verified input").

## Tool calls

<!-- tool-calls:start -->
- `clinvar_get_variant`(identifier, id_type) — direct
- `genereviews_search_passages`(q, query, gene, nbk_id, sections, heading_path_contains, mode, limit, exclude, include, snippet_chars, rerank) — direct
- `hgnc_resolve_symbol`(query) — direct
- `mondo_resolve_disease`(query) — direct
- `orphanet_search_diseases`(query; optional: limit, include_obsolete) — direct
<!-- tool-calls:end -->

## Evidence rules (all GeneFoundry skills)

- Research use only. Not clinical decision support. Use de-identified input only; if the input
  contains names, dates of birth or record numbers, stop and ask for a de-identified version.
- Every fact comes from a tool result in this session. Cite the record id the tool returned
  (PMID, VCV, HGNC id, MONDO id, ...). Never cite from memory.
- Give every checked claim one state: **confirmed** (tool agrees), **contradicted** (tool disagrees;
  show the evidence), **not confirmable** (tool returned nothing or failed), **not checked**.
  "Not found in source X" never means "negative" or "benign".
- State genome build and transcript before any variant lookup. Resolve identifiers once, in batch.
- Tools marked "via call_tool" below are not in your tool list: call them as
  `call_tool(name="<tool>", arguments={...})` straight away, with the argument names given,
  without trying the bare name or searching for them first.
- Scores, points and risks are added up step by step by the stated rule, never estimated.
- If a call errors or is rate limited, do not repeat the identical call: use another source or
  report the gap. Stop when the skill's stop rule is met.
- End with: tools used and the data versions their results already report (never make a call
  only to get a version).
