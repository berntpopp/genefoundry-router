# Fleet: Loopback-Bind the Base docker-compose Host Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal** Stop the base `docker/docker-compose.yml` in seven `-link` repos (gencc, clingen, hpo, mavedb, metadome, mgi, pubtator) from publishing the unauthenticated backend on `0.0.0.0` by binding every published host port to the loopback interface (`127.0.0.1`), so copying the base file to a server can never expose a backend on the public IP.

**Architecture** Each `-link` repo ships a layered Compose stack: a base `docker/docker-compose.yml` (local/dev convenience, publishes a host port) plus `docker-compose.prod.yml` / `docker-compose.npm.yml` overlays that already drop the host mapping with `ports: !reset []` (expose-only, fronted by a reverse proxy). The fix is a one-line, per-repo change to the base file's short-syntax port mapping — prepend `127.0.0.1:` to the existing `HOST_PORT:CONTAINER_PORT` string — guarded by a small text/YAML unit test. The prod/npm overlays are not touched, so the documented production path is unchanged, and CI conformance (which probes `http://127.0.0.1:<port>/health`) and `make docker-url` (which prints `http://127.0.0.1:<port>`) keep working because loopback-bound ports are reachable from the host's own loopback.

**Tech Stack** Docker Compose v2 (Compose Specification short-syntax `ports`); Python 3.12+ with `uv`; `pytest` + `pyyaml` (already in every repo's `uv.lock`) for the guard test; `ruff` + `mypy`; `make ci-local`.

## Global Constraints

Python 3.12+ with uv (uv sync --group dev, uv run); modern typing (X|None, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (scripts/check_file_size.py via make lint-loc); 'make ci-local' must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

Additional constraints specific to this plan:

- **Smallest correct change.** Only the base `docker-compose.yml` host-port mapping changes (plus clingen's standalone `docker-compose.dev.yml`, which is the same footgun). Do NOT edit `docker-compose.prod.yml` or `docker-compose.npm.yml` — they already expose-only via `ports: !reset []` and must stay byte-for-byte unchanged.
- **No behavior change for the documented production path.** Production is `docker compose -f docker-compose.yml -f docker-compose.prod.yml ...` (or the standalone `.npm.yml`); both reset `ports`, so loopback-binding the base mapping has zero effect there.
- **Preserve the env-var default.** Keep the existing `${<REPO>_LINK_HOST_PORT:-<default>}` interpolation exactly; only prepend the `127.0.0.1:` host-IP segment.
- **Planning artifact only.** Execution stops at local commits + `make ci-local`. Do NOT `git push`, open PRs, build/run Docker, or redeploy. The live VPS already runs the (unchanged) prod/npm overlays, so no redeploy is required for the fix to take effect.
- Each task is an independent repo and can be executed in parallel (no shared state).

## File Structure

| Path | Created/Modified | Responsibility |
| --- | --- | --- |
| `gencc-link/docker/docker-compose.yml` | Modified | Loopback-bind the published host port (line ~11). |
| `gencc-link/tests/test_docker_compose_loopback.py` | Created | Guard: base compose publishes only on `127.0.0.1`. |
| `clingen-link/docker/docker-compose.yml` | Modified | Loopback-bind the published host port (line ~11). |
| `clingen-link/docker/docker-compose.dev.yml` | Modified | Loopback-bind the standalone dev stack's host port (line ~11). |
| `clingen-link/tests/unit/test_docker_compose_loopback.py` | Created | Guard: base + dev compose publish only on `127.0.0.1`. |
| `hpo-link/docker/docker-compose.yml` | Modified | Loopback-bind the published host port (line ~16). |
| `hpo-link/tests/unit/test_docker_compose_loopback.py` | Created | Guard: base compose publishes only on `127.0.0.1`. |
| `mavedb-link/docker/docker-compose.yml` | Modified | Loopback-bind the published host port (line ~32). |
| `mavedb-link/tests/unit/test_docker_compose_loopback.py` | Created | Guard: base compose publishes only on `127.0.0.1`. |
| `metadome-link/docker/docker-compose.yml` | Modified | Loopback-bind the published host port (line ~11). |
| `metadome-link/tests/test_docker_compose_loopback.py` | Created | Guard: base compose publishes only on `127.0.0.1`. |
| `mgi-link/docker/docker-compose.yml` | Modified | Loopback-bind the published host port (line ~16). |
| `mgi-link/tests/unit/test_docker_compose_loopback.py` | Created | Guard: base compose publishes only on `127.0.0.1`. |
| `pubtator-link/docker/docker-compose.yml` | Modified | Loopback-bind both published host ports (postgres line ~21, server line ~66). |
| `pubtator-link/tests/unit/docker/test_compose_hardening.py` | Modified | Add guard: every base-compose published port binds `127.0.0.1`. |

## Reference: the guard-test template (used verbatim, with per-repo `ROOT` depth)

Every new test file uses this body. The ONLY per-repo difference is the `parents[N]` depth (stated in each task) and the docstring repo name. It loads the base compose with `yaml.safe_load` (the base files contain no custom `!reset` tag, unlike the prod overlay, so `safe_load` is valid) and asserts every published host-port mapping starts with `127.0.0.1:`.

```python
"""Security guard: the base docker-compose must not publish the unauthenticated
backend on all interfaces (0.0.0.0). It is dev/local-only and must loopback-bind
the host port; production reaches the backend only via the router/reverse proxy.
Research use only; not clinical decision support."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # <-- per-repo depth; see the task


def test_base_compose_binds_published_ports_to_loopback() -> None:
    compose = yaml.safe_load(
        (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    )
    published = [
        (name, mapping)
        for name, svc in compose["services"].items()
        for mapping in (svc.get("ports") or [])
    ]
    assert published, "base compose should publish at least one host port for local/dev use"
    for name, mapping in published:
        assert isinstance(mapping, str), (
            f"{name} uses long-form ports; extend this guard to read host_ip"
        )
        assert mapping.startswith("127.0.0.1:"), (
            f"{name} publishes {mapping!r} on all interfaces; bind the "
            "unauthenticated backend to loopback (127.0.0.1) — Docker otherwise "
            "binds 0.0.0.0 and bypasses the host firewall. Production reaches it "
            "only via the router/reverse proxy."
        )
```

The reusable inline comment block prepended above each base-file `ports:` stanza is:

```yaml
    # Dev/local only — loopback-bound (127.0.0.1) so copying this file to a
    # server never publishes the unauthenticated backend on the public IP
    # (Docker would otherwise bind 0.0.0.0 and bypass the host firewall).
    # Production fronts the container with a reverse proxy via the prod/npm
    # overlays (ports: !reset []). Backends are unauthenticated by design.
```

---

### Task 1: gencc-link — loopback-bind the base host port

**Files**
- Modify `gencc-link/docker/docker-compose.yml:10-11` (the `ports:` stanza).
- Test (Create) `gencc-link/tests/test_docker_compose_loopback.py` — flat `tests/` layout (`testpaths = ["tests"]`), so `ROOT = Path(__file__).resolve().parents[1]`.

**Interfaces**
- Consumes: `docker/docker-compose.yml` services map (`gencc-link` service, `ports` list).
- Produces: published host port mapping `"127.0.0.1:${GENCC_LINK_HOST_PORT:-8000}:8000"`.

- [ ] (1) Write the failing test. Create `gencc-link/tests/test_docker_compose_loopback.py` using the template with `ROOT = Path(__file__).resolve().parents[1]` and docstring naming gencc-link.
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/gencc-link && uv run pytest tests/test_docker_compose_loopback.py -v`. Expected: `AssertionError: gencc-link publishes '${GENCC_LINK_HOST_PORT:-8000}:8000' on all interfaces; bind ... to loopback (127.0.0.1) ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml`, replace:
  ```yaml
      ports:
        - "${GENCC_LINK_HOST_PORT:-8000}:8000"
  ```
  with:
  ```yaml
      # Dev/local only — loopback-bound (127.0.0.1) so copying this file to a
      # server never publishes the unauthenticated backend on the public IP
      # (Docker would otherwise bind 0.0.0.0 and bypass the host firewall).
      # Production fronts the container with a reverse proxy via the prod/npm
      # overlays (ports: !reset []). Backends are unauthenticated by design.
      ports:
        - "127.0.0.1:${GENCC_LINK_HOST_PORT:-8000}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/gencc-link && uv run pytest tests/test_docker_compose_loopback.py -v` → `1 passed`. Then `make ci-local` → all green.
- [ ] (5) Commit: `fix(docker): loopback-bind base compose host port (no public-IP exposure)`.

---

### Task 2: clingen-link — loopback-bind the base AND standalone dev host ports

**Files**
- Modify `clingen-link/docker/docker-compose.yml:10-11` (base `ports:` stanza).
- Modify `clingen-link/docker/docker-compose.dev.yml:10-11` (standalone dev stack `ports:` stanza — same footgun; `docker-compose.dev.yml` is a self-contained stack named `clingen-link-dev`, not a prod/npm overlay, so it is in scope).
- Test (Create) `clingen-link/tests/unit/test_docker_compose_loopback.py` — `tests/unit/` layout, so `ROOT = Path(__file__).resolve().parents[2]`.

**Interfaces**
- Consumes: `docker/docker-compose.yml` (`clingen-link` service) and `docker/docker-compose.dev.yml` (`clingen_link_dev` service) `ports` lists.
- Produces: both mappings prefixed `127.0.0.1:` over `${CLINGEN_LINK_HOST_PORT:-8479}:8000`.

- [ ] (1) Write the failing test. Create `clingen-link/tests/unit/test_docker_compose_loopback.py` using the template with `ROOT = Path(__file__).resolve().parents[2]`, plus a second test covering the dev stack:
  ```python
  def test_dev_compose_binds_published_ports_to_loopback() -> None:
      compose = yaml.safe_load(
          (ROOT / "docker" / "docker-compose.dev.yml").read_text(encoding="utf-8")
      )
      for name, svc in compose["services"].items():
          for mapping in (svc.get("ports") or []):
              assert isinstance(mapping, str) and mapping.startswith("127.0.0.1:"), (
                  f"{name} publishes {mapping!r} on all interfaces; loopback-bind it"
              )
  ```
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/clingen-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v`. Expected: both tests fail with `... publishes '${CLINGEN_LINK_HOST_PORT:-8479}:8000' on all interfaces ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml` replace the base stanza:
  ```yaml
      ports:
        - "${CLINGEN_LINK_HOST_PORT:-8479}:8000"
  ```
  with the loopback-bound stanza (prepend the same five-line dev-only comment, then):
  ```yaml
      ports:
        - "127.0.0.1:${CLINGEN_LINK_HOST_PORT:-8479}:8000"
  ```
  And in `docker/docker-compose.dev.yml` replace:
  ```yaml
      ports:
        - "${CLINGEN_LINK_HOST_PORT:-8479}:8000"
  ```
  with:
  ```yaml
      ports:
        # Standalone dev stack — loopback-bound; not for server deployment.
        - "127.0.0.1:${CLINGEN_LINK_HOST_PORT:-8479}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/clingen-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v` → `2 passed`. Then `make ci-local` → green.
- [ ] (5) Commit: `fix(docker): loopback-bind base + dev compose host ports (no public-IP exposure)`.

---

### Task 3: hpo-link — loopback-bind the base host port

**Files**
- Modify `hpo-link/docker/docker-compose.yml:15-16` (the `ports:` stanza; note the file also defines a `refresh` service under `profiles: ["tools"]` that publishes no ports — the guard tolerates it via `svc.get("ports") or []`).
- Test (Create) `hpo-link/tests/unit/test_docker_compose_loopback.py` — `tests/unit/` layout, `ROOT = Path(__file__).resolve().parents[2]`.

**Interfaces**
- Consumes: `docker/docker-compose.yml` (`hpo-link` service) `ports` list.
- Produces: `"127.0.0.1:${HPO_LINK_HOST_PORT:-8000}:8000"`.

- [ ] (1) Write the failing test. Create `hpo-link/tests/unit/test_docker_compose_loopback.py` using the template with `ROOT = ...parents[2]`.
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/hpo-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v`. Expected: `... hpo-link publishes '${HPO_LINK_HOST_PORT:-8000}:8000' on all interfaces ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml` replace:
  ```yaml
      ports:
        - "${HPO_LINK_HOST_PORT:-8000}:8000"
  ```
  with (prepend the five-line dev-only comment, then):
  ```yaml
      ports:
        - "127.0.0.1:${HPO_LINK_HOST_PORT:-8000}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/hpo-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v` → `1 passed`. Then `make ci-local` → green.
- [ ] (5) Commit: `fix(docker): loopback-bind base compose host port (no public-IP exposure)`.

---

### Task 4: mavedb-link — loopback-bind the base host port

**Files**
- Modify `mavedb-link/docker/docker-compose.yml:28-32` (the `ports:` stanza already carries a 3-line fleet-convention comment at lines 29-31; keep it and only change the mapping line 32).
- Test (Create) `mavedb-link/tests/unit/test_docker_compose_loopback.py` — `tests/unit/` layout, `ROOT = Path(__file__).resolve().parents[2]` (mirrors the existing `tests/unit/test_npm_deploy_config.py` style).

**Interfaces**
- Consumes: `docker/docker-compose.yml` (`mavedb-link` service) `ports` list.
- Produces: `"127.0.0.1:${MAVEDB_LINK_HOST_PORT:-8023}:8000"`.

- [ ] (1) Write the failing test. Create `mavedb-link/tests/unit/test_docker_compose_loopback.py` using the template with `ROOT = ...parents[2]`.
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/mavedb-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v`. Expected: `... mavedb-link publishes '${MAVEDB_LINK_HOST_PORT:-8023}:8000' on all interfaces ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml` keep the existing comment lines and replace ONLY the mapping line:
  ```yaml
        - "${MAVEDB_LINK_HOST_PORT:-8023}:8000"
  ```
  with:
  ```yaml
        # Loopback-bound: dev/local only; production uses the prod/npm overlays.
        - "127.0.0.1:${MAVEDB_LINK_HOST_PORT:-8023}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/mavedb-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v` → `1 passed`. Then `make ci-local` → green.
- [ ] (5) Commit: `fix(docker): loopback-bind base compose host port (no public-IP exposure)`.

---

### Task 5: metadome-link — loopback-bind the base host port

**Files**
- Modify `metadome-link/docker/docker-compose.yml:10-11` (the `ports:` stanza).
- Test (Create) `metadome-link/tests/test_docker_compose_loopback.py` — flat `tests/` layout (`testpaths = ["tests"]`), `ROOT = Path(__file__).resolve().parents[1]`.

**Interfaces**
- Consumes: `docker/docker-compose.yml` (`metadome-link` service) `ports` list.
- Produces: `"127.0.0.1:${METADOME_LINK_HOST_PORT:-8000}:8000"`.

- [ ] (1) Write the failing test. Create `metadome-link/tests/test_docker_compose_loopback.py` using the template with `ROOT = Path(__file__).resolve().parents[1]`.
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/metadome-link && uv run pytest tests/test_docker_compose_loopback.py -v`. Expected: `... metadome-link publishes '${METADOME_LINK_HOST_PORT:-8000}:8000' on all interfaces ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml` replace:
  ```yaml
      ports:
        - "${METADOME_LINK_HOST_PORT:-8000}:8000"
  ```
  with (prepend the five-line dev-only comment, then):
  ```yaml
      ports:
        - "127.0.0.1:${METADOME_LINK_HOST_PORT:-8000}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/metadome-link && uv run pytest tests/test_docker_compose_loopback.py -v` → `1 passed`. Then `make ci-local` → green.
- [ ] (5) Commit: `fix(docker): loopback-bind base compose host port (no public-IP exposure)`.

---

### Task 6: mgi-link — loopback-bind the base host port

**Files**
- Modify `mgi-link/docker/docker-compose.yml:15-16` (the `ports:` stanza; the file also has a `refresh` service under `profiles: ["tools"]` with no ports — tolerated by the guard). Note: mgi-link has NO `docker-compose.prod.yml`; production is the standalone `docker-compose.npm.yml` (expose-only) — do NOT touch it.
- Test (Create) `mgi-link/tests/unit/test_docker_compose_loopback.py` — `tests/unit/` layout, `ROOT = Path(__file__).resolve().parents[2]`.

**Interfaces**
- Consumes: `docker/docker-compose.yml` (`mgi-link` service) `ports` list.
- Produces: `"127.0.0.1:${MGI_LINK_HOST_PORT:-8000}:8000"`.

- [ ] (1) Write the failing test. Create `mgi-link/tests/unit/test_docker_compose_loopback.py` using the template with `ROOT = ...parents[2]`.
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/mgi-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v`. Expected: `... mgi-link publishes '${MGI_LINK_HOST_PORT:-8000}:8000' on all interfaces ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml` replace:
  ```yaml
      ports:
        - "${MGI_LINK_HOST_PORT:-8000}:8000"
  ```
  with (prepend the five-line dev-only comment, then):
  ```yaml
      ports:
        - "127.0.0.1:${MGI_LINK_HOST_PORT:-8000}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/mgi-link && uv run pytest tests/unit/test_docker_compose_loopback.py -v` → `1 passed`. Then `make ci-local` → green.
- [ ] (5) Commit: `fix(docker): loopback-bind base compose host port (no public-IP exposure)`.

---

### Task 7: pubtator-link — loopback-bind BOTH base host ports (server + postgres)

**Files**
- Modify `pubtator-link/docker/docker-compose.yml`: the `pubtator-postgres` service `ports` (line ~21, `"${PUBTATOR_LINK_POSTGRES_PORT:-5434}:5432"`) AND the `pubtator-link` service `ports` (line ~66, `"${PUBTATOR_LINK_PORT:-8000}:8000"`). Postgres is published on `0.0.0.0` with default credentials, so it is the same footgun class and must be loopback-bound too.
- Test (Modify) `pubtator-link/tests/unit/docker/test_compose_hardening.py` — extend the existing module (it already binds `BASE = Path("docker/docker-compose.yml").read_text()` at the top) with a YAML-based loopback guard. Do NOT touch the `PROD`/`NPM` assertions.

**Interfaces**
- Consumes: `docker/docker-compose.yml` services map (`pubtator-postgres`, `pubtator-link`).
- Produces: `"127.0.0.1:${PUBTATOR_LINK_POSTGRES_PORT:-5434}:5432"` and `"127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000"`.

- [ ] (1) Write the failing test. In `tests/unit/docker/test_compose_hardening.py`, add `import yaml` to the imports and append:
  ```python
  def test_base_compose_binds_published_ports_to_loopback() -> None:
      compose = yaml.safe_load(BASE)
      published = [
          (name, mapping)
          for name, svc in compose["services"].items()
          for mapping in (svc.get("ports") or [])
      ]
      assert published, "base compose should publish at least one host port"
      for name, mapping in published:
          assert isinstance(mapping, str) and mapping.startswith("127.0.0.1:"), (
              f"{name} publishes {mapping!r} on all interfaces; loopback-bind it "
              "(127.0.0.1) so the unauthenticated backend is never exposed on the host IP"
          )
  ```
- [ ] (2) Run it; expect FAIL. Command: `cd /home/bernt-popp/development/pubtator-link && uv run pytest tests/unit/docker/test_compose_hardening.py::test_base_compose_binds_published_ports_to_loopback -v`. Expected: `AssertionError: pubtator-postgres publishes '${PUBTATOR_LINK_POSTGRES_PORT:-5434}:5432' on all interfaces ...`.
- [ ] (3) Minimal implementation. In `docker/docker-compose.yml` replace the postgres mapping:
  ```yaml
      ports:
        - "${PUBTATOR_LINK_POSTGRES_PORT:-5434}:5432"
  ```
  with:
  ```yaml
      # Dev/local only — loopback-bound; never publish Postgres on a server.
      ports:
        - "127.0.0.1:${PUBTATOR_LINK_POSTGRES_PORT:-5434}:5432"
  ```
  and replace the server mapping:
  ```yaml
      # Port mapping
      ports:
        - "${PUBTATOR_LINK_PORT:-8000}:8000"
  ```
  with:
  ```yaml
      # Port mapping — dev/local only, loopback-bound (127.0.0.1) so copying this
      # file to a server never publishes the unauthenticated backend on the public
      # IP. Production uses the prod/npm overlays (ports: !reset []).
      ports:
        - "127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000"
  ```
- [ ] (4) Run; expect PASS. Command: `cd /home/bernt-popp/development/pubtator-link && uv run pytest tests/unit/docker/test_compose_hardening.py -v` → all pass (existing + new). Then `make ci-local` → green.
- [ ] (5) Commit: `fix(docker): loopback-bind base compose server + postgres ports (no public-IP exposure)`.

---

## Acceptance criteria

- **Each base file binds loopback.** For every repo: `grep -nE '127\.0\.0\.1:\$\{' docker/docker-compose.yml` returns the published mapping(s), and `python -c "import yaml,sys; c=yaml.safe_load(open('docker/docker-compose.yml')); print([m for s in c['services'].values() for m in (s.get('ports') or [])])"` shows every entry starting with `127.0.0.1:`. Concretely: gencc `127.0.0.1:${GENCC_LINK_HOST_PORT:-8000}:8000`; clingen `127.0.0.1:${CLINGEN_LINK_HOST_PORT:-8479}:8000` (base + dev.yml); hpo `127.0.0.1:${HPO_LINK_HOST_PORT:-8000}:8000`; mavedb `127.0.0.1:${MAVEDB_LINK_HOST_PORT:-8023}:8000`; metadome `127.0.0.1:${METADOME_LINK_HOST_PORT:-8000}:8000`; mgi `127.0.0.1:${MGI_LINK_HOST_PORT:-8000}:8000`; pubtator `127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000` and `127.0.0.1:${PUBTATOR_LINK_POSTGRES_PORT:-5434}:5432`.
- **Prod/npm overlays unchanged.** `git diff --name-only` in each repo lists only `docker/docker-compose.yml` (clingen also `docker/docker-compose.dev.yml`) and the test file — never `docker-compose.prod.yml` or `docker-compose.npm.yml`.
- **Guard test passes and fails-closed.** The new `test_docker_compose_loopback.py` (or pubtator's extended `test_compose_hardening.py`) passes; reverting the mapping makes it fail with the public-IP message.
- **No regression.** `make ci-local` is green in all seven repos.
- **CI conformance unaffected.** `.github/workflows/conformance.yml` still probes `http://127.0.0.1:${MCP_PORT}/health` via `make docker-up`; a loopback-bound published port is reachable on the host's own loopback, so the probe is unaffected (verified: every repo's conformance job and `make docker-url` already target `127.0.0.1`). Default `MCP_PORT` values stay correct (gencc/hpo/metadome/mgi/pubtator = 8000, clingen = 8479, mavedb = 8023) because the `${...:-<default>}` interpolation is preserved.
- **Disclaimer preserved.** No research-use / not-clinical-decision-support text removed; new comments reinforce "unauthenticated by design, reachable only via router/proxy".

## Risk & rollback

- **NOT execution-gated.** The plan stops at local commits + `make ci-local`. There is no `git push`, no PR, no Docker build/run, no redeploy. The live VPS already runs the unchanged prod/npm overlays (expose-only), so production is already safe and needs no redeploy; this change only removes the footgun from the repo file that an operator might copy to a server.
- **Risk: loopback bind breaks an unusual access pattern.** Very low. If a developer relied on reaching the dev container from another host on the LAN (e.g. `http://<host-lan-ip>:8000`), that no longer works — by design. Documented workaround: front it with the prod/npm reverse-proxy overlay (the supported path), or temporarily set the host IP via an env override. CI and `make docker-url` are unaffected (both use `127.0.0.1`).
- **Risk: YAML `!reset` tag crashes `yaml.safe_load`.** Not applicable — the guard only loads the base file, which has no custom tag. (Loading a prod overlay with `safe_load` would raise `ConstructorError`; the plan never does this.)
- **Rollback.** Per repo: `git revert <commit>` (single atomic commit, two files for clingen/pubtator). No data, image, or deployment state is touched, so revert is instantaneous and total.

## Effort

~1.5–2.5 hours total. Seven near-identical, parallelizable one-line changes plus a small guard test each; the dominant cost is running `make ci-local` (which builds the venv and runs the full suite) once per repo. No research, schema, or behavior design required — the fix is mechanical and the Compose loopback-binding syntax (`HOST_IP:HOST_PORT:CONTAINER_PORT`, e.g. `127.0.0.1:8001:8001`; omitting the host IP binds `0.0.0.0` and "bypass[es] host firewall rules") is confirmed by the Docker Compose Specification: https://docs.docker.com/reference/compose-file/services/#ports and https://docs.docker.com/engine/network/#published-ports .
