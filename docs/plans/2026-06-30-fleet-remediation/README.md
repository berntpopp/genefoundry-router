# GeneFoundry `-link` Fleet Remediation — Plan Index

**Date:** 2026-06-30
**Spec:** [`docs/specs/2026-06-30-fleet-remediation-design.md`](../../specs/2026-06-30-fleet-remediation-design.md)
**Source audit:** workflow `wf_bbf0fd58-f47`; memory `fleet-issue-audit-2026-06-30`
**How generated:** 15 parallel planning agents (workflow `wf_a0e7f198-7d5`), one per workstream, each grounding the plan in live code/APIs + targeted best-practice research. TDD plans use superpowers `subagent-driven-development` step format; L/M/N are decision briefs.

> Each planner verified its findings against the *current* code and live endpoints — several audit details were corrected (see **Corrections** below). Read the per-plan "Key decisions" before executing.

## Plan inventory

| ID | Plan | Pri | Tasks | Exec-gated | One-liner |
|----|------|-----|-------|------------|-----------|
| **A** | [gtex-link](A-gtex-link.md) | P0 | 3 | ⚠️ push+redeploy | Ship stranded GENCODE/v10 fix `8c48b7c`; reconcile `__init__` version |
| **B** | [litvar-link](B-litvar-link.md) | P0 | 6 | ⚠️ push+redeploy | Re-land entrypoint fixes + the *real* `resolve_rsid` #20 fix (autocomplete enrichment) |
| **C** | [stringdb-link](C-stringdb-link.md) | P0 | 5 | no | #5: BeforeValidator for comma-sep STRING JSON + ValidationError→502 net |
| **D** | [autopvs1-link](D-autopvs1-link.md) | P1 | 5 | no | #41: drop PII from logs, `ENVIRONMENT=production`, honest UA, provenance |
| **E** | [pubtator-link](E-pubtator-link.md) | P1 | 5 | no | #85: jail `export_path`, cap list inputs, gate write profile (XFF) |
| **F** | [fleet-branch-cleanup](F-fleet-branch-cleanup.md) | P2 | 3 | ⚠️ remote delete | `git cherry` classifier + gated pruner for ~40 stale branches (17 gate-reverting) |
| **G** | [fleet-version-single-source](G-fleet-version-single-source.md) | P2 | 5 | ⚠️ push+redeploy | `importlib.metadata` single-source in 5 repos + per-repo guard test |
| **H** | [fleet-compose-loopback](H-fleet-compose-loopback.md) | P2 | 7 | no | Bind base compose to `127.0.0.1` in 7 repos (+ pyyaml guard test) |
| **I** | [router-hardening](I-router-hardening.md) | P3 | 6 | no | container-security.yml (Trivy+SBOM), digest-pin base+uv, gate dep-review |
| **J** | [uniprot-link](J-uniprot-link.md) | P3 | 3 | no | Rewrite `FILTER EXISTS`→`HAVING`/`GROUP_CONCAT`; restore nightly integration CI |
| **K** | [clingen-link](K-clingen-link.md) | P3 | 3 | ⚠️ push+admin | Harden compose; complete+PR guidance branch; fix data-refresh base |
| **L** | [trivy-gate-policy](L-trivy-gate-policy.md) | P3 | brief | no | **Reframed**: already mandated by Hardening v1 → conformance gap in 12 repos |
| **M** | [tool-naming-v1.1](M-tool-naming-v1.1.md) | P3 | brief | no | Verb-canon forked into 8+ defs; router validator false-flags vep tools |
| **N** | [response-envelope-v1](N-response-envelope-v1.md) | P3 | brief | no | Standard is self-contradictory; ratify flat banner as v1, park strict as v2 |
| **O** | [p4-polish-bundle](O-p4-polish-bundle.md) | P4 | 11 | ⚠️ remote+admin | Dead names, `.claude/` hygiene, typo-dir, tickets, gnomad docs, router #3 |

## Execution waves

