# Scheduled Tool-Definition Drift Detection (CI) — Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing `genefoundry-router drift` tripwire run automatically every 6 hours via an opt-in GitHub Actions workflow that alerts on tool-definition drift through a deduplicated GitHub issue and a dead-man's-switch heartbeat.

**Architecture:** One small code change (the `drift` CLI learns to tell a *tampered* backend from an *unreachable* one, via exit codes 0/1/2) plus two new files (a committed non-secret `ci/fleet-urls.env` and an opt-in `.github/workflows/drift.yml`). The workflow loads the fleet URLs, runs the CLI against the pinned `tests/fixtures/fleet_manifest.json`, manages a single `tool-drift` issue, and always pings a healthchecks.io heartbeat.

**Tech Stack:** Python 3.12 + `uv`, `typer`/`rich` CLI, `fastmcp` Client, pydantic models in `genefoundry_router/devtools/fakes.py`, GitHub Actions, `gh` CLI.

Spec: `docs/specs/2026-06-29-drift-detection-ci-design.md`.

## Global Constraints

- Python **3.12+**; deps via **uv** (`uv sync`, `uv run`). `ruff` + `mypy` must pass; **600-LOC/module** budget. Run `make ci-local` before each commit that touches Python.
- **TDD**: write the failing test, watch it fail, implement minimally, watch it pass, commit. One atomic commit per task.
- GitHub Actions: **pin actions by commit SHA** (copy the exact SHAs already used in `.github/workflows/ci.yml`). Least-privilege `permissions`.
- **No token passthrough**: the workflow only reads backend tool lists; never send the caller's token to backends.
- Opt-in: the scheduled job must be a **no-op** unless `vars.DRIFT_ENABLED == 'true'` (or manual dispatch).

## File Structure

- `genefoundry_router/cli.py` — MODIFY: add a `TYPE_CHECKING` import of `Manifest`; `_snapshot_live` returns `(Manifest, set[str])` with bounded per-backend timeouts + a no-URL→unreachable guard; `drift` does reachable-only diffing + exit codes 0/1/2 (no `type: ignore`). (Already contains both; this refines them.)
- `tests/unit/test_cli_drift.py` — MODIFY: update fakes to the new tuple return; add unreachable + changed-tool cases.
- `ci/fleet-urls.env` — CREATE: committed, non-secret `GF_*_URL` lines for every enabled backend.
- `tests/unit/test_ci_fleet_urls.py` — CREATE: asserts the env file covers exactly the enabled backends' `url_env`s.
- `.github/workflows/drift.yml` — CREATE: the opt-in scheduled workflow.
- `tests/unit/test_drift_workflow_present.py` — CREATE: presence/gating assertions for the workflow.

`genefoundry_router/drift.py` is unchanged (its pure functions already do the fingerprint/diff).

---

### Task 1: `drift` CLI — distinguish tampered (drift) from unreachable (availability)

**Files:**
- Modify: `genefoundry_router/cli.py` (`_snapshot_live`, `drift`)
- Test: `tests/unit/test_cli_drift.py`

**Interfaces:**
- Consumes: `genefoundry_router.devtools.fakes.{Manifest, BackendSpec, ToolSpec, SnapshotMeta, load_manifest}`; `genefoundry_router.drift.diff_manifests`.
- Produces: `_snapshot_live(registry, attempts: int = 2) -> tuple[Manifest, set[str]]` (reachable-only manifest, set of unreachable namespaces); `drift` command exit codes **0** (no drift, all reachable), **1** (drift among reachable), **2** (no drift, ≥1 unreachable).

- [ ] **Step 1: Replace the drift CLI tests with the new contract (failing test)**

Overwrite `tests/unit/test_cli_drift.py`:

