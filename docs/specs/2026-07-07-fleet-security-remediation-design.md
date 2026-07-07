# Fleet Security Remediation — Design Spec

- **Date:** 2026-07-07
- **Status:** Revised after Codex `high` adversarial review (8 must-fix + 3 should-fix incorporated); ready for implementation plan
- **Review:** Codex `high` red-teamed this spec against the live `-link` source on 2026-07-07; its
  corrections are folded into D2–D8, the Theme B/C inventories, and the phasing. Raw review kept in
  the session artifact `codex-review-out.md`.
- **Owner:** Bernt Popp
- **Scope:** Remediate the findings of the 2026-07-06 fleet-wide security & bug audit across the 21
  active `-link` backends (the router itself is already hardened — see below). Fleet coordination
  artifact; each `-link` repo receives its own branch + local commits (no push/merge in this pass).
- **Boundary:** Research use only; not clinical decision support. Mirror backend disclaimers.

## 1. Summary

A 22-repo parallel security review (one agent per repo, each following that repo's
`security-review` + `code-quality-review` skills) found **0 critical, 0 high, 7 medium, ~60 low**.
No request-reachable exploit exists: every backend is a read-only proxy with parameterized SQL, a
fixed (non-caller-controlled) upstream host (no SSRF via tool args), no dangerous sinks, and
fleet-reference container hardening. The residuals are **logging hygiene, config defaults, and
defense-in-depth** — but several recur across many repos because they were copied from a shared
template, so the highest-leverage move is to fix each *pattern* once and propagate it with a guard
test.

The **router** is out of remediation scope: its inbound hardening (rate-limiter attribution +
bounded state, streaming body cap, `/metrics` bearer guard, correlation-ID ordering, Docker
fail-closed) was specified in `docs/specs/2026-07-06-security-hardening-fixes-design.md` and merged
as `9b40f05` ("five inbound-boundary hardening fixes (#30)"). The audit's router items are the
*deliberate no-op defaults* that spec chose, plus two new LOW defense-in-depth notes captured in
Phase 5.

This spec supersedes nothing; it **completes** the partially-landed 2026-06-30 fleet remediation
(`docs/plans/2026-06-30-fleet-remediation/`), whose compose-loopback plan (H) reached only 7 of the
18 affected repos.

## 2. Findings recap (audit 2026-07-06)

Full report: session artifact `FLEET-SECURITY-AUDIT-2026-07-06.md` and memory
`fleet-security-audit-2026-07-06`.

### 2.1 The 7 MEDIUMs

| # | Repo | Finding | Anchor |
|---|---|---|---|
| M1 | uniprot-link | SPARQL injection into IRIREF context: `escape_literal` escapes string-literal chars but **not** IRI-terminators (`>` `}` `<` `{` whitespace); `example_id` spliced raw after only a scheme check. | `services/queries/examples.py:56-61`, `proteins.py:388-390`, `validation.py:31-39` |
| M2 | autopvs1-link | Patient variant coords + free-text query logged at ERROR/WARNING (fire in prod). | `api/autopvs1_client.py:74-80,106-113,166-173,208-215`, `variant_recoder.py:223,228-233`, `services/autopvs1_service.py:53,116,138` |
| M3 | litvar-link | Variant/rsid in logged upstream URL (INFO) + query + raw upstream row at ERROR. | `logging_config.py:112-133,196-212`, `api/client.py:183`, `services/variant_service.py:210,265` |
| M4 | gnomad-link | `get_diagnostics` returns a process-global error ring storing raw `str(exc)` (variant coords / rejected input) → cross-session disclosure. | `mcp/errors.py:428,383,497,512`, `mcp/tools/diagnostics.py:54-59` |
| M5 | gtex-link | Base compose publishes the unauthenticated port on `0.0.0.0` and a comment calls it "safe to run directly". | `docker/docker-compose.yml:10-11` |
| M6 | hgnc-link | Live-REST fallback is dead code: `self._rest` built + reported enabled by diagnostics but never invoked; tools return `data_unavailable` at bootstrap; leaks an unclosed httpx client. | `services/hgnc_service.py:51,69,82`, `mcp/service_adapters.py:32` |
| M7 | genefoundry-router | Rate limiting off by default (`GF_RATE_LIMIT_RPM=0`). **Deliberate** per merged #30; addressed as a non-breaking startup warning only (Phase 5). | `config.py:47` |

