# Tool-Naming Standard v1.1 — Verb-Canon Ratification — Decision Brief

> Workstream **M** (P3, decision-brief) of the 2026-06-30 fleet remediation.
> See `docs/specs/2026-06-30-fleet-remediation-design.md` rows for M.
> **This is analysis, not a code plan.** No source file is changed by this brief.
> Scope = the federated 21-backend `-link` fleet in `servers.yaml` + the router.

---

## Context & problem

The GeneFoundry Tool-Naming Standard v1 (`docs/TOOL-NAMING-STANDARD-v1.md`)
mandates a CI guard (Rule 8) that every leaf tool name is unprefixed snake_case,
≤ 50 chars, and **starts with a canonical verb**. Rule 2 enumerates the canon as
`get, search, list, resolve, find, compare, compute, map`
(`TOOL-NAMING-STANDARD-v1.md:15`). The doc itself flags the gap:

- `TOOL-NAMING-STANDARD-v1.md:43-44` — **"Open: Standard v1.1 (pending decision)"**:
  the v1 canon is too strict for action/compute servers; extend with verbs like
  `predict`/`analyze`/`generate`/`download`/`build`/`index`/`submit`/`stage` **or**
  document them as per-tool exceptions — *"Decide once, fleet-wide, before mass-renaming."*

That decision was never ratified. As a result, **the verb canon has silently
forked into at least seven different definitions in live code, plus the router's
own, plus six backends that enforce nothing** — and the only written rationale for
the largest exception set lives in a *test docstring*, not in the Standard or any
tracking issue.

### Evidence — the actual fragmentation (verified against current code, 2026-06-30)

| Where | Verb set in code | Notes / evidence |
|---|---|---|
| **Standard doc (Rule 2)** | `get search list resolve find compare compute map` (8) | `TOOL-NAMING-STANDARD-v1.md:15` |
| **Router `make validate`** | core 8 **+ exceptions** `predict analyze annotate submit export generate download` | `genefoundry_router/cli.py:44` (core) + `:46-54` (exceptions) + `:57-65` (`check_leaf_name`) |
| **vep-link** | base-7 **+** `_ACTION_VERB_EXCEPTIONS{annotate, recode, liftover, check}` | `vep-link/tests/unit/test_tool_names.py:39,43-44`; rationale only in docstring `:13-27`; "used-exception" guard `:83-93` |
| **pubtator-link** | base-7 **+** `_ACTION_VERB_EXEMPT` (16 full tool **names**, not verbs) | `pubtator-link/tests/unit/test_tool_names.py:28,34-53`; covers `build/index/submit/add/record/stage/preflight/suggest/ground/export/estimate/inspect/convert` + `diagnostics/workflow_help/review_quickstart` |
| **spliceailookup-link** | base-7 **+ `predict`** + an **`ops`-tag carve-out** (skip verb rule for `warmup` etc.) | `spliceailookup-link/tests/unit/test_tool_names.py:25-27, 47-49`; "issue #2 resolution A" |
| **gtex-link** | base-7 **+** `_DEEP_RESEARCH_ALLOWLIST{search, fetch}` | `gtex-link/tests/unit/test_tool_names.py:19,23`; `fetch` = OpenAI deep-research/Apps SDK contract, not a domain verb (`:8-9`) |
| **orphanet-link** | **15 verbs**: base-7 + `predict analyze annotate submit export generate download map` | `orphanet-link/tests/unit/test_tool_names.py:18-36` — the loosest set in the fleet |
| **mondo-link** | `get search list resolve find map compare` (has `map`, **drops `compute`**) | `mondo-link/tests/unit/test_tool_names.py:24` |
| **base-7 (no `map`)** | `get search list resolve find compare compute` | clingen, genereviews, gnomad, hgnc, litvar, mavedb (tuple, `:14`), mgi, stringdb, uniprot |
| **NO verb test at all** | — | autopvs1, clinvar, gencc, hpo, metadome, panelapp (6 in-scope repos; Rule 8 DoD gap) |

Two concrete defects fall straight out of this table:

