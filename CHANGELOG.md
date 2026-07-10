# Changelog

All notable changes to genefoundry-router are documented here.

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