### 2.2 Systemic (cross-repo) themes

- **Theme A — PII / free-text in logs + shared `get_diagnostics` error-ring leak.** Direct leakage:
  autopvs1 (M2), litvar (M3), gtex (INFO), genereviews (`gene_symbol` INFO), vep (`repr(exc)` on
  internal error). Cross-session ring leak: gnomad (M4), clingen (LOW). Correct posture already
  exists in clinvar/spliceai (ring present but **un-wired/dead**) and mgi/clinvar (logs only
  exception class) — those are the templates to converge on.
- **Theme B — base `docker-compose.yml` publishes on `0.0.0.0`.** **14 repos** not covered by the
  2026-06-30 H plan (Codex review corrected the list — hgnc/genereviews/stringdb were also exposed):
  autopvs1, clinvar, gtex, gnomad, litvar, mondo, orphanet, panelapp, spliceailookup, uniprot, vep,
  **hgnc, genereviews, stringdb**. (gencc, clingen, hpo, mavedb, metadome, mgi, pubtator already
  loopback-bound.) **uniprot uses long-form `ports:`** (`uniprot-link/docker/docker-compose.yml:17-19`)
  → needs a `host_ip: 127.0.0.1` key, not a `127.0.0.1:` prefix. Mitigated by prod-overlay
  `ports: !reset []`, so footgun not live exposure. Each repo's guard test fails-closed if already fine.
- **Theme C — CORS `allow_credentials=True` on unauthenticated backends.** **12 repos** (Codex
  expanded the list): hpo, hgnc, mgi, mondo, orphanet, gtex, genereviews, pubtator, **litvar,
  panelapp, gencc, stringdb**. Safe today (localhost default origins); pointless (no cookies/auth) and
  a footgun if origins ever set to `*`. **Must preserve each repo's existing method list** (several
  serve GET `/health`/root, so `["POST"]`-only would break them).
- **Theme D — `follow_redirects=True` without host re-validation.** Two sub-classes (Codex): **JSON
  API clients** (fixed upstream, safe to set `follow_redirects=False` or host-revalidate) — clingen,
  hgnc, mgi, panelapp, spliceailookup, uniprot, mavedb(API); vs **release/bundle downloaders** that
  legitimately cross hosts (GitHub asset → object store) and need a **host allowlist**, NOT a
  hard-disable — clinvar, mavedb(bundle), hpo, orphanet. LOW (fixed trusted hosts).
- **Theme E — no size/decompression cap on data-bundle downloads.** clinvar, gencc, hpo, hgnc, mgi,
  mondo, orphanet. Boot/CI path; supply-chain-gated. Caps must apply **during** streaming and
  **during** decompression (Orphanet whole-reads-then-decompresses; HPO zstd `copy_stream` uncapped;
  MGI/Mondo stream without counting) — not after.
- **Theme F — bundle integrity is trust-on-first-use (sidecar hash, no signature).** clinvar,
  mavedb. Highest-value: clinvar variant data.

## 3. Non-goals / out of scope

- **No behavior change to any documented production path.** Prod/npm overlays (`ports: !reset []`,
  expose-only) are byte-for-byte untouched; the live VPS needs no redeploy for Theme B.
- **No new dependencies, no transport change** (Streamable HTTP only), **no token passthrough**, no
  change to any repo's auth model (backends remain unauthenticated by design), tool schemas,
  response-envelope shape, drift/conformance, or upstream data sources.
- **No push / no PR / no merge** in this pass. Each repo gets a feature branch + local commits +
  green `make ci-local`; the operator reviews and merges per repo (matches fleet convention).
- **Theme F (bundle signing)** is designed here but its implementation is **deferred to a follow-up**
  (needs signing-key infrastructure); Phase 4 lands only the fail-closed checksum hardening that
  needs no new infra.
