# Router Container-Hardening v1 Conformance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Make `genefoundry-router` — AGENTS.md's designated container-hardening *reference implementation* — actually satisfy the three universal gaps it still owes under `docs/CONTAINER-HARDENING-STANDARD-v1.md`: CI image scan + SBOM, digest-pinned base images, and a gating dependency review (plus two stale-doc corrections surfaced by the same 2026-06-30 audit).

**Architecture:** All changes are CI/build-metadata and docstring/comment edits — no router runtime code (`genefoundry_router/server.py`, `composition.py`, auth, transport) changes, so the federation/auth/transport behaviour is untouched. A new `container-security.yml` workflow builds `docker/Dockerfile` and runs Trivy (vuln gate) + Syft (SBOM) on PR and push; `docker/Dockerfile` pins both base images by `@sha256:` digest; a `.github/dependabot.yml` keeps those digests and the SHA-pinned actions current; `security.yml`'s dependency-review becomes a hard gate. The TDD harness is the existing CI-config contract-test pattern (`tests/unit/test_ci_fleet_urls.py`): unit tests read the workflow/Dockerfile/config files and assert the contract.

**Tech Stack:** GitHub Actions; Docker multi-stage build (`docker/Dockerfile`); `aquasecurity/trivy-action`; `anchore/sbom-action` (Syft); `actions/dependency-review-action`; Dependabot; Python 3.12 + `uv` + `pytest` (PyYAML 6.x already installed) for the contract tests.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

**Created**
- `.github/workflows/container-security.yml` — builds `docker/Dockerfile` (production target) and runs Trivy image scan (fail on fixable HIGH/CRITICAL) + Syft SBOM on PR and push.
- `.github/dependabot.yml` — weekly updates for the `docker` base-image digests, the SHA-pinned `github-actions`, and the `uv` Python deps.
- `tests/unit/test_dockerfile_digest_pinned.py` — asserts every external image in `docker/Dockerfile` is `@sha256:`-pinned and no `:latest` remains.
- `tests/unit/test_ci_container_security.py` — asserts the new workflow builds the image and runs Trivy (exit-code 1, HIGH/CRITICAL) + Syft on PR+push.
- `tests/unit/test_ci_dependency_review.py` — asserts `security.yml` dependency-review gates (no `continue-on-error`, `fail-on-severity: high`).
- `tests/unit/test_ci_dependabot.py` — asserts Dependabot watches `docker` + `github-actions` + `uv`.
- `tests/unit/test_drift_docstring.py` — asserts `genefoundry_router.drift.__doc__` names `ci/fleet-baseline.json`.
- `tests/unit/test_servers_yaml_comments.py` — asserts the stale `enable once …` hpo comment is gone.

**Modified**
- `docker/Dockerfile` (lines 3, 7, 16) — digest-pin `python:3.12-slim` (builder + production) and replace `ghcr.io/astral-sh/uv:latest` with `uv:0.8.7@sha256:…`.
- `.github/workflows/security.yml` (lines 53–55) — drop `continue-on-error: true`, add `fail-on-severity: high`.
- `genefoundry_router/drift.py` (lines 6–9 docstring) — name `ci/fleet-baseline.json` as the CI-pinned baseline; keep `tests/fixtures/fleet_manifest.json` correctly described as the offline fixture.
- `servers.yaml` (line 28) — remove the stale `enable once hpo-link.genefoundry.org is deployed` clause (hpo is live and baselined).

---

### Task 1: Digest-pin both base images in `docker/Dockerfile`

Closes Container-Hardening v1 §1.2 and the audit's universal gap #2. Current `docker/Dockerfile` has three floating refs: `FROM python:3.12-slim AS builder` (line 3), `COPY --from=ghcr.io/astral-sh/uv:latest` (line 7), `FROM python:3.12-slim AS production` (line 16).

**Files**
- Test: `tests/unit/test_dockerfile_digest_pinned.py` (Create)
- Modify: `docker/Dockerfile:3`, `docker/Dockerfile:7`, `docker/Dockerfile:16`

**Interfaces**
- Consumes: `docker/Dockerfile` (text).
- Produces: a regex extractor `_external_image_refs(text: str) -> list[str]` returning every `FROM <ref>` and registry-qualified `COPY --from=<ref>` (a ref containing `/`; build-stage names like `builder` are excluded).

