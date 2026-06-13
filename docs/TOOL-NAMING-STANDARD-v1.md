# GeneFoundry Tool-Naming & Normalization Standard v1

> Canonical reference for the GeneFoundry `-link` MCP fleet. Adopted 2026-06-13.
> A tracking issue titled "Adopt GeneFoundry Tool-Naming Standard v1" exists in
> each `-link` repo. Referenced by the `genefoundry-router` design spec
> (`docs/specs/2026-06-13-genefoundry-router-design.md`).

Part of the **GeneFoundry MCP router** initiative (`genefoundry-router`): all `*-link` MCP servers are being federated behind a single MCP endpoint. To avoid tool-name collisions, model tool-overload, and inconsistent ergonomics across the fleet, every server adopts one tool-naming / normalization standard. Each repo's tracking issue records bringing _that_ server into compliance.

## Rules

1. **Namespacing is the gateway's job — leaf tools stay UNPREFIXED.**
   Expose clean names (`get_variant_details`), not server-prefixed ones (`gnomad_get_variant_details`). The router applies the namespace at mount time (`mount(namespace="<TOKEN>")` → `<TOKEN>_get_variant_details` at the gateway). MCP clients already namespace standalone servers as `mcp__<server>__<tool>`, so a leaf-level prefix is redundant and causes **double-prefixing** at the gateway.

2. **`verb_noun` snake_case, canonical verbs only:** `get`, `search`, `list`, `resolve`, `find`, `compare`, `compute`. No synonyms (`fetch`→`get`, `lookup`→`get`/`resolve`, `query`→`search`).

3. **Length ≤ 50 chars.** Leaves headroom under the 64-char limit (MCP spec / SEP-986; most clients enforce `^[A-Za-z0-9_-]{1,64}$`) after the gateway prefix is added.

4. **Fleet-wide canonical argument names** (where applicable): `gene_symbol`, `hgnc_id`, `variant_id` (CHROM-POS-REF-ALT or rsID), `transcript_id`, `pmid`, `hpo_id`, `response_mode` (`minimal|compact|standard|full`), `limit`, `offset`. Rename local synonyms to these.

5. **Stable identity:** set `serverInfo.name` explicitly; document the canonical gateway **namespace token** for this server in the README.

6. **Descriptions:** concise, action-oriented, and name the underlying data source. Add **domain tags** (e.g. `variant`, `gene`, `expression`, `literature`, `phenotype`) so the gateway can filter/curate the surfaced toolset.

7. **Breaking renames — project decision: drop self-prefixes IMMEDIATELY** (no deprecation aliases), with a **MAJOR version bump** and a `CHANGELOG` migration note.

8. **CI guard:** add a test asserting every registered tool name matches `^[a-z0-9_]{1,50}$` and starts with a canonical verb.

## References
- MCP tools specification — tool-name rules; namespacing is the aggregator's responsibility
- SEP-986 — tool-name charset & 1–64 length
- SEP-993 (draft) — Namespaces direction (`namespace__tool`)
- FastMCP docs — server composition (`mount`/`namespace`), tool transformation, tool search

## Definition of Done (per repo)
- [ ] All tool names unprefixed, `verb_noun`, ≤ 50 chars, canonical verb
- [ ] Argument names aligned to the fleet canon
- [ ] `serverInfo.name` + namespace token documented in README
- [ ] Domain `tags` added to tools
- [ ] MAJOR version bump + `CHANGELOG` migration note (if any rename)
- [ ] CI tool-name lint test added

## Open: Standard v1.1 (pending decision)
The v1 verb canon is too strict for action/compute servers. Extend the canon with verbs like `predict` (spliceai), `analyze`/`generate`/`download` (stringdb), and `build`/`index`/`submit`/`stage` (pubtator) — or document them as explicit per-tool exceptions. Decide once, fleet-wide, before mass-renaming action tools.