- Router auth/OAuth/JWT semantics, insecure-bind guard, and the no-op rate-limit/metrics defaults
  from #30 are **unchanged** (only the additive startup warning + optional Host check in Phase 5).

## 4. Design decisions (locked)

| ID | Decision | Rationale |
|---|---|---|
| D1 | Fix each *pattern* once with a canonical shape + a per-repo **guard test**; do not hand-roll per repo. | Themes recur because code was copied; guard tests prevent regression and drift. |
| D2 | **Diagnostics rings (revised):** ring records store ONLY `tool_name`, `error_code`, `exc_type` (class name). Drop **both** `message` and `raw_message` from the recent-errors ring; make the **schema-drift** ring store only `tool_name` + parsed `error_field` (not raw message); stop `output_validation` writing raw SDK text into either ring. `field_errors` (field+reason only) may stay. Guard tests cover **both** rings. Applies to gnomad (`mcp/errors.py:422,427,428`; `mcp/tools/diagnostics.py:54,59,61`; `mcp/output_validation.py:53,57,62,65`; not-found embeds `processed_vars` at `api/base_client.py:219`) and clingen (`mcp/errors.py:340,345,346`; `mcp/tools/diagnostics.py:53,59,61`). | Converge on the clinvar/mgi posture; kills cross-session PII (M4). Codex: str(exc) was not the only leak — `message`, schema-drift raw text, and output-validation SDK text also leak. |
| D3 | **PII in logs (revised):** redact **by field name across ALL log paths** (INFO/DEBUG too, not just the ERROR/WARNING anchors), and hash/redact cache keys derived from queries. Keep correlation-id + tool + status + timing. Where a redactor middleware exists (autopvs1), route through it — never rely on log level. For **litvar, fix the shared helpers first** (`logging_config.py` `log_api_request` full `url`; `log_mcp_tool_call` full `params`; `log_error_with_context` `error_message`+arbitrary `context`), then remove caller-level identifiers. | Codex: patching only the cited ERROR anchors leaves many INFO/DEBUG identifier leaks (autopvs1 client/service/routes/cache_manager; litvar shared helpers + route callers). |
| D4 | **CORS (revised):** set `allow_credentials=False`; additionally reject `allow_credentials=True`+`*` origin at startup. **Preserve each repo's existing method list** (at minimum `GET, POST, OPTIONS`) — do NOT collapse to `["POST"]`, several serve GET `/health`/root. | Backends hold no cookies/session; credentials are meaningless. Codex: `["POST"]`-only would break GET endpoints. |
| D5 | **Redirects (revised, split by client class):** for **JSON API clients** (fixed upstream) set `follow_redirects=False` or host-revalidate — clingen, hgnc, mgi, panelapp, spliceailookup, uniprot, mavedb(API). For **release/bundle downloaders** that legitimately 3xx across hosts (GitHub asset → object store) add a **host allowlist** (`github.com`/`api.github.com` → asset host chain, reject any other final host) — do NOT hard-disable — clinvar, mavedb(bundle), hpo, orphanet. | Codex: hard-disabling redirects would break GitHub-release bundle fetches. |
| D6 | **Download caps (revised):** cap **while streaming** (count bytes, abort + `unlink` partial file on overflow; treat `Content-Length` as a precheck only) and cap decompressed output **during** decompression (bounded read loop / `max_output_size`), not after. Orphanet must stop whole-reading-then-decompressing. Boot/CI path only. | Codex: post-hoc caps still OOM (Orphanet reads whole asset; HPO zstd `copy_stream` uncapped). |
| D7 | **Compose loopback (Theme B, revised):** reuse the 2026-06-30 H template + `yaml.safe_load` guard test, never touch prod/npm overlays. For **short-syntax** repos prepend `127.0.0.1:`. For **uniprot long-syntax** add `host_ip: 127.0.0.1` (keep `target`/`published`/`protocol`). Inventory = 14 repos (adds hgnc, genereviews, stringdb). For gtex, also delete the "safe to run directly" comment (M5). | Precedented, mechanical, parallelizable, fails-closed via guard test. Codex corrected the inventory + the uniprot edit shape. |
| D8 | **uniprot IRI injection (M1, revised — two validators):** (a) **database short-names**: `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` — rejects `/ : % @`, still accepts `PDB`/`HGNC`/`Ensembl`. (b) **full example IRIs**: parse with `urlsplit`, require `http\|https` scheme + non-empty netloc, and reject IRIREF terminators/control chars `[\x00-\x20<>"{}|^\`\\]`. Do NOT reuse the loose charset for DB names. Accessions keep `validate_accession`. | Codex: one loose regex is wrong for the `<.../database/{d}>` local-name context; a full IRIREF needs structural validation, not the same charset. |
| D9 | **hgnc dead fallback (M6):** minimal honest fix — default `enable_live_fallback=False`, make `get_diagnostics` report the true wired state (`_rest is not None` currently lies), and ensure the `_rest` httpx client is closed on shutdown (or not constructed when disabled). Wiring the fallback is a **follow-up** (needs the `field`-path allowlist first). | Codex CONFIRMED: `_rest` built (`service_adapters.py:32`) + reported (`hgnc_service.py:69,82`) but never called (`:97,105,302`). |
| D10 | **Router (M7 + LOWs, refined):** additive only — log a startup **warning** specifically when the router is a **publicly-reachable production auth deployment with `GF_RATE_LIMIT_RPM==0`** (not a vague `auth!=none` predicate; public unauth bind is already blocked at `cli.py:101`). Optionally add allow-listed **Host** validation beside Origin. No default flip. | Respects #30's deliberate no-op defaults; Codex refined the warning condition. |

