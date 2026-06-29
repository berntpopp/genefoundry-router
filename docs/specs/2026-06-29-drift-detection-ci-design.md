# Scheduled Tool-Definition Drift Detection (CI) — Design Spec

- **Date:** 2026-06-29
- **Status:** Draft for review
- **Owner:** Bernt Popp
- **Scope:** A scheduled GitHub Actions tripwire that runs `genefoundry-router drift` against the
  live fleet every 6 hours, alerts on drift via a deduplicated GitHub issue, and uses a
  dead-man's-switch heartbeat so a dropped/disabled run self-alerts.
- **Boundary:** Research use only; not clinical decision support.

## 1. Summary

The router already ships a tool-definition **drift detector** (`genefoundry_router/drift.py`) and a
`genefoundry-router drift` CLI that fingerprints each backend tool's `{name, description,
inputSchema}` (SHA-256) and diffs a live snapshot against a committed baseline. Today nothing runs
it, so it protects nothing. This spec wires it into a **scheduled CI tripwire**: every 6 hours a
GitHub Actions workflow snapshots the live fleet, compares it to the pinned baseline, and — on any
added/removed/changed tool — opens or updates a GitHub issue. Every run pings a healthchecks.io
dead-man's-switch so that a *missing* run (GitHub silently drops cron runs under load and disables
schedules after 60 days of inactivity) is itself detected.

## 2. Background & threat

The MCP spec secures auth and transport but does **not** mandate tool-definition *integrity*. A
backend can serve a clean tool at review time and later change its **description or input schema**;
because a tool description is, in effect, instructions the model reads and may obey, that change is
the channel for a **rug pull** (definition swapped after approval) and **tool poisoning** (hidden
instructions injected into a description/schema).

OWASP's MCP Security Cheat Sheet is explicit: *"Pin tool definitions using cryptographic hashes and
alert on any changes (prevents rug pulls)"* and *"Monitor for changes to tool definitions
post-installation."* Google's SAIF calls for "Extend detection and response" and "Automate
defenses." This feature is the automated detection-and-alerting half of that guidance; the
fingerprinting half already exists in `drift.py`.

## 3. Goals / Non-goals

**Goals**
- Detect, automatically and on a schedule, when any live backend's tool definitions diverge from the
  reviewed baseline.
- Alert a human through a deduplicated, auto-closing GitHub issue (plus the red scheduled-run email).
- Detect the tripwire's *own* silent failure (dropped/disabled run) via a dead-man's-switch.
- Distinguish a **tampered** backend (security) from an **unreachable** backend (availability) so
  outages do not produce false rug-pull alerts (alert fatigue is what kills tripwires).

**Non-goals (this spec)**
- The in-router, per-relist / per-execution drift check (the "continuous" leg). Tracked as future
  work (§13); OWASP's "re-hash before each tool execution" is that path, not this one.
- Auto-remediation or auto-re-pinning. Refreshing the baseline stays a deliberate, reviewed human
  action — auto-refresh would disarm the tripwire.
- Backend availability/uptime monitoring as a product (handled by per-backend `/health`); this
  feature only needs to *not misclassify* an outage as drift.

## 4. Decisions (locked) + rationale

| Decision | Choice | Why |
|---|---|---|
| Where it runs / how it alerts | **GitHub Actions cron → auto GitHub issue** | Zero infra, lives with the code, the backends are public and reachable from GH runners; a red scheduled run also emails the workflow owner. |
| Watcher-died detection | **Dead-man's-switch, self-hosted (Uptime Kuma on the VPS)** | A scheduled check can stop silently (GH drops runs under load; disables after 60 days). A heartbeat the monitor *expects* turns "didn't run" into an alert. Self-hosted via `strato_v6_docker_npm` → no third-party dependency, fits the on-prem/data-residency story, reuses the existing NPM + Telegram alerting. Independence caveat in §6.5/§11. |
| Trusted baseline | **Reuse `tests/fixtures/fleet_manifest.json`** | No new artifact. ⚠️ It is also the discoverability snapshot — see §9 for the coupling discipline. |
| Frequency | **Every 6 hours, off-peak (`17 */6 * * *`)** | Drift-detection norm is "daily at minimum"; 6h gives a tighter window at trivial cost. `:17` avoids GH's `:00` cron congestion. |

Sources: [OWASP MCP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html),
[SAIF](https://blog.google/innovation-and-ai/technology/safety-security/introducing-googles-secure-ai-framework/),
[healthchecks.io](https://healthchecks.io/docs/monitoring_cron_jobs/),
[GitHub schedule docs](https://docs.github.com/en/actions/reference/events-that-trigger-workflows),
[drift-detection cadence](https://spacelift.io/blog/drift-detection).

## 5. Architecture & data flow

```
GitHub Actions (schedule: 17 */6 * * *, + workflow_dispatch)   ── runs on master only
   │  load ci/fleet-urls.env  → GF_*_URL (public, non-secret)
   ▼
