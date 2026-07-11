# Changelog

All notable changes to genefoundry-router are documented here.

## [0.6.1] - 2026-07-11

### Security

- Mark the Response-Envelope Standard v1.1 §"Error-message sanitation (secondary surface)" as
  **COMPLETE fleet-wide**: the tracked upstream error-path text-leak residual is closed on all
  21 backends. The 19 remaining backends (litvar/mavedb were done during v1.1 adoption) each
  patch-released an error-message-sanitation fix that (a) severs upstream API 4xx/5xx bodies and
  transport/decode exception text from caller-visible messages (fixed status-keyed messages),
  (b) builds caller-visible structured error fields only from fixed strings / closed enums /
  grammar-validated identifiers (instruction-shaped prose carries no forbidden code points, so
  code-point sanitation alone is insufficient), and (c) keeps raw bodies/exception text out of
  log sinks. Every merge was gated by an adversarial Codex (gpt-5.6-sol xhigh) review driving the
  real MCP tools with hostile upstream bodies and inputs. Spec + plan added under
  `docs/specs/2026-07-11-error-message-sanitation-fleet-sweep-design.md` and
  `docs/plans/2026-07-11-error-message-sanitation-fleet-sweep.md`.
- Fast-follow (tracked, out of scope here): FastMCP-core not-found surfaces (unknown-tool-name /
  unknown-resource-URI reflection) echo the caller's own requested name — a uniform middleware
  preflight/redaction sweep is tracked separately; several backends already carry the fix.

## [0.6.0] - 2026-07-11

### Security

- Complete fleet-wide Response-Envelope Standard v1.1 (untrusted-content fencing) adoption:
  the 16 free-text `-link` backends (autopvs1, clingen, clinvar, gencc, genereviews, gnomad,
  gtex, hpo, litvar, mavedb, mgi, mondo, orphanet, panelapp, stringdb, uniprot) each shipped a
  breaking release that fences every upstream free-text surface as the typed `untrusted_text`
  object and are flipped to `compatibility: breaking-v1.1` in
  `docs/conformance/untrusted-text-inventory.yml`, with the full set of tool+pointer surfaces
  and measured per-tool object-count ceilings recorded per row. The 4 no-text backends (hgnc,
  vep, metadome, spliceai) are confirmed `n/a-no-untrusted-text` with a regression-guard test
  citation. Corrected the `gencc` row (only `get_gene_disease_assertion` exposes a notes
  surface; `get_gene_curations`/`get_disease_curations` do not) and the `clinvar` row
  (`top_traits[].trait`, not `.name`).
- Add `tests/unit/test_untrusted_content_fleet_conformance.py`, a fleet completeness gate
  asserting every `untrusted-text` inventory row is `breaking-v1.1` with a named test vector,
  and every `no-untrusted-text` row is `n/a-no-untrusted-text` with evidence.
- Ratify `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md`: a limit breach MUST raise a backend-specific
  typed execution error (not one fleet-uniform code); document the error-message sanitation
  requirement for upstream error-body text as a secondary untrusted-content surface.

## [0.5.0] - 2026-07-11

### Security

- Add a patient-data / on-prem deployment profile (`docker/.env.patient-data.example`) that
  disables AutoPVS1 by omitting its backend URL, preventing third-country transfer of possibly
  Art. 9 variant data to `autopvs1.bgi.com` (router #32 / autopvs1-link #41). The profile
  mandates edge auth and drift enforcement and documents the required network-level egress deny.
- Gate the untrusted-text conformance inventory against the `servers.yaml` registry so a newly
  federated backend cannot ship a free-text tool without an explicit untrusted-content
  classification (Response-Envelope Standard v1.1 §9.2).

### Documentation

- Complete the untrusted-text source audit: every backend inventory row now names its exact
  free-text tool(s) + JSON pointer(s) with model evidence, or is classified `no-untrusted-text`.
- Add the fleet-modernization reconciliation ledger reconciling merged state against verified
  git reality with the remaining-work matrix.

## [0.4.0] - 2026-07-10

### Security

- Enforce exact Host and Origin allowlists at the outer HTTP boundary, including
  health, metrics, OAuth metadata, and MCP routes.
- Package the reviewed normalized fleet baseline and compare complete tool
  definitions at startup and on polling refreshes. Enforce mode fails startup
  on changed definitions and quarantines added or changed tools during polling.
- Publish bounded drift state through health and aggregate metrics, and require
  production Host and healthcheck configuration in the supplied Compose stack.

## [0.3.0] - 2026-07-10

### Security

- Add router-owned backend service credentials without forwarding caller Authorization headers.
- Require the `pubtator:write` caller scope for the canonical eight state-changing PubTator
  tools, with fail-closed no-auth behavior and PII-safe denial logging.
- Ignore missing or blank backend credentials instead of emitting an empty Bearer header, and
  document router-first credential staging for outage-free backend enforcement.

### Documentation

- Add the fleet modernization execution ledger with immutable Wave 0 merge, release, and
  validation evidence and explicit pending states for later security waves.
