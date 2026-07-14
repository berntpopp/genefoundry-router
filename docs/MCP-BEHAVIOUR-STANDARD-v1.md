# GeneFoundry MCP Behaviour Standard v1

> **Status: PROPOSED, 2026-07-14.** Tracking issue: `genefoundry-router#76`.
> Enforced by `docs/conformance/behaviour.py` (`tests/conformance/test_behaviour_v1.py`), which runs
> against a **live** server. It is **not yet in `ci-local`** — it currently fails on most of the
> fleet, which is the point of it. It joins `ci-local` in the change that drives it to zero.
>
> **All rules are now implemented and proven.** B4 and B7 were the two holes an adversarial review
> found in the first cut of the probe; both are closed. B5's premise was factually wrong and is
> corrected in place. See "Gaps in the current gate".

A tool that errors is honest. A tool that returns a **confident, well-formed, wrong answer** is not,
and an agent cannot tell the difference. This standard governs the second case.

## Why this exists

An audit of the fleet on 2026-07-14 exercised every tool on all 21 backends over their public
endpoints and confirmed **106 defects** (each reproduced twice, by two agents that never spoke; the
tester who found it and a verifier instructed to *refute* it). 18 of 21 backends carry at least one.

They are not 106 bugs. **They are three bugs, repeated** — and every one of them was already
forbidden, either by `RESPONSE-ENVELOPE-STANDARD-v1.1` (*"silent omission is not compliant"*) or by
the MCP specification itself. **Nothing enforced them.** That is the whole lesson: this fleet does
not lack contracts, it lacks *gates*.

| # | the bug | what the model concludes |
|---|---|---|
| 1 | **The silently-empty filter.** An unrecognised filter value matches nothing; the server returns `success: true, total: 0`. | *"There are no pathogenic variants in this gene."* |
| 2 | **The lying `total` / `truncated`.** `total` is set to the page size and `truncated` to `false`. | *"I have seen every result."* |
| 3 | **The error an LLM cannot act on.** A validation failure that never names the offending parameter — or, worse, reports itself as `not_found`. | *"This tool does not exist."* |

Real instances, all confirmed:

- `clinvar-link/get_variants_by_gene.classification` — an **undeclared, case-sensitive** vocabulary.
  ClinVar's own published wording `"Likely pathogenic"` returns **0 rows, `success: true`**.
- `litvar-link/search_genetic_variants` — `total` tracks the page size: `limit=25 → total=25`,
  `limit=100 → total=100`, `truncated: false` throughout. The true BRCA1 count is **>13,000**.
- `litvar-link` — the **entire schema-validation layer** answers `not_found` /
  *"The requested tool is not available."* — byte-identical to the reply for a tool that genuinely
  does not exist. The model abandons a tool that works.
- `gnomad-link` — **43** error responses carry `isError: false`; the MCP error flag is never set.

## Normative rules

A backend **MUST** satisfy all of the following. Each is checked against the server's **own
advertised schema** — there is no per-repo probe list to maintain, and a server cannot pass by
under-advertising.

### B1 — A value outside a declared `enum` MUST be rejected

If a parameter declares an `enum`, a value outside it **MUST** produce a typed error
(`error_code: invalid_input`). It **MUST NOT** be silently matched to nothing.

### B2 — An unrecognised value for an *undeclared* closed vocabulary MUST NOT silently zero the result set

If a parameter's real vocabulary is closed, it **MUST** declare an `enum`
(`TOOL-SCHEMA-DOCUMENTATION-STANDARD` S4) and then **B1** applies.

Until it does, a value the server does not understand **MUST NOT** return zero rows with
`success: true`. Detection is by control: a call proven to return rows, repeated with one
unrecognised filter value. Zero rows and no error is a **fail** — it is indistinguishable from *"the
data genuinely has none"*, and that is precisely the harm.

> This rule is what lets the fleet enforce something a static schema check **cannot see**. The
> undeclared vocabulary is invisible in the schema by definition; only a live probe finds it.

### B3 — `response_mode` narrows a payload; it MUST NOT destroy it

A narrower mode **MUST** return a reduced *projection* — the mandatory envelope plus stable
identifiers. It **MUST NOT** return zero records where a wider mode returns N, and it **MUST**
always carry the count. A mode that turns N records into nothing is a silent-empty by another name.