1. **Wave 0 — non-gated code, start immediately:** C, D, E, H, I, J (land via normal PR + `make ci-local`; no live-server impact until their own redeploy).
2. **Wave 1 — P0 deployed-bug fixes (gated):** A, B — end in `git push` + backend redeploy. Highest user impact.
3. **Wave 2 — P2 fleet batch:** G (version, gated by redeploy+baseline re-pin), F (gated remote-branch deletion — run *after* B/K so unique-work branches are merged).
4. **Wave 3 — P3 conformance/decisions:** K, then act on briefs L (→ mechanical scan-gate rollout across 12 repos), M (ratify canon + fix router `cli.py:46`), N (ratify flat banner v1 + align stringdb).
5. **Wave 4 — P4 polish:** O.

## Cross-plan dependencies & sequencing

- **gtex version (A ∩ G):** A bumps `__init__` to a literal `2.0.1`; G replaces that literal with `importlib.metadata` single-sourcing. **G is the canonical owner of all version work** — when executing, take A's GENCODE + regression tasks and let **G own gtex's version task** (don't do both literal-bump and refactor). 
- **autopvs1** appears in **D** (security) and **G** (version) — different files, no conflict.
- **F defers unique-work branches** (clingen `feat/clingen-guidance-manifest` → K; litvar `fix/litvar-entrypoint-reliability` → B; gtex/panelapp `feat/mcp-stateless-transport-reapply`; genereviews dependabot). **Run F's deletion after B and K land**, or F skips those branches (its classifier already separates SAFE-TO-DELETE from HAS-UNIQUE-WORK).
- **G's baseline re-pin is strictly post-redeploy:** the router `ci/fleet-baseline.json` currently records the *wrong* serverInfo versions (FastMCP `3.4.2` for gtex/hgnc/spliceai/mgi). Re-pin only *after* the 5 backends redeploy, or it re-captures stale values.
- **L/M/N are decisions that spawn follow-on code** (scan-gate rollout, verb-canon enforcement, envelope alignment) — each brief ends with its "if accepted" task outline.

## Corrections the planners made to the audit/spec

- **C (stringdb #5):** live STRING v12 now returns *arrays* and parses cleanly; the comma-separated 500 comes from an older version/mirror/prod pin. Fix is **defensive** (BeforeValidator + ValidationError→502), not a single reproducer. The network-tool 500 could not be reproduced on current public STRING — hardened by relaxed bounds + the 502 net.
- **B (litvar #20):** root cause is bigger than audited — the sensor payload carries **no** `variant_id/gene/variant_name` and uses key `link` (not `litvar_url`); the fix **enriches from the autocomplete endpoint**. Repo coverage gate is **90**, not 70.
- **G (versions):** worse than reported — gtex/hgnc/spliceai/mgi facades omit FastMCP's `version=` kwarg, so serverInfo silently advertises the **FastMCP library version `3.4.2`**.
- **F (branches):** ~40 stale branches; the 17 `chore/container-hardening-v1` set includes **litvar** but **not uniprot** (already clean). A real 3-way merge *keeps* the gate; loss only in narrow cases — the pruner is conservative regardless.
- **L (Trivy):** **not an open policy question** — Container-Hardening v1 (L129/168/208) already mandates "fail on fixable HIGH/CRITICAL", so the 12 report-only repos are a **conformance gap**; 8 repos already gate correctly.
- **N (envelope):** the standard is **internally contradictory** (2026-06-20 flat banner vs strict Rules §1–§7); **zero** backends implement the strict Rules; the whole live fleet ships the flat banner; stringdb is the lone outlier. Recommends ratifying the flat banner as v1.
- **O (P4):** genereviews **#40 `markdown_table` and token-estimate wins are already shipped/tested**, and **#27**'s corpus release already exists — both narrowed to close/rescope.

## Self-review (writing-plans)

- **Spec coverage:** all 15 workstreams (A–O) from spec §5 have a plan; every §6 acceptance criterion maps to tasks. ✓
- **Placeholder scan:** clean across all 15 files (no TBD/TODO/"add appropriate"/empty code steps). ✓
- **Type/interface consistency:** each plan is single-workstream and self-contained; the one cross-plan shared concern (version single-sourcing) is reconciled above (G owns it). ✓
