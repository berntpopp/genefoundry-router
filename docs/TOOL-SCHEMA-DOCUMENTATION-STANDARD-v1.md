# GeneFoundry Tool-Schema Documentation Standard v1

> **Status: PROPOSED, 2026-07-14.** Tracking issue: `genefoundry-router#75`. A tracking issue
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
- GeneFoundry — [Response-Envelope Standard v1](RESPONSE-ENVELOPE-STANDARD-v1.md) (the error frame a
  rejected value must use), [Tool-Naming Standard v1](TOOL-NAMING-STANDARD-v1.md) (canonical argument names)
- `gnomad-link` — the in-house exemplar: 113 properties, 98% documented, with `enum`, `examples`, `pattern`

## Definition of Done (per repo)

- [ ] 100% of input properties carry a `description` (`make lint-surface` is green for this repo)
- [ ] Every required property carries `examples`
- [ ] Every array-typed property carries `examples` showing the array form
- [ ] Every closed vocabulary is declared as an `enum`, and the behaviour gate proves an
      out-of-enum value is rejected rather than silently matching nothing
- [ ] Bounded numerics declare `minimum`/`maximum`; formatted strings declare `pattern`
- [ ] MINOR version bump + `CHANGELOG` note (schemas gain fields; no wire break)
