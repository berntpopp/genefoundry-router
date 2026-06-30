# GeneFoundry Tool-Naming & Normalization Standard v1.1

> Canonical reference for the GeneFoundry `-link` MCP fleet. Adopted 2026-06-13;
> **v1.1 ratified 2026-06-30** (two-tier verb canon + ops/meta tag carve-out).
> A tracking issue titled "Adopt GeneFoundry Tool-Naming Standard v1" exists in
> each `-link` repo. Referenced by the `genefoundry-router` design spec
> (`docs/specs/2026-06-13-genefoundry-router-design.md`).

Part of the **GeneFoundry MCP router** initiative (`genefoundry-router`): all `*-link` MCP servers are being federated behind a single MCP endpoint. To avoid tool-name collisions, model tool-overload, and inconsistent ergonomics across the fleet, every server adopts one tool-naming / normalization standard. Each repo's tracking issue records bringing _that_ server into compliance.

## Rules

1. **Namespacing is the gateway's job — leaf tools stay UNPREFIXED.**
   Expose clean names (`get_variant_details`), not server-prefixed ones (`gnomad_get_variant_details`). The router applies the namespace at mount time (`mount(namespace="<TOKEN>")` → `<TOKEN>_get_variant_details` at the gateway). MCP clients already namespace standalone servers as `mcp__<server>__<tool>`, so a leaf-level prefix is redundant and causes **double-prefixing** at the gateway.

2. **`verb_noun` snake_case, ratified two-tier verb canon (v1.1):**

   - **Tier 1 — universal read/query verbs (required on every backend):**
     `get`, `search`, `list`, `resolve`, `find`, `compare`, `compute`, `map`.
     Use `map` for cross-ontology or identifier-mapping tools that return relationships
     between namespaces (for example `map_cross_ontology`).
   - **Tier 2 — sanctioned domain action/compute verbs (fleet-wide; used only where a
     backend actually registers such a tool):**
     `predict`, `annotate`, `recode`, `liftover`, `analyze`, `score`,
     `submit`, `export`, `generate`, `download`.

   No synonyms (`fetch`→`get`, `lookup`→`get`/`resolve`, `query`→`search`).
   The router's `ACTION_VERB_EXCEPTIONS` constant in `genefoundry_router/cli.py` is the
   machine-readable mirror of the Tier-2 set; keep them in sync.

3. **Length ≤ 50 chars.** Leaves headroom under the 64-char limit (MCP spec / SEP-986; most clients enforce `^[A-Za-z0-9_-]{1,64}$`) after the gateway prefix is added.

4. **Fleet-wide canonical argument names** (where applicable): `gene_symbol`, `hgnc_id`, `variant_id` (CHROM-POS-REF-ALT or rsID), `transcript_id`, `pmid`, `hpo_id`, `response_mode` (`minimal|compact|standard|full`), `limit`, `offset`. Rename local synonyms to these.

5. **Stable identity:** set `serverInfo.name` explicitly; document the canonical gateway **namespace token** for this server in the README.

6. **Descriptions:** concise, action-oriented, and name the underlying data source. Add **domain tags** (e.g. `variant`, `gene`, `expression`, `literature`, `phenotype`) so the gateway can filter/curate the surfaced toolset.

7. **Breaking renames — project decision: drop self-prefixes IMMEDIATELY** (no deprecation aliases), with a **MAJOR version bump** and a `CHANGELOG` migration note.

8. **CI guard (v1.1):** add a test asserting every registered tool name matches
   `^[a-z0-9_]{1,50}$` and starts with a Tier-1 or Tier-2 verb from the ratified canon
   (see Rule 2). Tools tagged `ops` or `meta` are exempt from the verb check (tag carve-out)
   but must still pass the charset/length constraint. The router's `check_leaf_name`
   function in `genefoundry_router/cli.py` is the reference implementation.

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

## Standard v1.1 — ratified 2026-06-30

**Ratification:** Q1→1a (extend closed set), Q2→2a (minimal domain set), Q3→3a (tag carve-out),
Q4→4a (doc + router constants are canon; per-repo tests mirror them). Decision gates
`docs/plans/V1.0-GATE.md:16` are now closed.

### Ratified Tier-2 verb canon

The Tier-2 set (`predict, annotate, recode, liftover, analyze, score, submit, export, generate,
download`) is the router's `ACTION_VERB_EXCEPTIONS` constant. Each admitted verb maps to a real
domain action on at least one backend; pubtator's orchestration verbs (`build`, `index`, `stage`,
`ground`, `preflight`, `record`, `estimate`, `inspect`, `convert`, `suggest`) are **not** in the
Tier-2 canon — they are handled per-repo (rename toward canon where honest, or a *documented
by-tool* allowlist). This keeps the whitelist meaningful instead of admitting ~25 verbs.

### ops/meta tag carve-out

Tools tagged `ops` or `meta` skip the verb rule but still must pass the charset/length/no-self-prefix
constraints. This covers `check_upstream_health`, `warmup`, `diagnostics`, `*_help`,
`*_quickstart`, and the gtex deep-research `fetch`/`search` pair. The carve-out is
already battle-tested in `spliceailookup-link` and is now the fleet standard.

### Source of truth

The ratified canon lives in this document. The router `genefoundry_router/cli.py` constants
(`CANONICAL_VERBS`, `ACTION_VERB_EXCEPTIONS`, `_OPS_META_TAGS`) are the machine-readable mirror;
each per-repo test copies the two tiers verbatim. Drift is caught by the router validator
(`make validate` / `make doctor --strict-naming`) running over the full 21-backend catalog in CI.

### Per-repo follow-on (not blocking v1.1 ratification)

- **vep-link:** delete `_ACTION_VERB_EXCEPTIONS` (`:43`) and the used-exception guard; `recode`/
  `liftover`/`annotate` are now Tier-2; `check_upstream_health` moves to the `ops`/`meta` carve-out.
- **spliceailookup-link:** drop the local `predict` extension (now Tier-2); keep `ops` carve-out
  (now the fleet standard).
- **orphanet-link:** reconcile its 15-verb local set down to Tier-1 + the Tier-2 verbs it uses.
- **mondo-link:** add `compute` → full Tier-1 (8 verbs).
- **base-7 repos** (clingen, genereviews, gnomad, hgnc, litvar, mavedb, mgi, stringdb, uniprot):
  add `map` → full Tier-1 (8 verbs).
- **No-test repos** (autopvs1, clinvar, gencc, hpo, metadome, panelapp): add `test_tool_names.py`.
