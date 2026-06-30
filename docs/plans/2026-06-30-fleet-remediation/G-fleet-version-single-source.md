# Fleet: Single-Source the Version so serverInfo == Package Version Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Make MCP `serverInfo.version` and the FastAPI `/health` `version` field equal the installed package version in all five backends (`autopvs1-link`, `gtex-link`, `hgnc-link`, `spliceailookup-link`, `mgi-link`) by sourcing every version string from `importlib.metadata.version(...)`, with a per-repo unit test that prevents regression.

**Architecture:** Each repo gets a single source of truth: the package `__init__.py` resolves `__version__` from installed metadata (`importlib.metadata.version(dist)`, where `pyproject [project].version` is the upstream source of truth), and every consumer (FastMCP constructor `version=`, FastAPI `version=`, `/health`, capabilities, CLI, User-Agent) reads that one value. FastMCP 3.x advertises `serverInfo.version` from its constructor `version=` kwarg; when omitted it silently falls back to the FastMCP **library** version (3.4.2 today), which is the live bug in four of the five repos. A new `tests/unit/test_version_single_source.py` per repo asserts the whole chain (pyproject → installed metadata → `__version__` → serverInfo → `/health`) so a future hardcoded literal cannot drift again.

**Tech Stack:** Python 3.12+, `uv`, `fastmcp` 3.4.2 (verified installed), `fastapi`, `pytest`, `importlib.metadata` (stdlib), `tomllib` (stdlib, for the pyproject cross-check in tests).

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

---

## Verified evidence (2026-06-30, against current code + installed venvs)

Each repo's `pyproject [project].version` is already correctly installed into its editable venv metadata (`importlib.metadata.version(dist)` returns the pyproject value). The drift is entirely in **hardcoded literals** that shadow that metadata:

| repo | dist name | pyproject / `importlib.metadata` (correct) | `__version__` literal | live MCP `serverInfo.version` today | live `/health` today |
|---|---|---|---|---|---|
| autopvs1-link | `autopvs1-link` | `1.3.1` | `1.3.0` (`__init__.py:17`) | `1.3.0` (`server_info.py:19 SERVER_VERSION`) | `1.3.0` (`server_manager.py:78`) |
| gtex-link | `gtex-link` | `2.0.1` | `2.0.0` (`__init__.py:3`) | **`3.4.2`** (facade omits `version=`) | `2.0.0` (`app.py:93`) |
| hgnc-link | `hgnc-link` | `1.0.1` | `1.0.0` (`__init__.py:5`) | **`3.4.2`** (facade omits `version=`) | `1.0.0` (`app.py:66` via `build_info()`) |
| spliceailookup-link | `spliceailookup-link` | `2.2.1` | `2.2.1` (`__init__.py:3`) | **`3.4.2`** (facade omits `version=`) | `2.2.1` (`server_manager.py:88`) |
| mgi-link | `mgi-link` | `0.3.1` | `0.3.0` (`__init__.py:5`) | **`3.4.2`** (facade omits `version=`) | `0.3.0` (`app.py:64/75`) |

Cross-checked downstream corruption: the router's pinned drift baseline `genefoundry-router/ci/fleet-baseline.json` (`snapshot_meta.source: live`, captured `2026-06-30T05:34:05Z`) records `autopvs1=1.3.0`, `gtex=3.4.2`, `hgnc=3.4.2`, `spliceai=3.4.2`, `mgi=3.4.2` — i.e. four backends are pinned to the FastMCP library version. That baseline must be re-pinned **after** these fixes are deployed (see Risk & rollback — EXECUTION-GATED).

Key facts verified empirically against `fastmcp==3.4.2` in each repo's `.venv`:
- `FastMCP(name=..., version="2.0.1").version == "2.0.1"`; `FastMCP(name=...).version == "3.4.2"` (lib fallback). The public attribute `mcp.version` is the serverInfo value (equal to `mcp._mcp_server.create_initialization_options().server_version`).
- `gtex-link` (`gtex_link/mcp/metadata.py:33`) and `spliceailookup-link` (`spliceailookup_link/mcp/resources.py:21`) already use the exact `importlib.metadata.version(...)` pattern for their capabilities surface — internal precedent for the fix; only the `__init__`/facade/server_manager literals are wrong.
- No test asserts a *stale* package-version literal. `gtex tests/unit/test_gtex_service.py:68 result.version == "2.0.0"` is the upstream **GTEx Portal API** service version (Broad Institute), unrelated to the package. The shared conformance probe (`tests/conformance/conformance.py`) checks `serverInfo.name` and that `/health` *has* `version`/`transport` keys, but never their values — so it does not guard correctness.

