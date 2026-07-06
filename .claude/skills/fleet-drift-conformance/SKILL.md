---
name: fleet-drift-conformance
description: Use when working on tool-definition drift detection, MCP transport conformance, the live fleet probe, or the discovery-catalog snapshots that close the CI≠prod gap.
---

# Fleet Drift & Conformance

Follow `AGENTS.md` first. These tools catch two failure classes CI alone misses: **prod divergence** (a backend green in CI but dead / renamed in prod) and **tool poisoning / rug-pull** (a tool definition changing after approval).

## Tools

- `make fleet-probe` — live-probe every enabled backend for reachability + a non-zero tool harvest; fails loud on 0-tool or unreachable backends. Run before trusting `/health`.
- `genefoundry-router drift` (`drift.py`) — fingerprint each tool definition and diff a live snapshot against the pinned fleet manifest; exits non-zero for CI/cron.
- `make conformance` — MCP Transport & Session Standard v1 probe (single stateless `/mcp`, no 307, canonical `serverInfo` / health).
- `make snapshot-fleet` / `make snapshot-catalog` / `make snapshot-baseline` — regenerate the fleet manifest, discovery catalog, and pinned drift baseline.

## Workflow

1. Reproduce with `make fleet-probe` (or `drift` / `conformance`) and read the diff.
2. If the change is legitimate (a backend intentionally changed tools), review it, then re-pin: `make snapshot-baseline` / regenerate the catalog.
3. If unexpected, treat it as a possible rug-pull — investigate the backend before re-pinning.
4. Keep the guard / snapshot tests green; run `make ci-local`.

## Common mistakes

- Re-pinning the baseline to make drift "pass" without reviewing why it changed.
- Trusting a url-is-configured `/health` instead of a live harvest.