1. **Router validator disagrees with vep.** vep registers `recode_variant`,
   `liftover_variant`, `check_upstream_health` (confirmed live tool roster:
   `annotate_variant`, `annotate_variants_batch`, `recode_variant`,
   `liftover_variant`, `check_upstream_health`, `resolve_variant`,
   `get_capabilities`). The router's `ACTION_VERB_EXCEPTIONS` (`cli.py:46-54`)
   admits `annotate` but **not** `recode`, `liftover`, or `check`. So
   `make validate` on the federated catalog **flags three real vep tools** as
   non-canonical — vep's own CI passes, the gateway's does not. Same divergence
   would hit any backend whose local set is broader than the router's.

2. **"`map`" is canonical in the doc but absent from 9 of the 15 repo tests.**
   Rule 2 lists `map`; the base-7 frozensets omit it, while mondo (which actually
   ships `map_cross_ontology`) had to drop `compute` to fit `map` in. The "core"
   set itself is not agreed.

The router's v1.0 gate is blocked on exactly this: `docs/plans/V1.0-GATE.md:16`
— *"Spec §19 Q2 (Standard v1.1 verbs) decided fleet-wide."* — and the design spec
records it as an open question (`docs/specs/2026-06-13-genefoundry-router-design.md:182`).

**vep forces the decision** because `annotate`/`recode`/`liftover` are
domain-legitimate (Ensembl VEP + Variant Recoder); they cannot be honestly
renamed to `compute_*` without degrading the agent's tool model.

---

## Open question(s)

- **Q1 — Policy shape.** Does v1.1 (a) *extend* the closed canonical verb set with
  sanctioned action verbs, (b) keep the strict v1 set and *rename* every
  non-conforming action tool, or (c) *abandon* the closed whitelist for an open
  "verb-first, lint-by-pattern" policy?
- **Q2 — Membership.** If we extend, *exactly which verbs* are admitted
  fleet-wide, and how do we treat pubtator's orchestration sprawl
  (`build/index/stage/ground/preflight/record/estimate/inspect/convert/suggest`)
  without diluting the guard to meaninglessness?
- **Q3 — Operational / meta tools.** How are non-domain tools
  (`check_upstream_health`, `warmup`, `diagnostics`, `workflow_help`,
  `review_quickstart`, deep-research `fetch`) classified — by verb-whitelisting,
  or by a tag-based carve-out?
- **Q4 — Single source of truth.** Where does the ratified canon live so the
  router `cli.py` and ~21 per-repo tests cannot drift apart again, and what
  tracking artifact replaces the test-docstring-only decision?

---

## Options

### Q1 — Policy shape

| Option | What it means | Trade-offs |
|---|---|---|
| **1a. Extend the closed set (RECOMMENDED)** | Ratify a fixed, enumerated v1.1 canon: a universal read tier + a small sanctioned action tier. | Keeps the CI guard's real value (catches `fetch`/`lookup`/`query` synonym drift, accidental new verbs) while ending fragmentation. Requires one fleet-wide edit pass. |
| **1b. Strict + rename** | Keep v1's read-only canon; rename `annotate_variant`→`compute_annotation`, `predict_splicing`→`compute_splice_scores`, etc. | A MAJOR breaking rename of action tools across vep/spliceai/pubtator/orphanet; *worsens* tool clarity (generic `compute_*` hides intent — against Anthropic guidance to prefer specific verbs); high churn for negative ergonomic value. |
| **1c. Open verb-first policy** | Drop the whitelist; lint only `^[a-z]+_[a-z0-9_]+$` (verb-first, snake_case, ≤50, no self-prefix). | Zero false positives, near-zero maintenance, but loses the synonym guard that motivated Rule 8 — `fetch_*`/`lookup_*`/`query_*` drift returns with no CI signal. |

### Q2 — Membership of the action tier (under 1a)

