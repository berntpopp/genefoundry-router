# Deployment

Container hardening follows [`CONTAINER-HARDENING-STANDARD-v1.md`](CONTAINER-HARDENING-STANDARD-v1.md);
runtime configuration and auth live in [`configuration.md`](configuration.md).

> **Research use only. Not clinical decision support.** Provenance proves origin and
> integrity, not clinical validity or biomedical correctness.

## Fleet controller deploy contract

The fleet controller (`strato_v6_docker_npm`, `scripts/utils/deployment_preflight.py`)
deploys `docker/docker-compose.npm.yml` alone — a standalone file, per
[`DEPLOY-CONTRACT-STANDARD-v1.md`](DEPLOY-CONTRACT-STANDARD-v1.md) — and its `canonical_projection` accepts a
declared `user` only as numeric non-root — the runtime observer separately proves the
effective uid from `/proc`. `docker/docker-compose.npm.yml` therefore declares
`user: "10001:10001"`, this image's own uid:gid from `docker/Dockerfile`'s
`useradd --uid 10001` / `USER 10001:10001` — never copied from a sibling `-link` repo,
which may use a different uid. `user` must NOT appear in `docker/docker-compose.yml` or
`docker/docker-compose.prod.yml`: those are the Compose files gated by
`container-release.json`, and the release validator (`container_release.py
validate-compose`) forbids `user` there. `tests/unit/docker/test_compose.py` guards both
halves of this split.

## Community router instance

