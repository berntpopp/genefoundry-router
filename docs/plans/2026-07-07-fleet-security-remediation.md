# Fleet Security Remediation — Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** Use superpowers:subagent-driven-development / test-driven-development.
> Every fix is TDD: failing guard/behavior test first → minimal fix → that repo's `make ci-local`
> (or targeted `uv run pytest`) green → one atomic commit on a per-repo feature branch. **No push /
> PR / merge** — operator reviews per repo. Never touch `docker-compose.prod.yml` / `.npm.yml`.

**Source of truth:** `docs/specs/2026-07-07-fleet-security-remediation-design.md` (revised after Codex
`high` review). Findings recap: memory `fleet-security-audit-2026-07-06`.

**Global constraints (every repo):** Python 3.12+ / uv; ruff + mypy green; 600-LOC/module budget
(`make lint-loc`); no new dependencies; no token passthrough; Streamable HTTP only; backends
unauthenticated-by-design (reachable only via router/proxy); research-use-only disclaimer preserved.
Branch name per repo: `fix/security-remediation-2026-07-07`.

---

## Phase 1 (P0) — injection + logging/diagnostics PII

### T1.1 uniprot-link — SPARQL IRIREF injection (M1, D8) — DO FIRST
- Files: `uniprot_link/services/queries/validation.py` (add `validate_database_name`,
  `validate_example_iri`), `services/queries/examples.py:54,56,61` (validate `example_id` before
  splice), `services/queries/proteins.py:387,389` (validate each `databases[i]`).
- Validators (from spec §6.4): DB names `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`; example IRIs via
  `urlsplit` (scheme in http/https, non-empty netloc, reject `[\x00-\x20<>"{}|^\`\\]`).
- Tests: `tests/unit/` — assert `PDB`/`HGNC`/`Ensembl` accepted; `PDB> } OPTIONAL{?s ?p ?o} #`
  rejected; `example_id` with `>`/`}`/space rejected; existing valid example IDs (see
  `tests/unit/test_shaping.py:559,560`) still accepted. Then `make ci-local`.

### T1.2 gnomad-link — diagnostics rings (M4, D2)
- Files: `gnomad_link/mcp/errors.py:422,427,428` (ring stores only `tool_name`/`error_code`/
  `exc_type`; drop `message`+`raw_message`), `mcp/output_validation.py:53,57,62,65` (no raw SDK text
  into rings), schema-drift ring → `tool_name`+parsed `error_field` only. Keep `field_errors`
  (`:304,310`) as-is.
- Test: sentinel `SENTINEL-PII-7f3a` in an error message AND a schema-drift event → assert absent
  from full `get_diagnostics` output (`mcp/tools/diagnostics.py:54,59,61`). `make ci-local`.

### T1.3 clingen-link — diagnostics ring (D2)
- Same shape as T1.2: `clingen_link/mcp/errors.py:340,345,346`; `mcp/tools/diagnostics.py:53,59,61`.
  Sentinel guard test. `make ci-local`.

### T1.4 autopvs1-link — PII logging (M2, D3)
- Redact by field name across ALL log paths (INFO/DEBUG too): `api/autopvs1_client.py:74-80,91,106-113,
  127,150,161,166-173,179,193,208-215`; `services/autopvs1_service.py:52,53,75,85,115,116,120,137`;
  `api/routes/variant.py:129,141,157`; `api/routes/gene.py:93,105`; hash/redact cache keys at
  `utils/cache_manager.py:145`; `api/variant_recoder.py:223,228-233`. Keep correlation-id+tool+status+
  timing; route through the existing redactor middleware where present.
- Test: structlog capture; failing upstream with sentinel variant id → sentinel not in any emitted
  log value; cache-key log redacted. `make ci-local`.

### T1.5 litvar-link — PII logging, shared helpers first (M3, D3)
- Fix `litvar_link/logging_config.py`: `log_api_request` (:121,123,130,132) drop full `url`;
  `log_mcp_tool_call` (:156,167,174,176) drop raw `params`; `log_error_with_context`
  (:196,206,210,212) drop `error_message`+raw `context`. Then remove caller identifiers in
  `api/routes/{variants,publications,sensor,genes}.py`, `api/client.py:237,244`.
