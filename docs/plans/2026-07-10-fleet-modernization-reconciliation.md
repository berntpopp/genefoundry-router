# Fleet Security Modernization — Reconciliation & Remaining-Work Ledger

- **Date:** 2026-07-10
- **Author:** continuation pass (Claude) over the Codex-authored
  `docs/specs/2026-07-10-fleet-security-modernization-design.md` and its plans.
- **Purpose:** Reconcile the immutable
  `docs/plans/2026-07-10-fleet-execution-ledger.md` (whose "pending" rows are now
  stale) against **verified git/GitHub reality on 2026-07-10**, and enumerate the
  precise remaining work to cross the ≥9/10 security-acceptance bar.
- **Boundary:** Research use only; not clinical decision support.

This document does not mutate the immutable ledger. It records what is **verifiably
done**, what is **ready to merge**, and what **remains**, with per-repo scope.

## 1. Security-score trajectory (from the 2026-07-10 COO profile, 8.0/10)

The `genefoundry-mcp-security-profile` rated the MCP software **8.0/10**, dragged down by
two dimensions. Both have moved since that snapshot was written:

| Dimension | Profile score | What has since landed | Now blocked only by |
|---|---|---|---|
| Untrusted-content handling | 6.0 | Standard v1.1 + inventory + router opacity guard + contract tests (router #43); PubTator reference fence released v6.0.0; **inventory↔registry completeness gate (this pass)** | Fleet adoption on the free-text backends (§4) |
| Tool-poisoning / drift | 6.5 | Outer Host/Origin guard, packaged baseline, startup+poll drift **with fail-closed CI gate + dead-man heartbeat** (router #36, merged) | Live clean/degraded/enforced probes (deploy) |
| Backend isolation & deploy | 7.5 | AutoPVS1 egress+privacy released (autopvs1-link v3.0.0, #41 closed); **patient-data router profile disabling AutoPVS1 (this pass)** | Network-level egress deny + deploy evidence |
| Router auth & design | 8.5 | Host/Origin outer guard merged; PubTator write-scope + service-token boundary merged | — (already strong) |
| Code & supply-chain | 9.0 | Dependabot swept; CI actions SHA-pinned | Digest-pin + SBOM on the last few images |

The two already-merged programmes (drift gating; PubTator fencing) plus this pass's
router work move the aggregate materially above 8.0. **The single remaining lever that
gates >9 is untrusted-content adoption across the free-text backends (§4)** followed by
live deployment evidence (rubric dimension 10, operator-gated).

## 2. Router — verified state (main @ v0.4.0)

| Item | Issue | State |
|---|---|---|
| Untrusted-content standard v1.1 + inventory + hints opacity + contract tests | #31 | **MERGED** (PR #43) |
| Outer Host/Origin boundary + drift baseline/startup/poll + CI gate | #36 | **MERGED** (PR #41) |
| PubTator write-scope + backend service-token boundary | #33 | **MERGED** (PR #39) |
| Patient-data deployment profile (disables AutoPVS1) + test | #32 | **DONE this pass** — branch `fix/patient-data-egress-profile-2026-07-10`, ci-local green (unpushed) |
| Inventory↔servers.yaml completeness gate | #31 | **DONE this pass** — branch `fix/untrusted-inventory-registry-gate-2026-07-10`, ci-local green (unpushed) |

Router drift gating verified: `.github/workflows/drift.yml` fails the run on `changed`
(exit 1), auto-opens/closes a `tool-drift` issue, and pings a dead-man heartbeat;
`.github/workflows/fleet-probe.yml` proves the DEPLOYED fleet on a 6-hourly schedule.

## 3. Backend fleet — verified modernization matrix (2026-07-10)

Legend: ✅ done/released · ⬜ outstanding (on main; a fix may exist on an unpushed branch — see §4) · 🔴 main CI red

| Backend | Host/Origin re-enabled (FastMCP 3.4.4) | Ingest hardening | untrusted_text fencing | Latest | Notes |
|---|---|---|---|---|---|
| gnomad | ✅ | n/a | ⬜ | v7.0.0 | |
| clinvar | ✅ | ✅ | ⬜ | v0.3.0 | fully modernized bar fencing |
| hgnc | ✅ | ✅ | ⬜ | v2.0.0 | dead global-disable line to remove |
| mgi | ✅ | ✅ | ⬜ | v0.4.0 | dead global-disable line to remove |
| stringdb | ✅ | n/a | ⬜ | v3.0.0 | dead global-disable line to remove |
| mondo | ✅ | ✅ | ⬜ | v0.2.0 | |
| mavedb | ✅ | ✅ | ⬜ | v0.3.0 | |
| hpo | ✅ | ✅ | ⬜ | v0.2.0 | |
| orphanet | ✅ | ✅ | ⬜ | v0.2.0 | |
| gencc | ✅ | ✅ | ⬜ | v0.6.1 | |
| litvar | ✅ (on main) | n/a | ⬜ | 3.0.3 | **release PR #48 (4.0.0) READY TO MERGE** |
| genereviews | ✅ (on main) | n/a | ⬜ | 3.0.4 | **release PR #91 (4.0.0) READY TO MERGE** |
| vep | ✅ (on main) | n/a | ⬜ | 1.0.3 | no release tag cut yet |
| pubtator | ⬜ | n/a | ✅ (reference) | v6.0.0 | owes Host/Origin re-enable |
| autopvs1 | ⬜ | n/a | ⬜ | v3.0.0 | egress+privacy DONE (#41 closed); owes Host/Origin |
| gtex | ⬜ | n/a | ⬜ | 2.0.4 | on FastMCP 3.4.3; not started |
| uniprot | ⬜ | n/a | ⬜ | 2.0.3 | on FastMCP 3.2.0; not started |
| metadome | ⬜ | n/a | ⬜ | 0.1.2 | not started |
| clingen | ⬜ | (ETL) | ⬜ | 2.0.6 | 🔴 main CI red; not started |
| panelapp | ⬜ | n/a | ⬜ | 0.3.3 | 🔴 main CI red; not started |
| spliceai | ⬜ | n/a | ⬜ | 3.0.1 | 🔴 main CI red; FastMCP 3.2.0; not started |

## 4. Remaining work — prioritized

### P0 — ready NOW (operator merge/deploy)
1. **Merge release PR `litvar-link#48`** (bump 4.0.0) — MERGEABLE/CLEAN, all checks green.
2. **Merge release PR `genereviews-link#91`** (bump 4.0.0) — MERGEABLE/CLEAN, all checks green.
   Both only ship the already-merged strict Host/Origin major; merging closes the rollout tail.
3. **Merge the two router branches** from this pass (#32 profile, #31 inventory gate) after review.

### P1 — untrusted-content fleet adoption (the score lever, #31)
Per-backend classification map: see §5 (evidence-backed). Backends classified
`untrusted-text` implement the v1.1 typed object (NFC + control/zero-width strip +
provenance + `raw_sha256` + output-schema literal + hostile test vector) as a breaking
major; backends classified `no-untrusted-text` record that in the router inventory with a
source-evidence path. The router `untrusted-text-inventory.yml` rows move from
`__source_audit_required__` sentinels to real tool + JSON-pointer + classification data
(this pass fills the classifications from §5). PubTator is the reference implementation.

### P2 — Host/Origin re-enable on the laggards (#36, defense-in-depth)
**Correction to remote-only recon:** most laggards already carry the fix on **local unpushed
worktree branches** `fix/fastmcp-344-strict-host-origin` (a prior session), based on current
`origin/main` and faithful to the merged sibling template (`gencc` `12cb811`, `mondo`
`fd469eb`): bump `fastmcp>=3.4.4,<4`, delete the emergency global disable, add the outer
`HostOriginGuardMiddleware(mode="strict")` + native `host_origin_protection=True` with
explicit allowed hosts/origins, and a guard test.

All fix branches are `fix/fastmcp-344-strict-host-origin` (unpushed, local). Verified green
this pass:

| Backend | Fix branch HEAD | ci-local | Suggested release |
|---|---|---|---|
| clingen (red-CI) | `d12e79c` | **green, 428 passed** | patch 2.0.6→2.0.7 |
| panelapp (red-CI) | `6a9d104` | **green, 359 passed** | minor 0.3.3→0.4.0 (new default-deny) |
| spliceai (red-CI) | `561dbee` | **green, 370 passed** (also fixed a 3.2→3.4.4 `ValidationError` break) | patch 3.0.1→3.0.2 |
| gtex | `ffd150c` | prior-session branch, fresh vs origin/main — operator verify | patch |
| uniprot | `1415332` | prior-session branch, fresh — operator verify | patch |
| metadome | `d8dbc96` | prior-session branch, fresh — operator verify | patch |
| autopvs1 | `32a2700` | **green, 511 passed** (egress v3.0.0 untouched) | minor 3.0.0→3.1.0 |
| pubtator | `14ded40` | **green, 1332 passed** (fencing/write-boundary v6.0.0 untouched; added missing `mode="strict"`) | minor 6.0.0→6.1.0 |

**Host/Origin dimension is now COMPLETE fleet-wide** (13 already merged + 8 on green unpushed
branches). Remaining = operator verify/merge/release/deploy.

> **CRITICAL deploy prerequisite for every Host/Origin re-enable:** in production each backend's
> `*_ALLOWED_HOSTS` MUST include the proxied public hostname (JSON array), or the router will
> `421` when federating it. Default is loopback-only. Wire this in each backend's prod/npm
> Compose env before/with deploy.

The router already enforces one outer Host/Origin guard, so backend re-enable is
defense-in-depth. Root cause of the 3 red CIs: the 2026-07-07 emergency
`fix(mcp): pre-empt fastmcp 3.4.3 host-origin 421` hotfix mis-ordered an `import fastmcp`,
failing `ruff format --check` + lint `I001`; the 3.4.4 re-enable removes it. **Genuine
remaining Host/Origin work: autopvs1 and pubtator only.**

### P3 — hygiene
- Remove the dead import-time `http_host_origin_protection = False` line in hgnc, mgi, stringdb.
- Cut a vep-link release (Host/Origin already on main, no tag).
- Finish digest-pin + SBOM on the last few backend images (supply-chain 9.0 → 9.5).

### Operator-gated (rubric dimension 10 — cannot be closed by code)
Deploy in dependency order and capture live evidence: external attack-surface scan,
conformance probes, clean/degraded/enforced drift probes, patient-profile absence of
`autopvs1_` tools, and network-egress deny for autopvs1.bgi.com. Third-country-transfer
governance (disable vs self-host AutoPVS1) is a DPO/operator decision, now expressible via
the patient-data profile.

## 5. Untrusted-text surface classification (evidence-backed)

Evidence-backed per-backend source audit (2026-07-10). Backends marked
**untrusted-text** return externally-sourced free-text prose and MUST adopt the v1.1
typed `untrusted_text` object; **no-untrusted-text** backends return only structured/
numeric/identifier data and record that fact with a source-evidence path. PubTator is the
released reference. This table feeds `docs/conformance/untrusted-text-inventory.yml`.

| Backend | Class | Primary free-text tool(s) → JSON pointer | Evidence | Existing sanitation |
|---|---|---|---|---|
| pubtator | untrusted-text ✅ fenced | `get_publication_passages` `/passages/*/text` | `models/publication_passages.py:MCPPublicationPassage.text` | v1.1 fence (released 6.0.0) |
| genereviews | untrusted-text | `search_passages` `/results/*/text,/snippet`; `get_passage` `/passage/text`; `get_chapter_section` `/content`; `get_passages_batch` `/passages/*/text` | `models/genereview_models.py:RankedPassage.text` | `_clean_content()` HTML/ctrl strip on live path only (not a fence) |
| uniprot | untrusted-text | `get_protein` `/function`; `get_protein_features` `/features/*/description`; `get_protein_diseases` `/diseases/*/involvement`; `get_protein_variants` `/variants/*/description` | `services/shaping.py:190,270,288,341` (rdfs:comment) | none (output) |
| hpo | untrusted-text | `get_term` `/definition`; `search_terms` `/results/*/definition,/definition_snippet` | `services/shaping.py:165-170`; `data/repository.py:72` | none |
| mondo | untrusted-text | `get_disease` `/definition`; `search_diseases` `/results/*/definition`; `get_disease_batch` `/results/*/definition` | `services/mondo_service.py:157` | ingest-time OBO cleanup only |
| orphanet | untrusted-text | `get_disease` `/definition`; `search_diseases` `/results/*/definition` | `services/orphanet_service.py:174` | none |
| mavedb | untrusted-text | `get_score_set` `/short_description,/abstract_text,/method_text`; `get_experiment` `/abstract_text` | `services/shaping.py:174,222,223` (depositor prose) | none |
| clingen | untrusted-text | `get_variant_interpretation` `/summary`; `get_gene_dosage` `/*/haplo_description,/triplo_description`; `get_cspec` `/criteria/*/description`; `get_gene_validity` `/assertions/*/disease_name` | `models/models.py:142,75,79,257,30` | `strip_html()` on `disease_name` only (ERepo `summary`/CSpec unsanitized) |
| clinvar | untrusted-text (short labels) | `get_variant` `/traits/*/name` (+ variant/gene trait tools) | `models/variant_models.py:Trait.name (L26)` | none |
| gencc | untrusted-text (full mode) | `get_gene_disease_assertion` `/assertion/submissions/*/notes` (+ curation tools) | `models/records.py:SubmissionRecord.notes (L117)` | none |
| panelapp | untrusted-text | `get_panel` `/panel/description`; `get_panel_genes` `/entities/*/phenotypes,/evidence` | `services/shaping.py:109,175,176` (curator prose) | none |
| litvar | untrusted-text (full-mode HTML snippet) | `search_genetic_variants` `/results/*/match` | `models/endpoint_specific.py:AutocompleteVariantItem.match (L55)` | compact allowlist drops it; full mode unstripped |
| spliceai | no-untrusted-text | — (numeric splice deltas + identifiers; text is server-synthesized) | `mcp/shaping.py` (`headline`/`consequence_summary` composed locally) | n/a |
| gnomad | untrusted-text | `get_clinvar_variant_details` `/submissions/*/conditions/*/name,/submitter_name` (ClinVar submitter text) | `models/clinvar_models.py:ClinVarCondition.name (L9)` | none (freq data numeric; only ClinVar passthrough) |
| stringdb | untrusted-text | `resolve_protein_identifiers` `/mappings/*/annotation`; `compute_functional_enrichment` `/terms/*/description`; `get_functional_annotations` `/annotations/*/description` | `models/responses.py:65-69,295-299,346-350` | none |
| mgi | untrusted-text | `get_mp_term` `/definition`; `search_phenotype_terms` `/results/*/definition` (MP ontology defs) | `mcp/schemas.py:MP_TERM_SCHEMA.definition (L149)` | none (repo FTS escape is input-only) |
| gtex | untrusted-text (low-risk) | `get_gene_information` `/data/*/description`; `search_genes` `/data/*/description` (GENCODE descriptor) | `models/responses.py:Gene.description (L241)` | none |
| autopvs1 | untrusted-text (scraped, low-trust) | scraped PVS1 result presented by `present_variant` | `mcp/presenters/variant.py` (BGI HTML-derived) | shape-validation fail-closed (`UpstreamFormatError`); not a fence |
| hgnc | no-untrusted-text | — (curated nomenclature/IDs/enums only) | `mcp/schemas.py:GENE_SCHEMA` (name = approved symbol) | n/a |
| vep | no-untrusted-text | — (SO enums, HGVS notations, numeric scores) | `models/responses.py:TranscriptConsequence` (`extra="ignore"`) | n/a |
| metadome | no-untrusted-text | — (tolerance scores/positions/IDs) | `mcp/schemas.py` (no description field) | n/a |

**Fleet totals:** 17 untrusted-text (1 fenced: pubtator; 16 pending adoption) · 4 no-untrusted-text
(hgnc, vep, metadome, spliceai).

**Rollout note:** clinvar (short trait labels), gencc (`notes`, full-mode only), and litvar
(optional full-mode HTML `match`) are lower-surface untrusted-text; a reviewer may down-scope
them. genereviews, uniprot, hpo, mondo, orphanet, mavedb, clingen, panelapp carry the richest
upstream prose and are the priority adoption targets after PubTator.
