# GeneFoundry Tool-Surface Budget Standard v1

> **Status: PROPOSED, 2026-07-14.** Tracking issues: `genefoundry-router#73` (this standard) and
> `genefoundry-router#74` (the `outputSchema` measurement). A tracking issue titled "Adopt
> GeneFoundry Tool-Surface Budget Standard v1" exists in each `-link` repo.
> Checked offline by `scripts/check_tool_surface.py` (`make lint-surface`). **It is NOT yet in
> `ci-local`** — it currently reports 595 real violations across 20 of the 21 backends, which is the
> point of it: it is the failing test the fleet sweep exists to turn green. It joins `ci-local` in
> the same change that drives it to zero. A standard must not claim an enforcement it does not have.

A tool definition is not a one-off cost paid at connect time. It sits in the model's
system-prompt prefix and is re-sent on **every request** for the life of the session. Whatever a
server advertises, every client pays for repeatedly, before any work happens — and pays again
whether or not the tool is ever called.

Measured on 2026-07-14 against the pinned fleet baseline, the 21 backends advertise **272 tools
costing 259,072 tokens**. That is **130% of a 200,000-token context window**: the fleet cannot be
mounted directly by any client, at all. `pubtator-link` alone is **73,274 tokens — 37% of a 200k
context for one backend**. **54% of the whole fleet's surface is `outputSchema`**, a field the MCP
spec makes optional and that clients are only ever *recommended* to use.

This standard puts a ceiling on that tax.

## Why these numbers

The thresholds are not taste. They are the published trigger points, and the fleet is far past
them:

- Anthropic: *"Claude's ability to pick the right tool degrades once you exceed **30–50 available
  tools**."* Use tool search when *"you have **10 or more tools**"* or *"your tool definitions
  consume **more than 10k tokens**"*.
- Anthropic measured Opus 4 improving from **49% to 74%** on internal MCP tool-selection
  evaluations once definitions were loaded on demand instead of up front.
- MCP's client guidance is stricter still: switch to progressive discovery once definitions
  reach *"**1%-5%** of the context window"* — i.e. 2,000–10,000 tokens.

**10,000 tokens is therefore the outer edge of every published recommendation**, and it is the
number this standard adopts as a hard per-server ceiling.

> **This standard is fleet policy, not MCP conformance.** The MCP specification — every revision,
> including `draft` — says **nothing** about tool count, token cost, or progressive disclosure.
> The only size mechanism it defines is `tools/list` pagination, which does not reduce the
> model's context at all (a client simply pages until the cursor is exhausted and hands the model
> everything). Do not cite MCP as the authority for any rule below.

## Rules

1. **A tool definition MUST NOT exceed 1,200 tokens.** (`B1`)
   Measured as the serialized `tools/list` entry: name, description, `inputSchema`,
   `outputSchema`, annotations. The fleet's median tool is well under this; 37 tools exceed it
   today, of which the worst, `pubtator-link/get_review_context_batch`, is a **48,439-character
   single tool definition**.

2. **A server's total tool surface MUST NOT exceed 10,000 tokens.** (`B2`)
   Seven backends exceed it today. Every one of them falls under the ceiling by applying Rule 3
   alone — no tools need to be removed and no descriptions shortened.

3. **`outputSchema` MAY be published only within budget.** It is the first thing to cut when a
   tool is over. It is not free, it is not required, and for the fleet's biggest tools it is the
   entire problem: `autopvs1-link` is **88%** `outputSchema`, `pubtator-link` **87%**.

   Suppress it per-tool in FastMCP with `output_schema=None`:

   ```python
   @mcp.tool(output_schema=None)          # None SUPPRESSES. NotSet (the default) auto-infers.
   async def get_variant_pvs1_data(...) -> dict[str, Any]:
       return envelope(...)
   ```

   **You do not lose `structuredContent` by doing this** — verified against the installed
   fastmcp 3.4.4 (`fastmcp/tools/base.py:357-361`): with `output_schema=None`, FastMCP still
   emits `structuredContent` whenever the return value serialises to a JSON **object**. Every
   fleet tool returns a dict envelope (Response-Envelope Standard v1), so every fleet tool is
   safe.

   > **The one hard constraint.** A tool whose top-level return serialises to a JSON **array or
   > scalar** (`-> list[...]`, `-> int`) *does* silently lose `structuredContent` under
   > `output_schema=None`. Such a tool is already non-conformant with the Response-Envelope
   > Standard, which mandates a dict envelope. Fix the envelope first; do not reach for
   > `output_schema=None` on a tool that returns a bare list.

   > **Interaction with untrusted-text fencing (resolved 2026-07-15).** Response-Envelope v1.1
   > originally required the `untrusted_text` literal to be *declared in the tool's output schema*,
   > which a tool publishing no `outputSchema` cannot do. The two standards contradicted each
   > other. [v1.1a](RESPONSE-ENVELOPE-STANDARD-v1.1.md) resolves it: the fenced object MUST appear
   > **on the wire**, and the schema MUST declare it only *if* a schema is published. That is the
   > stronger requirement, not a relaxation — a server can publish a perfect schema and still emit
   > unfenced prose. Suppressing `outputSchema` does not weaken fencing.

