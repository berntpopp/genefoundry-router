# GeneFoundry Tool-Schema Documentation Standard v1

> **Status: PROPOSED, 2026-07-14; v1.1 (rule 7, description lead) added 2026-11-19.**
> Tracking issue: `genefoundry-router#75`. A tracking issue
> titled "Adopt GeneFoundry Tool-Schema Documentation Standard v1" exists in each `-link` repo.
> Enforced statically by `scripts/check_tool_surface.py` (`make lint-surface`) and empirically by
> `docs/conformance/behaviour.py` (the `tools/call` gate).

`outputSchema` is optional and a model never reads it. **The `description` on an input property
is what the model actually reads to choose an argument.** Four backends essentially omit it:
`clinvar-link` and `gtex-link` document **0%** of their parameters, `pubtator-link` **1%**,
`gencc-link` **17%**. Fleet-wide, **363 input properties carry no description at all**.

This is not a documentation nicety. It is the direct cause of the fleet's most dangerous class of
bug.

## The failure this prevents

`clinvar-link/get_variants_by_gene` declares:

```json
"classification": { "anyOf": [{"type": "string"}, {"type": "null"}], "default": null }
```

No description. No `enum`. No examples. The runtime vocabulary is in fact a closed set of
lowercase, underscore-joined tokens. So:

| call | result |
|---|---|
| `classification: "likely_pathogenic"` | **559 variants** |
| `classification: "Likely pathogenic"` — *ClinVar's own published wording* | `total_count: 0`, `success: true` |
| `classification: "BANANA"` | `total_count: 0`, `success: true` |

A model asking for BRCA1 variants using ClinVar's own canonical spelling is told, confidently,
that there are none. 559 of them are hidden behind a capitalization difference, and **nothing in
the response signals that anything went wrong.** An undeclared enum is what *produces* the
silently-empty filter; a silently-empty filter is what produces a false clinical statement to a
curator.

Declaring the enum kills the bug class at the source: an out-of-enum value can then be rejected,
and the behaviour gate can prove it is.

`gnomad-link` is the in-house exemplar — 113 properties, 98% documented, with `enum`, `examples`
and `pattern`. The fix already exists inside the fleet; it just has not been applied elsewhere.

## Rules

1. **Every input property MUST carry a non-empty `description`.** (`S1`)
   Write it for someone who has never seen the upstream API. Anthropic's guidance is the right
   bar: *"think of how you would describe your tool to a new hire on your team… Consider the
   context you might implicitly bring — specialized query formats, definitions of niche
   terminology, relationships between underlying resources — and make it explicit."*

2. **Every parameter whose runtime accepts a closed set of values MUST declare that set as an
   `enum`.** (`S4`)
   If the code will only ever honour a fixed vocabulary — classifications, sort orders, response
   modes, assemblies, dosage codes — the schema MUST say so. A bare `str` where an enum belongs
   forces the model to guess, and a wrong guess is silently indistinguishable from an empty
   result.

   This rule cannot be checked by reading a schema — an undeclared enum looks exactly like a free
   string — so it is enforced **empirically** by the behaviour gate, which sends an unrecognised
   value to every optional filter and requires the server to reject it rather than match nothing.
   See `docs/conformance/behaviour.py`.

3. **Every REQUIRED property MUST carry at least one `examples` value.** (`S2`)
   These are the parameters a model must get right to make any call at all. **109 of the fleet's
   207 required parameters have no example.** Anthropic reports input examples taking accuracy on
   complex parameter handling from **72% to 90%** — the single largest cited win available to this
   fleet, at a cost of ~20–50 tokens per example.

4. **Every ARRAY-typed property MUST carry at least one `examples` value showing the array form.**
   (`S3`)
   A model cannot tell a list from a scalar by name alone, and the fleet is full of array
   parameters with singular names: `gtex-link/get_gene_information` takes **`gene_id` (singular)
   which is actually an array**. An example (`[["BRCA1", "TP53"]]`) makes it unmissable where a
   name cannot.

   Renaming is *not* required — `include`, `exclude` and `bias_toward` legitimately take lists and
   read correctly in English. An example is required.