```python
"""`genefoundry-router drift` — drift vs unreachable, with exit codes 0/1/2."""

from pathlib import Path

from typer.testing import CliRunner

from genefoundry_router.cli import app
from genefoundry_router.devtools.fakes import load_manifest

runner = CliRunner()
PINNED = Path("tests/fixtures/fleet_manifest.json")


def test_drift_ok_when_live_matches_pinned(monkeypatch):
    async def fake(_registry):
        return load_manifest(PINNED), set()

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 0, result.output
    assert "no tool-definition drift" in result.output.lower()


def test_changed_tool_exits_1(monkeypatch):
    live = load_manifest(PINNED).model_copy(deep=True)
    ns = next(iter(live.backends))
    live.backends[ns].tools[0].description += " <IMPORTANT>tampered</IMPORTANT>"

    async def fake(_registry):
        return live, set()

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 1
    assert "CHANGED" in result.output


def test_unreachable_is_not_drift_exits_2(monkeypatch):
    pinned = load_manifest(PINNED)
    gone = next(iter(pinned.backends))
    live = pinned.model_copy(
        update={"backends": {k: v for k, v in pinned.backends.items() if k != gone}}
    )

    async def fake(_registry):
        return live, {gone}

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 2  # availability, not a rug-pull
    assert "UNREACHABLE" in result.output
    assert "REMOVED" not in result.output  # the unreachable backend is NOT reported as removed


def test_drift_takes_precedence_over_unreachable(monkeypatch):
    live = load_manifest(PINNED).model_copy(deep=True)
    names = list(live.backends)
    live.backends[names[0]].tools[0].description += " tampered"
    gone = names[1]
    del live.backends[gone]

    async def fake(_registry):
        return live, {gone}

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 1  # security beats availability
```

- [ ] **Step 2: Run the tests; verify they fail**

Run: `uv run pytest tests/unit/test_cli_drift.py -q`
Expected: FAIL — `_snapshot_live` returns a `Manifest`, not a tuple (current `drift` does `report = diff_manifests(pinned, live)` with `live` a coroutine-result Manifest), so unpacking/exit codes don't match (exit 2 path doesn't exist yet).

- [ ] **Step 3: Update `_snapshot_live` — typed tuple, no-URL guard, bounded timeouts, retry**

First, add a `TYPE_CHECKING` import near the top of `genefoundry_router/cli.py` (e.g. just below `import sys`) so the new return annotation resolves under mypy without importing fastmcp at module load:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genefoundry_router.devtools.fakes import Manifest
```

Then replace the body of `_snapshot_live` with:

```python
async def _snapshot_live(
    registry: list[BackendDef], attempts: int = 2
) -> tuple[Manifest, set[str]]:
    """Snapshot reachable backends' tools; return (live_manifest, unreachable_namespaces).

    A backend is *unreachable* if it is enabled but has no URL, or if listing its tools
    fails after ``attempts`` tries. Unreachable backends are excluded from the manifest and
    reported separately, so an outage (or a missing ``GF_*_URL``) is never mistaken for a
    removed tool. Per-backend timeouts keep one hung backend from stalling the whole run.
    """
    from fastmcp import Client

    from genefoundry_router.devtools.fakes import (
        BackendSpec,
        Manifest,
        SnapshotMeta,
        ToolSpec,
    )

    backends: dict[str, BackendSpec] = {}
    unreachable: set[str] = set()
    for b in registry:
        if not b.enabled:
            continue
        if not b.url:  # enabled but unconfigured: unreachable, NOT a removed tool
            unreachable.add(b.namespace)
            console.print(f"[yellow]WARN[/yellow] {b.name}: no URL configured ({b.url_env})")
            continue
        tools = None
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                # Bounded so one hung backend can't exceed the CI job timeout.
                async with Client(b.url, timeout=30, init_timeout=10) as client:
                    tools = await client.list_tools()
                break
            except Exception as exc:  # transient: retry, then mark unreachable
                last_exc = exc
        if tools is None:
            unreachable.add(b.namespace)
            console.print(f"[yellow]WARN[/yellow] {b.name} unreachable: {last_exc}")
            continue
        backends[b.namespace] = BackendSpec(
            version=None,
            tools=[
                ToolSpec(
                    name=t.name,
                    description=t.description or "",
                    inputSchema=t.inputSchema or {"type": "object", "properties": {}},
                    tags=list((t.meta or {}).get("fastmcp", {}).get("tags", [])),
                )
                for t in tools
            ],
        )
    manifest = Manifest(
        snapshot_meta=SnapshotMeta(captured_at="live", source="live", router_servers_file=""),
        backends=backends,
    )
    return manifest, unreachable
