# Scheduled Tool-Definition Drift Detection (CI) — Design Spec

- **Date:** 2026-06-29
- **Status:** Draft for review
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Owner:** Bernt Popp
- **Scope:** An **opt-in** scheduled GitHub Actions tripwire that runs `genefoundry-router drift`
  against the live fleet every 6 hours, alerts on drift via a deduplicated GitHub issue, and uses a
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
schedules after 60 days of inactivity) is itself detected. The whole feature is **opt-in and
configurable** — it must not impose itself on forks/other operators.

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
- **Opt-in & configurable.** Shipping the capability must not force it on forks/other operators.
  Whether the scheduled check runs, the alert channels (issue, heartbeat), the cadence, the fleet
  URLs, and the baseline path are all configuration — and the `drift` CLI stays usable standalone
  (anyone can wire it into their own cron/CI independently of the provided workflow).

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
| Watcher-died detection | **Dead-man's-switch (healthchecks.io, hosted free)** | A scheduled check can stop silently (GH drops runs under load; disables after 60 days). A heartbeat the monitor *expects* turns "didn't run" into an alert. Hosted because a watchman should be **independent of what it watches**: external is independent of both GitHub and the VPS, needs zero maintenance (no "watch the watchman" recursion), and the ping is non-sensitive. Open-source → self-hostable later if ever required. |
| Trusted baseline | **Reuse `tests/fixtures/fleet_manifest.json`** | No new artifact. ⚠️ It is also the discoverability snapshot — see §9 for the coupling discipline. |
| Frequency | **Every 6 hours, off-peak (`17 */6 * * *`)** | Drift-detection norm is "daily at minimum"; 6h gives a tighter window at trivial cost. `:17` avoids GH's `:00` cron congestion. |

