# clingen-link Compose-Hardening, Guidance-Branch Resolution & Data-Refresh Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal**: Make `clingen-link`'s base Compose safe-by-default (hardened + loopback-bound), land the rotting `feat/clingen-guidance-manifest` branch as a complete, wired, PR'd feature, and repair the `data-refresh` workflow so its snapshot PR is always based on current `main` and always actually opens.

**Architecture**: `clingen-link` is a thin FastMCP 3.x backend in the GeneFoundry `-link` fleet; it ships a bundled read-only SQLite snapshot and is deployed behind the router/reverse proxy only (auth=none at the trust boundary). Three independent surfaces are being remediated: the Docker Compose overlay set under `docker/`, the MCP resource registry under `clingen_link/mcp/`, and the scheduled GitHub Actions data-refresh workflow under `.github/workflows/`. Each surface is a separate branch and a separate PR; none depends on another.

**Tech Stack**: Python 3.12+, uv, FastMCP 3.x / `mcp` SDK, hatchling, pytest (+pytest-asyncio), PyYAML 6.x (already in the dev venv), Docker Compose v2 overlays, GitHub Actions (`peter-evans/create-pull-request@v8`), ruff + mypy.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

Created:
- `tests/unit/test_compose_hardening.py` — asserts the base Compose is hardened + loopback-bound and the overlays do not re-declare `tmpfs`.
- `tests/unit/test_data_refresh_workflow.py` — asserts the data-refresh workflow targets `main` and verifies a PR was opened.

Modified:
- `docker/docker-compose.yml` — backport overlay hardening (`read_only`, `tmpfs`, `cap_drop: ALL`, `no-new-privileges`, `init`, `pids_limit`, `deploy.resources.limits`) into the base; bind the published port to `127.0.0.1` only.
- `docker/docker-compose.prod.yml` — drop the now-duplicated `tmpfs` block (inherited from base) to avoid a Compose list-merge duplicate mount target.
- `docker/docker-compose.npm.yml` — same `tmpfs` de-duplication as prod.
- `clingen_link/mcp/tools/metadata.py` — register the previously-unbound `clingen://guidance` MCP resource (the gap the feat branch left).
- `clingen_link/mcp/resources.py` — (arrives via the `feat/clingen-guidance-manifest` rebase) `get_guidance_resource()` loader + `clingen://guidance` descriptor.
- `tests/unit/test_capabilities.py` — add `clingen://guidance` to the resolved-resources assertion + a read-back test.
- `.github/workflows/data-refresh.yml` — add explicit `base: main`, give the PR step an `id`, and add a "Verify pull request opened" guard step.

---

### Task 1: Harden the base `docker-compose.yml` and bind it to loopback

Closes Container & Deployment Hardening Standard v1 **universal gap #1** ("Base `docker-compose.yml` is unhardened and publishes a host port binding `0.0.0.0` — running it directly drops every control and exposes the backend outside the router"). `clingen-link` is Tier A in that standard's audit table: the prod/npm overlays are already fully hardened — only the *base* is weak. The audit-cited `docker/docker-compose.yml:10-11` publishes `"${CLINGEN_LINK_HOST_PORT:-8479}:8000"` on all interfaces and lines 24-37 carry only healthcheck/restart/logging/networks — no `read_only`, `cap_drop`, `security_opt`, `init`, or limits.

**Files**
- Modify: `docker/docker-compose.yml` (current: 42 lines; `ports` at lines 10-11; no hardening keys)
- Modify: `docker/docker-compose.prod.yml` (drop `tmpfs:` at lines 18-21)
- Modify: `docker/docker-compose.npm.yml` (drop `tmpfs:` at lines 33-34)
- Test: `tests/unit/test_compose_hardening.py` (new)

**Interfaces**
- Consumes: nothing at runtime (declarative Compose).
- Produces: a base service `clingen-link` that, when run bare (`docker compose -f docker/docker-compose.yml up`), is read-only-rootfs, drops all caps, sets `no-new-privileges`, has memory/cpu/pids limits, and publishes its port only on `127.0.0.1`. The prod (`clingen-link`) and npm (`clingen_link`) overlay services inherit the base `tmpfs` instead of re-declaring it.