---

## File Structure

### gtex-link
- **Modify** `gtex_link/__init__.py` — `__version__` resolved from `importlib.metadata.version("gtex-link")` (single source).
- **Modify** `gtex_link/mcp/facade.py` — pass `version=__version__` to `FastMCP(...)`.
- **Modify** `gtex_link/mcp/metadata.py` — `_server_version()` delegates to `__version__` (dedup; keeps capabilities aligned).
- **Create** `tests/unit/test_version_single_source.py` — regression guard.

### hgnc-link
- **Modify** `hgnc_link/__init__.py` — `__version__` from `importlib.metadata.version("hgnc-link")`.
- **Modify** `hgnc_link/mcp/facade.py` — pass `version=__version__` to `FastMCP(...)`.
- **Create** `tests/unit/test_version_single_source.py` — regression guard.

### mgi-link
- **Modify** `mgi_link/__init__.py` — `__version__` from `importlib.metadata.version("mgi-link")`.
- **Modify** `mgi_link/mcp/facade.py` — pass `version=__version__` to `FastMCP(...)`.
- **Modify** `CHANGELOG.md` — cut the `[Unreleased]` section to `[0.3.1] - 2026-06-30` (docs nit from audit).
- **Create** `tests/unit/test_version_single_source.py` — regression guard.

### spliceailookup-link
- **Modify** `spliceailookup_link/__init__.py` — `__version__` from `importlib.metadata.version("spliceailookup-link")`.
- **Modify** `spliceailookup_link/mcp/facade.py` — pass `version=__version__` to `FastMCP(...)`.
- **Modify** `spliceailookup_link/server_manager.py` — FastAPI host `version="2.0.0"` → `version=__version__`.
- **Modify** `spliceailookup_link/mcp/resources.py` — `_server_version()` delegates to `__version__` (dedup).
- **Create** `tests/unit/test_version_single_source.py` — regression guard.

### autopvs1-link
- **Modify** `autopvs1_link/__init__.py` — `__version__` from `importlib.metadata.version("autopvs1-link")`.
- **Modify** `autopvs1_link/mcp/server_info.py` — `SERVER_VERSION = __version__` (facade already passes `version=SERVER_VERSION`).
- **Modify** `autopvs1_link/config.py` — `MCPConfig.version` and `Settings.version` defaults `"1.2.0"` → `__version__`.
- **Create** `tests/unit/test_version_single_source.py` — regression guard.

---

## Task 1: gtex-link — single-source the version

**Files**
- Create `gtex-link/tests/unit/test_version_single_source.py`
- Modify `gtex-link/gtex_link/__init__.py:3`
- Modify `gtex-link/gtex_link/mcp/facade.py:35` (and add import)
- Modify `gtex-link/gtex_link/mcp/metadata.py:33-37`

**Interfaces**
- Consumes: `importlib.metadata.version("gtex-link")` → `str` (installed metadata; pyproject is the upstream source of truth).
- Produces: `gtex_link.__version__: str`; `create_gtex_mcp().version == version("gtex-link")`; `GET /health → {"version": version("gtex-link"), ...}`.

Steps:

- [ ] **(1) Write the failing test.** Create `gtex-link/tests/unit/test_version_single_source.py`:
  ```python
  """Guard: pyproject -> installed metadata -> __version__ -> serverInfo -> /health are one value."""

  from __future__ import annotations

  import tomllib
  from importlib.metadata import version
  from pathlib import Path

  from fastapi.testclient import TestClient

  from gtex_link import __version__
  from gtex_link.app import create_app
  from gtex_link.mcp.facade import create_gtex_mcp

  DIST = "gtex-link"


  def _pyproject_version() -> str:
      pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
      return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


  def test_pyproject_is_the_single_source() -> None:
      assert version(DIST) == _pyproject_version()


  def test_dunder_version_is_metadata_derived() -> None:
      assert __version__ == version(DIST)


  def test_mcp_server_info_version_matches_package() -> None:
      assert create_gtex_mcp().version == version(DIST)


  def test_health_version_matches_package() -> None:
      resp = TestClient(create_app()).get("/health")
      assert resp.status_code == 200
      assert resp.json()["version"] == version(DIST)
  ```
