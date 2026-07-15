# LitVar #67 — Effective Compose Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every supported LitVar Compose composition has an effective read-only root filesystem, bounded safe scratch, no capabilities, no-new-privileges, an init process, and a live PID ceiling, while preserving health and Streamable-HTTP MCP service without an NPM host port.

**Architecture:** Mandatory runtime controls move to the base application service so both direct base+NPM and base+prod inherit them. Production overlays retain only deploy-specific image, ingress, resources, and logging choices. A small rendered-Compose validator checks the effective model for all three supported compositions; a disposable Docker smoke verifies the Engine-level security settings and runtime write behavior.

**Tech Stack:** Docker Compose v2, Docker Engine inspect, Python 3.12, PyYAML, pytest, Make, FastAPI/FastMCP Streamable HTTP.

---

**Repository and branch:** `/home/bernt-popp/development/litvar-link`, new branch `fix/compose-hardening-67` from current `origin/main`. All implementation paths below are relative to that repository.

## Fixed policy

For the `litvar-link` service in each of `base+prod`, `base+npm`, and `base+prod+npm`, the rendered Compose model must contain exactly these material controls:

```yaml
read_only: true
tmpfs:
  - /tmp:rw,noexec,nosuid,size=64m,mode=1777
security_opt:
  - no-new-privileges:true
cap_drop:
  - ALL
init: true
pids_limit: 256
```

`deploy.resources.limits` remains present with `memory`, `cpus`, and `pids: 256`; `pids_limit: 256` is deliberately also set because it is directly applied by non-Swarm Docker Compose and is verified with `docker inspect`. NPM has no host `ports`; it is expose-only on port 8000. The canonical container release profile is the full `base+prod+npm` composition, while the two shorter compositions are independently supported and validated—not undocumented aliases.

## File map

| File | Change | Responsibility |
|---|---|---|
| `docker/docker-compose.yml` | Modify | Own all mandatory application hardening so base+NPM is safe. |
| `docker/docker-compose.prod.yml` | Modify | Remove duplicated hardening only when it inherits unchanged; retain production image, no host ports, limits, health, and logging. |
| `docker/docker-compose.npm.yml` | Modify | Preserve NPM networking/no host port and inherit base hardening without weak overrides. |
| `scripts/check_compose_hardening.py` | Create | Validate one rendered Compose document from stdin with deterministic diagnostics. |
| `tests/unit/test_compose_hardening.py` | Create | Render every supported composition and regression-test validator failures. |
| `Makefile` | Modify | Add direct base+NPM and full base+prod+NPM render/validation targets plus an isolated Docker smoke target. |
| `container-release.json` | Modify | Declare the canonical full production/NPM Compose sequence. |
| `docker/README.md` and `docs/deployment.md` | Modify | Document the three profiles and identify full production+NPM as canonical. |

### Task 1: Specify effective hardening with failing rendered-Compose tests

**Files:**
- Create: `tests/unit/test_compose_hardening.py`
- Create: `scripts/check_compose_hardening.py`

- [ ] **Step 1: Create a failing test module.**

  Use `subprocess.run` to render, never hand-merge YAML. The helper supplies the all-zero non-deployable digest that the Makefile uses only for configuration rendering and an external NPM network name that Docker need not inspect during `config`:

  ```python
  from __future__ import annotations

  import os
  import subprocess
  import sys
  from pathlib import Path

  ROOT = Path(__file__).resolve().parents[2]
  IMAGE = "ghcr.io/berntpopp/litvar-link@sha256:" + "0" * 64
  BASE = ROOT / "docker" / "docker-compose.yml"
  PROD = ROOT / "docker" / "docker-compose.prod.yml"
  NPM = ROOT / "docker" / "docker-compose.npm.yml"

  def _render(*files: Path) -> str:
      env = os.environ | {
          "LITVAR_LINK_IMAGE": IMAGE,
          "NPM_SHARED_NETWORK_NAME": "litvar-link-test-npm",
      }
      completed = subprocess.run(
          ["docker", "compose", *sum((["-f", str(path)] for path in files), []), "config"],
          cwd=ROOT,
          env=env,
          text=True,
          capture_output=True,
          check=True,
      )
      return completed.stdout

  def _validate(rendered: str) -> subprocess.CompletedProcess[str]:
      return subprocess.run(
          [sys.executable, "scripts/check_compose_hardening.py", "--service", "litvar-link"],
          cwd=ROOT,
          input=rendered,
          text=True,
          capture_output=True,
      )
  ```

  Add parameterized coverage for `(BASE, PROD)`, `(BASE, NPM)`, and `(BASE, PROD, NPM)`:

  ```python
  @pytest.mark.parametrize("files", [(BASE, PROD), (BASE, NPM), (BASE, PROD, NPM)])
  def test_every_supported_rendered_profile_is_hardened(
      files: tuple[Path, Path] | tuple[Path, Path, Path],
  ) -> None:
      result = _validate(_render(*files))
      assert result.returncode == 0, result.stderr
  ```

  Add unit fixtures encoded as dictionaries and assert the validator exits `2` and names the missing dotted field for each of: `read_only`, `/tmp` `noexec`, `/tmp` `nosuid`, `cap_drop`, `security_opt`, `init`, `pids_limit`, `deploy.resources.limits.pids`, and NPM `ports`.