genefoundry-router drift --manifest tests/fixtures/fleet_manifest.json
   │  for each enabled backend: fastmcp Client → list_tools  (with light retry)
   │  split: reachable → fingerprints ; unreachable → set
   │  diff reachable fingerprints vs baseline (unreachable namespaces excluded both sides)
   ▼  exit code
 0 no drift, all reachable ──► (close any open `tool-drift` issue) ; heartbeat ping
 2 no drift, ≥1 unreachable ─► ::warning:: annotation (job green) ; heartbeat ping
 1 drift among reachable ────► open/append `tool-drift` issue ; job RED ; heartbeat ping
   │
   ▼ (always, if: always())
curl $HEALTHCHECKS_PING_URL   ── missing ping ⇒ healthchecks.io alerts (watcher died)
```

## 6. Components

### 6.1 `drift` CLI refinement — reachable vs unreachable (the one code change)
`_snapshot_live` currently skips unreachable backends silently, so an outage makes their tools look
**removed** → a false rug-pull alert. Refine it to return `(live_manifest, unreachable: set[str])`,
with a light per-backend retry (e.g. 2 attempts) before declaring a backend unreachable. The `drift`
command then:
- diffs only the **reachable** namespaces (filter the baseline to the reachable set so unreachable
  backends are excluded from *both* sides, never reported as `removed`);
- exit-code semantics (security takes precedence over availability):
  - **1** — any added/removed/changed tool among reachable backends (drift);
  - **2** — no drift, but ≥1 backend unreachable;
  - **0** — no drift, all reachable.
- prints the `CHANGED/ADDED/REMOVED` lines (drift) and a separate `UNREACHABLE: <ns…>` line.
`drift.py`'s pure functions are unchanged; the orchestration/filtering lives in the CLI.

### 6.2 `ci/fleet-urls.env` (new, committed, non-secret)
The live URLs follow `https://<name>-link.genefoundry.org/mcp` and are **public**, so they are
committed, not secret: one `GF_<NAME>_URL=…` line per enabled backend, using the **exact** `url_env`
names from `servers.yaml`. The workflow injects them via `cat ci/fleet-urls.env >> "$GITHUB_ENV"`. A
unit test asserts the file covers every enabled backend's `url_env` (keeps the two in lockstep).

### 6.3 `.github/workflows/drift.yml` (new)
- `on: { schedule: [{cron: "17 */6 * * *"}], workflow_dispatch: {} }`.
- `permissions: { contents: read, issues: write }`; `concurrency` group to prevent overlap.
- SHA-pinned `actions/checkout`, `actions/setup-python` (3.12), `astral-sh/setup-uv` — versions
  matched to `ci.yml`. `uv sync --frozen --no-dev` (runtime deps suffice for the CLI).
- Steps: checkout → setup → load `ci/fleet-urls.env` → run `drift` (capture stdout + exit code) →
  issue management (§6.4) → heartbeat (§6.5, `if: always()`).

### 6.4 Issue management (deduplicated, auto-closing)
Using `gh` + the built-in `GITHUB_TOKEN`:
- **exit 1:** find an open issue labeled `tool-drift`; if present `gh issue comment` with the new
  diff, else `gh issue create --label tool-drift --title "🚨 Tool-definition drift detected"` with
  the diff body; then fail the job (red run + owner email).
- **exit 0:** if an open `tool-drift` issue exists, close it with a "drift resolved / re-pinned"
  comment.