Sources: [OWASP MCP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html),
[SAIF](https://blog.google/innovation-and-ai/technology/safety-security/introducing-googles-secure-ai-framework/),
[healthchecks.io](https://healthchecks.io/docs/monitoring_cron_jobs/),
[GitHub schedule docs](https://docs.github.com/en/actions/reference/events-that-trigger-workflows),
[drift-detection cadence](https://spacelift.io/blog/drift-detection).

## 5. Architecture & data flow

```
GitHub Actions (schedule: 17 */6 * * *, + workflow_dispatch)   ── runs on the default branch only
   │  gate: vars.DRIFT_ENABLED == 'true' (or manual dispatch) else no-op
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
curl $DRIFT_HEARTBEAT_URL   ── missing ping ⇒ healthchecks.io alerts (watcher died)
```

## 6. Components

### 6.1 `drift` CLI refinement — reachable vs unreachable (the one code change)
`_snapshot_live` currently skips unreachable backends silently, so an outage makes their tools look
**removed** → a false rug-pull alert. Refine it to return `(live_manifest, unreachable: set[str])`,
with a light per-backend retry (e.g. 2 attempts) and a bounded per-backend timeout (so one hung
backend can't exhaust the job's wall-clock or miss the heartbeat) before declaring a backend
unreachable. *Unreachable* also covers an **enabled-but-unconfigured** backend (no `GF_*_URL` in the
environment): it joins the `unreachable` set rather than vanishing from the live manifest, so a
missing URL surfaces as an availability warning, never as a false `removed`. The `drift`
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
- **Opt-in gate:** the job runs only when the repo variable `vars.DRIFT_ENABLED == 'true'`, or the
  run was triggered manually (`workflow_dispatch`). Unset → scheduled runs are a no-op, so forks and
  clones neither open issues nor ping the canonical fleet. The canonical repo sets `DRIFT_ENABLED=true`.
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
**Optional:** gated on `vars.DRIFT_OPEN_ISSUE != 'false'` (default on); set it `false` to rely only on
the red scheduled-run + owner email.

### 6.5 Dead-man's-switch heartbeat
Final step, `if: always()`: `curl -fsS -m 10 --retry 3 -o /dev/null "$DRIFT_HEARTBEAT_URL"`. The ping
means "the job ran," independent of the drift result (drift is signalled by the issue). The target is
a **healthchecks.io** check (hosted free tier): it expects a ping each scheduled period and, if one
fails to arrive, notifies you. Configure it for a 6h period + ~45 min grace (covers GH cron delay
≤30 min + run time). `DRIFT_HEARTBEAT_URL` is a repo secret holding the check's ping URL; the step is
skipped gracefully if unset — the heartbeat is therefore fully optional. The CI side is
**tool-agnostic** (it only curls a URL), so the monitor can be swapped (e.g. a self-hosted
healthchecks.io instance) without touching the workflow.

**Why external, not on-VPS:** a watchman should be independent of what it watches. A hosted check is
independent of *both* GitHub and the VPS, so it keeps alerting even in compound failures, needs zero
maintenance (no "who watches the watchman" recursion to solve), and the ping carries no sensitive
data ("a run happened"). healthchecks.io is open-source, so it can be self-hosted later if a
third-party heartbeat ever becomes undesirable.

## 7. Configuration surface (opt-in; no code edits to enable/disable/re-target)

| Knob | Kind | Required? | Default | Effect |
|---|---|---|---|---|
| `DRIFT_ENABLED` | repo variable | to enable scheduled runs | unset (off) | gates the scheduled job; `workflow_dispatch` runs regardless. Forks stay off. |
| `ci/fleet-urls.env` | committed file | to run the check | the genefoundry fleet's public `GF_*_URL`s | the backends drift queries; operators edit for their own fleet. Covered by a sync test. |
| baseline manifest | `--manifest` flag in the workflow | no | `tests/fixtures/fleet_manifest.json` | the pinned known-good snapshot |
| servers file | `--servers-file` flag | no | `servers.yaml` | which backends are in scope |
| schedule / cadence | `cron:` in the workflow | no | `17 */6 * * *` (6h) | edit to change frequency |
| `DRIFT_OPEN_ISSUE` | repo variable | no | `true` | open/update the `tool-drift` issue, vs rely on the red run/email only |
| `DRIFT_HEARTBEAT_URL` | repo secret | no | unset → heartbeat skipped | dead-man's-switch ping URL (healthchecks.io) |
| `GITHUB_TOKEN` | built-in | when issues on | `issues: write` | issue automation |
| backend auth | — | none today | — | if the fleet later needs auth, add a CI token secret; **never** the caller's token (no passthrough) |

The `drift` CLI is independently runnable (`genefoundry-router drift --servers-file … --manifest …`)
for operators who prefer their own cron/CI over the bundled workflow.

## 8. Failure modes & handling

| Event | Detected by | Response |
|---|---|---|
| Tool definition changed/added/removed (reachable) | `drift` exit 1 | `tool-drift` issue + red run + email |
| Backend unreachable (outage) | `drift` exit 2 | `::warning::`, green run — **not** a drift alert |
| Scheduled run dropped by GH / workflow disabled (60-day) | missing heartbeat | healthchecks.io alerts |
| GH-wide Actions outage | missing heartbeat | healthchecks.io alerts |
| Baseline legitimately stale after an approved backend change | drift exit 1 (expected) | review, then re-pin via PR (§9) |

## 9. Baseline lifecycle (re-pin discipline)

Because the baseline is the shared `tests/fixtures/fleet_manifest.json`:
- The CI compares live vs the committed manifest on the default branch.
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
- **Unit (config sync):** `ci/fleet-urls.env` defines a `GF_*_URL` for **exactly** the enabled
  backends in `servers.yaml` — none missing, and none left behind for a disabled/unknown backend.
- **Presence/lint:** tests assert `.github/workflows/drift.yml` exists with the expected triggers,
  the `DRIFT_ENABLED` opt-in gate and heartbeat secret; **least-privilege** permissions (exactly
  `contents: read` + `issues: write`, no broad grants); **every external `uses:` SHA-pinned**; a
  **fail-safe `always()` heartbeat**; and that the fleet URLs load through a `grep` filter (not a raw
  `cat`) so comment lines never reach `$GITHUB_ENV`.
- **Manual:** first `workflow_dispatch` run validates the YAML, URL loading, issue path (force a
  synthetic drift by temporarily editing the baseline in a scratch run), and the heartbeat ping.
- Whole change must pass `make ci-local`.

## 11. Security considerations

- The workflow only **reads** backend tool lists; no caller token is involved and none is forwarded
  (consistent with the router's no-passthrough invariant).
- `permissions:` is least-privilege (`contents: read`, `issues: write`).
- The heartbeat ping carries no sensitive data ("a run happened"). The monitor is external
  (healthchecks.io); it can be self-hosted later if a third-party heartbeat is undesirable. The ping
  URL is a secret only to prevent spoofed pings.
- The drift issue body contains tool names/old-vs-new fingerprints and changed field summaries —
  reference metadata, no patient data.

## 12. Operational runbook

1. **Setup (one-time):** create a healthchecks.io check (period 6h, grace ~45 min) and copy its ping
   URL into the `DRIFT_HEARTBEAT_URL` repo secret; create the `tool-drift` GitHub label; set the
   `DRIFT_ENABLED=true` repo variable to turn on scheduled runs.
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
- This is a **single-repo** change (workflow + CLI refinement + `ci/fleet-urls.env`); the heartbeat
  monitor is an external healthchecks.io check referenced only by the `DRIFT_HEARTBEAT_URL` secret.
- **Future (option 2):** wire drift into the router's startup + `PollingRefresher` relist for
  real-time, per-change detection (the "continuous" leg of the hybrid; OWASP's "re-hash before each
  execution"). This CI tripwire is the "scheduled scan" leg.

## 14. Open questions

1. Should *persistent* unreachability (exit 2 for N consecutive runs) escalate to its own
   availability issue, or stay log-only? **Default: log-only** (availability is out of scope here).
2. ~~Self-host vs hosted~~ **Resolved: hosted healthchecks.io free tier** (independent watchman, zero
   maintenance, non-sensitive ping); open-source, so self-hostable later if a third-party heartbeat
   ever becomes undesirable.
