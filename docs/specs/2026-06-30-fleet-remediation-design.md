# GeneFoundry `-link` Fleet Remediation — Design Spec

**Date:** 2026-06-30
**Status:** Draft (drives the per-workstream plans under `docs/plans/2026-06-30-fleet-remediation/`)
**Source:** 22-agent parallel audit of the whole `-link` fleet + router, run `link-fleet-issue-audit` (`wf_bbf0fd58-f47`), 2026-06-30. See memory `fleet-issue-audit-2026-06-30`.

## 1. Summary

The fleet is healthy at baseline — **0 red, 9 yellow, 13 green; CI green on `main` across all 22 repos**, with MCP-Transport v1, Tool-Naming v1, and Container-Hardening v1 adopted fleet-wide. The open issues are therefore *not* active fires; they cluster into: (a) a few **confirmed fixes that exist but are stranded unpushed**, so deployed servers run buggy code; (b) **two confirmed security tickets** touching genomic/clinical data; (c) **fleet-wide hygiene** (a stale-branch footgun, version drift, compose exposure); and (d) **three strategic standards decisions** that are open questions, not code defects.

This spec decomposes that backlog into **15 independent workstreams (A–O)**, each planned separately so they can be researched, planned, and later executed in parallel.

## 2. Goals / Non-goals

**Goals**
- Produce one researched, TDD-structured implementation plan per code workstream, and one decision brief per standards workstream.
- Every plan is independently executable (own repo, own acceptance criteria, own rollback) with no hidden cross-workstream ordering beyond the explicit dependencies in §9.
- Plans cite the exact `file:line` evidence from the audit and the relevant external best-practice/doc where it changes the fix.

**Non-goals**
- This spec does **not** execute any fix. No `git push`, no redeploy, no branch deletion happens during planning.
- No new backends, no feature work beyond what the audit surfaced.
- Response-Envelope v1 and Tool-Naming v1.1 are *scoped as decisions*, not implemented here.

## 3. Prioritization model

Rank = (impact × confidence) ÷ effort, bucketed:

- **P0** — deployed server is wrong **and** the fix is already written/known (highest leverage).
- **P1** — confirmed security on genomic/clinical data.
- **P2** — fleet-wide hygiene; cheap, batchable, removes systemic risk.
- **P3** — conformance gaps and fleet decisions.
- **P4** — polish, tickets, docs.

## 4. Plan format & conventions (every code plan MUST follow)

Match the existing repo convention (`docs/plans/2026-06-13-genefoundry-router-implementation.md`):

1. **Context & root cause** — with `file:line` evidence from the audit.
2. **Approach** — 2–3 options, trade-offs, a recommendation, and the external best-practice/doc that informs it (targeted research only).
3. **TDD task list** — each task: *write failing test → see it fail → minimal impl → see it pass*; one atomic commit per task. Respect the repo's **600-LOC/module budget** and `make ci-local` gate.
4. **Acceptance criteria** — copied from §6, made concrete (commands/asserts).
5. **Risk & rollback** — how to revert; blast radius; whether a redeploy is involved.
6. **Effort** — trivial / small / medium / large.

Cross-cutting constraints all plans inherit: no caller-token passthrough to backends; Streamable-HTTP only; backends unauthenticated-by-design and reachable only via router/proxy; research-use-only disclaimer preserved; FastMCP 3.x symbols verified against the installed package.

## 5. Workstream decomposition