- **exit 2:** emit a `::warning::` annotation listing the unreachable backends; job stays green; do
  **not** touch the drift issue.
One issue at a time — no per-run spam; red runs continue until the baseline is re-pinned (§9).

### 6.5 Dead-man's-switch heartbeat (self-hosted)
Final step, `if: always()`: `curl -fsS -m 10 --retry 3 -o /dev/null "$DRIFT_HEARTBEAT_URL"`. The ping
means "the job ran," independent of the drift result (drift is signalled by the issue). The target is
a **self-hosted monitor** deployed on the VPS via `strato_v6_docker_npm` — recommended:
**Uptime Kuma** (single container; its "Push" monitor *is* a dead-man's-switch — it expects a push
every period and marks the monitor DOWN + notifies, e.g. via Telegram, when one is missed; it can
*also* actively poll the fleet `/health` endpoints, so one tool covers both drift-heartbeat and fleet
uptime). Configure the push monitor for a 6h period + ~45 min grace (covers GH cron delay ≤30 min +
run time). `DRIFT_HEARTBEAT_URL` is a repo secret holding the monitor's push URL; the step is skipped
gracefully if unset (so forks/PRs without the secret don't error). The CI side is **tool-agnostic** —
it only curls a URL — so the monitor can be swapped (self-hosted healthchecks.io, etc.) without
touching the workflow.

**Independence caveat (important).** A monitor cannot detect the death of its own host. Running the
monitor on the same VPS as the fleet means it is independent of GitHub (so it *does* catch a
dropped/disabled GH run, GH outages, etc.) but shares a failure domain with the fleet: if the **VPS
itself** is down, the monitor is down too and cannot alert. That compound case is acceptable for this
tripwire's purpose — a rug pull is moot when the fleet is offline — and a VPS outage is a louder,
separately-monitored event. To close even that gap, add one **minimal external watcher** whose only
job is "is the VPS/monitor alive" (e.g. a free UptimeRobot check hitting the monitor's status page,
or the monitor pushing its own heartbeat to a free hosted healthchecks check). This is the one
irreducible external dependency; see §14 open question 2.

## 7. Configuration & secrets

| Item | Kind | Notes |
|---|---|---|
| `ci/fleet-urls.env` | committed, non-secret | public `GF_*_URL` values; covered by a sync test |
| `DRIFT_HEARTBEAT_URL` | repo secret | self-hosted monitor push URL (Uptime Kuma on the VPS); step no-ops if unset |
| `GITHUB_TOKEN` | built-in | needs `issues: write` for §6.4 |
| backend auth | none today | if the fleet later requires auth, add a CI token secret + send it; **not** the caller's token (no passthrough) |

## 8. Failure modes & handling

| Event | Detected by | Response |
|---|---|---|
| Tool definition changed/added/removed (reachable) | `drift` exit 1 | `tool-drift` issue + red run + email |
| Backend unreachable (outage) | `drift` exit 2 | `::warning::`, green run — **not** a drift alert |
| Scheduled run dropped by GH / workflow disabled (60-day) | missing heartbeat | self-hosted monitor (Uptime Kuma) alerts (e.g. Telegram) |
| GH-wide Actions outage | missing heartbeat | self-hosted monitor alerts |
| VPS (and thus the monitor) down | optional external watcher / fleet uptime monitoring | out of scope for this heartbeat — see §6.5 independence caveat |
| Baseline legitimately stale after an approved backend change | drift exit 1 (expected) | review, then re-pin via PR (§9) |

## 9. Baseline lifecycle (re-pin discipline)

Because the baseline is the shared `tests/fixtures/fleet_manifest.json`:
- The CI compares live vs the committed manifest on `master`.
- When drift fires for a **legitimate** backend change, refresh the baseline with
  `make snapshot-fleet` and merge it via a **reviewed PR**; the reviewer treats the tool-def diff as
  security-relevant. This re-pins the tripwire.
- ⚠️ **Coupling caveat:** the same file is refreshed for discoverability/search tuning. Any PR that
  touches `fleet_manifest.json` — for *either* reason — moves the security baseline, so the diff must
  be reviewed as a security change. (A dedicated baseline file would remove this coupling; deferred
  per the chosen design.)
- Never auto-refresh the pin in CI — that would silently bless a rug pull.

## 10. Testing strategy (TDD)

- **Unit (`drift`/CLI):** reachable-only diffing; unreachable backend is reported separately and
  does **not** appear as `removed`; exit codes 0/1/2 incl. the drift-and-unreachable precedence
  (→ 1). Monkeypatch `_snapshot_live` to return crafted `(manifest, unreachable)` pairs.
- **Unit (config sync):** `ci/fleet-urls.env` defines a `GF_*_URL` for every enabled backend in
  `servers.yaml` (and no stale extras).
- **Presence/lint:** a test asserts `.github/workflows/drift.yml` exists with the expected trigger +
  `issues: write` permission (mirrors the repo's existing presence tests).
- **Manual:** first `workflow_dispatch` run validates the YAML, URL loading, issue path (force a
  synthetic drift by temporarily editing the baseline in a scratch run), and the heartbeat ping.
- Whole change must pass `make ci-local`.

## 11. Security considerations

- The workflow only **reads** backend tool lists; no caller token is involved and none is forwarded
  (consistent with the router's no-passthrough invariant).
- `permissions:` is least-privilege (`contents: read`, `issues: write`).
- The heartbeat ping carries no sensitive data ("a run happened"). The monitor is **self-hosted** on
  the VPS (`strato_v6_docker_npm`) → no third-party dependency, supporting the on-prem/data-residency
  posture. The one residual external touchpoint is the optional VPS host-watcher (§6.5).
- The drift issue body contains tool names/old-vs-new fingerprints and changed field summaries —
  reference metadata, no patient data.

## 12. Operational runbook

1. **Setup (one-time):** deploy Uptime Kuma on the VPS via `strato_v6_docker_npm` (add a
   `uptime_kuma` entry to `config/projects.yaml` + an NPM proxy host via
   `scripts/setup_npm_hosts.py`, e.g. `status.genefoundry.org`); create a **Push** monitor
   (period 6h, grace ~45 min) with a Telegram notification; copy its push URL into the
   `DRIFT_HEARTBEAT_URL` repo secret; create the `tool-drift` GitHub label. Optionally add a minimal
   external watcher for the VPS host itself (§6.5).
2. **On a `tool-drift` issue:** inspect the diff. Legit upstream change → re-pin (§9). Unexpected →
   treat as a possible rug pull: disable/roll back the backend, investigate, then re-pin.
3. **On a healthchecks.io alert:** the tripwire stopped running — check Actions (disabled? GH
   outage?), re-enable, confirm the next run pings.

## 13. References & future work

- Existing code: `genefoundry_router/drift.py`, `genefoundry_router/cli.py` (`drift`,
  `_snapshot_live`), `scripts/snapshot_fleet.py`, `tests/fixtures/fleet_manifest.json`,
  `.github/workflows/ci.yml` (conventions to mirror).
- Sources: OWASP MCP Security Cheat Sheet; Google SAIF; healthchecks.io docs; GitHub `schedule`
  event docs; Spacelift drift-detection guide (all linked in §4).
- **Cross-repo:** the workflow + CLI changes live in `genefoundry-router`; the self-hosted monitor
  is deployed in `strato_v6_docker_npm` (new `uptime_kuma` project entry in `config/projects.yaml` +
  an NPM proxy host). The implementation plan spans both repos but the genefoundry-router side is
  self-contained (it only needs the `DRIFT_HEARTBEAT_URL` secret).
- **Future (option 2):** wire drift into the router's startup + `PollingRefresher` relist for
  real-time, per-change detection (the "continuous" leg of the hybrid; OWASP's "re-hash before each
  execution"). This CI tripwire is the "scheduled scan" leg.

## 14. Open questions

1. Should *persistent* unreachability (exit 2 for N consecutive runs) escalate to its own
   availability issue, or stay log-only? **Default: log-only** (availability is out of scope here).
2. ~~Self-host vs hosted~~ **Resolved: self-host — Uptime Kuma on the VPS via `strato_v6_docker_npm`.**
   Residual sub-decision: add a minimal external watcher for the VPS host itself (closes the §6.5
   independence gap) vs accept it (rug-pull detection is moot when the fleet is down). **Default:
   accept for v1; add the external watcher if/when host-down self-alerting is required.**