### B4 — `total` MUST NOT be the page size *(not yet implemented — see Gaps)*

`total` **MUST** be the number of matching records, not the number returned. If the upstream cannot
supply a true total, the field **MUST be omitted** rather than fabricated. Whenever more records
exist beyond the page, `truncated`/`has_more` **MUST** be `true`.

**Honesty of `total` is not decidable from a single response.** Conformance **MUST** therefore be
established with **two calls**: request `limit=N`, then `limit=2N` (or follow the cursor). If the
second returns more rows than the first declared as `total`, `total` lied. A single-response check
provably cannot catch the fleet's actual defect — `total == returned` with `truncated: false` is
internally consistent and only a second call exposes it.

### B5 — A missing or wrong-typed *required* argument MUST be an actionable `invalid_input`

A missing or unusable required argument **MUST** produce `invalid_input` naming the parameter.
*(Implemented: `check_argument_error` runs against every tool, including tools with no arguments,
and needs no `examples` to do it.)*

> **Correction, 2026-07-14.** An earlier draft of this rule claimed *"a required parameter carrying
> an undeclared closed vocabulary is exactly the `clinvar` case, and it is currently unprobed."*
> **That is factually wrong and the rule it motivated is unnecessary.** `clinvar-link/
> get_variants_by_gene` declares `required: ["gene_symbol"]` — `classification` is **optional**, with
> `default: null`. It therefore falls squarely inside the B2 optional-filter probe.
>
> The reason it is unprobed today is **B7, not B5**: `clinvar-link` publishes no `examples` on any
> required parameter, so no valid call can be constructed and the *entire tool* is UNGATED. Fix the
> examples and the `classification` probe fires by itself.
>
> Required parameters that *declare* an `enum` are already probed — `check_declared_enums` iterates
> every property, required or not. The only residual gap is a required parameter with an *undeclared*
> closed vocabulary, and the remedy for that is S4 (declare it), after which **B1** applies. No new
> probe is warranted, and one would be actively harmful: a required parameter is usually the tool's
> primary lookup key, where a nonsense value returning nothing is the honest answer, so a sentinel
> probe there would manufacture false accusations across the fleet.

### B6 — An error MUST be actionable, and MUST be flagged as an error

- A validation failure **MUST** return `error_code: invalid_input` — **never `not_found`**.
  `not_found` asserts the tool does not exist; it is not merely unhelpful, it is **false**, and it
  sends the agent to `get_server_capabilities` instead of fixing its argument.
- The message **MUST** name the offending parameter, and give either `allowed_values` or a
  correctly-formatted example.
- `error_code` **MUST** come from the closed fleet enum (`invalid_input`, `not_found`,
  `ambiguous_query`, `internal`, …). `gnomad-link`'s `validation_failed` is not in it; a closed
  vocabulary that is not closed cannot be branched on.
- The result **MUST** carry **`isError: true`**. Per MCP (2025-11-25, Tools § Error Handling), tool
  execution errors are reported this way and clients *SHOULD* surface them to the model for
  self-correction. An in-band `{"success": false}` body with `isError: false` gives the client **no
  protocol-level signal that the call failed** — the model may reason over an error payload as if it
  were data.

> Deliberate exception, unchanged from `RESPONSE-ENVELOPE-STANDARD-v1.1`: **per-item batch failures
> stay in-band.** One bad item must never fail its siblings. Only top-level and argument-level
> failures raise.

### B7 — A tool that cannot be probed has NOT passed *(not yet implemented)*

If the gate cannot construct a valid call for a tool — because a required parameter carries no
`examples` (`TOOL-SCHEMA-DOCUMENTATION-STANDARD` S2) — that tool is **UNGATED**, and UNGATED
**MUST fail**, not skip.

This is not pedantry. It is the difference between a gate and a decoration:

| server | failing checks | tools UNGATED |
|---|---|---|
| `gtex-link` | 3 | **8 of 9** |
| `clinvar-link` | 12 | **5 of 5 data tools** |
| `litvar-link` | 12 | 5 |

`gtex-link` has three error-contract failures and **eight unprobed tools**. Fix those three and it
is certified **CONFORMANT** — while `search` (returns the wrong genes, drops the gene the query
names) and `get_median_expression_levels` (headline reports the **least**-expressed tissue as the
*"highest"*) — both confirmed HIGH defects — were **never tested**.