**Approach & research.** The router's `docker/` is the canonical reference: `docker/docker-compose.prod.yml` is the hardening template (`read_only`, `tmpfs`, `security_opt`, `cap_drop`, `init`, `deploy.resources.limits`) and `docker/docker-compose.npm.yml` shows the `ports: !reset []` expose-only pattern. The standard's pitfall #1 says to make "non-publishing + hardened the default"; the acceptance criterion for this workstream is loopback-bind, which keeps the documented quick-start (`curl http://localhost:8479/health`) working while removing public-interface exposure. Because Docker Compose **merges (appends) sequence fields across `-f` overlays** (this is exactly why the npm overlay uses `ports: !reset []`; see `docs/CONTAINER-HARDENING-STANDARD-v1.md` §5 item 17 and the Compose merge spec https://docs.docker.com/reference/compose-file/merge/), putting a `tmpfs` entry in the base **and** leaving the identical entry in prod/npm would append two `/tmp/clingen-link` mounts and trip a duplicate-mount-target error. The fix removes the now-redundant `tmpfs` from both overlays; the remaining idempotent duplicates (`cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, scalar `read_only: true`) merge harmlessly. read-only+`/tmp/clingen-link` tmpfs is already proven in production (the prod overlay runs this way on the VPS), so adding it to the base carries no new runtime risk.

- [ ] **Write the failing test.** Create `tests/unit/test_compose_hardening.py`:
```python
"""Container & Deployment Hardening Standard v1: the base compose is safe-by-default.

The prod/npm overlays were already hardened (clingen-link is Tier A); this guards
universal gap #1 — running the *base* compose directly must not drop controls or
publish an auth=none backend on 0.0.0.0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DOCKER = Path(__file__).resolve().parents[2] / "docker"


def _service(compose_file: str, service: str) -> dict[str, Any]:
    data = yaml.safe_load((DOCKER / compose_file).read_text(encoding="utf-8"))
    return data["services"][service]


def test_base_compose_is_hardened() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert svc["init"] is True
    assert svc["pids_limit"] == 256
    assert svc["deploy"]["resources"]["limits"]["memory"]


def test_base_compose_binds_loopback_only() -> None:
    svc = _service("docker-compose.yml", "clingen-link")
    # An auth=none backend must never be published on 0.0.0.0.
    assert svc["ports"], "base compose must still publish a loopback port for the quick-start"
    assert all(str(p).startswith("127.0.0.1:") for p in svc["ports"]), svc["ports"]


def test_overlays_do_not_redeclare_tmpfs() -> None:
    # Base owns the tmpfs; an overlay re-adding /tmp/clingen-link makes Compose
    # list-merge yield a duplicate mount target. (Service keys differ by file.)
    assert "tmpfs" not in _service("docker-compose.prod.yml", "clingen-link")
    assert "tmpfs" not in _service("docker-compose.npm.yml", "clingen_link")
```
- [ ] **Run it, expect FAIL.** `cd /home/bernt-popp/development/clingen-link && uv run pytest tests/unit/test_compose_hardening.py -q` → FAILS: `test_base_compose_is_hardened` raises `KeyError: 'read_only'`, `test_base_compose_binds_loopback_only` asserts on `['${CLINGEN_LINK_HOST_PORT:-8479}:8000']` (no `127.0.0.1:` prefix), `test_overlays_do_not_redeclare_tmpfs` fails because both overlays still declare `tmpfs`.
- [ ] **Minimal implementation — base.** Edit `docker/docker-compose.yml`: replace the `ports` block (lines 10-11) with a loopback bind and insert the hardening block after the `command` line (line 23):
```yaml
    ports:
      # Loopback only: an auth=none backend must never be published on 0.0.0.0.
      # A bare `docker compose up` stays reachable solely from the host loopback;
      # the prod/npm overlays drop the host port entirely (ports: !reset []).
      - "127.0.0.1:${CLINGEN_LINK_HOST_PORT:-8479}:8000"
```
```yaml
    command: ["clingen-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
    # --- Container & Deployment Hardening Standard v1 (universal gap #1: harden the base) ---
    read_only: true
    tmpfs:
      # The store decompresses the snapshot .zst to a temp .sqlite at startup,
      # so the writable tmpfs must be large enough to hold it.
      - /tmp/clingen-link:rw,noexec,nosuid,size=256m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    init: true
    pids_limit: 256
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: "1.0"
          pids: 256
    healthcheck:
```
- [ ] **Minimal implementation — overlays.** In `docker/docker-compose.prod.yml` delete the `tmpfs:` block (current lines 18-21, the `read_only: true` on line 17 stays). In `docker/docker-compose.npm.yml` delete the `tmpfs:` block (current lines 33-34). Leave every other hardening key in both overlays untouched (they merge idempotently).
- [ ] **Run it, expect PASS.** `uv run pytest tests/unit/test_compose_hardening.py -q` → 3 passed. Then verify the merged prod config has exactly one tmpfs and no public port: `docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config | grep -E "read_only|tmpfs|ALL|127.0.0.1|published"` — expect `read_only: true`, a single `/tmp/clingen-link` tmpfs, `cap_drop: [ALL]`, and NO host-published port (prod resets it). If Docker is unavailable in the runner, the three pytest assertions are the gate.
- [ ] **Commit.**
```
chore(security): harden base docker-compose + loopback-bind (gap #1)

Backport the prod/npm overlay hardening (read_only, tmpfs, cap_drop: ALL,
no-new-privileges, init, pids_limit, resource limits) into the base compose
and bind the published port to 127.0.0.1 so a bare `docker compose up` of an
auth=none backend is safe-by-default. De-duplicate the now-redundant tmpfs in
the prod/npm overlays to avoid a Compose list-merge duplicate mount target.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 2: Land `feat/clingen-guidance-manifest` — rebase, wire the unbound resource, PR

The branch is 6 commits, local-only, tip `bfdf08c` (2026-06-29), forked from merge-base `45337b3` (2026-06-12) — i.e. **pre-conformance and pre-hardening**. It adds `get_guidance_resource()` + a `clingen://guidance` descriptor in `resources.py`, a provenance-verified `clingen_link/data/svi_guidance.json` manifest, and tests. **Decision: rebase + complete + PR (NOT delete)** — it is a finished, tested, license-audited feature with a 494-line plan doc; discarding it would lose real work. But it has a latent gap: it added the `clingen://guidance` *description* to `_RESOURCES` and the loader, yet **never registered the MCP resource handler** in `clingen_link/mcp/tools/metadata.py` (where every other `clingen://…` resource is bound via `@mcp.resource(...)`). So the resource is advertised in capabilities but not actually served. This task rebases the branch onto current `main` and closes that gap.