```

- [ ] **Step 4: Update the `drift` command — reachable-only diff + exit codes 0/1/2**

In `genefoundry_router/cli.py`, replace the body of the `drift` command (from `pinned = load_manifest(...)` onward) with:

```python
    from pathlib import Path

    from genefoundry_router.devtools.fakes import load_manifest
    from genefoundry_router.drift import diff_manifests

    pinned = load_manifest(Path(manifest))
    live, unreachable = asyncio.run(_snapshot_live(load_registry(servers_file, os.environ)))
    # Exclude unreachable backends from BOTH sides so an outage isn't read as "removed".
    pinned_reachable = pinned.model_copy(
        update={"backends": {ns: s for ns, s in pinned.backends.items() if ns not in unreachable}}
    )
    report = diff_manifests(pinned_reachable, live)
    for k in report.changed:
        console.print(f"[red]CHANGED[/red] {k}")
    for k in report.added:
        console.print(f"[yellow]ADDED[/yellow] {k}")
    for k in report.removed:
        console.print(f"[yellow]REMOVED[/yellow] {k}")
    if unreachable:
        console.print(f"[yellow]UNREACHABLE[/yellow]: {', '.join(sorted(unreachable))}")
    if report.has_drift:
        console.print("[red]tool-definition drift detected[/red] — review before refreshing pin")
        raise typer.Exit(1)
    if unreachable:
        console.print("[yellow]no drift, but some backends were unreachable[/yellow]")
        raise typer.Exit(2)
    console.print("[green]OK[/green] no tool-definition drift")
```

- [ ] **Step 5: Run the tests; verify they pass**

Run: `uv run pytest tests/unit/test_cli_drift.py tests/unit/test_drift.py -q`
Expected: PASS (all 4 CLI cases + the existing pure-function tests).

- [ ] **Step 6: Full gate + commit**

Run: `make ci-local`
Expected: format/lint/loc/mypy clean; all tests pass.

```bash
git add genefoundry_router/cli.py tests/unit/test_cli_drift.py
git commit -m "feat(drift): split reachable-vs-unreachable; exit 0/1/2 (no false rug-pull on outage)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `ci/fleet-urls.env` + coverage test

**Files:**
- Create: `ci/fleet-urls.env`
- Test: `tests/unit/test_ci_fleet_urls.py`

**Interfaces:**
- Consumes: `genefoundry_router.config.load_registry`.
- Produces: a committed env file defining `GF_<NAME>_URL` for every enabled backend; loaded by the workflow via `grep -E '^GF_[A-Z0-9_]+=' ci/fleet-urls.env >> "$GITHUB_ENV"` (comment lines filtered out).

- [ ] **Step 1: Write the coverage test (failing)**

Create `tests/unit/test_ci_fleet_urls.py`:

```python
"""ci/fleet-urls.env must define a public URL for exactly the enabled backends."""

import os
import re
from pathlib import Path

from genefoundry_router.config import load_registry


def test_ci_fleet_urls_covers_enabled_backends():
    registry = load_registry("servers.yaml", os.environ)
    enabled = {b.url_env for b in registry if b.enabled}

    text = Path("ci/fleet-urls.env").read_text(encoding="utf-8")
    # Comment lines (``# ...``) are ignored by this regex, so they're free to keep.
    defined = set(re.findall(r"^(GF_[A-Z0-9_]+)=\S+", text, re.MULTILINE))

    # Contract: define a URL for EXACTLY the enabled backends — no more, no less. A URL
    # for a disabled (or unknown) backend is dead weight the probe never reads, so flag it.
    missing = enabled - defined
    extra = defined - enabled
    assert not missing, f"ci/fleet-urls.env missing: {sorted(missing)}"
    assert not extra, f"ci/fleet-urls.env has vars not for enabled backends: {sorted(extra)}"
```

- [ ] **Step 2: Run it; verify it fails**