[Federation Standard v1 §2](FEDERATION-STANDARD-v1.md#2-deployment): community backends are
never loaded by the core endpoint. The core service in `docker/docker-compose.prod.yml` and
`docker/docker-compose.npm.yml` pins `GF_ALLOW_COMMUNITY_BACKENDS: "false"`, so a stray
`.env` value cannot flip it, and a set `GF_COMMUNITY_SERVERS_FILE` then fails startup.
Community backends are served by a second service, `genefoundry-community`, in
`docker/docker-compose.prod.yml` under the Compose profile `community` (so a plain
`docker compose up` and the single-service release gate never see it):

- same image as the core router, `GF_MCP_PATH=/community/mcp`;
- `GF_ALLOW_COMMUNITY_BACKENDS=true`, `GF_DRIFT_MODE=enforce`, `GF_POLL_INTERVAL=300`;
- `GF_RATE_LIMIT_RPM` / `GF_RATE_LIMIT_IP_RPM` of 30 (anonymous, per client IP), below the
  core recommendation;
- kill switch `docker/community/kill-switch` mounted read-only at `GF_KILL_SWITCH_FILE`
  (one namespace per line, re-read within 5 s without restart), latch state in
  `GF_QUARANTINE_FILE` on its own volume;
- its own bridge network `community` (interface `gf-community`) and nothing else: no link to
  the core backend containers or the identity provider. It reads only the non-secret
  `ci/fleet-urls.env`, so core backends are reached by their public URLs and none of the
  core `.env` secrets (OAuth, service tokens) are present; a core backend that takes a
  service token is called without one.

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  --profile community up -d genefoundry-community
```

### Host firewall (generic example)

Docker bridges route to every private network the host can reach. `scripts/community_firewall.sh`
(run as root on the host, idempotent) installs this rule set for the `gf-community` bridge:

```bash
iptables -N GF-COMMUNITY
iptables -A GF-COMMUNITY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
# public URLs of a reverse proxy on the same host hairpin back as DNAT to its container
iptables -A GF-COMMUNITY -p tcp -m conntrack --ctstate DNAT --ctorigdstport 443 -j RETURN
for p in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16; do
  iptables -A GF-COMMUNITY -d "$p" -j DROP
done
iptables -I DOCKER-USER -i gf-community -j GF-COMMUNITY
iptables -I INPUT -i gf-community -m conntrack --ctstate NEW -j DROP   # the Docker host
# IPv6: the same chain with fc00::/7 (ULA) and fe80::/10 (link-local)
```

Use `DROP`, not `REJECT`: the reachability check treats a refused connection as reachable.
If the proxy runs natively on the host rather than in a container, its public 443 arrives
through `INPUT`; allow exactly that port before the `INPUT` drop. Rules installed with
`iptables` are not persistent; load them from the host's firewall service at boot. A
separate egress IP is not configured by this repository; without one, community traffic
leaves from the host's address (a recorded limit of §2).

### Reverse proxy (generic example)

The proxy joins the `community` network as the only network it shares with the community
container:

```nginx
location /community/mcp {
    proxy_pass http://genefoundry_community:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;          # streamable HTTP
    proxy_read_timeout 60s;
}
```

### Reachability check

Run from inside the community container after every deploy and firewall change:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  --profile community exec -T genefoundry-community python - \
  --blocked genefoundry_router:8000 --blocked <core-container-ip>:8000 \
  --blocked <identity-provider>:<port> --open genefoundry.org:443 \
  < scripts/community_egress_check.py
```

It always adds the cloud metadata address `169.254.169.254:80` and the host gateway (ports
22, 2375, 8000) to the blocked set. A target counts as blocked only when its name does not
resolve, the connect times out or the network is unreachable; a refused connection means the
host answered. Exit 0 means isolated. CI (`community-egress` job in `.github/workflows/ci.yml`)
applies the same firewall script on throwaway bridges with stand-in core and identity-provider
containers and runs the same check.

## Rate limits at deploy

Set the rate limits in the deploy `.env`; Compose passes it through `env_file`. Since
v0.13.0, `GF_RATE_LIMIT_RPM` is a per-account limit when auth is on. The pre-auth per-IP
floor is `GF_RATE_LIMIT_IP_RPM`, which defaults to the same value. Raise the floor first, or
every account behind one IP still shares the old per-IP cap. Recommended starting values
and the `GF_RATE_LIMIT_OVERRIDES` format are in
[configuration.md § Rate limits](configuration.md#rate-limits). After a change, check one
authenticated response: it should carry `RateLimit-Policy` lines for both `"ip"` and
`"account"`.

## Private scheduled drift probe

The scheduled drift check reaches private fleet services from the controller's existing
`npm_default` Docker network. GitHub Actions does not receive the backend URLs or
`GF_PUBTATOR_TOKEN`; a main-only job in the `drift-probe` environment authenticates with
`DRIFT_SSH_PRIVATE_KEY` and the public host-key pin in `ci/drift_known_hosts`. The SSH
client sends no remote command, disables forwarding and TTY allocation, and requires the
pinned host key. The matching server-side key must be restricted to the root-owned
`scripts/readonly_drift_probe.py` installation as its forced command, reject a nonempty
`SSH_ORIGINAL_COMMAND`, and have no general shell or sudo rule attached to that key.
The forced command runs under the VPS host's `/usr/bin/python3` (3.12), not the router
image's 3.14, so this file must stay Python 3.12-compatible (ruff `per-file-target-version`
and `test_script_parses_as_python_3_12` enforce the syntax). Reinstall it whenever its
allowlist changes; a stale copy reports the missing backends as unreachable.

The forced command selects only the fixed allowlist of 30 production `GF_*_URL` values and
the single PubTator service token from the running router container's Docker metadata.
It validates those URLs against the registry's reviewed public endpoints, then runs
`genefoundry-router drift` using that container's exact cached release digest. The verifier
has no mounts or published ports, runs as uid 10001 with a read-only rootfs, dropped
capabilities, `no-new-privileges`, and CPU, memory, PID, output, and time limits. The
workflow updates the existing drift issue on native exit 1; native exit 2 and SSH,
preflight, or timeout failures fail the job. A host lock prevents overlapping runs. The
forced command removes only its fixed, labeled probe container after normal exit or timeout
and fails closed if cleanup cannot be verified. Only native exit 0 sends the
dead-man's-switch heartbeat; failed heartbeat delivery fails the job. Installing the
forced-command key and configuring the `drift-probe` environment are host/operator
prerequisites; the workflow must remain disabled until those controls are in place.

Self-check the rendered stack against the controller's own projection before deploying:

```bash
export GF_IMAGE="ghcr.io/berntpopp/genefoundry-router@sha256:<digest>"
export GF_ALLOWED_HOSTS="genefoundry.org" GF_HEALTHCHECK_HOST="genefoundry.org"
docker compose -f docker/docker-compose.npm.yml config --format json \
  > /tmp/genefoundry-router-rendered.json
# from the strato_v6_docker_npm checkout:
uv run python -c "
import sys, json; sys.path.insert(0, 'scripts')
from utils.deployment_preflight import canonical_projection
p = canonical_projection(json.load(open('/tmp/genefoundry-router-rendered.json')), project='genefoundry-router')
for n, s in p['services'].items(): print(n, 'user=', s.get('user'))
print('PROJECTION OK')"
```

## Fleet Compose Contract

`docker/docker-compose.npm.yml` is the file the fleet controller deploys, and it is **not**
gated by `validate-compose` (that gate covers the release Compose files listed in
`container-release.json` `service.compose_files`, where `user` is forbidden). The deployed
overlay is gated separately, by `container_release.py validate-deployed-overlay`, which the
shared reusable release workflow runs in `prepare` — before any image is built — for every
repository that calls it. Enforcing it centrally is what keeps the 21 `-link` backends on
one contract without 21 hand-written pull requests.

The command renders the deployed file set with `docker compose config --format json`,
substituting an inert, shape-correct placeholder for every required `${X:?...}` variable,
and prints one line per violation:

```
<service>: <rule> — <what to change>
```

```bash
uv run python scripts/container_release.py validate-deployed-overlay \
  --config container-release.json --project-dir .
```

### The rules, and the failure each one prevents

| Rule | Requirement | Failure it prevents |
| --- | --- | --- |
| `single-deployed-file` | `service.deployed_compose_files` is declared explicitly and names exactly one file (Deploy Contract C1) | A stack layered from several files is deployed differently by every tool that forgets one. |
| `deployed-file-set` | That file uses no `!reset`/`!override` | A merge directive means the file is layered on others, so gating it alone validates a stack nobody runs. |
| `project-name` | A literal top-level `name:` (C2) | Without it Compose names the project after the directory, and every repo's `docker/` collides in one `docker` project. |
| `volume-name` | Every top-level volume has an explicit `name:` (C5) | An unnamed volume follows the project name, so a rename starts the service on an empty volume. |
| `internal-network` | A non-external network has ≥2 services and an `ipam` subnet from the registry (C6) | Each project network takes a Docker default address pool; single-service networks exhausted them on 2026-11-19. |
| `numeric-user` | `user: "<uid>:<gid>"`, this image's own numeric ids | A named or absent user cannot be proven non-root from `/proc`; the controller refuses to deploy it. |
| `restart-policy` | `restart: unless-stopped` on every long-running service, `restart: "no"` on run-once ones | An `on-failure` container does not come back after a host reboot or a Docker upgrade. |
| `no-deploy-restart-policy` | No `deploy.restart_policy`, anywhere | Compose applies `deploy.restart_policy` *instead of* `restart`, silently swapping a reboot-surviving container onto `on-failure`; the runtime observer then reports `restart policy differs from Compose`. |
| `healthcheck-start-period` | A healthcheck with an explicit `start_period` on every non-init service | Without one the deploy cannot wait for readiness, and a slow first start is reported UNHEALTHY and rolled back. |
| `application-image` | `image: ${GF_IMAGE:-${<PROJECT>_IMAGE}}` or `${GF_IMAGE:?...}` (a digest reference at deploy), and no `build:` | A tag or a literal image lets the deploy run code no one pinned; the required variable makes a missing or wrong digest a hard error. |
| `sidecar-image` | A declared sidecar runs exactly its declared third-party image | An undeclared third-party image makes the stack ambiguous, so the controller keys on the wrong service. |
| `single-application-image` | Exactly one image variable across the application services | A two-image stack is refused with `target Compose projection does not name one application image`. |
| `volumes` | Named volumes or tmpfs only; a bind only if `read_only: true` and listed in `deployed_seed_binds` | Host state the release does not carry, or an image-declared `VOLUME`, surfaces as `container mounts are invalid`. |
| `expose` | No published `ports`; every `expose` entry a bare container port, including `container_port` | A published port exposes an unauthenticated backend on the public IP; an undeclared container port reads as `exposed ports differ from Compose`. |
| `cap-drop` | `cap_drop: [ALL]` | Retained capabilities widen a container escape into a host compromise. |
| `read-only-rootfs` | `read_only: true` | A writable root filesystem lets a compromised process persist across restarts. |
| `no-new-privileges` | `security_opt: [no-new-privileges:true]` | Without it a setuid binary inside the image can regain privilege. |
| `no-extension-keys` | No top-level `x-*` key survives the render | `docker compose config` echoes `x-*` back even when it is only a YAML anchor, and the controller's projection rejects any unsupported top-level field. |
| `declared-sidecar-present` | Every declared sidecar exists in the render | A stale declaration no longer describes what is deployed. |

A run-once service is recognised from the render itself: it is *init* if some other service
waits on it with `condition: service_completed_successfully`. Init services are exempt from
`restart: unless-stopped`, from the healthcheck rule, and from the `expose` port requirement.

### `container-release.json` fields

They live under `service`; only `deployed_compose_files` is required by the gate:

| Field | Default | Meaning |
| --- | --- | --- |
| `deployed_compose_files` | none — must be declared | The one standalone Compose file the fleet controller deploys. The gate refuses a missing key or more than one file. |
| `deployed_seed_binds` | `[]` | Container paths that may be `read_only: true` bind mounts (a seed directory staged on the host). Any other bind is refused. |
| `deployed_sidecars` | `[]` | `{"name": ..., "image": ...}` for each service running a third-party image, so the controller can key on the application service. |

Example, for a stack with a Postgres sidecar and a seed bind:

```json
"service": {
  "name": "example-link",
  "compose_files": ["docker/docker-compose.yml", "docker/docker-compose.prod.yml"],
  "deployed_compose_files": ["docker/docker-compose.npm.yml"],
  "deployed_seed_binds": ["/seed"],
  "deployed_sidecars": [{"name": "postgres", "image": "postgres:16-alpine"}]
}
```

### `data.schema_compatibility`

| Field | Default | Meaning |
| --- | --- | --- |
| `schema_compatibility` | `[]` | The data schema versions this application build can serve, as fleet identifiers (`"4"`, `"5.1"`, `"schema-6"`); the fleet controller's data attestation binds a data release to one member of this list and refuses an empty one, so a data-bearing repository that omits it can never activate a data release. |

It is declared inside `data`, in every mode, and the release workflow projects it verbatim
into the published manifest's `data_requirements.schema_compatibility`. Omitting it publishes
`[]`, which is what a code-only service wants.

```json
"data": {
  "mode": "restored-database",
  "release_tag": "data-2026.09.01",
  "digest": "sha256:<64 lowercase hex>",
  "schema_compatibility": ["4"]
}
```

### Opting out

The reusable workflow input `validate_deployed_overlay` defaults to `true`. A repository that
cannot yet meet the contract may call the workflow with `validate_deployed_overlay: false`
**and** a non-empty `deployed_overlay_waiver` stating why; the waiver is required and is
recorded in the run log. There is no silent opt-out.

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

Both rerun modes are supported. "Re-run failed jobs" consumes the sealed evidence the
succeeded jobs produced (artifacts are addressed through the producing job's outputs, kept
for 2 days). "Re-run all jobs" takes the recovery path: `prepare` re-verifies the published
image's provenance and SPDX attestations against this workflow's signer identity, the tag
and its commit, before it imports the existing digest instead of rebuilding.

ARM64 is not a v1 target; enable it only after native dependency resolution plus
platform-specific build, content, vulnerability, runtime, MCP, and data tests pass, and the
release manifest records the multi-platform identities.

## OAuth refresh-rotation observation

Production runs exactly one router process (`genefoundry-router run`) as declared by
the image `CMD`; do not add Uvicorn/Gunicorn workers or mount the same ledger into a
second writable router. The supported `refresh-report` command opens SQLite read-only
and may run while the router is live. This single-writer invariant keeps ownership of
in-flight refresh attempts unambiguous; local ownership expires after twice the stale
attempt threshold, well beyond FastMCP's bounded upstream HTTP timeout.

The production overlay stores `/data/genefoundry/refresh-observability.sqlite3` on the
existing `fastmcp_data:/data` volume. Preserve that file with the OAuth state during deploys
and rollbacks. The ledger is mode `0600`, bounded to 64 MiB plus an 8 MiB WAL, retains at
most 14 days/100,000 event rows, and uses a domain-separated key derived from the
effective OAuth signing key to HMAC client identity. Existing deployments retain FastMCP's deterministic
`GF_OAUTH_CLIENT_SECRET`-derived key when `GF_OAUTH_JWT_SIGNING_KEY` remains unset.
Do not rotate that key during the observation window.

The configured ledger is a durability authority, so an unreadable, empty, oversized, or
unsupported database fails startup rather than silently resetting the evidence window. To
recover, stop the router, move the database and its `-wal`/`-shm` sidecars together to a
private forensic backup, then start the router so it creates a fresh ledger. Record the
reset and restart the observation window; do not replace the file while the router runs.

After the router has run continuously for the decision window, read the aggregate report
without stopping or modifying the ledger:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec genefoundry-router genefoundry-router refresh-report --json
```

Interpret the result exactly as follows:

- `ready` requires at least 7 consecutive days of observation and 50 refresh attempts in
  that 7-day window.
- If 7 days contain fewer than 50 attempts, observation extends to 14 consecutive days.
  Fewer than 50 attempts after 14 days produces `insufficient_sample` rather than a policy
  conclusion.
- A ready sample is `material` only if the combined `reuse_after_rotation` and
  `upstream_invalid_grant` failures number at least 3 **and** exceed 1% of all refresh
  attempts, or at least 2 distinct HMAC-derived clients begin authorization within
  15 minutes after a refresh failure. Otherwise it is `not_material`.

### Reading `refresh_failures_total{reason=...}`

The reason vocabulary distinguishes the five causes that used to collapse into
`jwt_invalid` (98% of all recorded failures before this split). They call for different
actions, so alert on them differently:

| reason | meaning | action |
| --- | --- | --- |
| `jwt_expired` | the refresh token aged out | **none** — expected, high-volume; the client re-authorises. Exclude it from failure alerting or every threshold fires on background noise. |
| `jwt_issuer_mismatch` | token `iss` is not the router's issuer | **page** — configuration fault (2026-08-07 incident class) |
| `jwt_audience_mismatch` | token `aud` is not the router's resource URL | **page** — configuration fault (2026-07-15 incident class) |
| `jwt_signature_invalid` | signed with a different key | **investigate** — signing-key rotation, or a forged token. HS256 is symmetric, so these two are the same observation. |
| `jwt_token_use_mismatch` | an access token was sent as a refresh token | client bug |
| `jwt_malformed` | not a JWT at all | client bug, or an opaque token from another issuer |
| `jwt_invalid` | an unrecognised JOSE error | investigate; also the fallback if a fastmcp upgrade rewords a claim-check message |
| `internal_error` | the verifier itself failed | **page** — ours, not the client's |

A sustained burst of one `jwt_*` reason from one client class is the signal the previous
single bucket could not produce.

**One-way once written.** The `jwt_*` reasons are new to the vocabulary, and the ledger is
durable across deploys. Once a router has written a `jwt_expired` (or any other new reason)
row, an **older** router started against that same ledger fails at startup:
`restore_refresh_metrics` rejects a persisted label outside its own `FAILURE_REASONS`
(`ValueError: persisted OAuth refresh failure labels are not bounded`), and the call sits
unguarded in the lifespan. That is the fail-closed durability behaviour the ledger is
designed around, not a regression — but it means a rollback past this version needs the
ledger moved aside first, using the documented recovery procedure above (stop the router,
move the database and its `-wal`/`-shm` sidecars together to a private forensic backup,
start the router so it creates a fresh ledger, record the reset). Rolling **forward** needs
nothing: existing rows stay valid, and the new reasons simply start appearing.

The report contains aggregates and restart intervals only: it never emits client HMACs,
token hashes/prefixes, request IDs, authorization codes, or query parameters. Strict
one-time refresh-token rotation remains in force while measurements are collected; the
observer does not replay or accept rejected tokens, and the report does not change runtime
policy automatically.

New router tokens use issuer `https://genefoundry.org`. The exact historical
`https://genefoundry.org/` alias is accepted only before the immutable transition deadline
`2026-09-06T00:00:00Z`. Remove the alias after that deadline once live sessions have drained;
do not extend the deadline by restarting or reconfiguring the service. A connector that
cached `https://genefoundry.org/` as the authorization-server identifier can reject the
slashless metadata before presenting a token; if its discovery error reports an issuer
mismatch, disconnect and re-add that connector so it discovers the canonical identifier.

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
