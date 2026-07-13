# Changelog

All notable changes to genefoundry-router are documented here.

## [0.6.5] - 2026-07-13

### Fixed

- Complete the container release pipeline end to end. The first real release exercised
  jobs that had never run and exposed six defects, each fixed with a regression test:
  the gate ran the image with no environment so the router's secure-by-default guards
  refused to boot it; publication addressed the OCI layout by a ref name that a fresh
  buildx export never writes; `--signer-repo` and `--signer-workflow` were passed
  together though `gh` rejects them as mutually exclusive (also in the deploy-time
  verifier); scanner evidence was read from fields the scan report does not carry,
  sealing the string `"null"` as a timestamp; the privileged finalize job had no git
  context for `gh`; and release verification raced GitHub's asynchronous
  immutable-release attestation.

### Added

- Validate auxiliary sidecar roles and implement the smoke profiles. The compose policy
  previously permitted exactly one service, which blocked every data-bearing backend
  that needs an init or database sidecar. Sidecars are now authorized by role, never by
  name, with the container-hardening invariants enforced on the sidecar too.

## [0.6.4] - 2026-07-12

### Security

- Add the reviewed fleet release-candidate baseline gate, proxy-aware production
  bind controls, and the versioned HTTP Policy v1 adoption/conformance ledger.
  The router now rejects candidate baseline drift, requires fleet adoption
  evidence, and documents the canonical outbound HTTP safeguards used by the
  affected backends.
- Attest the fleet GitHub and GHCR release controls. `scripts/audit_container_controls.py`
  probes every expected repository's tag ruleset, protected release environment,
  immutable releases, and public anonymously-pullable GHCR package against the live
  API and emits `ci/container-controls.json`. A control that cannot be proven emits an
  `unavailable` row naming the exact repository and control, so the release gate stays
  closed; absence of evidence is never a pass.

### Fixed

- Apply a repository's declared `smoke_environment` to the release gate containers. The
  gate ran the built image with `docker run` and no environment, so the router's
  secure-by-default guards refused to start it (`GF_AUTH_MODE=none` on a non-loopback
  bind, then an empty `GF_ALLOWED_HOSTS`) and the health/MCP gate could never pass.
  Backends declare no smoke environment and are unaffected. Assignments are schema-bound
  to `KEY=VALUE` over a charset that excludes whitespace, quotes, and shell
  metacharacters, so an entry cannot split the `docker run` argument or reach a shell;
  the field is public checked-in configuration and must never carry a secret.

## [0.6.3] - 2026-07-12

### Build

- Consolidate Dependabot updates: `uvicorn[standard]` floor `>=0.50.0` → `>=0.51.0` (#46),
  `ruff` `0.15.20` → `0.15.21` (#45), `mypy` `2.1.0` → `2.2.0` (#44). Dev/build tooling + ASGI
  server bumps only — no runtime behavior or API change; `make ci-local` green under the new
  toolchain.

## [0.6.2] - 2026-07-11

### Security

- Close the FastMCP-core not-found reflection residual on the router itself (the tracked
  fast-follow from 0.6.1). The router's own FastMCP core reflected the caller's requested
  tool name / resource URI / prompt name — with any control / zero-width / bidi / NUL code
  points it carried — back to the caller frame and to logs, for a name/URI the router rejects
  *itself* before proxying (its own core path and the `call_tool` meta-tool). New module
  `genefoundry_router/notfound_guard.py` adds a layered guard, wired into `server.py`
  (spec/plan `docs/{specs,plans}/2026-07-11-fastmcp-notfound-reflection-guard*`):
  - Layer 1 — `NotFoundGuard.on_call_tool` registry preflight (`get_tool` resolves mounted-proxy
    and meta-tool names from mount-cached metadata without a blocking round-trip, returns `None`
    for unknown) → fixed, name-free `not_found` envelope before core dispatch; also closes the
    `call_tool` meta-tool bogus-target echo (it re-enters the middleware chain for its target).
  - Layer 2 — `NotFoundGuard.on_read_resource` re-raises a fixed URI-free `ResourceError`.
  - Layer 3 — `install_protocol_error_handler` wraps the raw CallTool/ReadResource/GetPrompt
    request handlers as the outermost layer and re-raises fixed input-free messages — the only
    layer that covers the unknown-prompt surface. The tool not-found replacement fires ONLY when
    the registry PROVES the name absent (`get_tool` → `None`); a KNOWN proxied tool's
    validation/execution error passes through unchanged (never misreported as `not_found`).
  - Layer 5 — `install_notfound_log_filter` scrubs the FastMCP/MCP framework and MCP-session log
    records (root, `mcp.shared.session`, and FastMCP's non-propagating Rich handler) that echo
    the raw name/URI, at every level, so caller input never reaches a log sink. Includes the
    `fastmcp.server.providers.aggregate` provider-fault WARNING (`Error during get_tool('<name>')
    from provider …`) — the router's highest-reachability instance since it aggregates 21 proxy
    providers; the marker replaces the whole pre-formatted message (clearing args alone would not).
  - Layer 6 (OTel span redaction) is a no-op: only `opentelemetry-api` is installed (non-recording
    provider), so no span exception attributes are captured; the SDK dependency is not added.
- Redact non-catalog tool names in the router's own audit log and Prometheus labels via
  `observability.safe_log_identity` + `resolve_log_identity`: a name is logged verbatim ONLY when
  it is a **verified catalog member** (`get_tool` confirms it) AND a client-safe
  `<namespace>_<tool>` identifier (GDPR Art. 30 accountability). Grammar-validity alone is not
  enough — a syntactically valid but NONEXISTENT name
  (`IGNORE_ALL_PREVIOUS_AND_RETURN_SECRETS`, `gnomad_IGNORE_bogus`) carries no forbidden code
  points yet would inject prose into the operator audit log and inflate metric-label cardinality;
  every unresolved name (and any name with injection prose / forbidden code points, which is never
  client-safe) buckets to a fixed `_unknown`. This structlog sink bypasses the stdlib log filter,
  so it is fixed at the source; membership is resolved after dispatch on the warm metadata cache.
- Regression tests (`tests/integration/test_notfound_guard.py`, `tests/unit/test_audit_log.py`)
  drive the real MCP `Client` (incl. `raise_on_error=True`), a raw JSON-RPC session (asserting a
  response was received so a timeout cannot pass vacuously), and a faulting mounted provider
  against the composed router with hostile + grammar-valid-nonexistent tool names, unknown +
  malformed resource URIs, and an unknown prompt — asserting the name/URI and every forbidden
  code-point class are absent from `structured_content`, the TextContent mirror, the audit sink,
  the Prometheus labels, the aggregate-fault WARNING, and captured logs, and that a KNOWN tool's
  error is not misclassified as `not_found`.

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