- [ ] **Step 2: Run the focused test to confirm the red state.**

  Run:

  ```bash
  uv run pytest tests/unit/test_compose_hardening.py -q
  ```

  Expected before implementation: collection fails because `scripts/check_compose_hardening.py` does not exist. After adding only the validator, `base+npm` fails because mandatory controls are currently supplied only by `docker-compose.prod.yml`.

- [ ] **Step 3: Implement the minimal validator.**

  Create `scripts/check_compose_hardening.py` with this complete contract:

  ```python
  from __future__ import annotations

  import argparse
  import sys
  from typing import Any

  import yaml

  REQUIRED_TMPFS = "/tmp:rw,noexec,nosuid,size=64m,mode=1777"

  def violations(model: dict[str, Any], service_name: str) -> list[str]:
      service = model.get("services", {}).get(service_name)
      if not isinstance(service, dict):
          return [f"services.{service_name}: missing"]
      errors: list[str] = []
      if service.get("read_only") is not True:
          errors.append(f"services.{service_name}.read_only must be true")
      if REQUIRED_TMPFS not in service.get("tmpfs", []):
          errors.append(f"services.{service_name}.tmpfs must contain {REQUIRED_TMPFS}")
      if service.get("cap_drop") != ["ALL"]:
          errors.append(f"services.{service_name}.cap_drop must equal [ALL]")
      if "no-new-privileges:true" not in service.get("security_opt", []):
          errors.append(f"services.{service_name}.security_opt lacks no-new-privileges:true")
      if service.get("init") is not True:
          errors.append(f"services.{service_name}.init must be true")
      if service.get("pids_limit") != 256:
          errors.append(f"services.{service_name}.pids_limit must equal 256")
      limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
      if limits.get("pids") != 256:
          errors.append(f"services.{service_name}.deploy.resources.limits.pids must equal 256")
      return errors

  def main() -> int:
      parser = argparse.ArgumentParser()
      parser.add_argument("--service", required=True)
      args = parser.parse_args()
      model = yaml.safe_load(sys.stdin.read())
      errors = violations(model if isinstance(model, dict) else {}, args.service)
      if errors:
          print("\n".join(errors), file=sys.stderr)
          return 2
      return 0

  if __name__ == "__main__":
      raise SystemExit(main())
  ```

  The NPM-port assertion belongs in the profile test: parse `_render(BASE, NPM)` with `yaml.safe_load` and assert `services["litvar-link"].get("ports", []) == []`.

- [ ] **Step 4: Commit the red contract and validator.**

  ```bash
  git add tests/unit/test_compose_hardening.py scripts/check_compose_hardening.py
  git commit -m "test: define LitVar effective Compose hardening"
  ```

### Task 2: Move hardening to the base service without changing deployment semantics

**Files:**
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `docker/docker-compose.npm.yml`

- [ ] **Step 1: Make the base service own the mandatory block.**

  Insert this directly below the base `restart: unless-stopped` stanza in `docker/docker-compose.yml`:

  ```yaml
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    init: true
    pids_limit: 256
  ```

  Retain the existing base `deploy.resources.limits.memory: 512M`; add `cpus: '1.0'` and `pids: 256` beneath it so the base composition has both Compose v2 and Engine-enforced bounds:

  ```yaml
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '1.0'
          pids: 256
        reservations:
          memory: 256M
  ```

- [ ] **Step 2: Remove only redundant prod controls and preserve production overrides.**

  In `docker/docker-compose.prod.yml`, delete its duplicated `read_only`, `tmpfs`, `security_opt`, `cap_drop`, and `init` entries. Do **not** remove its `ports: !reset []`, `expose: ["8000"]`, digest-only `image`, resource sizing (`memory: 1G`, `cpus: '1.0'`, `pids: 256`), health check, restart policy, or logging. Leave `pids_limit` inherited from base; do not set an incompatible overlay value.

- [ ] **Step 3: Keep NPM as an inheritance-only network/ingress overlay.**

  Do not add a weak `read_only`, `tmpfs`, capability, security option, init, or PID override in `docker/docker-compose.npm.yml`. Retain `ports: !reset []`, both networks, the explicit unified command, and production resource/logging policy. Update its opening comment from “extends base docker-compose.yml” to “layers over `docker-compose.yml`; validate directly as base+NPM and canonically as base+prod+NPM.”