- Test: sentinel through a failing variant lookup → absent from logs. `make ci-local`.

### T1.6 gtex-link / genereviews-link / vep-link — PII logging (LOW, D3)
- gtex: `services/gtex_service.py` query/ids + `api/client.py:271` full URL at INFO.
- genereviews: `api/routes/search.py:66-71` free-text `gene_symbol` at INFO.
- vep: `mcp/errors.py:313-320` `repr(exc)` may embed variant → log `exc_type`+correlation-id only.
- Each: sentinel guard test + `make ci-local`.

## Phase 2 (P0) — hgnc dead fallback (M6, D9)
- Files: `hgnc_link/config.py` default `enable_live_fallback=False`; `services/hgnc_service.py:69,82`
  diagnostics reports true wired state (not `_rest is not None`); ensure `_rest` httpx client closed
  on shutdown or not constructed when disabled (`mcp/service_adapters.py:32`).
- Test: diagnostics reports `live_fallback_enabled=False` by default; no unclosed-client warning.
  Wiring the fallback = documented follow-up (needs `field`-path allowlist, `client.py:87/92`).

## Phase 3 (P1) — Theme B: base-compose loopback (D7) — 14 repos
Reuse the 2026-06-30 H template (`docs/plans/2026-06-30-fleet-remediation/H-fleet-compose-loopback.md`):
prepend `127.0.0.1:` to the base `ports:` mapping + the 5-line dev-only comment + a `yaml.safe_load`
guard test asserting every published port starts with `127.0.0.1:`. Prod/npm overlays untouched.
- Short-syntax repos: autopvs1, clinvar, gtex, gnomad, litvar, mondo, orphanet, panelapp,
  spliceailookup, vep, hgnc, genereviews, stringdb.
- Long-syntax repo: **uniprot** — add `host_ip: 127.0.0.1` to the long-form mapping
  (`docker/docker-compose.yml:17-19`), keep `target`/`published`/`protocol`; guard test reads
  `host_ip`.
- gtex also: delete the "safe to run directly" comment (`docker/docker-compose.yml:33`, M5).
- Per repo: failing guard test → edit → `make ci-local`.

## Phase 4 (P1) — Theme C: CORS credentials (D4) — 12 repos
Set `allow_credentials=False` and reject `*`+creds at startup; **preserve each repo's existing
method list**. Repos: hpo, hgnc, mgi, mondo, orphanet, gtex, genereviews, pubtator, litvar, panelapp,
gencc, stringdb. Test: assert `allow_credentials is False` and that GET `/health` still returns 200.

## Phase 5 (P2) — defense-in-depth sweep
- **Theme D redirects (D5):** API clients (clingen, hgnc, mgi, panelapp, spliceailookup, uniprot,
  mavedb-API) → `follow_redirects=False`. Bundle downloaders (clinvar, mavedb-bundle, hpo, orphanet)
  → host allowlist on the final redirect target.
- **Theme E download caps (D6):** cap while streaming (abort+unlink on overflow; Content-Length =
  precheck only) and during decompression. Repos: clinvar, gencc, hpo, hgnc, mgi, mondo, orphanet
  (Orphanet: stop whole-read-then-decompress).
- **Theme F (partial):** clinvar+mavedb — treat a missing checksum sidecar as fail-closed; pin
  expected digest in CI/config. Detached signing = deferred follow-up.
- **Router (D10):** startup warning when publicly-reachable prod auth deployment has
  `GF_RATE_LIMIT_RPM==0`; optional allow-listed Host validation beside Origin.

## Definition of Done
- Per repo: guard/behavior test added (fails before, passes after), `make ci-local` green, one atomic
  commit on `fix/security-remediation-2026-07-07`, no push.
- 7 MEDIUMs closed (M7 surfaced non-breakingly); Themes A–D applied with guard tests; Theme E applied;
  Theme F fail-closed hardening applied, signing deferred.
- Prod/npm overlays untouched; no new deps; no token passthrough; Streamable HTTP only.
- Operator hand-off: list per-repo branches to review/merge + deferred follow-ups (hgnc fallback
  wiring; Theme F signing; router rate-limit default flip).
