# Deployment

Container hardening follows [`CONTAINER-HARDENING-STANDARD-v1.md`](CONTAINER-HARDENING-STANDARD-v1.md);
runtime configuration and auth live in [`configuration.md`](configuration.md).

> **Research use only. Not clinical decision support.** Provenance proves origin and
> integrity, not clinical validity or biomedical correctness.

## Container release

The public application image is code-only and AMD64-only in release standard v1. A
protected exact `vX.Y.Z` source tag authorizes publication; pull requests build and gate but
never publish. Release evidence binds the full source revision, `linux/amd64` image digest,
SBOM, provenance, vulnerability report, and MCP-definition digest.

Production accepts only `ghcr.io/berntpopp/genefoundry-router@sha256:<64 lowercase hex>`,
and its effective Compose model has neither an inherited `build:` nor a backend host port.

To release: merge the reviewed version/changelog/lockfile change, create the protected tag,
and let `container-release.yml` publish and attest the exact gated manifest. Before
deployment:

```bash
make container-deploy-verify MANIFEST=<application-release-manifest.json>
GENEFOUNDRY_IMAGE=<name>@sha256:<digest> make docker-prod-config
```

**Never deploy from a tag.**

### One-time GHCR bootstrap

Before the first real release, an owner publishes a disposable source-labelled image, links
the package to this repository, makes the public package visible, verifies an anonymous
pull, and removes only the disposable tag. Record the protected tag ruleset, immutable
GitHub Releases, protected `release` environment, read-only default token permissions,
public linkage, and retention of deployed and rollback digests. No standing package PAT is
retained.

### Rollback

The rollback tuple is: application image digest, application version and revision, exact
data identity (or explicit `none`), compatible schema version, and MCP-definition digest.
Preserve the previous-known-good tuple **and** the OAuth state volume.

A partial push, alias collision, attestation failure, anonymous-pull failure,
mutable-release result, or definition mismatch is a **release incident**: do not select the
version alias, retain evidence, and rerun the original tag event only after the cause is
understood. Roll back by the complete previous tuple, not an image tag alone.

ARM64 is not a v1 target; enable it only after native dependency resolution plus
platform-specific build, content, vulnerability, runtime, MCP, and data tests pass, and the
release manifest records the multi-platform identities.

## Drift detection (scheduled CI)

A backend can serve a clean tool at review time and later change its definition — the
channel for a *rug pull* / tool poisoning. `genefoundry-router drift` fingerprints each
normalized tool's name, description, input/output schemas, annotations, and execution
metadata (SHA-256), then diffs the **live** fleet against a pinned baseline.
`.github/workflows/drift.yml` runs it every 6 h (opt-in) and alerts via a deduplicated
`tool-drift` GitHub issue plus a healthchecks.io dead-man's-switch.

**Exit codes:** `0` no drift, all reachable · `1` drift among reachable backends (alert) ·
`2` no drift but ≥1 backend unreachable (availability warning, **not** an alert). Security
beats availability: drift *plus* an outage still exits `1`.

### The three committed (non-secret) files

| File | What | Keep in sync |
|------|------|--------------|
| `ci/fleet-urls.env` | Public `GF_*_URL=https://<name>-link.genefoundry.org/mcp` for every enabled backend — the URLs CI probes | `test_ci_fleet_urls.py` asserts it matches `servers.yaml` exactly |
| `ci/release-candidate-inventory.json` | Backend-only reviewed release identity plus immutable 40-hex backend revisions, exact HTTPS MCP endpoints, and canonical per-backend definition SHA-256 attestations | Must cover `servers.yaml`'s enabled namespaces exactly; snapshot capture uses these endpoints, not ambient URL variables, and refuses a definition digest mismatch. Router provenance comes from its protected release manifest and Strato lock/runtime attestation, not recursive candidate data. |
| `genefoundry_router/data/fleet-baseline.json` | The packaged reviewed-release tool-definition pin | `make snapshot-baseline RELEASE_CANDIDATE_INVENTORY=ci/release-candidate-inventory.json`, only after reviewing candidate definitions and immutable provenance |

**Runtime response:** in `enforce` mode a changed startup definition fails the boot, so
operators must review the live definition before the router accepts traffic. Poll-time
changes and additions are quarantined; additions/removals mark health degraded without
killing unaffected tools.

**Re-pin discipline.** `make snapshot-baseline` is allowed only after code review of the
complete definition diff *and* the candidate inventory's endpoint/revision provenance. Treat
it as security-relevant. Never re-pin merely to restore green status, and never auto-refresh
in CI — that would silently bless a rug pull.

### Enabling scheduled drift (one-time, on the default branch)

Configured in GitHub → *Settings → Secrets and variables → Actions*:

| Setting | Kind | Required? | Value / where to get it |
|---------|------|-----------|-------------------------|
| `DRIFT_ENABLED` | repo **variable** | to enable scheduled runs | `true`. Unset/`false` ⇒ scheduled runs are a no-op (forks stay off); `workflow_dispatch` always runs |
| `DRIFT_HEARTBEAT_URL` | repo **secret** | optional (heartbeat) | Ping URL of a [healthchecks.io](https://healthchecks.io) check (period **6 h**, grace **~45 min**). Unset ⇒ heartbeat step skipped |
| `DRIFT_OPEN_ISSUE` | repo **variable** | optional | `false` to rely only on the red run + owner email instead of the auto-issue (default on) |

```bash
gh variable set DRIFT_ENABLED --body true
gh secret   set DRIFT_HEARTBEAT_URL --body 'https://hc-ping.com/<your-uuid>'
gh workflow run drift.yml        # smoke-test; expect exit 0 and the healthchecks.io check green
```

Because `drift.yml` is `schedule`/`workflow_dispatch`-only and gated behind `DRIFT_ENABLED`,
it is deliberately **not** badged in the README — a status badge would report a stale or
absent run rather than the current commit.

The `drift` CLI is independently runnable for your own cron/CI:

```bash
genefoundry-router drift --servers-file servers.yaml
# reads GF_*_URL from the environment:
#   set -a; . ./.env; set +a     (locally)
#   or load ci/fleet-urls.env    (in CI)
```