- [ ] **(2) Run it — expect FAIL.** `cd /home/bernt-popp/development/gtex-link && uv run pytest tests/unit/test_version_single_source.py -q`
  Expected: `test_mcp_server_info_version_matches_package` FAILS (`'3.4.2' != '2.0.1'`) and `test_dunder_version_is_metadata_derived` / `test_health_version_matches_package` FAIL (`'2.0.0' != '2.0.1'`).
- [ ] **(3) Minimal implementation.**
  `gtex_link/__init__.py` (replace lines 1-4):
  ```python
  """GTEx-Link: High-performance MCP/API server for GTEx Portal."""

  from __future__ import annotations

  from importlib.metadata import PackageNotFoundError, version

  try:
      __version__ = version("gtex-link")
  except PackageNotFoundError:  # pragma: no cover - source tree without install
      __version__ = "0.0.0"

  __author__ = "GTEx-Link Development Team"
  ```
  `gtex_link/mcp/facade.py` — add `from gtex_link import __version__` to the import block (after line 5 `from fastmcp import FastMCP`) and pass it at line 35:
  ```python
      mcp = FastMCP(
          name="gtex-link",
          version=__version__,
          instructions=GTEX_SERVER_INSTRUCTIONS,
          mask_error_details=True,
      )
  ```
  `gtex_link/mcp/metadata.py` — dedup `_server_version()` (lines 33-37) onto the one source:
  ```python
  def _server_version() -> str:
      from gtex_link import __version__

      return __version__
  ```
  (The `from importlib.metadata import PackageNotFoundError, version` at line 8 may now be unused — remove it if ruff flags F401; `version` is no longer referenced in this module.)
- [ ] **(4) Run — expect PASS.** `cd /home/bernt-popp/development/gtex-link && uv run pytest tests/unit/test_version_single_source.py -q && make ci-local`
  Expected: 4 passed; existing `tests/test_api/test_health.py` (asserts `/api/health` `== __version__`) and `tests/unit/test_app.py` still pass; ruff/mypy/lint-loc clean.
- [ ] **(5) Commit.** `git switch -c fix/version-single-source && git commit -am "fix(version): single-source serverInfo/health from importlib.metadata"`
  Conventional-commit body: `serverInfo.version and /health now derive from importlib.metadata.version('gtex-link'); facade was silently advertising the FastMCP lib version (3.4.2).`

---

## Task 2: hgnc-link — single-source the version

**Files**
- Create `hgnc-link/tests/unit/test_version_single_source.py`
- Modify `hgnc-link/hgnc_link/__init__.py:5`
- Modify `hgnc-link/hgnc_link/mcp/facade.py:21` (and add import)

**Interfaces**
- Consumes: `importlib.metadata.version("hgnc-link")` → `str`.
- Produces: `hgnc_link.__version__`; `create_hgnc_mcp().version == version("hgnc-link")`; `GET /health → {**build_info()}` where `build_info()["version"] == version("hgnc-link")`.

Steps:

- [ ] **(1) Write the failing test.** Create `hgnc-link/tests/unit/test_version_single_source.py`:
  ```python
  """Guard: pyproject -> installed metadata -> __version__ -> serverInfo -> /health are one value."""

  from __future__ import annotations

  import tomllib
  from importlib.metadata import version
  from pathlib import Path

  from fastapi.testclient import TestClient

  from hgnc_link import __version__
  from hgnc_link.app import create_app
  from hgnc_link.buildinfo import build_info
  from hgnc_link.mcp.facade import create_hgnc_mcp

  DIST = "hgnc-link"


  def _pyproject_version() -> str:
      pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
      return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


  def test_pyproject_is_the_single_source() -> None:
      assert version(DIST) == _pyproject_version()


  def test_dunder_version_is_metadata_derived() -> None:
      assert __version__ == version(DIST)
      assert build_info()["version"] == version(DIST)


  def test_mcp_server_info_version_matches_package() -> None:
      assert create_hgnc_mcp().version == version(DIST)


  def test_health_version_matches_package() -> None:
      resp = TestClient(create_app()).get("/health")
      assert resp.status_code == 200
      assert resp.json()["version"] == version(DIST)
  ```