## 5. Phasing & priority

Phases are ordered by value-density. Each phase is independently shippable; repos within a phase are
independent and parallelizable.

- **Phase 1 (P0) — the confirmed injection first, then logging/diagnostics PII.** Order (Codex
  should-fix #1): **uniprot IRI injection (M1, D8)** first — it is the only query-injection class
  issue — then diagnostics rings gnomad (M4) + clingen (D2), then PII logging autopvs1 (M2) + litvar
  (M3, shared-helpers-first), then gtex/genereviews/vep (LOW, D3). Deliverable: canonical fix shapes +
  per-repo guard tests asserting no sentinel in **both** diagnostics rings and no forbidden field in
  emitted log values / cache keys. **Optional standard addendum:**
  `docs/LOGGING-STANDARD-v1.1-diagnostics.md` codifying D2/D3.
- **Phase 2 (P0) — hgnc dead fallback (M6, D9).**
- **Phase 3 (P1) — Theme B: compose loopback** for the **14** repos (D7; short-syntax prefix +
  uniprot long-syntax `host_ip`), incl. gtex M5 comment.
- **Phase 4 (P1) — Theme C: CORS credentials** for the **12** repos (D4; preserve method lists).
- **Phase 5 (P2) — defense-in-depth sweep.** Theme D redirects (D5), Theme E download caps (D6),
  Theme F checksum fail-closed (partial), router additive warning + Host check (D10).

## 6. Canonical fix shapes

### 6.1 Diagnostics rings (D2)

```python
# BEFORE: ring stores message=... and raw_message=str(exc)  -> embeds input_value / variant coords /
#         processed_vars, returned to any caller by get_diagnostics
# AFTER (recent-errors ring AND schema-drift ring):
record_mcp_error(error_code=code, exc_type=type(exc).__name__, tool_name=tool)   # no message/raw_message
record_schema_drift(tool_name=tool, error_field=parsed_field)                    # no raw message text
# output_validation must NOT push raw SDK text into either ring.
```

Guard test (BOTH rings): drive one error whose exception message and one schema-drift event whose raw
text each contain a high-entropy sentinel (`"SENTINEL-PII-7f3a"`); assert the sentinel appears
**nowhere** in the full `get_diagnostics` output (recent_errors AND recent_schema_drift).

### 6.2 PII-safe logging (D3)

```python
# BEFORE: log.error("fetch failed", url=url, variant_id=vid, query=q)
# AFTER:
log.error("fetch_failed", tool=tool, request_id=rid, status=status, elapsed_ms=ms)  # no url/variant/query
```

Guard test: install a structlog capture; drive a failing upstream call with a sentinel identifier;
assert no emitted record's rendered value contains the sentinel.

### 6.3 CORS (D4)

```python
# Preserve the repo's EXISTING method list (do not collapse to POST-only); only flip credentials off.
CORSMiddleware(app, allow_origins=origins, allow_credentials=False,
               allow_methods=existing_methods, allow_headers=existing_headers)
# + startup guard: assert not (allow_credentials and "*" in origins)
```

### 6.4 uniprot IRI validators (D8 — two distinct helpers)

```python
# (a) database short-name allowlist for <http://purl.uniprot.org/database/{d}>
_DB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")   # accepts PDB/HGNC/Ensembl; rejects / : % @
def validate_database_name(value: str) -> str:
    if not _DB_RE.match(value):
        raise InvalidInput("illegal database name")
    return value

# (b) full example IRI spliced into <IRIREF>: structural, not charset-reuse
_IRIREF_FORBIDDEN = re.compile(r"[\x00-\x20<>\"{}|^`\\]")     # SPARQL IRIREF terminators + control chars
def validate_example_iri(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or _IRIREF_FORBIDDEN.search(value):
        raise InvalidInput("illegal example IRI")
    return value
```

## 7. Per-file change inventory (by phase)

- **Phase 1:** each repo's logging module + diagnostics tool/errors module + a new `test_no_pii_in_logs.py` / extend `test_diagnostics.py`. (gnomad `mcp/errors.py`+`mcp/tools/diagnostics.py`; autopvs1 client/service/recoder; litvar `logging_config.py`; clingen `mcp/errors.py`; gtex `services/gtex_service.py`; genereviews `api/routes/search.py`; vep `mcp/errors.py`.)
- **Phase 2:** uniprot `services/queries/{examples,proteins}.py`+`validation.py`+`tests/`; hgnc `config.py`+`services/hgnc_service.py`+`mcp/service_adapters.py`+`tests/`.
- **Phase 3:** 11 × `docker/docker-compose.yml` + guard test (H template); gtex comment deletion.
- **Phase 4:** 8 × CORS app-setup module + startup guard + test.
- **Phase 5:** per-repo client `follow_redirects`/allowlist; downloader byte/decompress caps; clinvar+mavedb checksum fail-closed; router `cli.py`/`server.py` warning + optional `security.py` Host check.

## 8. Test plan (TDD, per repo)

Every change is TDD: write the failing guard/behavior test first, see it fail, implement the minimal
fix, see it pass, then `make ci-local` green. Guard tests are the durable anti-regression contract:
they assert the *property* (no sentinel in logs; loopback-bound port; `allow_credentials is False`;
IRI component rejected) rather than incidental strings. Line-budget (`make lint-loc`, 600 LOC) and
mypy/ruff must stay green in every repo.

## 9. Definition of Done

- Per repo: failing test → fix → `make ci-local` green; one atomic commit per fix on a feature
  branch `fix/security-remediation-2026-07-07` (or per-theme branch); **no push/PR/merge**.
- The 7 MEDIUMs are closed or (M7) surfaced non-breakingly.
- Themes A–D fully applied with guard tests; Theme E applied; Theme F fail-closed hardening applied
  and signing designed-but-deferred.
- No prod/npm overlay touched; no new dependency; no token passthrough; Streamable HTTP only; audit
  logs remain PII-minimal.
- Operator hand-off note lists per-repo branches to review/merge and the deferred follow-ups
  (hgnc live-fallback wiring; Theme F signing; router default flip).

## 10. Risks & rollback

- **Guard tests that over-match** could make CI flaky (e.g. a sentinel that legitimately appears).
  Mitigation: use a high-entropy sentinel and assert on rendered log values only.
- **Redirect disabling** could break a client that genuinely needs a documented redirect. Mitigation:
  per-client review; allowlist rather than hard-disable where a cross-host redirect is expected.
- **Download caps set too low** could reject a legitimately-grown bundle. Mitigation: ceilings set
  well above current sizes; abort message names the limit.
- Rollback is per-repo `git revert` of atomic commits; no data/image/deploy state is touched.