4. **Servers SHOULD construct `FastMCP(dereference_schemas=False)`.**
   The constructor defaults it to `True` (`fastmcp/server/server.py:337`) and appends
   `DereferenceRefsMiddleware` (`:450-456`), which inlines every `$defs`/`$ref` at every use
   site in both `parameters` and `output_schema`. It is an **amplifier of roughly 1.35×, not the
   root cause** — with `$defs` fully preserved, pubtator's output schemas are still ~251k
   characters. Turning it off is free and safe (**0/35** pubtator and **0/7** autopvs1 *input*
   schemas contain a `$ref`, so no input-schema client can be affected), but it is nowhere near
   sufficient on its own. Do Rule 3 as well.

5. **The fleet MUST NOT be mounted directly, and the router MUST keep its discovery gateway.**
   At 259,072 tokens the fleet does not fit in a context window with room to work. The router's
   curated entrypoints plus `search_tools`/`call_tool` are not an optimisation — they are the
   only supported way to reach the fleet. The router's own surface MUST NOT exceed **20,000
   tokens** (it federates 21 servers behind a stable two-tool gateway; MCP's own client guidance
   names exactly this shape — *"route every call through a single stable `call_tool({name, args})`
   meta-tool so the array never changes"* — as the way to keep a growing catalog from destroying
   prompt caching).

6. **A tool over budget MUST NOT be fixed by deleting its description.** Descriptions and
   parameter documentation are what the model actually reads; `outputSchema` is what it does not.
   Cut in that order. Rule 1 of the Tool-Schema Documentation Standard takes precedence over this
   one — if a tool cannot fit in 1,200 tokens with its parameters documented, the tool is doing
   too much and should be split.

## What publishing `outputSchema` actually buys, and what it costs

| | |
|---|---|
| **The spec** | `outputSchema` is **optional**. If declared, *"Servers **MUST** provide structured results that conform to this schema"* and *"Clients **SHOULD** validate"*. Returning `structuredContent` **without** declaring `outputSchema` is explicitly permitted — the spec imposes no precondition. |
| **The benefit** | An optional, client-side validation contract that clients are not required to honour. |
| **The cost** | 54% of the fleet's entire tool surface, on every request, forever. |
| **The alternative** | The router's `search_tools` already serves complete schemas on demand (`detail: full`). The information is not lost; it becomes lazy instead of eager. |

## CI gate

`scripts/check_tool_surface.py` enforces `B1` and `B2` offline against
`genefoundry_router/data/fleet-baseline.json` — the digest-attested snapshot of every backend's
real tool definitions. No network, fully deterministic, wired into `make ci-local` via
`make lint-surface`. Every backend in the baseline is checked; the server list is **derived, never
hardcoded**, so a new backend is gated the day it is snapshotted.

For a live reading of production, `scripts/mcp_survey.py` reports the same metrics over public
HTTPS. Both import their measurement from one module (`scripts/surface.py`) so the gate and the
survey cannot drift apart.

## References

- Anthropic — [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) (Tool
  Search Tool; 49%→74% on Opus 4; ~55k tokens for a five-server setup)
- Anthropic — [Tool search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
  (degradation past 30–50 tools; the 10-tool / 10k-token trigger)
- Anthropic — [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- MCP — [Client best practices](https://modelcontextprotocol.io/docs/develop/clients/client-best-practices)
  (the 1–5%-of-context threshold; the stable `call_tool` meta-tool and prompt caching)
- MCP — [Tools: Output Schema](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
  (`outputSchema` optional; server MUST conform if declared; client SHOULD validate)
- FastMCP 3.4.4 — `tools/base.py:357-361`, `tools/function_tool.py:351-354`,
  `server/server.py:337,450-456` (verified against the installed package, not the docs)

## Definition of Done (per repo)

- [ ] No tool definition exceeds 1,200 tokens (`make lint-surface` in the router is green for it)
- [ ] Total tool surface is under 10,000 tokens
- [ ] `output_schema=None` on every tool that needed it, and every such tool returns a dict envelope
- [ ] `FastMCP(dereference_schemas=False)`
- [ ] A regression test asserts the server's own surface stays under budget
- [ ] MINOR version bump (the wire contract loses an optional field) + `CHANGELOG` note