5. **A parameter name MUST NOT lie about its type or its unit.** Prefer `user_id` to `user`
   (Anthropic's own example). Where a format is constrained, express it — `pattern` for a regex,
   `minimum`/`maximum` for a bound. `gtex-link` accepted a **negative `top_n`** and
   negative-sliced its tissue list, silently deleting the two kidney tissues from a UMOD query;
   `"minimum": 1` would have made that unrepresentable.

6. **Descriptions are load-bearing and MUST NOT be cut to meet the token budget.** If a tool
   cannot fit inside the Tool-Surface Budget with its parameters documented, drop its
   `outputSchema` (which the model never reads) — and if it still does not fit, the tool is doing
   too much and should be split.

7. **v1.1 — A tool description's FIRST sentence MUST say what the tool returns, in domain
   words, before any usage advice.** (`D1`)
   The lead is what search ranks on and what clients show: the router's reranker reads the
   first 300 characters, and many hosts truncate the description in their tool pickers. Put
   the result there — *"Return a Mondo disease term: …"*, not *"Use this when a user needs …"*.
   The fleet's `Use this when …` house style stays valid; the returns statement then lives in
   the same sentence (`Use this when …; returns …`) or as a labelled field list
   (`Use this for X: a, b, c`).

   The check is deterministic and checks form, not quality. The **lead** is the first sentence,
   capped at 300 characters (`e.g.`/`i.e.` do not end it). It passes on any of:

   | form | example (verbatim fleet leads) |
   |---|---|
   | output verb first: `Get`, `Retrieve`, `Resolve`, `Search`, `List`, `Predict`, … (also after `Use this to`) | `orphanet_resolve_disease`: *"Resolve a disease label, synonym, or ORPHAcode … to the canonical Orphanet term {orpha_code, name, match_type}."* |
   | returns statement: `Return(s/ed/ing) …`, `→`, `->` — not negated (`does not return`) and not a size (`Returns ~7kB`) | `mondo_get_disease`: *"Return a Mondo disease term: definition, synonyms, grouped cross-references, direct parents and children, …"* |
   | labelled field list: `…: a, b, …` | `clingen_get_gene_actionability`: *"Use this for ClinGen clinical actionability of a gene: adult/pediatric assertion status, release, disease, and SEPIO links."* |

   It fails these real leads, and the fix is one sentence:

   | tool | lead today | rewrite |
   |---|---|---|
   | `pubtator_search_literature` | *"Use this when a user needs PubMed literature search through PubTator3."* | *"Use this when a user needs PubMed literature; returns ranked articles (PMID, title, journal, year) from PubTator3 search."* |
   | `metadome_get_tolerance_landscape` | *"Signature: get_tolerance_landscape(transcript_id, …)."* | *"Return per-residue missense tolerance scores along one transcript's protein."* |
   | `stringdb_compute_functional_enrichment` | *"Perform functional enrichment analysis."* | *"Return enriched GO, KEGG and Reactome terms (with FDR) for a protein set."* |
   | `genereviews_get_genereview_summary` | *"Convenience orchestration tool."* | *"Return one GeneReviews chapter's summary sections and metadata for a gene or NBK id."* |

   A *"Use this when a caller needs allele counts per population"* lead fails on purpose: it
   names the caller's need, not the result, and a lead in that style scored 0.09 with the
   reranker until its rewrite to a returns-first lead scored 0.58 (genefoundry-bench, Jev 1.13).
   Write the returns statement; don't make the reader infer it.

   Order the rest after it: when to use it and when not, then caveats (what it does *not*
   return). The lead must stand on its own — no "same as `get_x`" — because search shows one
   tool at a time. Keep it tight: a pinned tool's description is paid on every turn.

   **Enforcement is advisory.** `scripts/check_tool_surface.py` reports `D1` as a warning;
   `--strict-lead` makes it fail, so each repo opts in once its leads are fixed. The router
   runs it in report mode over the pinned fleet baseline (`make lint-description-lead`, part
   of `ci-local`). A backend gates its own advertised tools by calling
   `surface.lead_states_return(tool["description"])` from its vendored `scripts/surface.py`,
   next to its existing S1–S3 test.

   Measured on the baseline at adoption (2026-11-19): **96 of 285** tools fail `D1` —
   pubtator 35/35, metadome 11/11, gnomad 20/22, spliceai 6/7, genereviews 5/13, clingen 3/17,
   gencc 3/12, hgnc 2/9, mgi 2/13, stringdb 2/10, and one each in clinpgx, clinvar, hpo, mondo,
   orphanet, uniprot, vep. autopvs1, gtex, litvar, mavedb and panelapp pass.

> **This standard is fleet policy, not MCP conformance.** The MCP specification says **nothing**
> about parameter descriptions, examples, or enums on tool inputs; `description` is an optional
> field the spec calls *"a hint to the model"*. Every rule above is GeneFoundry policy, grounded
> in Anthropic's published tool-design guidance and in the fleet's own measured defects. Do not
> cite MCP as the authority for it.

## CI gates

| rule | gate | how |
|---|---|---|
| `S1` description on every property | `scripts/check_tool_surface.py` | static, offline, against the pinned baseline |
| `S2` examples on every required property | `scripts/check_tool_surface.py` | static |
| `S3` examples on every array property | `scripts/check_tool_surface.py` | static |
| `D1` first sentence says what it returns (v1.1) | `scripts/check_tool_surface.py` | static; warning by default, failure with `--strict-lead` |
| `S4` enum on every closed vocabulary | `docs/conformance/behaviour.py` | **empirical** — sends an unrecognised value and requires rejection, not a silent zero |

Static and dynamic together close the loop. Neither closes it alone: a static gate cannot see an
enum that was never declared, and a dynamic gate cannot test an enum that does not exist. Rule 2
is what turns the second into the first — **declare the vocabulary, and it is automatically
gated from that day on, with no test to write.**

The two gates also share a fixture. The `examples` required by `S2`/`S3` are what the behaviour
gate uses to construct a valid call before it perturbs a filter. One artifact, two uses: the
thing that teaches the model how to call the tool is the same thing that proves the tool rejects a
bad call.

## References

- Anthropic — [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
  ("describe it to a new hire"; `user` → `user_id`; actionable errors over opaque codes)
- Anthropic — [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
  (`input_examples`: 72% → 90% on complex parameter handling)
- Anthropic — [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
  ("provide extremely detailed descriptions… at least 3-4 sentences per tool")
- Anthropic — [Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools):
  descriptions are "by far the most important factor in tool performance"; the model example
  opens "Retrieves the current stock price…" and is praised for saying "what data it returns"
- OpenAI — [Function calling](https://developers.openai.com/api/docs/guides/function-calling):
  "Explicitly describe the purpose of the function … and what the output represents"
- MCP — [Tools, 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools):
  `description` is the "Human-readable description of functionality" — the only field written
  for the model to choose the tool by; `title`/`annotations` are display and behaviour hints
- Hasan et al. — [MCP Tool Descriptions Are Smelly!](https://arxiv.org/abs/2602.14878): 56% of
  856 tools fail to state their purpose clearly; fixed descriptions raise task success
  (median +5.85 pp)
- [From Docs to Descriptions](https://arxiv.org/abs/2602.18914): "missing return descriptions"
  is a common smell and smells "significantly affect LLM tool selection"; fixing functionality
  wording moved selection +11.6%
- Shi et al. — [ToolRet](https://arxiv.org/abs/2503.01763): retrieval, not the model, is the
  bottleneck; poor tool retrieval lowers task pass rate
- Hsieh et al. — [Tool documentation enables zero-shot tool-usage](https://arxiv.org/abs/2308.00675);
  Yuan et al. — [EasyTool](https://arxiv.org/abs/2401.06201): concise, purpose-first tool text
  improves tool use and cuts tokens
- None of these sources prescribes *first-sentence* order: that part is GeneFoundry policy,
  grounded in the router reranker reading the lead and in the bench measurement above
- GeneFoundry — [Response-Envelope Standard v1](RESPONSE-ENVELOPE-STANDARD-v1.md) (the error frame a
  rejected value must use), [Tool-Naming Standard v1](TOOL-NAMING-STANDARD-v1.md) (canonical argument names)
- `gnomad-link` — the in-house exemplar: 113 properties, 98% documented, with `enum`, `examples`, `pattern`

## Rolling out `D1` (v1.1), one backend PR at a time

1. Rewrite the first sentence of each failing tool (list: `make lint-description-lead` in the
   router, or `--rule D1 --json`). Keep names, parameters and the rest of the text unchanged.
2. In the backend, assert `lead_states_return` over the advertised tools (a test beside the
   S1–S3 one), with the router's current `scripts/surface.py` vendored byte-identical.
3. Measure retrieval before and after, on demand: in the router, re-snapshot the catalog with
   the new descriptions and run `scripts/search_rerank_report.py` on both eval sets — the
   held-out set in `tests/discoverability/` and `tests/discoverability/agent_queries.yaml`.
   genefoundry-bench can run the Jev reranker sweep on the same change. A rewrite that lowers
   hit@1 on the held-out set is reverted, whatever the lint says.
4. Fix pinned entry points first (`servers.yaml` `entrypoints`): they are always listed, so
   their lead is the one every session reads. A pass is not enough there — a 53-character
   `metadome_resolve_transcript` or the 58–86-character clinpgx resolvers (`search_records`,
   `search_dataset`, `get_record`, `get_related_records`) need a lead that names the record
   returned and the identifiers it accepts.
5. Once a repo is clean, it runs the check strict; the router baseline re-pin then drops it
   from the report.

## Definition of Done (per repo)

- [ ] 100% of input properties carry a `description` (`make lint-surface` is green for this repo)
- [ ] Every required property carries `examples`
- [ ] Every array-typed property carries `examples` showing the array form
- [ ] Every closed vocabulary is declared as an `enum`, and the behaviour gate proves an
      out-of-enum value is rejected rather than silently matching nothing
- [ ] Bounded numerics declare `minimum`/`maximum`; formatted strings declare `pattern`
- [ ] (v1.1) Every tool description's first sentence says what it returns (`D1`), checked strict
- [ ] MINOR version bump + `CHANGELOG` note (schemas gain fields; no wire break)
