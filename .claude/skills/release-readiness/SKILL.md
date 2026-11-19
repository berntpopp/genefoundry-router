---
name: release-readiness
description: Use when preparing to tag, publish, or deploy a genefoundry-router build.
---

# Release Readiness (router)

Follow `AGENTS.md` first.

## Workflow

1. Confirm the worktree holds only intended release changes.
2. `make ci-local`; then `make docker-build` and any `docker-*-config` validation targets the Makefile exposes.
3. `make fleet-probe` — confirm every enabled backend is reachable and harvests a non-zero tool set (closes the CI≠prod gap); `make list-tools` for the namespaced surface.
4. `make snapshot-catalog` and review; re-pin the drift baseline (`make snapshot-baseline`) only after reviewing the diff.
5. Verify single-source versioning (no `serverInfo.version` framework leak) and hardened container overlays (`ports: !reset []`, cap-drop, read-only).
6. Confirm the auth posture for the target env (edge auth on for public; secure-by-default guard intact); record residual risks.
7. After pushing tag `vx.y.z` the Container release run appears within seconds; approve its `release` environment gate via `gh api repos/berntpopp/genefoundry-router/actions/runs/<id>/pending_deployments` — it can gate 2–3 times (`status: waiting` is the gate, not a slow build).

## Common mistakes

- `gh release create` by hand — NEVER: releases are immutable, so an empty release burns the version for good. The tag push drives the release workflow.
