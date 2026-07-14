# Fleet MCP contract hardening — design

**Date:** 2026-07-14
**Status:** ADOPTED. Standards written, gate built and validated against production, fleet sweep in progress.
**Issues:** `genefoundry-router` #73 #74 #75 #76 #77 #68, plus 15 per-repo defect issues.
**Input:** the adversarial fleet audit of 2026-07-14 (82 confirmed defects, fleet average 6.3/10).

## The problem, restated

The audit reported 82 confirmed defects and proposed four new standards. Measuring the fleet myself
produced a sharper framing:

**Most of these defects are violations of standards the fleet had already ratified and never
enforced.** Response-Envelope Standard v1 already requires `isError: true` on every error, already
closes the `error_code` enum to six values, already mandates `_meta.pagination.{total_count,
has_more}`, and v1.1 already says *"silent omission is not compliant."* The fleet wrote all of that
down and shipped 21 servers that break it.

So the fleet did not mainly need new standards. It needed **enforcement**, plus the two things
genuinely nobody had written down: a token budget, and a schema-documentation contract.

## What the research changed

Three premises we started from turned out to be wrong. They are recorded here because the corrected
versions are what the standards are built on.

| premise | verdict | what is actually true |
|---|---|---|
| "Tool definitions above ~10% of the context window measurably degrade tool selection." | **No such guidance exists.** Not in Anthropic's blog, docs, or the MCP spec. | Anthropic: selection degrades past **30–50 tools**; use tool search past **10 tools or 10k tokens of definitions**. MCP client guidance: switch to progressive discovery at **1–5% of context**. The real bar is **2–10× stricter** than we assumed. Publicly corrected on #73. |
| "The MCP spec makes `isError: true` a MUST." | **It is a SHOULD**, and only in `schema.ts` TSDoc, not the prose. | Our Response-Envelope v1 says REQUIRED. A fleet may be stricter than the spec — but the gate must cite *our* standard, not MCP. |
| "Invalid arguments are a JSON-RPC protocol error." | **Inverted by SEP-1303 in spec 2025-11-25.** | Input-validation errors are now **Tool Execution Errors with `isError:true`**; only a malformed `CallToolRequest` envelope is a protocol error. Our in-band `invalid_input` envelope is therefore the *right shape* — it simply was not setting `isError`. |

The 49% → 74% Opus 4 figure is real (Tool Search Tool, Anthropic's internal "MCP evaluations"). It is
not a named public benchmark; the "MCP-Atlas" attribution circulating in third-party write-ups does
not appear in Anthropic's own text and is not cited.

## Decisions

**1. Budget `outputSchema`; do not ban it.** A per-tool cap (1,200t) and a per-server cap (10,000t),
with `outputSchema` permitted only inside the budget. A ban would punish `vep-link` (3%
`outputSchema`) and `spliceailookup-link` (7%) — servers that never caused the problem — for zero
further benefit. Budgeting kills the monsters (`pubtator` 87%, `autopvs1` 88%) and leaves the honest
servers untouched. Every backend lands under the ceiling by budgeting `outputSchema` alone.

Verified against the installed fastmcp 3.4.4, because these symbols are post-cutoff and the docs are
not the truth: `@mcp.tool(output_schema=None)` suppresses the schema (`None` suppresses; the
auto-infer sentinel is `NotSet`), and **`structuredContent` survives** for any return that serialises
to a JSON object (`tools/base.py:357-361`). A `list`/scalar return *does* lose it — that constraint is
written into the standard rather than discovered later.

**2. Derive the conformance probes from the schema; do not maintain a probe list.** A per-repo
`conformance-behaviour.yml` would rot, and a repo could quietly under-declare and still report PASS.
That is the same "hardcoded input list" failure the Contract-Truth sweep already hit once. Instead the
gate reads each server's own advertised schema: every declared enum gets probed, so a tool is gated
the day it ships.

**3. The two standards compose into a closed loop.** This is the load-bearing idea.

- A static gate **cannot see an undeclared enum** — it looks exactly like a free string. So it cannot
  catch clinvar's `classification`.
- A dynamic gate **cannot test an enum that does not exist**.
- The Schema-Documentation Standard forces the enum to be *declared*; the behaviour gate then
  *automatically* proves an out-of-enum value is rejected rather than silently matching nothing.

Declare the vocabulary once, and it is gated from that day on with no test to write.

The two gates also **share a fixture**: the `examples` that S2/S3 require are what the behaviour gate
uses to construct a valid call before it perturbs a filter. The artifact that teaches a model how to
call the tool is the same artifact that proves the tool rejects a bad call.

A tool that cannot be probed is reported **UNGATED**, never as passing. Under-documentation shows up
as lost coverage, never as a green tick.

## Architecture

| gate | where | when | enforces |
|---|---|---|---|
| `scripts/check_tool_surface.py` | router, offline against the pinned baseline | `make lint-surface` → `ci-local` | B1, B2 (budget); S1, S2, S3 (schema docs) |
| `docs/conformance/behaviour.py` | vendored byte-identical into all 22 repos, against the local container | each repo's `conformance.yml` | S4 (undeclared enums), silent-empty filters, lying `total`/`truncated`, actionable errors, `isError`, closed `error_code` |
| `scripts/mcp_survey.py` | router, live over public HTTPS | `make survey`, release verification | observability — enforces nothing |

The static gate reads `genefoundry_router/data/fleet-baseline.json`, the digest-attested snapshot of
every backend's real tool definitions. Deterministic, no network, and **derived — never hardcoded**:
every backend in the baseline is checked, so a new backend is gated the day it is snapshotted.

## Validated before being trusted

The gate was run against production before any repo was touched, on the principle that a gate nobody
has tried to break is not a gate:

- **Catches what it should.** orphanet: `response_mode=minimal` destroys the payload on **five** tools
  (22 records → 0), not the one the audit found. litvar: a bad argument is answered *"The requested
  tool is not available"* on **all six** of its tools. gnomad: ships `validation_failed`, outside the
  ratified enum.
- **Does not cry wolf.** Against `hpo-link` — the fleet's cleanest backend — it returns 146 passes and
  flags only the one systemic `isError` violation. No spurious silent-empty or pagination findings.

## Out of scope, and honestly so

From #77's coverage gaps, this work closes the `isError`-vs-protocol-error matrix and the undeclared-
enum class. It does **not** close: `untrusted_text` fencing compliance (the inventory exists;
verifying each declared row actually fences is a separate probe), `resources`/`prompts`/pagination
surfaces, concurrency and cache coherency, and real-client compatibility. Those remain known unknowns,
not clean bills of health.