**Files**
- Rebase target: branch `feat/clingen-guidance-manifest` onto `main` (tip `37f034c`).
- Modify: `clingen_link/mcp/tools/metadata.py` (imports at lines 13-20; resource bindings end at line 120 — add the new binding after it).
- Modify: `tests/unit/test_capabilities.py` (resolved-resources list at lines 63-73).
- Arrives via rebase: `clingen_link/mcp/resources.py` (+`get_guidance_resource`, `_guidance_manifest`, `_RESOURCES["clingen://guidance"]`), `clingen_link/data/svi_guidance.json`, `tests/unit/test_guidance_data.py`, `tests/unit/test_mcp_infra.py` (+`test_guidance_resource_shape`), `docs/superpowers/plans/2026-06-12-clingen-guidance-manifest.md`.
- Test: `tests/unit/test_capabilities.py::TestResources` (extended).

**Interfaces**
- Consumes: `get_guidance_resource() -> dict[str, Any]` from `clingen_link.mcp.resources` (delivered by the branch).
- Produces: a bound MCP resource `clingen://guidance` (`mime_type="application/json"`, `_RESOURCE_ANNOTATIONS`) returning the SVI variant-classification manifest, mirroring `citations_resource`.

**Rebase conflict analysis (verified).** On `main` since the merge-base, `resources.py` changed only two lines: `get_clingen_diagnostics` → `get_diagnostics` in the private `_TOOLS` dict (~line 48) and in an error string (~line 206). The branch's edits to `resources.py` are in disjoint regions (imports ~lines 12-18, `_RESOURCES` ~line 60, new functions appended ~line 275), so the 3-way rebase **auto-merges** `resources.py` with no markers. If git nonetheless flags a conflict, it is only the diagnostics rename — keep `main`'s `get_diagnostics`. `test_mcp_infra.py` gains an appended test function (low-risk append; keep both sides). All other branch files are new (no conflict). The manifest JSON sits beside the already-shipped `clingen.sqlite.zst` under `clingen_link/data/`, which hatchling already packages (`[tool.hatch.build.targets.wheel] packages = ["clingen_link"]`), so no `pyproject.toml` change is needed.