- [ ] **(2) Run it — expect FAIL.** `cd /home/bernt-popp/development/hgnc-link && uv run pytest tests/unit/test_version_single_source.py -q`
  Expected: `test_mcp_server_info_version_matches_package` FAILS (`'3.4.2' != '1.0.1'`); `test_dunder_version_is_metadata_derived` / `test_health_version_matches_package` FAIL (`'1.0.0' != '1.0.1'`).
- [ ] **(3) Minimal implementation.**
  `hgnc_link/__init__.py` (replace whole file):
  ```python
  """hgnc-link: an MCP/API server grounding gene nomenclature in the HGNC dataset."""

  from __future__ import annotations

  from importlib.metadata import PackageNotFoundError, version

  try:
      __version__ = version("hgnc-link")
  except PackageNotFoundError:  # pragma: no cover - source tree without install
      __version__ = "0.0.0"

  __all__ = ["__version__"]
  ```
  `hgnc_link/mcp/facade.py` — add `from hgnc_link import __version__` to the import block (after line 5) and pass it at line 21:
  ```python
      mcp = FastMCP(
          name="hgnc-link",
          version=__version__,
          instructions=HGNC_SERVER_INSTRUCTIONS,
          mask_error_details=True,
      )
  ```
  (All other consumers — `app.py`, `buildinfo.py:98`, `mcp/capabilities.py:87`, `config.py:56/156` UA — already read `__version__`, so they correct automatically.)
- [ ] **(4) Run — expect PASS.** `cd /home/bernt-popp/development/hgnc-link && uv run pytest tests/unit/test_version_single_source.py -q && make ci-local`
  Expected: 4 passed; ruff/mypy/lint-loc clean.
- [ ] **(5) Commit.** `git switch -c fix/version-single-source && git commit -am "fix(version): single-source serverInfo/health from importlib.metadata"`

---

## Task 3: mgi-link — single-source the version + cut the changelog

**Files**
- Create `mgi-link/tests/unit/test_version_single_source.py`
- Modify `mgi-link/mgi_link/__init__.py:5`
- Modify `mgi-link/mgi_link/mcp/facade.py:22` (and add import)
- Modify `mgi-link/CHANGELOG.md` (cut `[Unreleased]` → `[0.3.1]`)

**Interfaces**
- Consumes: `importlib.metadata.version("mgi-link")` → `str`.
- Produces: `mgi_link.__version__`; `create_mgi_mcp().version == version("mgi-link")`; `GET /health → {"version": version("mgi-link"), ...}`.

Steps:

- [ ] **(1) Write the failing test.** Create `mgi-link/tests/unit/test_version_single_source.py`:
  ```python
  """Guard: pyproject -> installed metadata -> __version__ -> serverInfo -> /health are one value."""

  from __future__ import annotations

  import tomllib
  from importlib.metadata import version
  from pathlib import Path

  from fastapi.testclient import TestClient

  from mgi_link import __version__
  from mgi_link.app import create_app
  from mgi_link.mcp.facade import create_mgi_mcp

  DIST = "mgi-link"


  def _pyproject_version() -> str:
      pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
      return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


  def test_pyproject_is_the_single_source() -> None:
      assert version(DIST) == _pyproject_version()


  def test_dunder_version_is_metadata_derived() -> None:
      assert __version__ == version(DIST)


  def test_mcp_server_info_version_matches_package() -> None:
      assert create_mgi_mcp().version == version(DIST)


  def test_health_version_matches_package() -> None:
      resp = TestClient(create_app()).get("/health")
      assert resp.status_code == 200
      assert resp.json()["version"] == version(DIST)
  ```
- [ ] **(2) Run it — expect FAIL.** `cd /home/bernt-popp/development/mgi-link && uv run pytest tests/unit/test_version_single_source.py -q`
  Expected: `test_mcp_server_info_version_matches_package` FAILS (`'3.4.2' != '0.3.1'`); `test_dunder_version_is_metadata_derived` / `test_health_version_matches_package` FAIL (`'0.3.0' != '0.3.1'`).