| Option | Action tier | Trade-offs |
|---|---|---|
| **2a. Minimal domain set (RECOMMENDED)** | `predict, annotate, recode, liftover, analyze, score` + keep the router's already-shipped `submit, export, generate, download`. **Excludes** pubtator's orchestration verbs. | Each admitted verb maps to a real domain action on ≥1 backend; pubtator's sprawl handled separately (rename-toward-canon or named per-tool allowlist) so the whitelist stays meaningful. |
| **2b. Union of everything in the wild** | Add all of pubtator's `build/index/stage/ground/preflight/record/estimate/inspect/convert/suggest` too. | Trivially makes every repo pass, but the verb rule then admits ~25 verbs — the guard no longer guards. Rejected. |
| **2c. Flat single fleet-wide set** | One identical frozenset everywhere (read + action), no per-server subsetting. | Simplest to maintain and zero router/repo drift, but a pure read backend (e.g. stringdb) would "pass" an `annotate_*` tool it should never have. Acceptable fallback if per-server subsetting proves fiddly. |

### Q3 — Operational / meta tools

| Option | Mechanism | Trade-offs |
|---|---|---|
| **3a. Tag carve-out (RECOMMENDED)** | Tools tagged `ops`/`meta` skip the verb rule but still must pass charset/length/no-self-prefix. | Already proven in spliceai (`ops` carve-out, `test_tool_names.py:47-49`); cleanly covers `check`/health, `warmup`, `diagnostics`, `*_help`, `*_quickstart`, deep-research `fetch`. Keeps these *out* of the domain verb canon. |
| **3b. Whitelist `check`/`fetch`/etc. as verbs** | Add them to the canon. | Pollutes the domain canon with infra verbs; `fetch` directly conflicts with the v1 synonym rule (`fetch`→`get`). Rejected. |

### Q4 — Source of truth

| Option | Mechanism | Trade-offs |
|---|---|---|
| **4a. Doc + router constants are canon; per-repo tests mirror them; one tracking issue (RECOMMENDED)** | Ratify in `TOOL-NAMING-STANDARD-v1.md`; the router `cli.py` constants are the machine-readable mirror; each repo test copies the two tiers verbatim; open a tracking issue. | Smallest change; matches how the fleet already works (each repo carries its own test). Drift risk mitigated by the router validator running over the whole catalog in CI. |
| **4b. Vendored shared `tool_naming` constants** | A tiny shared snippet/probe vendored per repo (like the conformance probe, MEMORY: sha 431c51cc). | Strongest anti-drift, but adds a vendoring mechanism for ~10 lines of constants — overkill now; revisit if drift recurs. |

---

## Recommendation

**Ratify Standard v1.1 as a closed, two-tier verb canon plus a tag-based
operational carve-out** — i.e. **Q1→1a, Q2→2a, Q3→3a, Q4→4a**.

**Tier 1 — universal read/query canon (every backend, required):**
`get, search, list, resolve, find, compare, compute, map`
— exactly the router's current `CANONICAL_VERBS` (`cli.py:44`). Adopt this as the
single core; fixes mondo (add `compute`) and the 9 base-7 repos (add `map`).

**Tier 2 — sanctioned domain action/compute verbs (admitted fleet-wide, used
only where a backend actually registers such a tool):**
`predict, annotate, recode, liftover, analyze, score, submit, export, generate, download`.
This is the router's existing exception set reconciled with vep's real verbs
(adds `recode`, `liftover`; `analyze`/`score` reserved for compute backends).

**Operational/meta carve-out (by tag, not verb):** tools tagged `ops` or `meta`
skip the verb rule (still charset/length/no-self-prefix). Covers
`check_upstream_health`, `warmup`, `diagnostics`, `*_help`, `*_quickstart`, and
gtex's deep-research `fetch`/`search` pair.

**Pubtator orchestration verbs are explicitly NOT folded into the canon.** They
are handled per-repo in the follow-up as either (i) a rename toward canon where
honest (`convert_article_ids`→`map_article_ids`; `inspect_review_index`→`get_…`;
`record_…`/`submit_…` consolidation) or (ii) a *documented, by-tool* allowlist
(the existing `_ACTION_VERB_EXEMPT`, kept but justified per tool). This keeps the
verb whitelist meaningful instead of admitting ~25 verbs.

### Rationale (best practice)

