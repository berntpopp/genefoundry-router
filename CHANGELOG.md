# Changelog

All notable changes to genefoundry-router are documented here.

## [0.7.0] - 2026-07-19

### Added

- Establish trusted-builder governance for reusable container releases, with
  immutable workflow provenance, protected-branch control audits, and sealed
  release manifests.
- Publish the canonical Contract Truth v1 and Runtime Data Identity v1
  conformance helpers, backed by router dogfood tests and rollout gates.

### Changed

- Require release evidence to bind observed runtime data identity while
  preserving explicit compatibility with historical release records.
- Harden GitHub ruleset verification against permissive, unknown, and malformed
  branch-control policy representations.

## [0.6.15] - 2026-07-18

### Fixed

- Re-pin the digest-locked Python runtime base image so the released container
  includes Debian's `liblzma5 5.8.1-1+deb13u1` fix for CVE-2026-34743 (Trivy
  alert #5), without suppressing the vulnerability scanner.

### Changed

- Consolidate the reviewed Dependabot updates for FastAPI, Typer, Ruff, Mypy,
  and the pinned CI, attestation, and scanner actions.

## [0.6.14] - 2026-07-16

### Changed

- Re-pin the reviewed runtime baseline, release-candidate inventory, and
  discoverability catalog to the fully deployed 21-backend fleet. The inventory
  carries the exact immutable application-release provenance for each backend,
  including ClinGen v4.0.1 and MetaDome v0.3.1.
- Make fleet-level README and citation checks reliable from isolated Git
  worktrees by resolving the repository/fleet location from Git's main
  checkout.

## [0.6.13] - 2026-07-16

### Changed

- Give OAuthProxy-issued MCP reference tokens a configurable, bounded 12-hour
  lifetime by default. The router continues to validate and refresh the
  short-lived upstream Keycloak token, improving hosted-connector continuity
  without extending the upstream bearer-token lifetime.

## [0.6.12] - 2026-07-16

### Documentation

- Publish the reviewed cross-fleet issue-remediation design and implementation
  plans, explicitly distinguishing completed remediations from proposed
  follow-up controls.

## [0.6.11] - 2026-07-16

### Changed

- Re-pin the reviewed, signed LitVar-Link v6.0.0 release and its live six-tool
  definition surface. This restores router-to-runtime attestation after the
  backend's runtime-hardening release.

## [0.6.10] - 2026-07-16

### Changed

- Re-pin the reviewed, signed fleet inventory and packaged drift baseline to AutoPVS1
  v4.1.1, StringDB v4.1.0, and PubTator-Link v7.1.4 after live definition
  verification. This restores end-to-end deployment attestation for those releases.

## [0.6.9] - 2026-07-15

### Added

- Add MCP Behaviour Standard v1, Tool-Surface Budget Standard v1, Tool-Schema
  Documentation Standard v1, and the canonical behaviour conformance gate used by
  the GeneFoundry backend fleet.

### Fixed

- Treat `not_found` from a tool's own example-acceptance probe as inconclusive
  while still failing malformed examples.
- Keep auxiliary empty objects from hiding collection rows in grouped payload
  detection.

## [0.6.8] - 2026-07-15

### Fixed

- **Restore OAuth login. Reverts the auth part of #71, which broke every Claude/ChatGPT
  login to `genefoundry.org/mcp` (incident 2026-07-15).** #71 set the OAuthProxy's
  `resource_base_url` to the root origin, on the theory that FastMCP forms
  `_resource_url = resource_base_url + set_mcp_path("/mcp")`. That is true only when you call
  `set_mcp_path("/mcp")` by hand. Under the real mount — `server.http_app(path="/mcp")` inside
  FastAPI — the OAuthProxy's `set_mcp_path` receives the sub-app's own root, `""`, so
  `_resource_url == resource_base_url` verbatim. With the origin, the live `_resource_url` was
  the bare origin: the OAuthProxy's RFC 8707 resource check rejected every client sending
  `…/mcp` (the endpoint the RFC 9728 metadata itself advertises) with `server_error`, and
  minted tokens carried `audience == origin`, which the router's own `JWTVerifier`
  (`GF_JWT_AUDIENCE` = `…/mcp`) rejects.

  `resource_base_url` is now `GF_JWT_AUDIENCE` again, so the live `_resource_url` is the
  endpoint (correct resource check + correct minted audience). `_install_resource_tolerance()`
  is restored: because FastMCP's PRM derivation *does* append the path, it can advertise
  `…/mcp/mcp`, and the tolerance collapses that doubled segment so clients echoing it back
  still validate. `tests/unit/test_auth_resource_url.py` now models the LIVE mount
  (`set_mcp_path("")`), not the hand-call, so origin-base fails it — #71 cannot silently
  return. The proper long-term fix is upstream in FastMCP: the PRM advertisement and the
  OAuthProxy resource check must derive the resource URI the same way (see the tracking issue).

**Operator note:** minted-token audience changes back to `…/mcp`, so live OAuth sessions
re-authenticate after the redeploy (clients re-run dynamic client registration).

## [0.6.7] - 2026-07-14

### Fixed

- Stop advertising a doubled protected-resource URI (`https://host/mcp/mcp`) in OAuth
  mode. FastMCP appends the MCP mount path itself — `set_mcp_path("/mcp")` sets
  `_resource_url = resource_base_url + "/mcp"` — so `resource_base_url` must be the ROOT
  origin. The router passed `GF_JWT_AUDIENCE`, which already ends in `/mcp`, baking the
  segment in twice. Consequences, all now gone: the RFC 9728 metadata advertised a
  resource that is not the endpoint; `OAuthProxy` minted tokens with an audience
  (`…/mcp/mcp`) that the router's own `JWTVerifier` — configured with `GF_JWT_AUDIENCE`
  (`…/mcp`) — would reject; and spec-compliant clients, reading that metadata, echoed the
  doubled URI back in their RFC 8707 `resource` parameter.

  `jwt` mode was always correct (it passed `GF_PUBLIC_BASE_URL`); only the deployed
  `oauth` mode regressed. The two modes now agree, and
  `tests/unit/test_auth_resource_url.py` pins the resource URI in both — nothing in the
  suite covered it, which is how this shipped.

- Remove the `_install_resource_tolerance()` monkeypatch. It rewrote FastMCP's resource
  normalizer to collapse `/mcp/mcp` → `/mcp`, attributing the doubled URI to "some MCP
  clients (e.g. ChatGPT)". The clients were behaving correctly: they read the router's own
  metadata and echoed it back. With the root cause fixed, the compensation is unnecessary,
  and the router no longer patches a dependency's internals at import time.

**Operator note:** this changes the minted token audience, so live OAuth sessions
re-authenticate after the redeploy (clients re-run dynamic client registration).

## [0.6.6] - 2026-07-13

### Fixed

- Authenticate to GHCR in the finalize job. It held `packages: write` and pushed the
  version alias with `oras cp`, but never logged in, so the final step of the final job
  failed with `denied` after the image, the release, and every attestation had already
  published. `publish-attest` was the only job that authenticated. The login is placed
  immediately before the push, after the sealed evidence is verified, preserving the
  "verify before credentials" ordering. A guard test now fails any job that pushes to
  GHCR without authenticating.

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