**B7 makes `TOOL-SCHEMA-DOCUMENTATION-STANDARD` a hard prerequisite for this one**, which is the
honest sequencing: a server that does not document its inputs cannot be behaviourally verified at
all, and must not be told it passed.

### B8 — An unreachable upstream MUST NOT buy a green tick

A probe that cannot reach the server **skips or errors — it never passes**. A gate that goes green
because it could not run is the failure mode this entire standard exists to prevent.
(`scripts/mcp_survey.py` currently exits `0` when every host fails to resolve. That is a bug.)

## Gaps in the current gate — closed, and how

The first cut of the probe implemented **B1, B2, B3, B6, B8** and silently missed **B4** and **B7**.
An adversarial review found both, and demonstrated the consequence: a purpose-built server exhibiting
all three target bugs on those unexercised paths **passed the gate** — `conformant=True, 6 passed,
0 failed`. That is the defect class this whole effort exists to kill, reproduced inside our own
enforcement. Both are now closed, and each was **proven by breaking it**, not by being written.

**B4 — `total` MUST be invariant under `limit`.** The original check failed only when
`len(rows) < total`. litvar's bug satisfies neither branch: `returned == total` with
`truncated: false` is a perfectly self-consistent page, and **no single response can expose it**.
Only a second call can. Verified live on the real server:

    limit=5   -> returned=5,   total=5,   truncated=false
    limit=25  -> returned=25,  total=25,  truncated=false
    limit=100 -> returned=100, total=100, truncated=false

`total` is echoing `limit`. The same server's sibling tool reports the true BRCA1 count: **13,264**.
`check_total_is_not_the_page_size` now calls each paginated tool twice and requires `total` to be
invariant. Driven directly at the lying tool it FAILS, as it must; run against `orphanet-link` and
`hpo-link`, whose totals are honest, it raises nothing. Caught the bug; did not cry wolf.

**B7 — UNGATED MUST fail, not skip.** `conformant = not failed` meant a server whose tools could not
be probed *at all* was certified CONFORMANT. `gtex-link`, with 8 of 9 tools unprobed for want of
`examples`, would have passed while both of its confirmed HIGH defects went untested. The report now
carries a separate `ungated` list and `conformant = not failed and not ungated`. litvar-link, which
publishes no `examples` anywhere, now reports **5 UNGATED** and fails — where before it would have
been certified once its error contract was fixed, with its fabricated `total` never once exercised.

This makes `TOOL-SCHEMA-DOCUMENTATION-STANDARD` a hard prerequisite for behaviour conformance, which
is the honest sequencing: **a server that does not document its inputs cannot be verified, and must
not be told it passed.**

**B5** rested on a false premise and needed no new probe; see the correction under B5 above.

With B4 and B7 closed, this standard is enforceable and the gate is the thing that enforces it. It
joins `ci-local` in the change that drives the fleet to zero.

## Definition of Done (per repo)

1. `make conformance-behaviour` (live, against the built container) exits `0`.
2. No tool is UNGATED — i.e. every required parameter carries `examples` (`S2`).
3. Every closed vocabulary is declared as an `enum` (`S4`); B2 then reduces to B1.
4. Regression tests cover each defect the audit confirmed for this repo, in the repo's own suite.

## References

- `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md` — *"silent omission is not compliant"*; the error
  envelope and the closed `error_code` enum; the batch carve-out.
- `docs/TOOL-SCHEMA-DOCUMENTATION-STANDARD-v1.md` — S2 (`examples` on required params) and S4
  (closed vocabularies declare `enum`). **B7 depends on it.**
- [MCP 2025-11-25 — Tools, Error Handling](https://modelcontextprotocol.io/specification/2025-11-25/server/tools):
  *"Tool Execution Errors contain actionable feedback that language models can use to self-correct
  and retry with adjusted parameters."*
- [Writing effective tools for agents — Anthropic](https://www.anthropic.com/engineering/writing-tools-for-agents):
  on signalling truncation and steering the agent rather than silently dropping rows.
- The audit: `docs/reports/2026-07-14-fleet-mcp-audit.md` in `berntpopp/strato_v6_docker_npm`.