- A **closed, enumerated, consistent** verb vocabulary is what lets an agent build
  an accurate "mental map" of a large federated toolset and *predict* tool names
  (the `search_*`/`get_*` pattern) — Anthropic, *Writing effective tools for AI
  agents* and the MCP design guidance both stress consistency and predictability
  over flexibility. That argues against 1c (open policy).
- Anthropic explicitly advises **specific verbs + objects over generic verbs**
  (`send_email` not `notify`). Renaming `annotate_variant`→`compute_annotation`
  (option 1b) *reduces* clarity, so 1b is rejected on the very guidance that
  motivates a naming standard.
- There is **no MCP-mandated verb whitelist** — the spec only constrains charset
  and length (`^[A-Za-z0-9_-]{1,64}$`, SEP-986). The whitelist is a GeneFoundry
  *governance* choice, so we are free to extend it; the value we are protecting is
  cross-fleet consistency + synonym-drift detection, both preserved by 1a.
- Tag-based carve-out (3a) is already battle-tested in spliceai and keeps infra
  verbs (`check`, `fetch`) from polluting the domain canon — consistent with the
  v1 rule that `fetch`→`get`.

---

## Impact / migration (if accepted)

Touches the Standard doc, the router validator, and ~16 repo tests. All edits are
to **docs + test/constants only** (no behavioural tool renames under the
recommendation — the whole point of 1a is to *avoid* renames). Rough size: **small**.

| Repo / file | Change | Size |
|---|---|---|
| `genefoundry-router/docs/TOOL-NAMING-STANDARD-v1.md` | Replace the "Open: v1.1" section (`:43-44`) with the **ratified** two-tier canon + tag carve-out; update Rule 2/Rule 8 to reference it. | ~25 lines |
| `genefoundry-router/genefoundry_router/cli.py:44-54` | Set `ACTION_VERB_EXCEPTIONS` to the ratified Tier-2 set (**add `recode`, `liftover`, `analyze`, `score`**; this is the fix for the validator-vs-vep divergence). Add the `ops`/`meta` tag carve-out to `check_leaf_name` (or its caller). | ~10 lines + 1 test |
| **vep-link** `tests/unit/test_tool_names.py` | **DELETE `_ACTION_VERB_EXCEPTIONS`** (`:43`) and the used-exception guard (`:83-93`): `annotate/recode/liftover` move to Tier-2; `check_upstream_health` moves to the `ops`/`meta` tag carve-out. Net **shrink to zero exceptions**. | −~20 lines |
| **spliceailookup-link** | Drop the local "issue #2" `predict` extension (now Tier-2); keep `ops` carve-out (now the fleet rule). | small |
| **gtex-link** | Rename `_DEEP_RESEARCH_ALLOWLIST` handling to the standard `ops`/`meta` tag carve-out (or keep, now blessed by the Standard). | small |
| **pubtator-link** | Move `submit`→Tier-2 and `diagnostics`/`workflow_help`/`review_quickstart`→`meta` tag; keep the remaining `_ACTION_VERB_EXEMPT` as a *documented per-tool* allowlist (follow-up rename pass tracked separately). Net **shrink**. | medium |
| **orphanet-link** | Reconcile its 15-verb local set **down** to Tier-1 + only the Tier-2 verbs it actually uses. | small |
| **mondo-link** `:24` | Add `compute` → adopt full Tier-1 (8). | 1 line |
| **base-7 repos** (clingen, genereviews, gnomad, hgnc, litvar, mavedb, mgi, stringdb, uniprot) | Add `map` → adopt full Tier-1 (8). Harmless; ends the "is `map` canonical?" ambiguity. | 1 line each |
| **No-test repos** (autopvs1, clinvar, gencc, hpo, metadome, panelapp) | Out of scope for *this* brief, but they should receive the standard `test_tool_names.py` so the ratified canon is actually enforced — fold into the same follow-up. | 1 test each |

Net effect on the headline metric: **vep's `_ACTION_VERB_EXCEPTIONS` shrinks to
zero**; pubtator's `_ACTION_VERB_EXEMPT` shrinks; spliceai/gtex/orphanet's ad-hoc
local extensions collapse into one ratified standard; the router validator stops
false-flagging vep. The number of distinct verb-canon definitions in the fleet
goes from **8+ to 1**.

