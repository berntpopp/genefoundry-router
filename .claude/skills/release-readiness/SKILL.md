---
name: release-readiness
description: Use when preparing to tag, publish, or deploy a genefoundry-router build.
---

# Release Readiness (router)

Follow `AGENTS.md` first.

## Workflow

1. Confirm the worktree holds only intended release changes.
2. `make ci-local`; then `make docker-build`, `make docker-prod-config`, `make docker-npm-config`.
3. `make fleet-probe` — confirm every enabled backend is reachable and harvests a non-zero tool set (closes the CI≠prod gap); `make list-tools` for the namespaced surface.
4. `make snapshot-catalog` and review; re-pin the drift baseline (`make snapshot-baseline`) only after reviewing the diff.
5. Verify single-source versioning (no `serverInfo.version` framework leak) and hardened container overlays (`ports: !reset []`, cap-drop, read-only).
6. Confirm the auth posture for the target env (edge auth on for public; secure-by-default guard intact); record residual risks.