Digests resolved 2026-06-30 (Docker Hub / GHCR manifest-list digests). Re-resolve at execution time — base images get patched and Dependabot (Task 2) keeps them current thereafter:
```bash
docker buildx imagetools inspect python:3.12-slim     --format '{{.Manifest.Digest}}'   # -> sha256:423ed6…
docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.8.7 --format '{{.Manifest.Digest}}'  # -> sha256:1e26f9…
```

Steps:
- [ ] (1) Write the failing test `tests/unit/test_dockerfile_digest_pinned.py`:
  ```python
  """docker/Dockerfile must pin every external image by digest (Container-Hardening v1 §1.2)."""

  import re
  from pathlib import Path

  DOCKERFILE = Path("docker/Dockerfile")


  def _external_image_refs(text: str) -> list[str]:
      """Every `FROM <ref>` and registry-qualified `COPY --from=<ref>` (excludes build-stage names)."""
      refs: list[str] = []
      for line in text.splitlines():
          s = line.strip()
          if m := re.match(r"^FROM\s+(\S+)", s):
              refs.append(m.group(1))
          if (m := re.match(r"^COPY\s+--from=(\S+)", s)) and "/" in m.group(1):
              refs.append(m.group(1))  # a registry ref (has "/"), not a stage alias like `builder`
      return refs


  def test_no_floating_latest_tag() -> None:
      assert ":latest" not in DOCKERFILE.read_text(encoding="utf-8"), (
          "no :latest base images — mutable tag is a supply-chain hole"
      )


  def test_every_external_image_is_digest_pinned() -> None:
      refs = _external_image_refs(DOCKERFILE.read_text(encoding="utf-8"))
      assert refs, "expected at least one FROM / COPY --from image ref"
      unpinned = [r for r in refs if "@sha256:" not in r]
      assert not unpinned, f"digest-pin these (Container-Hardening v1 §1.2): {unpinned}"
  ```