---

## If accepted, the follow-on implementation plan is:

1. **Ratify the canon in the Standard.** Edit
   `TOOL-NAMING-STANDARD-v1.md`: replace `:43-44` with the v1.1 two-tier canon +
   tag carve-out; bump the doc to "v1.1, ratified 2026-06-xx"; update Rule 2 +
   Rule 8 cross-refs. (Docs only — no code; atomic commit.)
2. **Open the tracking issue** `berntpopp/genefoundry-router`: *"Ratify Tool-Naming
   Standard v1.1 verb canon (fleet)"*, linking each `-link` repo's existing
   "Adopt Tool-Naming Standard v1" issue as a child checklist. (Replaces the
   test-docstring-only decision; closes `V1.0-GATE.md:16`.)
3. **Fix the router validator** (TDD): failing test that `recode_variant`,
   `liftover_variant` pass `check_leaf_name` and a `meta`-tagged
   `check_upstream_health` is exempt → set Tier-2 + tag carve-out in `cli.py` →
   green → `make ci-local`. (One atomic commit.)
4. **Per-repo convergence** (one atomic commit + PR per repo, TDD where the test
   changes assertions): vep (delete exceptions) → spliceai/gtex (formalize
   carve-out) → orphanet (tighten) → mondo + base-7 (adopt full Tier-1) →
   pubtator (shrink exempt + open rename follow-up).
5. **Backfill the 6 missing tests** (autopvs1, clinvar, gencc, hpo, metadome,
   panelapp) with the standard `test_tool_names.py` so the ratified canon is
   enforced across the whole federated fleet.
6. **Verify at the gateway:** `make validate` over the live 21-backend catalog is
   green (no verb violations) — this is the fleet-level acceptance check.

---

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing
(`X | None`, builtin generics); `ruff` lint+format and `mypy` must pass; TDD
(failing test first, one atomic commit per task); 600-LOC/module budget
(`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass
before handoff; FastMCP 3.x symbols verified against the INSTALLED package
(post-cutoff, fast-moving); no caller-`Authorization` passthrough to backends;
Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable
ONLY via router/proxy; research-use-only / not-clinical-decision-support
disclaimer preserved.

---

## References

- `docs/TOOL-NAMING-STANDARD-v1.md` (Rule 2 `:15`, Rule 8 `:27`, Open v1.1 `:43-44`)
- `genefoundry_router/cli.py:44-65` — router verb canon + `check_leaf_name`
- `vep-link/tests/unit/test_tool_names.py:13-27, 39, 43-44, 83-93`
- `spliceailookup-link/tests/unit/test_tool_names.py:25-27, 47-49`
- `gtex-link/tests/unit/test_tool_names.py:8-9, 19, 23`
- `pubtator-link/tests/unit/test_tool_names.py:28, 34-53`
- `orphanet-link/tests/unit/test_tool_names.py:18-36`
- `mondo-link/tests/unit/test_tool_names.py:24`; `mavedb-link/tests/unit/test_tool_names.py:14`
- `docs/specs/2026-06-13-genefoundry-router-design.md:182` (spec §19 Q2);
  `docs/plans/V1.0-GATE.md:16` (gate item);
  `docs/specs/2026-06-30-fleet-remediation-design.md:64,82` (workstream M)
- Anthropic, *Writing effective tools for AI agents* — consistency, predictable
  verbs, specific-over-generic, namespacing:
  https://www.anthropic.com/engineering/writing-tools-for-agents
- MCP/agent tool-naming conventions (verb_noun, snake_case, consistency,
  predictable `search_*`/`get_*`, ≤64 chars):
  https://hasmcp.com/glossary/tool-naming-conventions ·
  https://www.speakeasy.com/mcp/tool-design ·
  https://github.com/awslabs/mcp/blob/main/DESIGN_GUIDELINES.md
- MCP tool-name charset/length (SEP-986, `^[A-Za-z0-9_-]{1,64}$`) — Standard refs `:30-32`