Run: `uv run pytest tests/unit/test_ci_fleet_urls.py -q`
Expected: FAIL — `ci/fleet-urls.env` does not exist (`FileNotFoundError`).

- [ ] **Step 3: Generate `ci/fleet-urls.env` from `servers.yaml` (exact var names)**

Generate the lines deterministically so the names match `servers.yaml` exactly, then review the hosts:

Run:
```bash
mkdir -p ci
uv run python - <<'PY' > ci/fleet-urls.env
import os
from genefoundry_router.config import load_registry
print("# Public production /mcp URLs for the drift CI (NON-SECRET).")
print("# Loaded by .github/workflows/drift.yml via a grep filter into $GITHUB_ENV")
print("# (comment lines like this one are filtered out, so they're safe to keep).")
print("# Keep in lockstep with servers.yaml (enforced by tests/unit/test_ci_fleet_urls.py).")
for b in load_registry("servers.yaml", os.environ):
    if b.enabled:
        print(f"{b.url_env}=https://{b.repo.split('/')[-1]}.genefoundry.org/mcp")
PY
```

Then **review the generated hosts** against the deployed fleet (the host is derived from each backend's `repo` name, e.g. `gnomad-link.genefoundry.org`, `spliceailookup-link.genefoundry.org`). Fix any that differ from the real deployment. (Correctness here only affects reachability; a wrong host surfaces as an `UNREACHABLE` exit-2 warning on the first run, never as a false drift.)

- [ ] **Step 4: Run the test; verify it passes**

Run: `uv run pytest tests/unit/test_ci_fleet_urls.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ci/fleet-urls.env tests/unit/test_ci_fleet_urls.py
git commit -m "feat(drift): committed non-secret ci/fleet-urls.env + coverage test

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `.github/workflows/drift.yml` (opt-in) + presence test

**Files:**
- Create: `.github/workflows/drift.yml`
- Test: `tests/unit/test_drift_workflow_present.py`

**Interfaces:**
- Consumes: Task 1's exit codes (0/1/2), Task 2's `ci/fleet-urls.env`, repo variables `DRIFT_ENABLED`/`DRIFT_OPEN_ISSUE`, secret `DRIFT_HEARTBEAT_URL`, built-in `GITHUB_TOKEN`.
- Produces: the scheduled tripwire (no further code depends on it).

- [ ] **Step 1: Write the presence/gating test (failing)**

Create `tests/unit/test_drift_workflow_present.py`:

```python
"""The drift workflow exists, is opt-in, least-privilege, SHA-pinned, fail-safe."""

import re
from pathlib import Path

WF = Path(".github/workflows/drift.yml")


def test_drift_workflow_present_and_gated():
    text = WF.read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "DRIFT_ENABLED" in text  # opt-in gate
    assert "DRIFT_HEARTBEAT_URL" in text  # heartbeat
    assert "tool-drift" in text  # dedup label


def test_permissions_are_least_privilege():
    text = WF.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "issues: write" in text
    # No broad grants.
    assert "write-all" not in text
    assert "contents: write" not in text


def test_all_external_actions_are_sha_pinned():
    refs = re.findall(r"uses:\s*(\S+)", WF.read_text(encoding="utf-8"))
    assert refs, "expected at least one external action"
    for ref in refs:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"


def test_heartbeat_is_fail_safe():
    # The dead-man's-switch must fire even when the drift step fails.
    assert re.search(r"always\(\)\s*&&\s*env\.DRIFT_HEARTBEAT_URL", WF.read_text(encoding="utf-8"))


def test_fleet_urls_loaded_via_filter_not_raw_cat():
    text = WF.read_text(encoding="utf-8")
    # Comments in ci/fleet-urls.env must not reach $GITHUB_ENV — load via a grep filter.
    assert "grep -E" in text and "ci/fleet-urls.env" in text
    assert "cat ci/fleet-urls.env" not in text
```

- [ ] **Step 2: Run it; verify it fails**

Run: `uv run pytest tests/unit/test_drift_workflow_present.py -q`
Expected: FAIL — file does not exist.

- [ ] **Step 3: Create the workflow**

First read the pinned action SHAs to reuse: `grep -nE 'uses: (actions|astral)' .github/workflows/ci.yml`. Use those exact `@<sha> # vX` values below (shown with the SHAs currently in `ci.yml`).

Create `.github/workflows/drift.yml`:

```yaml
name: Drift detection

on:
  schedule:
    - cron: "17 */6 * * *" # every 6h at :17 (off-peak; GH drops :00 runs under load)
  workflow_dispatch: {}

permissions:
  contents: read
  issues: write

concurrency:
  group: drift-${{ github.ref }}
  cancel-in-progress: false

jobs:
  drift:
    # Opt-in: scheduled runs only when DRIFT_ENABLED=true; manual dispatch always allowed.
    if: ${{ vars.DRIFT_ENABLED == 'true' || github.event_name == 'workflow_dispatch' }}
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      DRIFT_HEARTBEAT_URL: ${{ secrets.DRIFT_HEARTBEAT_URL }}
    steps:
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - name: Set up Python
        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: "3.12"
      - name: Set up uv
        uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true
          version: "0.8.7"
      - name: Install
        run: uv sync --frozen --no-dev
      - name: Load fleet URLs
        # Filter to GF_*=... assignments only; comment lines must not reach $GITHUB_ENV
        # (GitHub treats the env file as KEY=value pairs, not a dotenv with comments).
        run: grep -E '^GF_[A-Z0-9_]+=' ci/fleet-urls.env >> "$GITHUB_ENV"
      - name: Run drift check
        id: drift
        run: |
          set +e
          uv run genefoundry-router drift --manifest tests/fixtures/fleet_manifest.json \
            > drift_output.txt 2>&1
          echo "exit_code=$?" >> "$GITHUB_OUTPUT"
          cat drift_output.txt
      - name: Open / update drift issue
        if: ${{ steps.drift.outputs.exit_code == '1' && vars.DRIFT_OPEN_ISSUE != 'false' }}
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh label create tool-drift -c FF0000 -d "Tool-definition drift" --force || true
          existing=$(gh issue list --label tool-drift --state open --json number -q '.[0].number')
          if [ -n "$existing" ]; then
            gh issue comment "$existing" -F drift_output.txt
          else
            gh issue create --label tool-drift \
              --title "Tool-definition drift detected" -F drift_output.txt
          fi
      - name: Close resolved drift issue
        if: ${{ steps.drift.outputs.exit_code == '0' && vars.DRIFT_OPEN_ISSUE != 'false' }}
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          for n in $(gh issue list --label tool-drift --state open --json number -q '.[].number'); do
            gh issue comment "$n" --body "Drift resolved / baseline re-pinned. Closing."
            gh issue close "$n"
          done
      - name: Unreachable backends (availability, not drift)
        if: ${{ steps.drift.outputs.exit_code == '2' }}
        run: echo "::warning::drift check: some backends were unreachable (availability, not a rug pull)"
      - name: Heartbeat (dead-man's-switch)
        if: ${{ always() && env.DRIFT_HEARTBEAT_URL != '' }}
        run: curl -fsS -m 10 --retry 3 -o /dev/null "$DRIFT_HEARTBEAT_URL" || true
      - name: Fail the run on drift
        if: ${{ steps.drift.outputs.exit_code == '1' }}
        run: exit 1
```

- [ ] **Step 4: Run the test; verify it passes**

Run: `uv run pytest tests/unit/test_drift_workflow_present.py -q`
Expected: PASS.

- [ ] **Step 5: Lint the YAML locally (optional but recommended)**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/drift.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 6: Full gate + commit**

Run: `make ci-local`
Expected: PASS.

```bash
git add .github/workflows/drift.yml tests/unit/test_drift_workflow_present.py
git commit -m "feat(drift): opt-in scheduled drift workflow (issue + heartbeat)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Wire-up & first live run (operator)

**Files:** none (GitHub settings + healthchecks.io). Do this after Tasks 1–3 merge to the default branch (scheduled workflows only run there).

- [ ] **Step 1:** Create a healthchecks.io check (period 6h, grace 45 min); put its ping URL in repo secret `DRIFT_HEARTBEAT_URL`.
- [ ] **Step 2:** Set repo variable `DRIFT_ENABLED=true` (and optionally `DRIFT_OPEN_ISSUE`, default on).
- [ ] **Step 3:** Trigger a manual run: `gh workflow run drift.yml`. Confirm: it lists tools, exits 0 (or 2 if a host is wrong → fix `ci/fleet-urls.env`), and the healthchecks.io check goes green.
- [ ] **Step 4 (drill):** On a scratch branch, edit one tool's `description` in `tests/fixtures/fleet_manifest.json`, dispatch the workflow, confirm a `tool-drift` issue opens; revert and confirm it closes on the next clean run.

---

## Self-Review

**Spec coverage:**
- §6.1 reachable/unreachable + exit codes → Task 1. ✓
- §6.2 `ci/fleet-urls.env` + sync test → Task 2. ✓
- §6.3 workflow (triggers, opt-in gate, permissions, SHA-pinned, steps) → Task 3. ✓
- §6.4 issue dedup/auto-close + `DRIFT_OPEN_ISSUE` → Task 3 workflow. ✓
- §6.5 heartbeat (optional, `if: always()`, `DRIFT_HEARTBEAT_URL`) → Task 3. ✓
- §7 config knobs (`DRIFT_ENABLED`, `DRIFT_OPEN_ISSUE`, `--manifest`, `--servers-file`, cron) → Task 3 + CLI flags (already exist). ✓
- §8 failure modes (1/2/missing-heartbeat) → Tasks 1+3. ✓
- §10 tests (CLI exit codes, env sync, workflow presence) → Tasks 1–3. ✓
- §12 runbook setup → Task 4. ✓

**Placeholder scan:** none — every code/YAML/test block is concrete.

**Type consistency:** `_snapshot_live -> tuple[Manifest, set[str]]` (resolved via the `TYPE_CHECKING` import) is unpacked as `live, unreachable` in `drift`; `backends: dict[str, BackendSpec]` matches `Manifest.backends`, so no `type: ignore` is needed on the `Manifest(...)` construction or the `diff_manifests(pinned_reachable, live)` call; `report.{changed,added,removed,has_drift}` match `DriftReport`. Test fakes return the `(Manifest, set)` tuple to match. ✓

## Review incorporated (codex pass, 2026-06-30)

A pre-implementation review surfaced six issues; five are folded into the tasks above, the sixth is intentionally not applied:

1. **`$GITHUB_ENV` would choke on comment lines (High)** → Task 3 loads `ci/fleet-urls.env` with `grep -E '^GF_[A-Z0-9_]+=' … >> "$GITHUB_ENV"` instead of a raw `cat`; `test_fleet_urls_loaded_via_filter_not_raw_cat` guards it.
2. **A missing `GF_*_URL` would read as false drift (High)** → Task 1 `_snapshot_live` now treats *enabled-but-no-URL* as unreachable (added to the `unreachable` set), so that namespace is excluded from both sides of the diff rather than reported REMOVED.
3. **Hung backend could blow the job timeout / miss the heartbeat (Medium)** → bounded `Client(b.url, timeout=30, init_timeout=10)` (both params verified present in the installed FastMCP `Client`).
4. **Typing weaker than the repo's mypy gate (Medium)** → `tuple[Manifest, set[str]]` + `dict[str, BackendSpec]` with a `TYPE_CHECKING` import; both `type: ignore[arg-type]` comments removed.
5. **Workflow tests too shallow (Medium)** → Task 3 now asserts SHA-pinned `uses:`, exactly `contents: read` + `issues: write`, a fail-safe `always()` heartbeat, and the grep loader; the env-sync test uses `defined - enabled` (also flags URLs left behind for disabled backends).
6. **Emoji + co-author cleanup (Low)** → emoji dropped from the issue title (ASCII default). **Co-author trailer kept**: every merged commit in this repo carries `Co-Authored-By: Claude Opus 4.8 (1M context)` — it is the established convention here, so removing it would be the inconsistency.