- [ ] (2) Run it — expect FAIL: `uv run pytest tests/unit/test_dockerfile_digest_pinned.py -q`. Both tests fail: `:latest` is present (line 7) and all three refs lack `@sha256:`.
- [ ] (3) Minimal implementation — edit `docker/Dockerfile`:
  - Line 3 `FROM python:3.12-slim AS builder` →
    `FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder`
  - Line 7 `COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv` →
    `COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv`
    (also pins the **version** to 0.8.7, matching `ci.yml`/`drift.yml`'s `astral-sh/setup-uv` `version: "0.8.7"`, so build-time and CI uv agree.)
  - Line 16 `FROM python:3.12-slim AS production` →
    `FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS production`
- [ ] (4) Run — expect PASS: `uv run pytest tests/unit/test_dockerfile_digest_pinned.py -q` (2 passed). Optional smoke: `make docker-build` still builds (digest matches `3.12-slim`).
- [ ] (5) Commit: `build(docker): pin base images by sha256 digest (Container-Hardening v1 §1.2)`

---

### Task 2: Add `.github/dependabot.yml` to keep digests + pinned actions current

Closes Container-Hardening v1 §8.28 / DoD "base/deps watched for patch bumps." Dependabot's `uv` ecosystem is GA since 2025-03-13 (<https://github.blog/changelog/2025-03-13-dependabot-version-updates-now-support-uv-in-general-availability/>). A pinned-but-never-patched base is a liability, not a control — this is what makes the Task 1 digest pin maintainable.

**Files**
- Test: `tests/unit/test_ci_dependabot.py` (Create)
- Create: `.github/dependabot.yml`

**Interfaces**
- Consumes: `.github/dependabot.yml` (YAML).
- Produces: `updates[].package-ecosystem` set ⊇ `{docker, github-actions, uv}`.

Steps:
- [ ] (1) Write the failing test `tests/unit/test_ci_dependabot.py`:
  ```python
  """Dependabot must watch the Docker base-image digests, pinned actions, and uv deps."""

  from pathlib import Path

  import yaml


  def test_dependabot_watches_docker_actions_and_uv() -> None:
      cfg = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))
      ecosystems = {u["package-ecosystem"] for u in cfg["updates"]}
      assert "docker" in ecosystems, "watch docker base-image digests (Container-Hardening v1 §8.28)"
      assert "github-actions" in ecosystems, "watch SHA-pinned actions"
      assert "uv" in ecosystems, "watch uv.lock / pyproject deps"
  ```
- [ ] (2) Run it — expect FAIL: `uv run pytest tests/unit/test_ci_dependabot.py -q` → `FileNotFoundError: .github/dependabot.yml`.
- [ ] (3) Minimal implementation — create `.github/dependabot.yml`:
  ```yaml
  version: 2
  updates:
    # Keep the digest-pinned base images in docker/Dockerfile current (Container-Hardening v1 §8.28).
    - package-ecosystem: docker
      directory: /docker
      schedule:
        interval: weekly
      commit-message:
        prefix: build

    # Keep SHA-pinned GitHub Actions current (security.yml / ci.yml / drift.yml / container-security.yml).
    - package-ecosystem: github-actions
      directory: /
      schedule:
        interval: weekly
      commit-message:
        prefix: ci

    # Keep Python deps (pyproject.toml + uv.lock) patched; GA since 2025-03-13.
    - package-ecosystem: uv
      directory: /
      schedule:
        interval: weekly
      commit-message:
        prefix: build
  ```
- [ ] (4) Run — expect PASS: `uv run pytest tests/unit/test_ci_dependabot.py -q` (1 passed).
- [ ] (5) Commit: `ci(dependabot): watch docker digests, pinned actions, uv deps`

---

### Task 3: Add `.github/workflows/container-security.yml` (build + Trivy + Syft)

Closes Container-Hardening v1 §8.27 & §8.29 and the audit's universal gap #3 — the router image is currently **never built in CI** and never scanned. Trigger on PR **and** push (the standard says "scan every image in CI," not PR-only). Decisions, with sources:
- **Single Trivy scan, `format: sarif` + `exit-code: 1`, then `upload-sarif` with `if: always()`** so the job *fails* on fixable HIGH/CRITICAL while still recording findings in code scanning. (`aquasecurity/trivy-action` README: <https://github.com/aquasecurity/trivy-action>.)
- **`ignore-unfixed: true`** so the gate flags only *actionable* (fixable) vulns — matches the standard's "fail on fixable HIGH/CRITICAL."
- **Syft via `anchore/sbom-action`**, SPDX-JSON, `upload-artifact: true` — retained supply-chain evidence (§8.29). (<https://github.com/anchore/sbom-action>.)
- SARIF upload guarded `!github.event.repository.private` (mirrors `security.yml`'s CodeQL guard) so private forks without code scanning still get the build-failing gate.
- Actions SHA-pinned with a version comment (repo convention); reuse `actions/checkout` and `github/codeql-action/upload-sarif` pins already in `security.yml`.

**Files**
- Test: `tests/unit/test_ci_container_security.py` (Create)
- Create: `.github/workflows/container-security.yml`

**Interfaces**
- Consumes: `.github/workflows/container-security.yml` (text + YAML).
- Produces: a job that runs `docker build … --target production`, then `aquasecurity/trivy-action` (`exit-code: "1"`, `severity: CRITICAL,HIGH`) and `anchore/sbom-action`, on `pull_request` and `push`.

Pinned refs (resolved 2026-06-30 via `gh api`): `aquasecurity/trivy-action` v0.36.0 → `ed142fd0673e97e23eac54620cfb913e5ce36c25`; `anchore/sbom-action` v0.24.0 → `e22c389904149dbc22b58101806040fa8d37a610`.

Steps:
- [ ] (1) Write the failing test `tests/unit/test_ci_container_security.py` (text+YAML asserts, mirroring `tests/unit/test_ci_fleet_urls.py`; YAML-1.1 maps the `on:` key to boolean `True`, so triggers are asserted on raw text):
  ```python
  """container-security.yml must build the image and run Trivy + Syft on PR and push."""

  from pathlib import Path

  import yaml

  WF = Path(".github/workflows/container-security.yml")


  def test_workflow_is_valid_yaml_with_a_job() -> None:
      doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
      assert doc["jobs"], "expected at least one job"


  def test_triggers_on_pull_request_and_push() -> None:
      text = WF.read_text(encoding="utf-8")
      assert "pull_request" in text and "push" in text, "scan on PR and push (Container-Hardening v1 §8.27)"


  def test_builds_image_and_runs_trivy_and_syft() -> None:
      text = WF.read_text(encoding="utf-8")
      assert "docker build" in text and "docker/Dockerfile" in text, "must build the production image in CI"
      assert "aquasecurity/trivy-action@" in text, "Trivy image scan required"
      assert 'exit-code: "1"' in text, "Trivy must fail the build on findings"
      assert "CRITICAL,HIGH" in text, "gate on HIGH/CRITICAL"
      assert "ignore-unfixed: true" in text, "gate on fixable vulns only"
      assert "anchore/sbom-action@" in text, "SBOM (Syft) required (Container-Hardening v1 §8.29)"
  ```
- [ ] (2) Run it — expect FAIL: `uv run pytest tests/unit/test_ci_container_security.py -q` → `FileNotFoundError: .github/workflows/container-security.yml`.
- [ ] (3) Minimal implementation — create `.github/workflows/container-security.yml`:
  ```yaml
  name: Container security

  on:
    pull_request:
    push:
      branches:
        - main

  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: true

  permissions:
    contents: read

  jobs:
    image-scan:
      name: Build, scan (Trivy), SBOM (Syft)
      runs-on: ubuntu-latest
      timeout-minutes: 20
      permissions:
        contents: read
        security-events: write # upload Trivy SARIF to code scanning
      env:
        IMAGE: genefoundry-router:${{ github.sha }}
      steps:
        - name: Checkout
          uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

        - name: Build production image
          run: docker build -f docker/Dockerfile --target production -t "$IMAGE" .

        - name: Generate SBOM (Syft)
          uses: anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610 # v0.24.0
          with:
            image: ${{ env.IMAGE }}
            format: spdx-json
            output-file: ./sbom.spdx.json
            artifact-name: router-image-sbom
            upload-artifact: true

        - name: Scan image (Trivy) — fail on fixable HIGH/CRITICAL
          uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
          with:
            image-ref: ${{ env.IMAGE }}
            format: sarif
            output: trivy-results.sarif
            exit-code: "1"
            severity: CRITICAL,HIGH
            ignore-unfixed: true
            vuln-type: os,library

        - name: Upload Trivy SARIF to code scanning
          if: ${{ always() && !github.event.repository.private }}
          uses: github/codeql-action/upload-sarif@ed410739ba306e4ebe5e123421a6bd694e494a2b # v4
          with:
            sarif_file: trivy-results.sarif
  ```
- [ ] (4) Run — expect PASS: `uv run pytest tests/unit/test_ci_container_security.py -q` (3 passed). Lint the YAML: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/container-security.yml'))"` (no output = valid).
- [ ] (5) Commit: `ci(security): build + Trivy scan + Syft SBOM for the router image`

---

### Task 4: Make dependency-review a hard gate in `security.yml`

Closes the audit finding that `security.yml:55 continue-on-error: true` makes dependency-review advisory-only. The action fails the job by default on the threshold; `fail-on-severity: high` (per <https://github.com/actions/dependency-review-action>) blocks PRs that introduce HIGH/CRITICAL-vuln deps. (The `if: github.event_name == 'pull_request'` guard on line 44 stays — the action only runs on PRs by design; push coverage comes from CodeQL + the Task 3 image scan.)

**Files**
- Test: `tests/unit/test_ci_dependency_review.py` (Create)
- Modify: `.github/workflows/security.yml:53-55`

**Interfaces**
- Consumes: `.github/workflows/security.yml` (YAML).
- Produces: the `dependency-review` job's `actions/dependency-review-action` step has `with.fail-on-severity == "high"` and no `continue-on-error`.

Steps:
- [ ] (1) Write the failing test `tests/unit/test_ci_dependency_review.py`:
  ```python
  """security.yml dependency-review must be a hard gate, not advisory."""

  from pathlib import Path

  import yaml


  def _dep_review_step() -> dict:
      doc = yaml.safe_load(Path(".github/workflows/security.yml").read_text(encoding="utf-8"))
      for step in doc["jobs"]["dependency-review"]["steps"]:
          if "dependency-review-action" in str(step.get("uses", "")):
              return step
      raise AssertionError("dependency-review-action step not found")


  def test_dependency_review_is_a_gate() -> None:
      step = _dep_review_step()
      assert "continue-on-error" not in step, "dependency-review must not be advisory-only"
      assert step.get("with", {}).get("fail-on-severity") == "high", "gate on HIGH+ severity"
  ```
- [ ] (2) Run it — expect FAIL: `uv run pytest tests/unit/test_ci_dependency_review.py -q`. The current step has `continue-on-error: true` and no `with:` block.
- [ ] (3) Minimal implementation — edit `.github/workflows/security.yml`. Replace lines 53–55:
  ```yaml
        - name: Dependency Review
          uses: actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294 # v5.0.0
          continue-on-error: true
  ```
  with:
  ```yaml
        - name: Dependency Review
          uses: actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294 # v5.0.0
          with:
            fail-on-severity: high
  ```
- [ ] (4) Run — expect PASS: `uv run pytest tests/unit/test_ci_dependency_review.py -q` (1 passed).
- [ ] (5) Commit: `ci(security): gate dependency-review on HIGH severity (drop advisory-only)`

---

### Task 5: Correct the `drift.py` docstring to name `ci/fleet-baseline.json`

Closes the audit's two-baseline confusion. Current `genefoundry_router/drift.py:6-9` says the module diffs against `scripts/snapshot_fleet.py → tests/fixtures/fleet_manifest.json`, but the scheduled drift workflow (`drift.yml:46`) actually pins `ci/fleet-baseline.json`. `tests/fixtures/fleet_manifest.json` is the *offline fake-fleet fixture* (used by unit/e2e tests); `ci/fleet-baseline.json` is the *live* CI baseline (`make snapshot-baseline`, enforced by `tests/unit/test_ci_fleet_baseline.py`). Name both correctly.

**Files**
- Test: `tests/unit/test_drift_docstring.py` (Create)
- Modify: `genefoundry_router/drift.py:6-9` (module docstring only — no code change)

**Interfaces**
- Consumes: `genefoundry_router.drift.__doc__`.
- Produces: docstring contains `ci/fleet-baseline.json`.

Steps:
- [ ] (1) Write the failing test `tests/unit/test_drift_docstring.py`:
  ```python
  """drift.py docstring must name the CI-pinned baseline the drift workflow actually diffs."""

  import genefoundry_router.drift as drift_mod


  def test_docstring_names_the_ci_baseline() -> None:
      doc = drift_mod.__doc__ or ""
      assert "ci/fleet-baseline.json" in doc, "name the live CI baseline (drift.yml pins it)"
      assert "tests/fixtures/fleet_manifest.json" in doc, "keep the offline fixture correctly described"
  ```
- [ ] (2) Run it — expect FAIL: `uv run pytest tests/unit/test_drift_docstring.py -q`. The docstring names only `tests/fixtures/fleet_manifest.json`.
- [ ] (3) Minimal implementation — in `genefoundry_router/drift.py`, replace the docstring sentence (lines 6–9):
  ```python
  This module fingerprints each tool's security-relevant definition (name + description +
  inputSchema) and diffs a live snapshot against a reviewed, pinned manifest
  (``scripts/snapshot_fleet.py`` → ``tests/fixtures/fleet_manifest.json``). Surface any
  drift loudly; treat ``changed`` as the highest-signal event.
  ```
  with:
  ```python
  This module fingerprints each tool's security-relevant definition (name + description +
  inputSchema) and diffs a live snapshot against a reviewed, pinned baseline. The scheduled
  drift workflow (``.github/workflows/drift.yml``) pins ``ci/fleet-baseline.json`` — the live,
  full-fleet baseline produced by ``make snapshot-baseline``; ``tests/fixtures/fleet_manifest.json``
  is the offline fake-fleet fixture the unit/e2e tests pin against. Surface any drift loudly;
  treat ``changed`` as the highest-signal event.
  ```
- [ ] (4) Run — expect PASS: `uv run pytest tests/unit/test_drift_docstring.py -q` (1 passed).
- [ ] (5) Commit: `docs(drift): name ci/fleet-baseline.json as the CI-pinned drift baseline`

---

### Task 6: Remove the stale `enable once …` hpo comment from `servers.yaml`

Closes the last audit item. `servers.yaml:28` ends `# build complete + DB artifact published (db-v2026-06-06); enable once hpo-link.genefoundry.org is deployed`, but hpo is enabled (no `enabled: false`) and already in `ci/fleet-baseline.json` (34 hpo tool entries; `tests/unit/test_ci_fleet_baseline.py` enforces baseline⇔enabled lockstep). The "enable once … deployed" clause is stale.

**Files**
- Test: `tests/unit/test_servers_yaml_comments.py` (Create)
- Modify: `servers.yaml:28`

**Interfaces**
- Consumes: `servers.yaml` (text) + `genefoundry_router.config.load_registry`.
- Produces: no `enable once` substring; hpo still present and enabled.

Steps:
- [ ] (1) Write the failing test `tests/unit/test_servers_yaml_comments.py`:
  ```python
  """servers.yaml must not carry stale 'enable once … deployed' comments for live backends."""

  import os
  from pathlib import Path

  from genefoundry_router.config import load_registry


  def test_no_stale_enable_once_comment() -> None:
      text = Path("servers.yaml").read_text(encoding="utf-8")
      assert "enable once" not in text, "remove stale 'enable once … deployed' comment"


  def test_hpo_is_live_and_enabled() -> None:
      registry = load_registry("servers.yaml", os.environ)
      hpo = next(b for b in registry if b.namespace == "hpo")
      assert hpo.enabled, "hpo is deployed + baselined; it must stay enabled"
  ```
- [ ] (2) Run it — expect FAIL: `uv run pytest tests/unit/test_servers_yaml_comments.py -q`. `test_no_stale_enable_once_comment` fails on the line-28 clause.
- [ ] (3) Minimal implementation — edit `servers.yaml:28`, replacing the trailing comment
  `# build complete + DB artifact published (db-v2026-06-06); enable once hpo-link.genefoundry.org is deployed`
  with
  `# deployed (hpo-link.genefoundry.org); DB artifact db-v2026-06-06, baselined in ci/fleet-baseline.json`
  (leave the YAML mapping itself unchanged).
- [ ] (4) Run — expect PASS: `uv run pytest tests/unit/test_servers_yaml_comments.py -q` (2 passed).
- [ ] (5) Commit: `docs(registry): drop stale hpo 'enable once deployed' comment (hpo is live)`

---

## Acceptance criteria

Concrete asserts/commands (all must pass):

- [ ] `uv run pytest tests/unit/test_dockerfile_digest_pinned.py tests/unit/test_ci_container_security.py tests/unit/test_ci_dependency_review.py tests/unit/test_ci_dependabot.py tests/unit/test_drift_docstring.py tests/unit/test_servers_yaml_comments.py -q` → all pass.
- [ ] `grep -c '@sha256:' docker/Dockerfile` ≥ 3 and `! grep -q ':latest' docker/Dockerfile`.
- [ ] `test -f .github/workflows/container-security.yml` and `grep -q 'aquasecurity/trivy-action@' .github/workflows/container-security.yml` and `grep -q 'anchore/sbom-action@' .github/workflows/container-security.yml` and `grep -q 'exit-code: "1"' .github/workflows/container-security.yml`.
- [ ] `.github/workflows/container-security.yml` triggers contain both `pull_request:` and `push:`.
- [ ] `! grep -q 'continue-on-error' .github/workflows/security.yml` and `grep -q 'fail-on-severity: high' .github/workflows/security.yml`.
- [ ] `test -f .github/dependabot.yml` and it lists ecosystems `docker`, `github-actions`, `uv`.
- [ ] `python -c "import genefoundry_router.drift as d; assert 'ci/fleet-baseline.json' in d.__doc__"`.
- [ ] `! grep -q 'enable once' servers.yaml`.
- [ ] `make ci-local` passes (format-check, ruff, lint-loc, mypy, unit + integration) — no new module approaches the 600-LOC budget (all new files are tests/CI config).
- [ ] Each new file maps 1:1 to a Container-Hardening v1 DoD bullet: image scan (§8.27), SBOM (§8.29), digest pin (§1.2), Dependabot watch (§8.28), dependency-review gate.

## Risk & rollback

- **NOT execution-gated.** Every task ends at a local `git commit`; this plan performs **no `git push`, no redeploy, and no destructive remote operation**. Pushing the branch / opening the PR / merging is a separate, human-gated step outside this plan.
- **First real CI run is on the PR, not in this plan.** When the branch is eventually pushed, `container-security.yml` builds and scans the image for the first time. If `python:3.12-slim` ships a *fixable* HIGH/CRITICAL CVE, the Trivy gate will (correctly) fail — remediate by bumping the digest (Dependabot opens that PR) or, only with written justification, narrowing `severity`/adding a scoped `.trivyignore`. `ignore-unfixed: true` already prevents un-actionable unfixed-CVE failures.
- **SARIF upload needs code scanning enabled.** The upload step is guarded `!github.event.repository.private`; on a private repo without GitHub code scanning it is skipped, but the build-failing Trivy gate still runs. No behavioural risk to the router runtime — zero `genefoundry_router/` source changes.
- **Rollback:** each task is one atomic commit; `git revert <sha>` (or delete the new file) restores the prior state with no migration. The Dockerfile digest pin is the only build-affecting change — revert restores the floating tag.

## Effort

~0.5 day for an engineer with zero repo context. Six small TDD tasks (3 created CI/config files, 4 edited lines across Dockerfile/security.yml + 2 docstring/comment edits, 6 contract tests). Longest pole is verifying the Trivy/Syft workflow renders and the digest builds (`make docker-build`); the doc/comment tasks are minutes each.