- [ ] **(3) Minimal implementation.**
  `mgi_link/__init__.py` (replace whole file):
  ```python
  """mgi-link: an MCP/API server grounding mouse genetics in the MGI dataset."""

  from __future__ import annotations

  from importlib.metadata import PackageNotFoundError, version

  try:
      __version__ = version("mgi-link")
  except PackageNotFoundError:  # pragma: no cover - source tree without install
      __version__ = "0.0.0"

  __all__ = ["__version__"]
  ```
  `mgi_link/mcp/facade.py` — add `from mgi_link import __version__` to the import block (after line 5) and pass it at line 22:
  ```python
      mcp = FastMCP(
          name="mgi-link",
          version=__version__,
          instructions=MGI_SERVER_INSTRUCTIONS,
          mask_error_details=True,
      )
  ```
  `CHANGELOG.md` — rename the `## [Unreleased]` heading (line 5) to `## [0.3.1] - 2026-06-30` so the changelog matches `pyproject` (the audit's docs nit). Leave a fresh empty `## [Unreleased]` above it if the repo's convention keeps one.
- [ ] **(4) Run — expect PASS.** `cd /home/bernt-popp/development/mgi-link && uv run pytest tests/unit/test_version_single_source.py -q && make ci-local`
  Expected: 4 passed; ruff/mypy/lint-loc clean. (`mgi_link/mcp/capabilities.py:92` and `app.py` already read `__version__`, so they correct automatically.)
- [ ] **(5) Commit.** `git switch -c fix/version-single-source && git commit -am "fix(version): single-source serverInfo/health from importlib.metadata; cut CHANGELOG 0.3.1"`

---

## Task 4: spliceailookup-link — single-source the version (facade + FastAPI host)

**Files**
- Create `spliceailookup-link/tests/unit/test_version_single_source.py`
- Modify `spliceailookup-link/spliceailookup_link/__init__.py:3`
- Modify `spliceailookup-link/spliceailookup_link/mcp/facade.py:51` (and add import)
- Modify `spliceailookup-link/spliceailookup_link/server_manager.py:65`
- Modify `spliceailookup-link/spliceailookup_link/mcp/resources.py:21-25`

**Interfaces**
- Consumes: `importlib.metadata.version("spliceailookup-link")` → `str`.
- Produces: `spliceailookup_link.__version__`; `create_spliceai_mcp(service_factory=...).version == version(...)`; FastAPI host `app.version == version(...)`; `GET /health → {"version": version(...), ...}`.

Steps:

- [ ] **(1) Write the failing test.** Create `spliceailookup-link/tests/unit/test_version_single_source.py` (mirrors the existing `tests/unit/test_server_manager.py` host-app pattern):
  ```python
  """Guard: pyproject -> installed metadata -> __version__ -> serverInfo -> FastAPI host are one value."""

  from __future__ import annotations

  import asyncio
  import logging
  import tomllib
  from importlib.metadata import version
  from pathlib import Path

  from fastapi.testclient import TestClient

  from spliceailookup_link import __version__
  from spliceailookup_link.config import ServerConfig
  from spliceailookup_link.mcp.facade import create_spliceai_mcp
  from spliceailookup_link.server_manager import UnifiedServerManager

  DIST = "spliceailookup-link"


  def _pyproject_version() -> str:
      pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
      return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


  def test_pyproject_is_the_single_source() -> None:
      assert version(DIST) == _pyproject_version()


  def test_dunder_version_is_metadata_derived() -> None:
      assert __version__ == version(DIST)


  def test_mcp_server_info_version_matches_package() -> None:
      mcp = create_spliceai_mcp(service_factory=lambda: object())  # type: ignore[arg-type]
      assert mcp.version == version(DIST)


  def test_fastapi_host_and_health_version_match_package() -> None:
      manager = UnifiedServerManager()
      manager.logger = logging.getLogger("test")
      manager._current_transport = "streamable-http-stateless"
      app = asyncio.run(manager._create_fastapi_app(ServerConfig(transport="unified")))
      assert app.version == version(DIST)
      with TestClient(app) as client:
          resp = client.get("/health")
          assert resp.status_code == 200
          assert resp.json()["version"] == version(DIST)
  ```
- [ ] **(2) Run it — expect FAIL.** `cd /home/bernt-popp/development/spliceailookup-link && uv run pytest tests/unit/test_version_single_source.py -q`
  Expected: `test_mcp_server_info_version_matches_package` FAILS (`'3.4.2' != '2.2.1'`); `app.version` assertion FAILS (`'2.0.0' != '2.2.1'`). (`__version__`/`/health` already equal `2.2.1`, so those two assertions pass — the test still fails overall.)
- [ ] **(3) Minimal implementation.**
  `spliceailookup_link/__init__.py` (replace whole file):
  ```python
  """spliceailookup-link: MCP + REST server for SpliceAI / Pangolin splice prediction."""

  from __future__ import annotations

  from importlib.metadata import PackageNotFoundError, version

  try:
      __version__ = version("spliceailookup-link")
  except PackageNotFoundError:  # pragma: no cover - source tree without install
      __version__ = "0.0.0"
  ```
  `spliceailookup_link/mcp/facade.py` — add `from spliceailookup_link import __version__` to the import block and pass it at line 51:
  ```python
      mcp = FastMCP(
          name="spliceailookup-link",
          version=__version__,
          instructions=_INSTRUCTIONS,
          mask_error_details=True,
      )
  ```
  `spliceailookup_link/server_manager.py:65` — `__version__` is already imported at line 20; replace the hardcoded literal:
  ```python
          app = FastAPI(
              title="spliceailookup-link MCP Host",
              description="Thin FastAPI host exposing /health and mounting the MCP HTTP app at /mcp.",
              version=__version__,
              lifespan=lifespan,
              docs_url=None,
              redoc_url=None,
              openapi_url=None,
          )
  ```
  `spliceailookup_link/mcp/resources.py` — dedup `_server_version()` (lines 21-25) onto the one source (remove the now-unused `from importlib.metadata import PackageNotFoundError, version` at line 7 if ruff flags F401):
  ```python
  def _server_version() -> str:
      from spliceailookup_link import __version__

      return __version__
  ```
- [ ] **(4) Run — expect PASS.** `cd /home/bernt-popp/development/spliceailookup-link && uv run pytest tests/unit/test_version_single_source.py tests/unit/test_server_manager.py -q && make ci-local`
  Expected: new test 4 passed; existing `test_server_manager.py` (`test_health_has_version_and_transport_fields`) still passes; ruff/mypy/lint-loc clean.
- [ ] **(5) Commit.** `git switch -c fix/version-single-source && git commit -am "fix(version): single-source serverInfo + FastAPI host from importlib.metadata"`

---

## Task 5: autopvs1-link — collapse three version constants onto one source

**Files**
- Create `autopvs1-link/tests/unit/test_version_single_source.py`
- Modify `autopvs1-link/autopvs1_link/__init__.py:17`
- Modify `autopvs1-link/autopvs1_link/mcp/server_info.py:19`
- Modify `autopvs1-link/autopvs1_link/config.py:157` and `:186`

**Interfaces**
- Consumes: `importlib.metadata.version("autopvs1-link")` → `str`.
- Produces: `autopvs1_link.__version__`; `server_info.SERVER_VERSION == __version__`; `build_mcp_server().version == version(...)` (facade already passes `version=SERVER_VERSION`); `settings.version == version(...)` (FastAPI OpenAPI `version=settings.version` at `server_manager.py:49` + CLI); `GET /health → {"version": version(...), ...}` (`server_manager.py:78` reads `__version__`).

Steps:

- [ ] **(1) Write the failing test.** Create `autopvs1-link/tests/unit/test_version_single_source.py`:
  ```python
  """Guard: pyproject -> installed metadata -> __version__/SERVER_VERSION/settings -> serverInfo -> /health."""

  from __future__ import annotations

  import tomllib
  from importlib.metadata import version
  from pathlib import Path

  from fastapi.testclient import TestClient

  from autopvs1_link import __version__
  from autopvs1_link.config import settings
  from autopvs1_link.mcp.facade import build_mcp_server
  from autopvs1_link.mcp.server_info import SERVER_VERSION

  DIST = "autopvs1-link"


  def _pyproject_version() -> str:
      pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
      return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


  def test_pyproject_is_the_single_source() -> None:
      assert version(DIST) == _pyproject_version()


  def test_all_constants_are_metadata_derived() -> None:
      assert __version__ == version(DIST)
      assert SERVER_VERSION == version(DIST)
      assert settings.version == version(DIST)


  def test_mcp_server_info_version_matches_package() -> None:
      assert build_mcp_server().version == version(DIST)


  def test_health_version_matches_package() -> None:
      from autopvs1_link.server_manager import app

      resp = TestClient(app).get("/health")
      assert resp.status_code == 200
      assert resp.json()["version"] == version(DIST)
  ```
- [ ] **(2) Run it — expect FAIL.** `cd /home/bernt-popp/development/autopvs1-link && uv run pytest tests/unit/test_version_single_source.py -q`
  Expected: `test_all_constants_are_metadata_derived` FAILS (`__version__='1.3.0'`, `SERVER_VERSION='1.3.0'`, `settings.version='1.2.0'` vs `'1.3.1'`); `test_mcp_server_info_version_matches_package` FAILS (`'1.3.0' != '1.3.1'`); `/health` FAILS (`'1.3.0' != '1.3.1'`).
- [ ] **(3) Minimal implementation.**
  `autopvs1_link/__init__.py` — add the metadata import and replace the literal at line 17 (keep the existing defusedxml block intact):
  ```python
  """AutoPVS1-Link package init."""

  from __future__ import annotations

  import warnings
  from importlib.metadata import PackageNotFoundError, version

  import defusedxml

  with warnings.catch_warnings():
      warnings.filterwarnings(
          "ignore",
          message=r"defusedxml\.cElementTree is deprecated.*",
          category=DeprecationWarning,
      )
      defusedxml.defuse_stdlib()  # type: ignore[attr-defined]

  try:
      __version__ = version("autopvs1-link")
  except PackageNotFoundError:  # pragma: no cover - source tree without install
      __version__ = "0.0.0"
  ```
  `autopvs1_link/mcp/server_info.py:19` — replace `SERVER_VERSION = "1.3.0"` with a re-export of the one source:
  ```python
  from autopvs1_link import __version__ as SERVER_VERSION
  ```
  (Place this import right after `from __future__ import annotations`. `SERVER_VERSION` stays a module-level `str`, so the facade `FastMCP(version=SERVER_VERSION)`, `health_tool`, `capabilities` presenter, and the `registries.SERVER_VERSION` monkeypatch in `test_capabilities_presenter.py:233` all keep working. `test_server_info.py:80-81` — `SERVER_VERSION.count(".") == 2` and digit-only parts — holds for `"1.3.1"`.)
  `autopvs1_link/config.py` — add `from autopvs1_link import __version__` near the top imports, then change both defaults:
  - line 157: `version: str = Field(default=__version__, description="MCP server version")`
  - line 186: `version: str = Field(default=__version__, description="Application version")`
  (`envelope.py:16` already imports `from autopvs1_link import __version__`, proving this import is cycle-free in this package.)
- [ ] **(4) Run — expect PASS.** `cd /home/bernt-popp/development/autopvs1-link && uv run pytest tests/unit/test_version_single_source.py tests/unit/mcp/test_server_info.py tests/unit/mcp/test_envelope.py tests/unit/mcp/test_capabilities_presenter.py -q && make ci-local`
  Expected: new test 4 passed; `test_envelope.py:18` (`meta.server_version == SERVER_VERSION`) still passes because both sides now resolve to `__version__`; ruff/mypy/lint-loc clean.
- [ ] **(5) Commit.** `git switch -c fix/version-single-source && git commit -am "fix(version): collapse __init__/SERVER_VERSION/config onto importlib.metadata"`

---

## Acceptance criteria

For each of the five repos, after `uv sync --group dev`:

1. `uv run pytest tests/unit/test_version_single_source.py -q` → all assertions pass.
2. `uv run python -c "from importlib.metadata import version as v; import <pkg>; print(<pkg>.__version__ == v('<dist>'))"` → `True`.
3. MCP serverInfo equals the package version. Spot-check:
   - gtex/hgnc/mgi: `uv run python -c "from importlib.metadata import version as v; from <pkg>.mcp.facade import create_<x>_mcp; print(create_<x>_mcp().version == v('<dist>'))"` → `True`.
   - spliceai: `create_spliceai_mcp(service_factory=lambda: object()).version == version("spliceailookup-link")`.
   - autopvs1: `build_mcp_server().version == version("autopvs1-link")`.
4. `/health` `version` equals the package version (behavioral, via `TestClient` in the guard test): `resp.json()["version"] == version("<dist>")`.
5. No hardcoded package-version literal remains in `__init__.py` / `server_info.py` / `facade.py` / `server_manager.py` / `config.py`. Verify per repo: `! grep -RnE '= *"[0-9]+\.[0-9]+\.[0-9]+"' <pkg>/__init__.py <pkg>/mcp/server_info.py <pkg>/mcp/facade.py <pkg>/server_manager.py <pkg>/config.py 2>/dev/null` returns nothing (the literal `"0.0.0"` fallback is allowed inside the `except PackageNotFoundError` branch only).
6. `make ci-local` passes in each repo.

Fleet-level (router): after redeploy (see Risk & rollback), `genefoundry-router/ci/fleet-baseline.json` re-pinned shows `autopvs1=1.3.1`, `gtex=2.0.1`, `hgnc=1.0.1`, `spliceai=2.2.1`, `mgi=0.3.1` (no more `3.4.2` entries), and `make validate` / the drift workflow report no version drift.

## Risk & rollback

- **Per-repo risk: low.** Pure metadata wiring; no behavior change to tools or data paths. The `except PackageNotFoundError → "0.0.0"` fallback keeps a non-installed source checkout importable. Each task is one atomic commit; rollback is `git revert <sha>` (or `git switch main` since these are commits on a `fix/version-single-source` branch, never pushed by this plan).
- **Import-cycle risk: none observed.** `config.py`/`server_info.py` importing `from autopvs1_link import __version__` is already done by `envelope.py`; package `__init__` does not import `config`/`mcp`. Verify with `uv run python -c "import autopvs1_link, autopvs1_link.config, autopvs1_link.mcp.server_info"` after the change.
- **Editable-install caveat:** `importlib.metadata.version()` reads the `.dist-info` written at install time, so a version bump in `pyproject.toml` only propagates after `uv sync` (re-installs the editable package). `make ci-local` runs `uv sync` first, so CI always sees the current pyproject; the `test_pyproject_is_the_single_source` assertion deliberately fails locally if a dev bumps `pyproject` without re-syncing — a useful reminder, not a defect.
- **EXECUTION-GATED downstream (out of scope for these planning-only tasks):** the realized fleet fix requires (a) pushing each `fix/version-single-source` branch + opening PRs, (b) merging, (c) **rebuilding/redeploying the five live backends** on the VPS, and only then (d) **re-pinning the router baseline** `genefoundry-router/ci/fleet-baseline.json` (`make` capture against the live fleet) so the drift check stops flagging `3.4.2`. Steps (a)-(d) are push/redeploy/destructive-remote operations and MUST NOT be performed as part of this plan — they are the deploy gate after the five repos' guard tests are green. Re-pinning the baseline **before** redeploy would itself corrupt the baseline (it would record the stale live values); re-pin strictly after redeploy.

## Effort

~0.5 day total. Per repo: gtex/hgnc/mgi ~20-30 min each (2 edits + 1 test); spliceai ~40 min (3 edits + host-app test); autopvs1 ~45 min (3 edits across `__init__`/`server_info`/`config` + test, plus re-running the three existing version tests). Plus the gated redeploy + baseline re-pin (~30 min, separate deploy window).

---

### Research cited
- `importlib.metadata.version()` + `PackageNotFoundError` canonical runtime single-source pattern — Python docs: https://docs.python.org/3/library/importlib.metadata.html
- PyPA "Single-sourcing the Project Version" (recommends reading the version from installed metadata at runtime; `pyproject [project].version` is the source of truth): https://packaging.python.org/en/latest/discussions/single-source-version/
- FastMCP `serverInfo.version` behavior — **verified empirically against the installed `fastmcp==3.4.2`** in each repo venv: constructor kwarg `version=` sets `FastMCP(...).version` / `serverInfo.server_version`; omitting it falls back to the FastMCP library version (`3.4.2`). Project ref: https://gofastmcp.com
