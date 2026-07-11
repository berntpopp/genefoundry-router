# Changelog

All notable changes to genefoundry-router are documented here.

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