- [ ] **Step 4: Re-run the rendered tests.**

  Run:

  ```bash
  uv run pytest tests/unit/test_compose_hardening.py -q
  ```

  Expected: all three rendered profiles pass; the base+NPM test proves the previous coverage gap is closed.

- [ ] **Step 5: Commit the Compose-only change.**

  ```bash
  git add docker/docker-compose.yml docker/docker-compose.prod.yml docker/docker-compose.npm.yml tests/unit/test_compose_hardening.py
  git commit -m "fix: harden LitVar base and NPM Compose profiles"
  ```

### Task 3: Make direct profile validation a first-class Make/CI contract

**Files:**
- Modify: `Makefile`
- Modify: `.github/workflows/docker.yml`
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Extend `.PHONY` with `docker-prod-npm-config` and `docker-smoke-hardening`.**

- [ ] **Step 2: Replace the current NPM target and add the full canonical target.**

  Keep `PLACEHOLDER_IMAGE` unchanged and use exactly these targets:

  ```make
  docker-prod-config: ## Render and validate base + production Compose configuration
	LITVAR_LINK_IMAGE=$${LITVAR_LINK_IMAGE:-$(PLACEHOLDER_IMAGE)} \
		$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config | \
		uv run python scripts/check_compose_hardening.py --service litvar-link

  docker-npm-config: ## Render and validate direct base + NPM Compose configuration
	LITVAR_LINK_IMAGE=$${LITVAR_LINK_IMAGE:-$(PLACEHOLDER_IMAGE)} \
		$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.npm.yml --env-file .env.docker.example config | \
		uv run python scripts/check_compose_hardening.py --service litvar-link

  docker-prod-npm-config: ## Render and validate canonical base + production + NPM configuration
	LITVAR_LINK_IMAGE=$${LITVAR_LINK_IMAGE:-$(PLACEHOLDER_IMAGE)} \
		$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.prod.yml -f docker/docker-compose.npm.yml --env-file .env.docker.example config | \
		uv run python scripts/check_compose_hardening.py --service litvar-link
  ```

  Add `make docker-prod-npm-config` after the existing two Compose checks in both workflows. This is a validation-only change; neither workflow should launch containers at this task.

- [ ] **Step 3: Run all three direct validators.**

  Run:

  ```bash
  make docker-prod-config
  make docker-npm-config
  make docker-prod-npm-config
  ```

  Expected: each exits 0 with no validator diagnostic. In particular, `make docker-npm-config` now renders *only* base+NPM, not an implicit prod overlay.

- [ ] **Step 4: Commit the validation wiring.**

  ```bash
  git add Makefile .github/workflows/docker.yml .github/workflows/release.yml scripts/check_compose_hardening.py
  git commit -m "ci: validate every LitVar Compose profile"
  ```

### Task 4: Prove Engine-level hardening and service behavior in an isolated smoke

**Files:**
- Modify: `Makefile`
- Create: `scripts/smoke_compose_hardening.sh`

- [ ] **Step 1: Write the smoke script before wiring it.**

  `scripts/smoke_compose_hardening.sh` must run with `set -euo pipefail`, create an isolated external network named `litvar67_npm`, build `litvar-link:issue67` with `docker build -f docker/Dockerfile -t litvar-link:issue67 .`, and use a unique Compose project `litvar67-$RANDOM`. It must start the direct base+NPM profile with:

  ```bash
  NPM_SHARED_NETWORK_NAME=litvar67_npm \
  LITVAR_LINK_IMAGE=litvar-link:issue67 \
  docker compose -p "$project" -f docker/docker-compose.yml -f docker/docker-compose.npm.yml \
    --env-file .env.docker.example up -d --wait
  ```

  Obtain the container ID with `cid="$(docker compose -p "$project" -f docker/docker-compose.yml -f docker/docker-compose.npm.yml ps -q litvar-link)"` and require these exact inspect fields:

  ```bash
  test "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$cid")" = true
  test "$(docker inspect -f '{{json .HostConfig.CapDrop}}' "$cid")" = '["ALL"]'
  test "$(docker inspect -f '{{.HostConfig.SecurityOpt}}' "$cid")" = '[no-new-privileges:true]'
  test "$(docker inspect -f '{{.HostConfig.PidsLimit}}' "$cid")" = 256
  ```

  Prove filesystem behavior and no NPM host port:

  ```bash
  docker exec "$cid" sh -ec '! touch /root/issue67-root-write; touch /tmp/issue67-tmp-write'
  test -z "$(docker compose -p "$project" -f docker/docker-compose.yml -f docker/docker-compose.npm.yml port litvar-link 8000)"
  docker exec "$cid" curl -fsS -H 'Host: localhost' http://127.0.0.1:8000/health | jq -e '.status == "ok"'
  docker exec "$cid" curl -fsS -X POST http://127.0.0.1:8000/mcp \
    -H 'Host: localhost' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"issue67-smoke","version":"1"}}}' | jq -e '.result.serverInfo.name == "litvar-link"'
  ```

  Add an `EXIT` trap that always brings the project down with `--volumes --remove-orphans` and removes `litvar67_npm`; preserve container logs to stderr if a command fails.