- [ ] **Rebase the branch onto current main (no code yet).**
```
cd /home/bernt-popp/development/clingen-link
git fetch origin && git checkout feat/clingen-guidance-manifest
git rebase main
```
Expect a clean rebase (or the trivial diagnostics-rename conflict above; resolve to `get_diagnostics`, then `git rebase --continue`). Do **not** push yet.
- [ ] **Write the failing test.** In `tests/unit/test_capabilities.py`, add `"clingen://guidance"` to the `uris` list in `test_all_resources_resolve` (currently lines 63-70) and append a read-back test to `TestResources`:
```python
    async def test_guidance_resource_resolves_and_has_baseline(self, tool_mcp: FastMCP) -> None:
        registered = {str(r.uri) for r in await tool_mcp.list_resources()}
        assert "clingen://guidance" in registered
        result = await tool_mcp.read_resource("clingen://guidance")
        import json

        payload = json.loads(result.contents[0].content)
        assert payload["baseline"]["gn_id"] == "GN001"
        assert payload["unsafe_for_clinical_use"] is True
        assert payload["research_use_notice"]
        assert all(e["oa_license"] for e in payload["recommendations"])
```
- [ ] **Run it, expect FAIL.** `uv run pytest tests/unit/test_capabilities.py -q` → FAILS: `test_all_resources_resolve` and the new test both assert `clingen://guidance in registered`, but it is not registered (the branch only described it, never bound it) → `AssertionError: clingen://guidance`.
- [ ] **Minimal implementation — wire the resource.** In `clingen_link/mcp/tools/metadata.py`, add `get_guidance_resource` to the import block (lines 13-20, keep it alphabetical — between `get_freshness_resource` and `get_reference_resource`):
```python
from clingen_link.mcp.resources import (
    get_capabilities_resource,
    get_citations_resource,
    get_freshness_resource,
    get_guidance_resource,
    get_reference_resource,
    get_research_use_resource,
    get_usage_resource,
)
```
Then append the binding immediately after `citations_resource` (after line 120), mirroring it exactly:
```python
    @mcp.resource(
        "clingen://guidance",
        annotations=_RESOURCE_ANNOTATIONS,
        mime_type="application/json",
    )
    def guidance_resource() -> dict[str, Any]:
        return get_guidance_resource()
```
- [ ] **Run it, expect PASS.** `uv run pytest tests/unit/test_capabilities.py tests/unit/test_mcp_infra.py tests/unit/test_guidance_data.py -q` → all pass (registration + read-back + the branch's own shape/provenance tests).
- [ ] **Commit the wiring (atomic, on top of the rebased branch).**
```
feat(guidance): register clingen://guidance MCP resource

The guidance-manifest branch added the loader + capability descriptor but never
bound the resource handler, so clingen://guidance was advertised yet unserved.
Bind it in metadata.py alongside the other clingen:// resources.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
- [ ] **Gate + PR (EXECUTION-GATED).** `make ci-local` must be green. Then force-push the rebased branch and open the PR:
```
make ci-local
git push --force-with-lease origin feat/clingen-guidance-manifest
gh pr create --repo berntpopp/clingen-link --base main --head feat/clingen-guidance-manifest \
  --title "feat(guidance): SVI variant-classification manifest + clingen://guidance resource" \
  --body "Adds a provenance-verified ClinGen/SVI recommendation manifest (pointers + OA-license map only, no fulltext redistributed) and the bound clingen://guidance MCP resource. Research use only; not for clinical decision support."
```
Confirm CI (`ci.yml`, `mcp-conformance`, `container-security.yml`) is green on the PR before handoff.

---

### Task 3: Repair `data-refresh.yml` — base on current `main`, always open/update a PR

The audit found `origin/data-refresh/snapshot` tip `4215f33` (2026-06-29) carries a valid refreshed snapshot but its merge-base with `main` is `9b7e8bc` (2026-06-15) — a **pre-conformance/pre-hardening** base — and there is **no open PR** (`gh pr list` returns none). Root cause: the workflow's `create-pull-request` step (`.github/workflows/data-refresh.yml:109-137`) does not set `base:` explicitly and does not verify the PR was actually opened, so a run that pushes the branch but cannot open a PR (the classic "Allow GitHub Actions to create and approve pull requests" repo toggle being off) rots silently. This task makes the PR base explicit and turns the silent no-PR case into a hard CI failure.

**Files**
- Modify: `.github/workflows/data-refresh.yml` (the `Open pull request` step, lines 109-137)
- Test: `tests/unit/test_data_refresh_workflow.py` (new)

**Interfaces**
- Consumes: `peter-evans/create-pull-request@v8` outputs `pull-request-number`, `pull-request-url` (verified at https://github.com/peter-evans/create-pull-request/blob/main/docs/concepts-guidelines.md and https://github.com/peter-evans/create-pull-request#action-outputs).
- Produces: a workflow whose PR step has `id: cpr`, `base: main`, and a downstream "Verify pull request opened" step that fails the job when `steps.cpr.outputs.pull-request-number` is empty.

**Approach & research.** The `checkout` step has no `ref:`, so for `schedule` and `workflow_dispatch` it already checks out the default branch (`main`) at trigger time — the branch base is therefore current `main` by construction; the staleness was a one-off from a run that landed before the conformance/hardening PRs merged later the same day. peter-evans/create-pull-request rebases the PR branch onto the checked-out base each run, but its docs state: "unless the `base` input is supplied, the action expects the target repository to be checked out on the pull request base" — so setting `base: main` makes the target unambiguous and robust to future trigger changes. The action exposes `pull-request-number`/`pull-request-url` outputs; gating on an empty number converts a push-without-PR into a loud failure and prints the repo-setting remediation (Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests", https://docs.github.com/actions/security-guides/automatic-token-authentication). This is the smallest change that satisfies "bases its refresh branch on CURRENT main and auto-opens/updates a PR."

- [ ] **Write the failing test.** Create `tests/unit/test_data_refresh_workflow.py`:
```python
"""data-refresh.yml must target current main and never silently skip the PR."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "data-refresh.yml"
)


def _steps() -> list[dict[str, Any]]:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return wf["jobs"]["refresh"]["steps"]


def _pr_step() -> dict[str, Any]:
    return next(s for s in _steps() if "create-pull-request" in s.get("uses", ""))


def test_pr_targets_main_on_fixed_branch() -> None:
    pr = _pr_step()
    assert pr["with"]["base"] == "main"
    assert pr["with"]["branch"] == "data-refresh/snapshot"
    assert pr.get("id") == "cpr"  # outputs must be referenceable


def test_pr_creation_is_verified() -> None:
    # A "branch pushed but no PR opened" run must fail the job, not rot.
    names = [s.get("name", "") for s in _steps()]
    assert any("Verify pull request" in n for n in names), names
```
- [ ] **Run it, expect FAIL.** `uv run pytest tests/unit/test_data_refresh_workflow.py -q` → FAILS: `test_pr_targets_main_on_fixed_branch` raises `KeyError: 'base'` (and the step has no `id`); `test_pr_creation_is_verified` finds no "Verify pull request" step.
- [ ] **Minimal implementation.** Edit `.github/workflows/data-refresh.yml`. Give the PR step an `id` and an explicit `base`, then add the guard step. The `Open pull request` step header (lines 109-112) becomes:
```yaml
      - name: Open pull request
        id: cpr
        if: ${{ steps.rebuild.outcome == 'success' }}
        uses: peter-evans/create-pull-request@5f6978faf089d4d20b00c7766989d076bb2fc7f1 # v8.1.1
        with:
          base: main
          add-paths: |
            clingen_link/data/clingen.sqlite.zst
            clingen_link/data/clingen.sqlite.sha256
          branch: data-refresh/snapshot
          delete-branch: true
```
(keep the existing `commit-message`, `title`, `body`, `labels` below unchanged). Append a new step at the end of the `steps:` list:
```yaml
      - name: Verify pull request opened
        if: ${{ steps.rebuild.outcome == 'success' }}
        run: |
          set -euo pipefail
          num="${{ steps.cpr.outputs.pull-request-number }}"
          if [ -z "$num" ]; then
            echo "::error::create-pull-request produced no PR number: the snapshot branch was"
            echo "::error::pushed without a PR. Enable Settings -> Actions -> General ->"
            echo "::error::'Allow GitHub Actions to create and approve pull requests'."
            exit 1
          fi
          echo "Opened/updated PR #${num}: ${{ steps.cpr.outputs.pull-request-url }}"
```
- [ ] **Run it, expect PASS.** `uv run pytest tests/unit/test_data_refresh_workflow.py -q` → 2 passed. If `actionlint` is installed, also run `actionlint .github/workflows/data-refresh.yml` and expect no findings.
- [ ] **Commit.**
```
ci(data-refresh): base snapshot PR on main + verify it opened

Set `base: main` explicitly and add a guard that fails the job when
create-pull-request returns no PR number, so a push-without-PR (e.g. the
"Allow GitHub Actions to create and approve pull requests" toggle being off)
surfaces loudly instead of leaving the snapshot branch rotting against an old
base. The next scheduled run rebases the branch onto current main.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
- [ ] **PR (EXECUTION-GATED).** `make ci-local` green, then `git push -u origin ci/data-refresh-base-main` and `gh pr create --repo berntpopp/clingen-link --base main --title "ci(data-refresh): base snapshot PR on main + verify it opened" --body "..."`.
- [ ] **Clean up the stale snapshot branch (EXECUTION-GATED, destructive remote op — operator confirmation required).** Only after the workflow fix is merged: delete the rotting branch so the next scheduled run recreates it cleanly off current `main`. Verify first that it has no open PR and is fully captured by the latest snapshot, then:
```
gh pr list --repo berntpopp/clingen-link --head data-refresh/snapshot --state all   # expect: none open
git push origin --delete data-refresh/snapshot
```

---

**Acceptance criteria**
- `uv run pytest tests/unit/test_compose_hardening.py tests/unit/test_data_refresh_workflow.py -q` → all pass.
- `docker/docker-compose.yml` service `clingen-link`: `read_only: true`, `cap_drop: [ALL]`, `security_opt` contains `no-new-privileges:true`, `init: true`, `pids_limit: 256`, `deploy.resources.limits` set, and the only `ports` entry is prefixed `127.0.0.1:`.
- `docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config` renders exactly one `/tmp/clingen-link` tmpfs and publishes no host port.
- `feat/clingen-guidance-manifest` is rebased onto current `main`, `clingen://guidance` is in `await tool_mcp.list_resources()`, `make ci-local` is green, and a PR is open with `ci.yml` + `mcp-conformance` + `container-security.yml` passing.
- `.github/workflows/data-refresh.yml`: PR step has `id: cpr` + `base: main`; a "Verify pull request opened" step fails the job on an empty PR number. The stale `origin/data-refresh/snapshot` branch is deleted (or explicitly retained with a documented reason).
- `make ci-local` passes on all three branches before handoff; research-use-only disclaimer preserved in the guidance manifest and PR bodies.

**Risk & rollback** — **EXECUTION-GATED**: execution ends in three `git push` operations + three `gh pr create` calls + one destructive remote op (`git push origin --delete data-refresh/snapshot`); the merged data-refresh workflow also auto-opens PRs on schedule. Mitigations: (1) Compose — base hardening is proven by the live prod overlay; if a bare `docker compose up` regresses (a write outside `/tmp/clingen-link` under read-only rootfs), revert `docker-compose.yml` only; the tmpfs de-dup is independently revertible. (2) Branch — work is on `feat/clingen-guidance-manifest` with `--force-with-lease`; the pre-rebase tip `bfdf08c` is recoverable via reflog; do not delete the branch. (3) data-refresh — workflow-only edits; revert the file to roll back. Defer the destructive branch deletion until the workflow fix is merged and confirmed; it is reconstructable from the next scheduled run. Do not toggle GitHub repo/org settings without operator sign-off.

**Effort**: ~0.5 day. Task 1 ~1.5h (mechanical + merge-semantics care), Task 2 ~2h (rebase + wiring + CI/conformance wait), Task 3 ~1h (workflow + guard + branch cleanup). Lowest-risk first ordering: Task 1 → Task 3 → Task 2 (Task 2 blocks on CI/conformance turnaround).