| ID | Repo(s) | Priority | Type | Findings | Effort |
|----|---------|----------|------|----------|--------|
| **A** | gtex-link | P0 | code | Stranded GENCODE/v10 fix `8c48b7c` unpushed; `__init__` 2.0.0 vs pyproject 2.0.1 | trivial |
| **B** | litvar-link | P0 | code | `resolve_rsid` null fields (#20); canonical-id `#`/`@` URL-encoding; stranded `fix/litvar-entrypoint-reliability`; PMID int→str; PR #33 | medium |
| **C** | stringdb-link | P0 | code | #5 enrichment/network HTTP 500 (pydantic rejects comma-sep STRING JSON; error swallowed); `required_score` docstrings | small |
| **D** | autopvs1-link | P1 | code | #41 PII (`client_ip`+`query_params`) at INFO; prod compose missing `ENVIRONMENT=production`; spoofed UA; provenance | small |
| **E** | pubtator-link | P1 | code | #85 `export_path` path-traversal; uncapped `pmids`/`curated_urls`; anonymous write profile + no rate-limit/XFF | small–medium |
| **F** | fleet (~17 repos) | P2 | code/runbook | Stale `chore/container-hardening-v1` & merged branches that would delete conformance gate if merged | trivial×N |
| **G** | autopvs1, gtex, hgnc, spliceai, mgi | P2 | code | Version drift → serverInfo reports stale version → corrupts router drift baseline | small |
| **H** | gencc, clingen, hpo, mavedb, metadome, mgi, pubtator | P2 | code | Base `docker-compose.yml` publishes unauthenticated backend on `0.0.0.0` | trivial×N |
| **I** | genefoundry-router | P3 | code | Reference impl violates Container-Hardening v1: no CI scan/SBOM, floating base + `uv:latest`, dependency-review advisory-only; drift docstring | small |
| **J** | uniprot-link | P3 | code | `examples.py:18` still uses QLever-rejected `FILTER EXISTS`; live integration test dropped from CI | small |
| **K** | clingen-link | P3 | code | Compose missing hardening; `feat/clingen-guidance-manifest` unpushed; `data-refresh.yml` stale base | small |
| **L** | fleet | P3 | decision-brief | Trivy scan report-only (`exit-code 0`) — gate vs document policy | trivial→N |
| **M** | fleet / router | P3 | decision-brief | Tool-Naming v1.1 verb-canon (vep forces `annotate/recode/liftover/check`); no tracking issue | small |
| **N** | fleet / router | P3 | decision-brief | Response-Envelope v1 DRAFT (4 open questions) — finalize or defer | large |
| **O** | mondo, panelapp, orphanet, genereviews, gnomad, router | P4 | code/checklist | Dead bare tool-names; checked-in `.claude/` memory; typo-dir clone; junk `-f`; PR #82; #40/#27/#49; #3 docs; router #3 close | mixed |

## 6. Per-workstream acceptance criteria

- **A — gtex:** `origin/main` contains the GENCODE enum (`models/gtex.py`, v19/v26/v39) + dataset-aware `resolve_gene_ids` (`mcp/search_match.py`); `gtex_v10` median expression returns non-empty rows for a known gene; CI green post-push; `__init__.__version__` == pyproject.
- **B — litvar:** `resolve_rsid` returns populated `variant_id/gene/variant_name`; `get_variant_literature` succeeds for a canonical id containing `#`; the resolve→fetch chain passes an integration test; PMIDs are `str`; PR #33 merged; #20 closeable. Note: the stranded branch *works around* #20 but does not repopulate `resolve_rsid` — the field-mapping gap (`variant_service.py:378-382`) must be closed explicitly.
- **C — stringdb:** `compute_functional_enrichment` and `search_protein_interactions` return 200 + non-empty on their schema example inputs; a `field_validator(mode='before')` splits STRING's comma-separated `input_genes`/`preferred_names`; upstream parse failures surface as a structured 502 (not bare 500); regression tests added; `required_score` docstrings corrected to the 0–1000 scale; #5 closeable.
- **D — autopvs1:** default-level request logs carry no `client_ip`/`query_params` (correlation_id/method/path/tool only); `docker-compose.prod.yml` sets `AUTOPVS1_LINK_ENVIRONMENT=production` so `debug=False`/WARNING/json engages; default User-Agent identifies the tool; response envelope carries an upstream-provenance note; #41 addressable.
- **E — pubtator:** `export_path` outside the configured base (absolute or `..`) is rejected by a test; `pmids`/`curated_urls` enforce `max_length`; write/full profile documented + enforced as gateway-only (auth or rate-limit when directly reachable); default compose binds loopback; #85 addressable.
- **F — fleet branches:** a verification script lists every superseded branch fleet-wide with a `git log main..<branch>` "0 unique commits" **and** "would-delete-conformance" check; branches deleted only after the check passes; zero conformance gates lost.
- **G — versions:** `serverInfo.version` (and `/health`) equals the installed package version in all five repos via a single `importlib.metadata` source; a fleet assertion (router drift or a per-repo test) guards it.
- **H — compose:** base `docker-compose.yml` no longer publishes the unauthenticated backend on `0.0.0.0` (loopback bind or documented dev-only header) in all seven repos; prod/npm overlays unchanged.
- **I — router:** CI builds `docker/Dockerfile`, runs an image vuln scan + SBOM (matching the standard the repo defines); base + `uv` images digest-pinned; dependency-review gates (no `continue-on-error`); `drift.py` docstring names `ci/fleet-baseline.json`.
- **J — uniprot:** `search_example_queries` text path no longer 400s against live QLever (BOUND-over-OPTIONAL / CONTAINS over `FILTER EXISTS`), verified by an integration test; a scheduled/dispatch CI job runs `make test-integration` off the PR critical path.
- **K — clingen:** compose carries `read_only`/`cap_drop:ALL`/`no-new-privileges`/limits + loopback bind; `feat/clingen-guidance-manifest` is rebased+PR'd or deleted; `data-refresh.yml` bases its branch on current `main` and opens/updates a PR.
- **L — Trivy:** a written policy (gate on fixable CRITICAL/HIGH with `ignore-unfixed`, SBOM stays non-gating — *recommended*) or an explicit "report-only by design" note, applied consistently fleet-wide.
- **M — Tool-Naming v1.1:** a ratified canonical-verb decision in `docs/TOOL-NAMING-STANDARD-v1.md`, a tracking issue, and the resulting shrink of per-repo `_ACTION_VERB_EXCEPTIONS`.
- **N — Response-Envelope v1:** the 4 open questions answered with recommendations; greenlight-or-defer call; if greenlit, an adoption sequence noting which backends already conform (clingen/gnomad/hgnc/uniprot).
- **O — polish:** a per-item checklist with concrete commands/tests for each P4 item (mondo bare-names, panelapp `.claude/` removal, orphanet typo-dir deletion after secret-check, genereviews `-f`/PR #82/#40/#27/#49, gnomad MkDocs site, router #3 benchmark + close).

## 7. Execution & parallelization model

**Planning phase (now):** one planning agent per workstream A–O, dispatched concurrently. Each agent reads its repo, does targeted best-practice/doc research, and writes `docs/plans/2026-06-30-fleet-remediation/<ID>-<repo>.md` in the §4 format. The orchestrator then writes the index (`README.md` in that dir) and self-reviews for consistency.

**Execution phase (later, separate approval):** waves by priority — P0 (A,B,C) → P1 (D,E) → P2 batch (F,G,H) → P3 (I,J,K,L,M,N) → P4 (O). Items ending in `git push`/redeploy/remote-branch-deletion require explicit user go-ahead.

## 8. Dependencies & risks

- **A, B** end in push + redeploy of a live backend — execution-gated.
- **F** is destructive on remote refs — the verification script must prove "no unique commits" before any deletion; surface the full list first.
- **G** should land before/with the next router drift-baseline refresh so serverInfo versions and the baseline agree.
- **L/M/N** are decisions that may *create* follow-on code workstreams once resolved; their briefs end with "if accepted, the implementation plan is …".
- Research-use-only / not-clinical boundary and GDPR Art. 9 framing (autopvs1, pubtator) must be preserved in every user-facing change.

## 9. References

- Audit findings: memory `fleet-issue-audit-2026-06-30`; workflow `wf_bbf0fd58-f47`.
- Fleet standards: `docs/TOOL-NAMING-STANDARD-v1.md`, `docs/MCP-TRANSPORT-STANDARD-v1.md`, `docs/CONTAINER-HARDENING-STANDARD-v1.md`, `docs/RESPONSE-ENVELOPE-STANDARD-v1.md`, `docs/SECURITY-ASSESSMENT-2026-06-29.md`.
- Plan-format precedent: `docs/plans/2026-06-13-fleet-logging-cli-standard-implementation.md`, `docs/plans/2026-06-29-mcp-transport-and-session-standard.md`.