- [ ] **Step 2: Add the exact Make target.**

  ```make
  docker-smoke-hardening: ## Build and prove the direct NPM profile's live hardening
	bash scripts/smoke_compose_hardening.sh
  ```

- [ ] **Step 3: Run the smoke.**

  Run:

  ```bash
  make docker-smoke-hardening
  ```

  Expected: all four Engine settings match, the root write fails, the `/tmp` write succeeds, `/health` includes `{"status":"ok","transport":"streamable-http-stateless"}`, MCP initialization returns `result.serverInfo.name == "litvar-link"`, and `docker compose port` prints nothing.

- [ ] **Step 4: Commit the live regression proof.**

  ```bash
  git add Makefile scripts/smoke_compose_hardening.sh
  git commit -m "test: smoke LitVar Compose runtime hardening"
  ```

### Task 5: Reconcile release metadata and deployment documentation

**Files:**
- Modify: `container-release.json`
- Modify: `docker/README.md`
- Modify: `docs/deployment.md`
- Modify: `tests/unit/test_docs_reconciled.py`

- [ ] **Step 1: Declare the canonical profile.**

  Replace the `service.compose_files` array with:

  ```json
  [
    "docker/docker-compose.yml",
    "docker/docker-compose.prod.yml",
    "docker/docker-compose.npm.yml"
  ]
  ```

  Leave the data contract as `mode: "none"`; #67 changes container execution hardening, not LitVar’s live upstream/data semantics.

- [ ] **Step 2: Correct documentation commands and profile meanings.**

  In both documentation files, list exactly:

  - `base+prod` — direct reverse-proxy production, no host port;
  - `base+npm` — direct NPM compatibility profile, no host port;
  - `base+prod+npm` — canonical released NPM deployment.

  Every NPM launch example must use all three files for the canonical deployment:

  ```bash
  gh release download --repo berntpopp/litvar-link --pattern application-release-manifest.json --dir /tmp/litvar67-release
  export LITVAR_LINK_IMAGE="ghcr.io/berntpopp/litvar-link@$(jq -r '.image.digest' /tmp/litvar67-release/application-release-manifest.json)"
  docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
    -f docker/docker-compose.npm.yml --env-file .env.docker up -d
  ```

- [ ] **Step 3: Add a reconciliation assertion.**

  Extend `tests/unit/test_docs_reconciled.py` to load `container-release.json` and assert its exact three-file order, then assert the documentation includes `base+prod+npm` and no longer claims NPM is `base+prod` only.

- [ ] **Step 4: Verify and commit.**

  Run:

  ```bash
  uv run pytest tests/unit/test_compose_hardening.py tests/unit/test_docs_reconciled.py -q
  make ci-local
  make docker-prod-config
  make docker-npm-config
  make docker-prod-npm-config
  make docker-smoke-hardening
  ```

  Expected: all Python gates and the three rendered profiles pass; the live smoke proves the direct NPM profile’s Engine state.

  ```bash
  git add container-release.json docker/README.md docs/deployment.md tests/unit/test_docs_reconciled.py
  git commit -m "docs: declare canonical LitVar NPM deployment profile"
  ```

### Task 6: PR, release, deployment, and issue-close evidence

- [ ] **Step 1: Add an unreleased `CHANGELOG.md` entry for #67, push the branch, and open a draft PR with `Fixes #67`.**

- [ ] **Step 2: Before merge, attach the outputs of `make ci-local`, all three `make docker-*-config` targets, and `make docker-smoke-hardening`; obtain an independent review and wait for required GitHub checks on the exact PR head SHA.**

- [ ] **Step 3: Merge only that green SHA, tag the required post-merge release under the repository versioning standard, and capture the container-release workflow’s image digest, SBOM/provenance, scan verdict, and canonical full-profile render.**

- [ ] **Step 4: Deploy the digest with base+prod+npm; rerun the inspect/root-write/tmp-write/health/MCP/no-port probes against the deployed digest. Post the merge SHA, image digest, release/run URLs, and probe output summary on #67, then close the issue.**
