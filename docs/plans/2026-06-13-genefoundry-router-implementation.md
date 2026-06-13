# GeneFoundry Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `genefoundry-router`, a thin FastMCP 3.x aggregator that federates the ~13 GeneFoundry `*-link` MCP backends behind one Streamable-HTTP endpoint (`genefoundry`) with collision-free namespacing, BM25 tool-search, pluggable auth, and a config-driven registry.

**Architecture:** A FastMCP server (`genefoundry`) mounts one namespaced proxy per backend (`mount(create_proxy(url), namespace=token)` → `<token>_<tool>`). A thin FastAPI host wraps the MCP ASGI app to serve `/health` + `/metrics`, mirroring the `-link` fleet. Config comes from a committed `servers.yaml` (structure) + gitignored `.env` (URLs/secrets). Tool overload is controlled by `BM25SearchTransform`; non-compliant backends are normalized by per-backend `ToolTransform`s until the source repos adopt the Tool-Naming Standard v1.

**Tech Stack:** Python 3.12+, `uv` + hatchling, `fastmcp>=3.2.0,<4.0.0`, `fastapi` + `uvicorn`, `typer` (CLI), `structlog` (JSON logs), `prometheus-client`, `asgi-correlation-id`, `pydantic` + `pydantic-settings`, `PyYAML`; `ruff` + `mypy` + `pytest` (coverage ≥70).

---

## Convention notes & verified deviations (READ FIRST)

These were resolved before planning. They are deliberate; do not "fix" them back.

1. **CLI = `typer`, logging = `structlog`.** *(DECIDED — typer + structlog IS the fleet convention; verified across all 14 `-link` repos.)* `structlog.configure()` is used in **10/14** repos (autopvs1, gencc, genereviews, gtex, hgnc, litvar, mgi, pubtator, stringdb, uniprot) and in **100% of the current-generation** repos (mgi/uniprot 2026-06-13; hgnc/litvar/gtex 06-12); `typer.Typer()` in **7/14** including the newest (mgi, hgnc, litvar). `../gnomad-link` uses **argparse + stdlib** but is a **stale outlier** (last touched May 31, pre-dates the structlog wave) — do NOT treat its CLI/logging as the template. **CLI + logging template = `../stringdb-link`** (pure `typer.Typer(help=…, no_args_is_help=True)` + `rich.Console`, plus a `logging_config.py` that wires `structlog`) and **`../litvar-link`** (typer with a `cli_commands/` subpackage). Use `../gnomad-link` only for the parts that ARE uniform fleet-wide: `uv`+hatchling, ruff/mypy/pytest config, the `/mcp`+`/health` FastAPI-host pattern, Docker overlays, the 600-LOC discipline.

2. **`create_proxy(url, cache_ttl=…)` does NOT exist — it raises `TypeError`.** Verified against installed `fastmcp 3.4.2`. `create_proxy(target, **settings)` forwards `**settings` to `FastMCP.__init__`, which rejects `cache_ttl`. The locked pattern `mount(create_proxy(url), namespace=token)` works and caches metadata at the **default 300 s** TTL. To honor a *non-default* per-backend `cache_ttl` from `servers.yaml`, register via `server.add_provider(ProxyProvider(client_factory=…, cache_ttl=…), namespace=token)` instead (Task 14). Both paths produce identical `<token>_<tool>` names; both satisfy the locked "per-backend proxy, namespaced" decision.

3. **`mount` uses `namespace=`, not `prefix=`.** Signature (3.4.2): `mount(server, namespace=None, ...)`. `prefix=` still exists but is **deprecated** and warns. Delimiter is a single underscore: namespace `gnomad` + tool `get_variant_details` → `gnomad_get_variant_details`.

4. **`create_proxy` import:** `from fastmcp.server import create_proxy`. `FastMCP.as_proxy()` is deprecated → do not use. A bare URL string is a valid `target` (auto-wrapped in a `ProxyClient`).

5. **Proxy discovery is TTL-based, not push.** `ProxyProvider` does **not** auto-subscribe to upstream `notifications/tools/list_changed`; it refreshes on `list_*` calls after `cache_ttl` expiry. `BM25SearchTransform` lazily rebuilds its index when the tool-set hash changes (verified in source), so a forced re-list refreshes search too. So spec §10's "list_changed subscription" is implemented as **`cache_ttl` + a polling re-list wired into the app lifespan** (Task 22 builds it; Task 23 wires it). True push would need a custom `ProxyClient(message_handler=…)`; out of scope for v1.

6. **Streamable HTTP:** `transport="http"` (canonical; `"streamable-http"` is an alias). Get the ASGI app with `mcp.http_app(path=…)` (NOT `asgi_app()`), and **forward its `.lifespan`** to the outer FastAPI app or the session manager never initializes. Default MCP sub-path mounts at `/mcp/`.

7. **Verified FastMCP imports** (fastmcp 3.4.2) used by this plan:
   - `from fastmcp import FastMCP`
   - `from fastmcp.server import create_proxy`
   - `from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider`
   - `from fastmcp.server.transforms.search.bm25 import BM25SearchTransform`
   - `from fastmcp.server.transforms.tool_transform import ToolTransform`
   - `from fastmcp.tools.tool_transform import ToolTransformConfig, ArgTransformConfig`
   - `from fastmcp.server.auth import MultiAuth, OAuthProxy, TokenVerifier`
   - `from fastmcp.server.auth.providers.jwt import JWTVerifier`
   - `from fastmcp.server.middleware import Middleware, MiddlewareContext` (instrumentation — `on_call_tool`/`on_list_tools` hooks)
   - `await server.list_tools()` (async; **there is no `server._tool_manager`**) + `server.add_tool_transformation(name, ToolTransformConfig(...))`
   - `from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings` (Origin/Host allowlist; from the `mcp` SDK, not `fastmcp`)
   - **Re-verify each symbol at execution time** with a 1-line import smoke check (Task 2, Step "import smoke") before relying on it. If an import path moved, fix it once in the affected task and note it.

---

## Code-review revisions (R1) — verified against `fastmcp 3.4.2` + MCP 2025-11-25

This plan was reviewed (Codex) and every claim re-verified against the installed `fastmcp 3.4.2` source and the **current** MCP spec revision **2025-11-25** (not 2025-06-18). Folded-in outcomes:

- **R1.1 — `BackendDef` must accept `transport`.** The committed `servers.yaml` sets `defaults.transport: http`; `BackendDef` has `extra="forbid"`, so the merged dict would raise `ValidationError` (reproduced). Fixed in **Task 4** by adding `transport: Literal["http"] = "http"` with a validator that rejects SSE. *(Confirmed bug.)*
- **R1.2 — normalization used a nonexistent private field.** `server._tool_manager` does not exist on 3.4.2. **Task 15** now enumerates with the public async `await server.list_tools()` and applies `server.add_tool_transformation(...)`; the normalization step runs in the lifespan startup (**Task 23**), tolerant of an unreachable backend. *(Confirmed bug.)*
- **R1.3 — README ordering.** `pyproject.readme = "README.md"` means `uv sync` (Task 2) builds the project and needs the file; the Dockerfile copies it too. **Task 1** now creates a minimal `README.md` stub; **Task 30** expands it. *(Confirmed bug — broader than the Docker-only catch.)*
- **R1.4 — Origin validation (MCP MUST).** Transport spec 2025-11-25: servers **MUST** validate the `Origin` header (403 if present-and-invalid; DNS-rebinding defense). New `security.py` middleware created + wired in **Task 12**; end-to-end test in **Task 24**. Absent `Origin` passes (non-browser clients send none). Binding `0.0.0.0` *inside the container* behind nginx-proxy-manager is **not** a violation (the localhost-bind SHOULD is scoped to local servers).
- **R1.5 — OAuth correctness + audience.** `OAuthProxy.token_verifier` is **required** (no default) → the old builder could pass `None`. **Task 19** now requires `GF_JWT_*` in `oauth` mode, adds `GF_PUBLIC_BASE_URL` for `base_url`/`resource_base_url` (the canonical resource URI **MUST** be the public URL behind the proxy), and validates audience (`GF_JWT_AUDIENCE`). FastMCP auto-serves Protected-Resource-Metadata + `WWW-Authenticate` once a provider is attached (`JWTVerifier.get_well_known_routes` exists) — **Task 25** asserts the 401 + `/.well-known/oauth-protected-resource` contract.
- **R1.6 — no token passthrough (MCP MUST, gateway-critical).** A federating MCP server **MUST NOT** forward the client's token to upstreams (confused-deputy). Documented as an invariant in **Task 10/20**; backends are currently public/no-auth so nothing is forwarded, but the proxy client must never relay caller credentials.
- **R1.7 — metrics were dead; health was shallow.** Counters were defined but never incremented. **Task 17** adds a FastMCP `MetricsMiddleware` (`on_call_tool`/`on_list_tools`) for tool-call/search/latency, and enriches `/health` with cached per-backend reachability.
- **R1.8 — registry `tags` were unused.** **Task 15** injects backend `tags` into mounted tools so `BM25SearchTransform` indexes them (also matches Anthropic's "semantic keywords + service-prefix" guidance for large catalogs).
- **R1.9 — fleet-standard linting at the edge.** **Task 26** adds `doctor --strict-naming`: per-backend leaf audit (unprefixed, `verb_noun`, ≤50 chars, canonical verb) — the router enforcing Tool-Naming Standard v1.
- **R1.10 — client compatibility documented.** FastMCP's synthetic `search_tools`/`call_tool` is **not** Anthropic's API-level tool-search (`tool_search_tool_bm25_20251119`); ours is client-agnostic. Federated names also satisfy **Gemini**'s stricter rule (snake_case, `[a-z0-9_]`, ≤64, no dots/dashes) — folded into the name check (**Task 5**) and README (**Task 30**).

---

## File structure

Package `genefoundry_router/` (each module < 600 LOC per fleet discipline):

| File | Responsibility |
|------|----------------|
| `genefoundry_router/__init__.py` | Package version + top-level exports |
| `genefoundry_router/exceptions.py` | Error hierarchy (`ConfigurationError`, `RegistryError`, `StartupError`) |
| `genefoundry_router/registry.py` | `TransformConfig`, `BackendDef` models; name-length + namespace helpers |
| `genefoundry_router/config.py` | `RouterSettings` (pydantic-settings); `load_registry()` (yaml + defaults merge + env URL resolution) |
| `genefoundry_router/normalization.py` | async name/tag normalization: strip_prefix / rename / arg-remap + tag injection |
| `genefoundry_router/composition.py` | `build_proxy()`, `register_backend()` (proxy → mount/namespace; no token passthrough) |
| `genefoundry_router/tool_search.py` | `BM25SearchTransform` wiring + `always_visible` set |
| `genefoundry_router/auth.py` | `build_auth()` — MultiAuth assembly from `GF_AUTH_MODE` (audience + public base URL) |
| `genefoundry_router/discovery.py` | polling re-list fallback (TTL-based freshness helper) |
| `genefoundry_router/security.py` | Origin-validation ASGI middleware (MCP DNS-rebinding MUST) |
| `genefoundry_router/observability.py` | structlog config, `/health` (reachability), `/metrics`, `MetricsMiddleware`, correlation-id |
| `genefoundry_router/server.py` | `build_server()` (FastMCP assembly) + `build_app()` (FastAPI host + lifespan orchestration) |
| `genefoundry_router/cli.py` | typer app: `run` / `validate` / `list-tools` / `doctor [--strict-naming]` |
| `servers.yaml` | Committed backend registry (structure, no secrets) |
| `.env.example` / `.env.docker.example` | Env templates (URLs, auth knobs) |
| `docker/` | Dockerfile + compose overlays (yml/prod/dev/npm) |
| `scripts/check_file_size.py` | 600-LOC budget enforcer (copied from fleet) |
| `tests/unit/…`, `tests/integration/…` | TDD suites; in-process fake FastMCP backends |

Test support:
- `tests/conftest.py` — shared fixtures.
- `tests/integration/conftest.py` — `make_fake_backend()` + `fake_registry` fixtures (in-process FastMCP servers with colliding tool names).

---

## Phases / milestones

- **Phase 0 — Scaffold** (Tasks 1–3): repo skeleton, tooling, README stub, package smoke test.
- **Phase 1 — Config & Registry** (Tasks 4–8): models (+`transport`), settings (+security env), yaml loader, name limits, `servers.yaml`.
- **Phase 2 — Composition + Server + `/health` + Origin security + CLI run/doctor** → **v0.1 PoC** (Tasks 9–13).
- **Phase 3 — Normalization (async) + tags + cache_ttl + Tool-search + `/metrics` + `list-tools`** → **v0.2** (Tasks 14–18).
- **Phase 4 — Auth + `validate`** → **v0.3 (logic)** (Tasks 19–21).
- **Phase 4.5 — Security & observability hardening** (Tasks 22–26): polling refresher (22), lifespan orchestration wiring (23), Origin-validation test (24), OAuth PRM/401 contract test (25), `doctor --strict-naming` (26).
- **Phase 5 — Docker + deploy** → **v0.3 (deploy)** (Tasks 27–29).
- **Phase 6 — Docs + v1.0 gate** (Tasks 30–31).

Each phase ends in working, testable software. Run `make ci-local` (Task 3) at the end of every phase.

---

# Phase 0 — Scaffold

### Task 1: Repository skeleton, tooling, and metadata files

**Files:**
- Create: `pyproject.toml`
- Create: `Makefile`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `.dockerignore`
- Create: `.gitattributes`
- Create: `.pre-commit-config.yaml`
- Create: `.loc-allowlist`
- Create: `scripts/check_file_size.py`
- Create: `LICENSE`

- [ ] **Step 1: Create `.python-version`**

```
3.12
```

- [ ] **Step 2: Create `pyproject.toml`** (mirrors gnomad-link tooling; router deps — no FastAPI-REST domain libs, adds `pyyaml`)

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "genefoundry-router"
version = "0.1.0"
description = "A thin FastMCP 3.x aggregator federating the GeneFoundry -link MCP fleet behind one endpoint."
readme = "README.md"
authors = [{ name = "Bernt Popp" }]
license = { text = "MIT" }
requires-python = ">=3.12"
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Scientific/Engineering :: Bio-Informatics",
    "Topic :: Software Development :: Libraries :: Python Modules",
]
dependencies = [
    "fastmcp>=3.2.0,<4.0.0",
    "mcp[cli]>=1.27.0,<2.0.0",
    "fastapi>=0.115.0,<1.0.0",
    "uvicorn[standard]>=0.46.0,<1.0.0",
    "gunicorn>=25.3.0,<27.0.0",
    "pydantic>=2.11.0,<3.0.0",
    "pydantic-settings>=2.6.0,<3.0.0",
    "pyyaml>=6.0.2,<7.0.0",
    "httpx>=0.28.0,<1.0.0",
    "typer>=0.25.1,<1.0.0",
    "rich>=15.0.0,<16.0.0",
    "structlog>=24.4.0,<26.0.0",
    "orjson>=3.10.0,<4.0.0",
    "asgi-correlation-id>=4.3.0,<5.0.0",
    "prometheus-client>=0.21.0,<1.0.0",
]

[dependency-groups]
dev = [
    "pytest>=9.0.3,<10.0.0",
    "pytest-asyncio>=1.3.0,<2.0.0",
    "pytest-cov>=6.0.0,<8.0.0",
    "pytest-mock>=3.14.0,<4.0.0",
    "pytest-xdist>=3.6.0,<4.0.0",
    "respx>=0.22.0,<1.0.0",
    "types-pyyaml>=6.0.0,<7.0.0",
    "ruff>=0.8.0,<1.0.0",
    "mypy>=1.14.0,<3.0.0",
    "pre-commit>=4.0.0,<5.0.0",
]

[project.scripts]
genefoundry-router = "genefoundry_router.cli:main"

[project.urls]
Homepage = "https://github.com/berntpopp/genefoundry-router"
Repository = "https://github.com/berntpopp/genefoundry-router"
Issues = "https://github.com/berntpopp/genefoundry-router/issues"

[tool.hatch.build.targets.wheel]
packages = ["genefoundry_router"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
extend-select = ["E", "W", "F", "I", "N", "UP", "B", "C4", "S", "T20", "SIM", "RUF"]
ignore = ["S101", "E501", "B008", "N999", "RUF006", "RUF012", "RUF022", "SIM108", "SIM117", "T201", "UP042"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
line-ending = "lf"

[tool.ruff.lint.per-file-ignores]
"tests/**/*" = ["S101", "T20"]

[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = false
disallow_untyped_defs = false
disallow_incomplete_defs = false
check_untyped_defs = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = false
warn_no_return = true
warn_unreachable = true
exclude = [".*site-packages.*", ".*/.venv/.*", "htmlcov/.*"]

[[tool.mypy.overrides]]
module = ["fastmcp.*", "mcp.*", "structlog.*", "asgi_correlation_id.*", "prometheus_client.*", "uvicorn.*", "yaml.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
addopts = ["--strict-markers", "-ra", "--import-mode=importlib"]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks tests as integration tests",
]

[tool.coverage.run]
source = ["genefoundry_router"]
omit = ["tests/*", "*/tests/*", "*/__init__.py"]
branch = true

[tool.coverage.report]
fail_under = 70
precision = 2
show_missing = true
skip_empty = true
exclude_also = [
    "def __repr__",
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "@(abc\\.)?abstractmethod",
]

[tool.coverage.html]
directory = "htmlcov"
```

- [ ] **Step 3: Create `Makefile`** (router targets; package dir is `genefoundry_router`, no root `server.py`/`mcp_server.py`)

```makefile
.PHONY: help install lock upgrade sync format format-check lint lint-ci lint-fix lint-loc typecheck typecheck-fresh test test-fast test-unit test-integration test-cov test-all check ci-local precommit clean run validate doctor list-tools docker-build docker-up docker-down docker-logs docker-prod-config docker-npm-config

.DEFAULT_GOAL := help

PKG := genefoundry_router
DOCKER_COMPOSE := $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

help: ## Display this help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install: ## Install project and development dependencies with uv
	uv sync --group dev

sync: install ## Alias for install

lock: ## Resolve and update uv.lock
	uv lock

upgrade: ## Upgrade locked dependencies
	uv lock --upgrade

format: ## Format Python code
	uv run ruff format $(PKG) tests

format-check: ## Check formatting without writing
	uv run ruff format --check $(PKG) tests

lint: ## Lint Python code
	uv run ruff check $(PKG) tests

lint-ci: ## Lint without modifying files (CI output)
	uv run ruff check $(PKG) tests --output-format=github

lint-fix: ## Lint and apply safe fixes
	uv run ruff check $(PKG) tests --fix

lint-loc: ## Enforce per-file line budget
	uv run python scripts/check_file_size.py

typecheck: ## Type check package
	uv run mypy $(PKG)

typecheck-fresh: ## Clear mypy cache and run typecheck
	rm -rf .mypy_cache
	uv run mypy $(PKG)

test: ## Run unit tests quickly
	uv run pytest tests/unit -q

test-fast: ## Run unit tests in parallel
	uv run pytest tests/unit -q -n auto

test-unit: test-fast ## Alias for parallel unit tests

test-integration: ## Run in-process integration tests
	uv run pytest tests/integration -q

test-cov: ## Run tests with coverage
	uv run pytest tests/unit tests/integration --cov=$(PKG) --cov-report=term-missing --cov-report=html --cov-report=xml

test-all: test-cov ## Alias for full test run with coverage

check: format lint ## Format and lint

ci-local: format-check lint-ci lint-loc typecheck test-fast test-integration ## Fast local CI-equivalent checks

precommit: ci-local ## Run checks expected before commit

clean: ## Remove local caches and generated reports
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml

run: ## Run the router over Streamable HTTP locally
	uv run genefoundry-router run --host 127.0.0.1 --port 8000

validate: ## Validate servers.yaml + env
	uv run genefoundry-router validate

doctor: ## Ping each backend and report reachability
	uv run genefoundry-router doctor

list-tools: ## Enumerate federated tools
	uv run genefoundry-router list-tools

docker-build: ## Build Docker image
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml build

docker-up: ## Start Docker dev stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker dev stack
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml down

docker-logs: ## Follow Docker logs
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml logs -f

docker-prod-config: ## Render production Compose configuration
	$(DOCKER_COMPOSE) -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config

docker-npm-config: ## Render NPM Compose configuration
	$(DOCKER_COMPOSE) --env-file .env.docker.example -f docker/docker-compose.yml -f docker/docker-compose.prod.yml -f docker/docker-compose.npm.yml config
```

- [ ] **Step 4: Create `scripts/check_file_size.py`** (600-LOC enforcer over the package)

```python
#!/usr/bin/env python
"""Enforce a per-file line budget across the package.

Hard cap: 600 lines per Python module in ``genefoundry_router/``. Files listed in
``.loc-allowlist`` (``path<TAB>ceiling``) are grandfathered at their recorded
ceiling. Tests are exempt. Exits non-zero on any violation.
"""

from __future__ import annotations

import sys
from pathlib import Path

HARD_CAP = 600
ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["genefoundry_router"]
ALLOWLIST = ROOT / ".loc-allowlist"


def load_allowlist() -> dict[str, int]:
    ceilings: dict[str, int] = {}
    if not ALLOWLIST.exists():
        return ceilings
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            ceilings[parts[0]] = int(parts[1])
    return ceilings


def iter_py_files() -> list[Path]:
    files: list[Path] = []
    for target in TARGETS:
        base = ROOT / target
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def main() -> int:
    ceilings = load_allowlist()
    violations: list[str] = []
    for path in iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8").splitlines())
        ceiling = ceilings.get(rel, HARD_CAP)
        if count > ceiling:
            violations.append(f"{rel}: {count} lines > {ceiling}")
    if violations:
        print("Line budget violations:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("Line budget OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Create `.loc-allowlist`**

```
# path<TAB>ceiling for grandfathered oversized modules.
# No production modules currently exceed the 600-LOC budget.
```

- [ ] **Step 6: Create `.python-version`, `.gitattributes`, `.gitignore`, `.dockerignore`, `.pre-commit-config.yaml`, `LICENSE`**

`.gitattributes`:
```
* text=auto
```

`.gitignore`:
```
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.dmypy.json
htmlcov/
.coverage
coverage.xml
.env
.env.*
!.env.example
!.env.docker.example
.claude/
.idea/
.vscode/
uv.lock.bak
```

`.dockerignore`:
```
.git/
.gitignore
.gitattributes
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
.env
.env.*
!.env.example
!.env.docker.example
docs/
scripts/
tests/
htmlcov/
```

`.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-json
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: debug-statements
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: uv run mypy genefoundry_router
        language: system
        pass_filenames: false
      - id: file-size-budget
        name: per-file line budget
        entry: uv run python scripts/check_file_size.py
        language: system
        pass_filenames: false
        files: ^(genefoundry_router/|\.loc-allowlist$)
```

`LICENSE` — MIT, copyright `2026 Bernt Popp` (copy the text of `../gnomad-link/LICENSE`, updating the year/holder if needed).

- [ ] **Step 7: Create a minimal `README.md` stub** (R1.3 — required by `uv sync`, which builds the project via `pyproject.readme`, and by the Dockerfile `COPY`; expanded in Task 30)

```markdown
# GeneFoundry Router

`genefoundry-router` — a thin FastMCP 3.x aggregator that federates the GeneFoundry
`*-link` MCP fleet behind one Streamable-HTTP endpoint (`genefoundry`).

Status: under construction. See `docs/specs/2026-06-13-genefoundry-router-design.md`
and `docs/plans/2026-06-13-genefoundry-router-implementation.md`. Research use only.
```

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml Makefile .python-version .gitignore .dockerignore .gitattributes .pre-commit-config.yaml .loc-allowlist scripts/check_file_size.py LICENSE README.md
git commit -m "chore: scaffold project tooling and metadata"
```

---

### Task 2: Package skeleton + version smoke test

**Files:**
- Create: `genefoundry_router/__init__.py`
- Create: `genefoundry_router/exceptions.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Test: `tests/unit/test_package.py`

- [ ] **Step 1: Install deps**

Run: `uv sync --group dev`
Expected: resolves and installs; creates `uv.lock`.

- [ ] **Step 2: Import smoke for FastMCP symbols** (de-risks post-cutoff API before writing logic)

Run:
```bash
uv run python -c "from fastmcp import FastMCP; from fastmcp.server import create_proxy; from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider; from fastmcp.server.transforms.search.bm25 import BM25SearchTransform; from fastmcp.server.transforms.tool_transform import ToolTransform; from fastmcp.tools.tool_transform import ToolTransformConfig, ArgTransformConfig; from fastmcp.server.auth import MultiAuth, OAuthProxy, TokenVerifier; from fastmcp.server.auth.providers.jwt import JWTVerifier; print('imports OK')"
```
Expected: `imports OK`. If any import fails, locate the moved symbol (`uv run python -c "import fastmcp; print(fastmcp.__version__)"` then grep the installed package) and update the **Convention notes §7** import table + affected tasks before proceeding.

- [ ] **Step 3: Write the failing test**

`tests/unit/test_package.py`:
```python
from genefoundry_router import __version__


def test_version_is_semver_string():
    assert isinstance(__version__, str)
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_package.py -v`
Expected: FAIL — `ModuleNotFoundError: genefoundry_router`.

- [ ] **Step 5: Create the package files**

`genefoundry_router/__init__.py`:
```python
"""GeneFoundry Router — a FastMCP aggregator for the GeneFoundry -link fleet."""

__version__ = "0.1.0"
```

`genefoundry_router/exceptions.py`:
```python
"""Error hierarchy for the GeneFoundry router."""

from __future__ import annotations


class RouterError(Exception):
    """Base class for all router errors."""


class ConfigurationError(RouterError):
    """Raised when settings or environment are invalid."""


class RegistryError(RouterError):
    """Raised when servers.yaml is malformed or a backend definition is invalid."""


class StartupError(RouterError):
    """Raised when the server fails to assemble or start."""
```

`tests/__init__.py`, `tests/unit/__init__.py`: empty files.

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_package.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add genefoundry_router tests/__init__.py tests/unit/__init__.py tests/unit/test_package.py uv.lock
git commit -m "feat: package skeleton with version and exception hierarchy"
```

---

### Task 3: Wire `make ci-local` green on the empty skeleton

**Files:** none new (validates tooling end-to-end).

- [ ] **Step 1: Run the full local CI gate**

Run: `make ci-local`
Expected: format-check, lint, lint-loc, typecheck, unit + integration tests all pass. (Integration dir is empty — pytest reports "no tests ran" for that path, which is non-fatal; if it errors on a missing path, create `tests/integration/__init__.py` now.)

- [ ] **Step 2: Create `tests/integration/__init__.py`** (empty) so the integration path resolves.

- [ ] **Step 3: Commit (if anything changed)**

```bash
git add -A
git commit -m "chore: green ci-local on skeleton"
```

---

# Phase 1 — Config & Registry

### Task 4: `BackendDef` / `TransformConfig` registry models

**Files:**
- Create: `genefoundry_router/registry.py`
- Test: `tests/unit/test_registry_models.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_registry_models.py`:
```python
import pytest
from pydantic import ValidationError

from genefoundry_router.registry import BackendDef, TransformConfig


def test_backenddef_minimal_defaults():
    b = BackendDef(name="gnomad", url_env="GF_GNOMAD_URL", namespace="gnomad")
    assert b.enabled is True
    assert b.cache_ttl == 300
    assert b.tags == []
    assert b.transform is None
    assert b.url is None


def test_namespace_must_be_lowercase_token():
    with pytest.raises(ValidationError):
        BackendDef(name="x", url_env="GF_X_URL", namespace="Bad-Name")


def test_transform_config_parses_nested():
    b = BackendDef(
        name="pubtator",
        url_env="GF_PUBTATOR_URL",
        namespace="pubtator",
        transform={"strip_prefix": "pubtator_"},
    )
    assert isinstance(b.transform, TransformConfig)
    assert b.transform.strip_prefix == "pubtator_"
    assert b.transform.rename == {}
    assert b.transform.arg_rename == {}


def test_transport_defaults_to_http_and_accepts_it():
    # R1.1: servers.yaml sets defaults.transport: http -> BackendDef must accept it.
    b = BackendDef(name="gnomad", url_env="GF_GNOMAD_URL", namespace="gnomad", transport="http")
    assert b.transport == "http"
    assert BackendDef(name="g", url_env="X", namespace="g").transport == "http"


def test_non_http_transport_rejected():
    with pytest.raises(ValidationError):
        BackendDef(name="x", url_env="GF_X_URL", namespace="x", transport="sse")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_registry_models.py -v`
Expected: FAIL — cannot import `genefoundry_router.registry`.

- [ ] **Step 3: Write minimal implementation**

`genefoundry_router/registry.py`:
```python
"""Backend registry models and naming helpers."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAMESPACE_RE = re.compile(r"^[a-z0-9]+$")
MAX_QUALIFIED_NAME_LEN = 64


class TransformConfig(BaseModel):
    """Per-backend stopgap normalization until the source adopts Standard v1."""

    model_config = ConfigDict(extra="forbid")

    strip_prefix: str | None = None
    rename: dict[str, str] = Field(default_factory=dict)
    arg_rename: dict[str, dict[str, str]] = Field(default_factory=dict)


class BackendDef(BaseModel):
    """A single federated backend, resolved from servers.yaml + .env."""

    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    url_env: str
    repo: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    cache_ttl: int = 300
    transport: Literal["http"] = "http"  # R1.1: present in servers.yaml defaults; SSE not offered
    transform: TransformConfig | None = None
    url: str | None = None  # resolved from os.environ[url_env] at load time

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, v: str) -> str:
        if not NAMESPACE_RE.match(v):
            raise ValueError(f"namespace must match {NAMESPACE_RE.pattern!r}, got {v!r}")
        return v
```

> The `Literal["http"]` field both (a) absorbs `defaults.transport: http` from `servers.yaml` so `extra="forbid"` no longer rejects it, and (b) rejects `transport: sse` with a clear `ValidationError` (Streamable HTTP only, per spec §11).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_registry_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/registry.py tests/unit/test_registry_models.py
git commit -m "feat: BackendDef and TransformConfig registry models"
```

---

### Task 5: Qualified-name + 64-char limit helpers

**Files:**
- Modify: `genefoundry_router/registry.py`
- Test: `tests/unit/test_naming.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_naming.py`:
```python
from genefoundry_router.registry import (
    MAX_QUALIFIED_NAME_LEN,
    exceeds_name_limit,
    is_client_safe_name,
    qualified_name,
)


def test_qualified_name_uses_single_underscore():
    assert qualified_name("gnomad", "get_variant_details") == "gnomad_get_variant_details"


def test_exceeds_name_limit_false_for_short():
    assert exceeds_name_limit("gnomad", "get_variant_details") is False


def test_exceeds_name_limit_true_for_long():
    long_tool = "x" * MAX_QUALIFIED_NAME_LEN
    assert exceeds_name_limit("gnomad", long_tool) is True


def test_limit_boundary_is_inclusive():
    # namespace(6) + "_"(1) = 7 prefix chars; tool of 57 -> exactly 64 -> OK
    tool = "t" * (MAX_QUALIFIED_NAME_LEN - len("gnomad_"))
    name = qualified_name("gnomad", tool)
    assert len(name) == MAX_QUALIFIED_NAME_LEN
    assert exceeds_name_limit("gnomad", tool) is False


def test_client_safe_name_rejects_dots_and_dashes():
    # R1.10: Gemini wants snake_case, [a-zA-Z0-9_], <=64, leading letter/underscore.
    assert is_client_safe_name("gnomad_get_variant_details") is True
    assert is_client_safe_name("gnomad-get-variant") is False  # dashes
    assert is_client_safe_name("gnomad.get") is False           # dots
    assert is_client_safe_name("1bad") is False                 # leading digit
    assert is_client_safe_name("x" * 65) is False               # too long
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_naming.py -v`
Expected: FAIL — `ImportError: cannot import name 'qualified_name'`.

- [ ] **Step 3: Add helpers to `registry.py`** (append below the models)

```python
CLIENT_SAFE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def qualified_name(namespace: str, tool: str) -> str:
    """Return the gateway-visible name for a tool under a namespace."""
    return f"{namespace}_{tool}"


def exceeds_name_limit(namespace: str, tool: str) -> bool:
    """True when the namespaced tool name exceeds the MCP 64-char limit."""
    return len(qualified_name(namespace, tool)) > MAX_QUALIFIED_NAME_LEN


def is_client_safe_name(name: str) -> bool:
    """True when a tool name is portable across MCP clients incl. Gemini.

    snake_case, ``[A-Za-z0-9_]`` only (no dots/dashes), leading letter/underscore,
    <=64 chars. (R1.10 — Gemini's FunctionDeclaration.name is stricter than MCP's
    ``[A-Za-z0-9_-]`` and rewrites non-conforming names, which would desync routing.)
    """
    return bool(CLIENT_SAFE_NAME_RE.match(name))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_naming.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/registry.py tests/unit/test_naming.py
git commit -m "feat: qualified-name and 64-char limit helpers"
```

---

### Task 6: `RouterSettings` (pydantic-settings, `GF_*` env)

**Files:**
- Create: `genefoundry_router/config.py`
- Test: `tests/unit/test_settings.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_settings.py`:
```python
from genefoundry_router.config import RouterSettings


def test_defaults(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("GF_"):
            monkeypatch.delenv(k, raising=False)
    s = RouterSettings(_env_file=None)
    assert s.GF_AUTH_MODE == "none"
    assert s.GF_PORT == 8000
    assert s.GF_HOST == "127.0.0.1"
    assert s.GF_MCP_PATH == "/mcp"
    assert s.GF_SERVERS_FILE == "servers.yaml"
    assert s.GF_SEARCH_MAX_RESULTS == 5
    assert s.GF_POLL_INTERVAL == 0
    assert s.GF_LOG_LEVEL == "INFO"
    assert s.GF_ALLOWED_ORIGINS == []         # R1.4 — empty = reject any present Origin
    assert s.GF_PUBLIC_BASE_URL is None       # R1.5 — public URL for OAuth metadata


def test_allowed_origins_parses_csv(monkeypatch):
    monkeypatch.setenv("GF_ALLOWED_ORIGINS", "https://claude.ai, https://cursor.sh")
    s = RouterSettings(_env_file=None)
    assert s.GF_ALLOWED_ORIGINS == ["https://claude.ai", "https://cursor.sh"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.setenv("GF_PORT", "9001")
    s = RouterSettings(_env_file=None)
    assert s.GF_AUTH_MODE == "jwt"
    assert s.GF_PORT == 9001


def test_invalid_auth_mode_rejected(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "bogus")
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RouterSettings(_env_file=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL — cannot import `genefoundry_router.config`.

- [ ] **Step 3: Write minimal implementation**

`genefoundry_router/config.py`:
```python
"""Router runtime settings and registry loading."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["none", "jwt", "oauth"]


class RouterSettings(BaseSettings):
    """Environment-driven runtime settings (prefix ``GF_``)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Transport / server
    GF_HOST: str = "127.0.0.1"
    GF_PORT: int = 8000
    GF_MCP_PATH: str = "/mcp"
    GF_LOG_LEVEL: str = "INFO"

    # Registry
    GF_SERVERS_FILE: str = "servers.yaml"

    # Tool search
    GF_SEARCH_MAX_RESULTS: int = 5

    # Discovery
    GF_POLL_INTERVAL: int = 0  # seconds; 0 disables the polling re-list

    # Transport security (R1.4 — MCP Origin/DNS-rebinding MUST)
    GF_ALLOWED_ORIGINS: list[str] = []   # CSV in env; [] = reject any present Origin header
    GF_PUBLIC_BASE_URL: str | None = None  # public URL behind the proxy (OAuth resource URI)

    # Auth
    GF_AUTH_MODE: AuthMode = "none"
    GF_JWT_ISSUER: str | None = None
    GF_JWT_JWKS_URL: str | None = None
    GF_JWT_AUDIENCE: str | None = None
    GF_OAUTH_PROVIDER: str | None = None
    GF_OAUTH_CLIENT_ID: str | None = None
    GF_OAUTH_CLIENT_SECRET: str | None = None
    GF_OAUTH_BASE_URL: str | None = None
    GF_OAUTH_AUTHORIZE_URL: str | None = None
    GF_OAUTH_TOKEN_URL: str | None = None

    @field_validator("GF_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept a comma-separated string from env and split into a list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/config.py tests/unit/test_settings.py
git commit -m "feat: RouterSettings env-driven configuration"
```

---

### Task 7: `load_registry()` — yaml parse, defaults merge, env URL resolution

**Files:**
- Modify: `genefoundry_router/config.py`
- Test: `tests/unit/test_load_registry.py`
- Test fixture: `tests/unit/fixtures/servers_min.yaml`

- [ ] **Step 1: Write the fixture**

`tests/unit/fixtures/servers_min.yaml`:
```yaml
defaults:
  enabled: true
  cache_ttl: 300
  tags: []
servers:
  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad, tags: [variant] }
  - { name: hgnc, url_env: GF_HGNC_URL, namespace: hgnc, enabled: false }
  - { name: pubtator, url_env: GF_PUBTATOR_URL, namespace: pubtator, cache_ttl: 600,
      transform: { strip_prefix: "pubtator_" } }
```

- [ ] **Step 2: Write the failing test**

`tests/unit/test_load_registry.py`:
```python
from pathlib import Path

import pytest

from genefoundry_router.config import load_registry
from genefoundry_router.exceptions import RegistryError

FIX = Path(__file__).parent / "fixtures" / "servers_min.yaml"


def test_load_merges_defaults_and_resolves_urls():
    env = {"GF_GNOMAD_URL": "https://gnomad-link.example.org/mcp",
           "GF_PUBTATOR_URL": "https://pubtator-link.example.org/mcp"}
    backends = load_registry(FIX, env)
    by_name = {b.name: b for b in backends}

    assert by_name["gnomad"].url == "https://gnomad-link.example.org/mcp"
    assert by_name["gnomad"].cache_ttl == 300  # from defaults
    assert by_name["gnomad"].enabled is True

    assert by_name["pubtator"].cache_ttl == 600  # per-server override wins
    assert by_name["pubtator"].transform.strip_prefix == "pubtator_"

    # disabled backend with no env var still loads but url stays None
    assert by_name["hgnc"].enabled is False
    assert by_name["hgnc"].url is None


def test_missing_url_for_enabled_backend_leaves_url_none():
    env: dict[str, str] = {}  # no GF_GNOMAD_URL
    backends = load_registry(FIX, env)
    gnomad = next(b for b in backends if b.name == "gnomad")
    assert gnomad.enabled is True
    assert gnomad.url is None  # caller (validate/startup) decides how to warn/skip


def test_missing_file_raises_registry_error(tmp_path):
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "nope.yaml", {})


def test_duplicate_namespace_raises(tmp_path):
    p = tmp_path / "dup.yaml"
    p.write_text(
        "servers:\n"
        "  - { name: a, url_env: GF_A_URL, namespace: dup }\n"
        "  - { name: b, url_env: GF_B_URL, namespace: dup }\n"
    )
    with pytest.raises(RegistryError):
        load_registry(p, {})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_load_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_registry'`.

- [ ] **Step 4: Implement `load_registry()`** (append to `config.py`)

```python
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from genefoundry_router.exceptions import RegistryError
from genefoundry_router.registry import BackendDef


def load_registry(path: str | Path, environ: Mapping[str, str]) -> list[BackendDef]:
    """Parse servers.yaml, merge ``defaults`` into each server, and resolve URLs.

    URLs come from ``environ[server.url_env]`` when present; a missing var leaves
    ``url=None`` (the caller decides whether to skip/warn). Raises RegistryError on
    a missing/malformed file, an invalid backend, or a duplicate namespace.
    """
    path = Path(path)
    if not path.exists():
        raise RegistryError(f"registry file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - exercised via malformed yaml
        raise RegistryError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError(f"{path} must be a mapping with 'servers'")

    defaults = raw.get("defaults") or {}
    servers = raw.get("servers")
    if not isinstance(servers, list) or not servers:
        raise RegistryError(f"{path} must define a non-empty 'servers' list")

    backends: list[BackendDef] = []
    seen_namespaces: set[str] = set()
    for entry in servers:
        if not isinstance(entry, dict):
            raise RegistryError(f"each server entry must be a mapping, got {entry!r}")
        merged = {**defaults, **entry}
        try:
            backend = BackendDef(**merged)
        except ValidationError as exc:
            raise RegistryError(f"invalid backend {entry.get('name', entry)!r}: {exc}") from exc
        if backend.namespace in seen_namespaces:
            raise RegistryError(f"duplicate namespace: {backend.namespace!r}")
        seen_namespaces.add(backend.namespace)
        backend.url = environ.get(backend.url_env)
        backends.append(backend)
    return backends
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_load_registry.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add genefoundry_router/config.py tests/unit/test_load_registry.py tests/unit/fixtures/servers_min.yaml
git commit -m "feat: load_registry with defaults merge and env URL resolution"
```

---

### Task 8: Ship the real `servers.yaml` + env templates

**Files:**
- Create: `servers.yaml`
- Create: `.env.example`
- Create: `.env.docker.example`
- Test: `tests/unit/test_servers_yaml.py`

- [ ] **Step 1: Write the failing test** (the committed registry must parse and match spec §5 facts)

`tests/unit/test_servers_yaml.py`:
```python
from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 13 backends defined
    assert len(backends) == 13
    # hgnc stays disabled until the live deployment is fixed (spec §3 caveat)
    assert by_name["hgnc"].enabled is False
    # pubtator carries the stopgap strip_prefix transform (spec §5)
    assert by_name["pubtator"].transform is not None
    assert by_name["pubtator"].transform.strip_prefix == "pubtator_"
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_servers_yaml.py -v`
Expected: FAIL — registry file not found.

- [ ] **Step 3: Create `servers.yaml`** (verbatim from spec §5)

```yaml
# servers.yaml — committed backend registry (no secrets; URLs come from .env)
defaults:
  transport: http
  enabled: true
  cache_ttl: 300          # proxy metadata cache TTL (seconds)
  tags: []
servers:
  - { name: gnomad,      repo: berntpopp/gnomad-link,        url_env: GF_GNOMAD_URL,      namespace: gnomad,      tags: [variant, gene, frequency, population] }
  - { name: gtex,        repo: berntpopp/gtex-link,          url_env: GF_GTEX_URL,        namespace: gtex,        tags: [expression, tissue] }
  - { name: hgnc,        repo: berntpopp/hgnc-link,          url_env: GF_HGNC_URL,        namespace: hgnc,        tags: [gene, nomenclature], enabled: false }  # deployment blocker (serves mgi binary)
  - { name: mgi,         repo: berntpopp/mgi-link,           url_env: GF_MGI_URL,         namespace: mgi,         tags: [mouse, phenotype, model] }
  - { name: uniprot,     repo: berntpopp/uniprot-link,       url_env: GF_UNIPROT_URL,     namespace: uniprot,     tags: [protein, function] }
  - { name: clingen,     repo: berntpopp/clingen-link,       url_env: GF_CLINGEN_URL,     namespace: clingen,     tags: [gene-disease, curation] }
  - { name: gencc,       repo: berntpopp/gencc-link,         url_env: GF_GENCC_URL,       namespace: gencc,       tags: [gene-disease, curation] }
  - { name: litvar,      repo: berntpopp/litvar-link,        url_env: GF_LITVAR_URL,      namespace: litvar,      tags: [variant, literature] }
  - { name: stringdb,    repo: berntpopp/stringdb-link,      url_env: GF_STRINGDB_URL,    namespace: stringdb,    tags: [ppi, network] }
  - { name: autopvs1,    repo: berntpopp/autopvs1-link,      url_env: GF_AUTOPVS1_URL,    namespace: autopvs1,    tags: [variant, acmg, pvs1] }
  - { name: spliceai,    repo: berntpopp/spliceailookup-link, url_env: GF_SPLICEAI_URL,   namespace: spliceai,    tags: [variant, splicing, prediction] }
  - { name: genereviews, repo: berntpopp/genereviews-link,   url_env: GF_GENEREVIEWS_URL, namespace: genereviews, tags: [literature, gene-disease] }
  - { name: pubtator,    repo: berntpopp/pubtator-link,      url_env: GF_PUBTATOR_URL,    namespace: pubtator,    tags: [literature, entity],
      transform: { strip_prefix: "pubtator_" } }   # remove once pubtator-link drops self-prefix
```

- [ ] **Step 4: Create `.env.example`** (URL placeholders per spec §19 Q1 default pattern)

```
# --- Router runtime ---
GF_HOST=127.0.0.1
GF_PORT=8000
GF_MCP_PATH=/mcp
GF_LOG_LEVEL=INFO
GF_SERVERS_FILE=servers.yaml
GF_SEARCH_MAX_RESULTS=5
GF_POLL_INTERVAL=0

# --- Transport security (MCP Origin/DNS-rebinding) ---
# Comma-separated allowlist; empty rejects any request that SENDS an Origin header.
# Non-browser MCP clients (Claude connector, Gemini, scripts) send no Origin and pass through.
GF_ALLOWED_ORIGINS=
# Public URL clients use (behind nginx-proxy-manager); becomes the OAuth resource URI.
# GF_PUBLIC_BASE_URL=https://genefoundry.example.org/mcp

# --- Auth (default: none for the v0.1 PoC; none is LOCAL/PoC ONLY) ---
GF_AUTH_MODE=none
# GF_JWT_ISSUER=
# GF_JWT_JWKS_URL=
# GF_JWT_AUDIENCE=          # required for protected (jwt/oauth) deployments — token audience
# GF_OAUTH_PROVIDER=
# GF_OAUTH_CLIENT_ID=
# GF_OAUTH_CLIENT_SECRET=
# GF_OAUTH_BASE_URL=
# GF_OAUTH_AUTHORIZE_URL=
# GF_OAUTH_TOKEN_URL=

# --- Backend /mcp URLs (pattern: https://<name>-link.<domain>/mcp) ---
GF_GNOMAD_URL=https://gnomad-link.example.org/mcp
GF_GTEX_URL=https://gtex-link.example.org/mcp
# GF_HGNC_URL=https://hgnc-link.example.org/mcp   # disabled until deployment fixed
GF_MGI_URL=https://mgi-link.example.org/mcp
GF_UNIPROT_URL=https://uniprot-link.example.org/mcp
GF_CLINGEN_URL=https://clingen-link.example.org/mcp
GF_GENCC_URL=https://gencc-link.example.org/mcp
GF_LITVAR_URL=https://litvar-link.example.org/mcp
GF_STRINGDB_URL=https://stringdb-link.example.org/mcp
GF_AUTOPVS1_URL=https://autopvs1-link.example.org/mcp
GF_SPLICEAI_URL=https://spliceailookup-link.example.org/mcp
GF_GENEREVIEWS_URL=https://genereviews-link.example.org/mcp
GF_PUBTATOR_URL=https://pubtator-link.example.org/mcp
```

- [ ] **Step 5: Create `.env.docker.example`** (adds host-port + NPM network knobs; binds to 0.0.0.0)

```
GF_HOST=0.0.0.0
GF_PORT=8000
GF_MCP_PATH=/mcp
GF_LOG_LEVEL=INFO
GF_AUTH_MODE=none
# Behind nginx-proxy-manager, binding 0.0.0.0 inside the container is fine (the
# localhost-bind SHOULD is scoped to local servers). Set the public URL + origin allowlist:
GF_PUBLIC_BASE_URL=https://genefoundry.example.org/mcp
GF_ALLOWED_ORIGINS=https://claude.ai,https://cursor.sh
GENEFOUNDRY_ROUTER_HOST_PORT=8010
NPM_NETWORK_NAME=npm_network

GF_GNOMAD_URL=https://gnomad-link.example.org/mcp
GF_GTEX_URL=https://gtex-link.example.org/mcp
GF_MGI_URL=https://mgi-link.example.org/mcp
GF_UNIPROT_URL=https://uniprot-link.example.org/mcp
GF_CLINGEN_URL=https://clingen-link.example.org/mcp
GF_GENCC_URL=https://gencc-link.example.org/mcp
GF_LITVAR_URL=https://litvar-link.example.org/mcp
GF_STRINGDB_URL=https://stringdb-link.example.org/mcp
GF_AUTOPVS1_URL=https://autopvs1-link.example.org/mcp
GF_SPLICEAI_URL=https://spliceailookup-link.example.org/mcp
GF_GENEREVIEWS_URL=https://genereviews-link.example.org/mcp
GF_PUBTATOR_URL=https://pubtator-link.example.org/mcp
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_servers_yaml.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add servers.yaml .env.example .env.docker.example tests/unit/test_servers_yaml.py
git commit -m "feat: ship servers.yaml registry and env templates"
```

---

# Phase 2 — Composition, server assembly, /health, Origin security, CLI (v0.1 PoC)

### Task 9: In-process fake-backend test harness

**Files:**
- Create: `tests/integration/__init__.py` (exists from Task 3)
- Create: `tests/integration/conftest.py`

- [ ] **Step 1: Create the fake-backend factory**

`tests/integration/conftest.py`:
```python
"""In-process FastMCP fake backends for integration tests (no network)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP


def make_fake_backend(name: str, tool_names: list[str]) -> FastMCP:
    """Build a FastMCP server exposing trivial echo tools with the given names."""
    server = FastMCP(name)
    for tool_name in tool_names:

        def _make(tn: str):
            async def _tool(value: str = "") -> dict[str, str]:
                return {"tool": tn, "server": name, "value": value}

            _tool.__name__ = tn
            return _tool

        server.tool(name=tool_name)(_make(tool_name))
    return server


@pytest.fixture
def gnomad_fake() -> FastMCP:
    # clean, Standard-v1-compliant leaf names
    return make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])


@pytest.fixture
def gtex_fake() -> FastMCP:
    # deliberately collides with gnomad on search_genes
    return make_fake_backend("gtex-link", ["get_gene_information", "search_genes"])


@pytest.fixture
def pubtator_fake() -> FastMCP:
    # self-prefixed leaf names (non-compliant) -> exercises strip_prefix
    return make_fake_backend("pubtator-link", ["pubtator_search_literature", "pubtator_get_passages"])
```

- [ ] **Step 2: Sanity-check the harness imports**

Run: `uv run pytest tests/integration/conftest.py --collect-only -q`
Expected: no collection errors (file has no tests; should report cleanly).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: in-process fake FastMCP backend harness"
```

---

### Task 10: `composition.build_proxy()` + `register_backend()`

**Files:**
- Create: `genefoundry_router/composition.py`
- Test: `tests/integration/test_composition.py`

- [ ] **Step 1: Write the failing test** (mount two fakes, assert namespacing + collision-freedom + round-trip)

`tests/integration/test_composition.py`:
```python
import pytest
from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.registry import BackendDef


@pytest.fixture
def gateway() -> FastMCP:
    return FastMCP("genefoundry")


async def _tool_names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {t.name for t in await client.list_tools()}


async def test_namespacing_is_collision_free(gateway, gnomad_fake, gtex_fake):
    register_backend(gateway, BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
                     proxy_target=gnomad_fake)
    register_backend(gateway, BackendDef(name="gtex", url_env="X", namespace="gtex"),
                     proxy_target=gtex_fake)
    names = await _tool_names(gateway)
    # both backends expose search_genes; namespacing keeps them distinct
    assert "gnomad_search_genes" in names
    assert "gtex_search_genes" in names
    assert "gnomad_get_variant_details" in names
    assert "gtex_get_gene_information" in names


async def test_proxied_call_round_trips(gateway, gnomad_fake):
    register_backend(gateway, BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
                     proxy_target=gnomad_fake)
    async with Client(gateway) as client:
        result = await client.call_tool("gnomad_get_variant_details", {"value": "hi"})
    assert result.data == {"tool": "get_variant_details", "server": "gnomad-link", "value": "hi"}
```

> Note: `register_backend` takes an optional `proxy_target` so tests can inject an in-process FastMCP instead of a live URL. In production the target is `backend.url`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_composition.py -v`
Expected: FAIL — cannot import `genefoundry_router.composition`.

- [ ] **Step 3: Write minimal implementation**

`genefoundry_router/composition.py`:
```python
"""Build per-backend proxies and mount them with a namespace."""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.server import create_proxy

from genefoundry_router.registry import BackendDef

log = structlog.get_logger(__name__)


def build_proxy(backend: BackendDef, target: Any | None = None) -> FastMCP:
    """Create a FastMCP proxy for a backend.

    ``target`` overrides the proxy target (used by tests to inject an in-process
    FastMCP). In production it defaults to ``backend.url``.

    R1.6 — confused-deputy invariant: a bare URL target is auto-wrapped in a plain
    ``ProxyClient`` that uses the router's OWN connection to the backend. The
    router MUST NOT forward the caller's auth token to upstreams. Do NOT pass the
    request's Authorization header into the proxy client. Backends are public/no-auth
    today; if a backend ever needs auth, give the proxy its OWN service credential.
    """
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    return create_proxy(proxy_target, name=f"{backend.name}-proxy")


def register_backend(
    server: FastMCP,
    backend: BackendDef,
    proxy_target: Any | None = None,
) -> None:
    """Mount a backend's proxy onto ``server`` under ``backend.namespace``.

    Tools surface as ``<namespace>_<tool>``. Normalization transforms (Task 15) and
    cache_ttl handling (Task 14) extend this; the async normalization pass runs from
    the lifespan (Task 23).
    """
    proxy = build_proxy(backend, target=proxy_target)
    server.mount(proxy, namespace=backend.namespace)
    log.info("backend_mounted", backend=backend.name, namespace=backend.namespace)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_composition.py -v`
Expected: PASS. (If `result.data` shape differs in your fastmcp build, print `result` once and adjust the assertion to the returned structure — the round-trip itself is the contract.)

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/composition.py tests/integration/test_composition.py
git commit -m "feat: per-backend proxy creation and namespaced mounting"
```

---

### Task 11: structlog config + `/health` + correlation-id (observability)

**Files:**
- Create: `genefoundry_router/observability.py`
- Test: `tests/unit/test_observability.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_observability.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import configure_logging, register_health
from genefoundry_router.registry import BackendDef


def test_configure_logging_is_idempotent():
    configure_logging("INFO")
    configure_logging("DEBUG")  # must not raise on re-config


def test_health_reports_enabled_backends():
    app = FastAPI()
    backends = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp"),
        BackendDef(name="hgnc", url_env="Y", namespace="hgnc", enabled=False),
    ]
    register_health(app, backends)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["backends"]["enabled"] == 1
    assert "gnomad" in body["backends"]["namespaces"]
    assert "hgnc" not in body["backends"]["namespaces"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: FAIL — cannot import `genefoundry_router.observability`.

- [ ] **Step 3: Write minimal implementation**

`genefoundry_router/observability.py`:
```python
"""Logging, health, and metrics for the router."""

from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI

from genefoundry_router.registry import BackendDef

_LOG_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON to stdout. Safe to call repeatedly."""
    global _LOG_CONFIGURED
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _LOG_CONFIGURED = True


def register_health(app: FastAPI, backends: list[BackendDef]) -> None:
    """Attach GET /health returning liveness + a per-backend summary."""
    enabled = [b for b in backends if b.enabled]

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "service": "genefoundry",
            "backends": {
                "total": len(backends),
                "enabled": len(enabled),
                "namespaces": [b.namespace for b in enabled],
            },
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_observability.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/observability.py tests/unit/test_observability.py
git commit -m "feat: structlog config and /health endpoint"
```

---

### Task 12: `build_server()` + `build_app()` (FastMCP + FastAPI host + Origin security)

**Files:**
- Create: `genefoundry_router/security.py`
- Create: `genefoundry_router/server.py`
- Test: `tests/unit/test_security.py`
- Test: `tests/integration/test_server.py`

- [ ] **Step 0: Write the Origin-middleware unit test** (R1.4 — MCP DNS-rebinding MUST)

`tests/unit/test_security.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.security import add_origin_validation


def _app(allowed: list[str]) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    add_origin_validation(app, allowed_origins=allowed)
    return TestClient(app)


def test_absent_origin_passes():
    # non-browser MCP clients send no Origin -> must not be blocked
    assert _app([]).get("/health").status_code == 200


def test_present_allowed_origin_passes():
    client = _app(["https://claude.ai"])
    assert client.get("/health", headers={"origin": "https://claude.ai"}).status_code == 200


def test_present_disallowed_origin_403():
    client = _app(["https://claude.ai"])
    assert client.get("/health", headers={"origin": "https://evil.example"}).status_code == 403
```

- [ ] **Step 0b: Run it (fails), then implement `security.py`**

Run: `uv run pytest tests/unit/test_security.py -v` → FAIL (no module).

`genefoundry_router/security.py`:
```python
"""Transport security: Origin-header validation (MCP DNS-rebinding defense).

Per the MCP Streamable-HTTP transport spec (2025-11-25): servers MUST validate the
``Origin`` header and respond 403 when it is present and not allow-listed. Requests
with NO ``Origin`` header (non-browser MCP clients, curl health checks) pass through.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = structlog.get_logger(__name__)


class OriginValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self._allowed = set(allowed_origins)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        origin = request.headers.get("origin")
        if origin is not None and origin not in self._allowed:
            log.warning("origin_rejected", origin=origin)
            return JSONResponse({"error": "forbidden origin"}, status_code=403)
        return await call_next(request)


def add_origin_validation(app: FastAPI, allowed_origins: list[str]) -> None:
    """Attach Origin validation. Empty allowlist rejects ANY request that sends Origin."""
    app.add_middleware(OriginValidationMiddleware, allowed_origins=allowed_origins)
```

Run again → PASS.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_server.py`:
```python
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app, build_server


def test_build_server_skips_disabled_and_urlless(gnomad_fake):
    settings = RouterSettings(_env_file=None)
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        BackendDef(name="hgnc", url_env="Y", namespace="hgnc", enabled=False),
        BackendDef(name="gtex", url_env="Z", namespace="gtex"),  # url=None -> skipped
    ]
    server = build_server(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    assert isinstance(server, FastMCP)


async def test_built_server_exposes_namespaced_tools(gnomad_fake):
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    server = build_server(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
    assert "gnomad_get_variant_details" in names


def test_build_app_serves_health(gnomad_fake):
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "healthy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_server.py -v`
Expected: FAIL — cannot import `genefoundry_router.server`.

- [ ] **Step 3: Write minimal implementation**

`genefoundry_router/server.py`:
```python
"""Assemble the genefoundry FastMCP server and its FastAPI host."""

from __future__ import annotations

from typing import Any

import structlog
from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI
from fastmcp import FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.config import RouterSettings
from genefoundry_router.observability import configure_logging, register_health
from genefoundry_router.registry import BackendDef
from genefoundry_router.security import add_origin_validation

log = structlog.get_logger(__name__)


def build_server(
    settings: RouterSettings,
    registry: list[BackendDef],
    proxy_targets: dict[str, Any] | None = None,
) -> FastMCP:
    """Build the genefoundry FastMCP server from a resolved registry.

    Disabled backends and backends with no resolved URL are skipped with a warning.
    ``proxy_targets`` maps a backend name to an in-process target (tests only).
    """
    proxy_targets = proxy_targets or {}
    server: FastMCP = FastMCP("genefoundry")
    for backend in registry:
        if not backend.enabled:
            log.info("backend_skipped", backend=backend.name, reason="disabled")
            continue
        target = proxy_targets.get(backend.name)
        if target is None and backend.url is None:
            log.warning("backend_skipped", backend=backend.name, reason="missing_url")
            continue
        register_backend(server, backend, proxy_target=target)
    return server


def build_app(
    settings: RouterSettings,
    registry: list[BackendDef],
    proxy_targets: dict[str, Any] | None = None,
) -> FastAPI:
    """Build the FastAPI host: /health + Origin validation + mounted MCP app.

    NOTE (extended in later tasks): Task 17 adds /metrics + MetricsMiddleware; Task 23
    replaces the bare ``lifespan=mcp_app.lifespan`` with a composed lifespan that also
    runs async normalization (Task 15) and starts/stops the polling refresher (Task 22).
    """
    configure_logging(settings.GF_LOG_LEVEL)
    server = build_server(settings, registry, proxy_targets=proxy_targets)
    mcp_app = server.http_app(path="/")  # ASGI sub-app; lifespan must be forwarded
    app = FastAPI(title="GeneFoundry Router", lifespan=mcp_app.lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)  # R1.4 — MCP Origin MUST
    register_health(app, registry)
    app.mount(settings.GF_MCP_PATH, mcp_app)
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_server.py tests/unit/test_security.py -v`
Expected: PASS. (The default `GF_ALLOWED_ORIGINS=[]` means health checks with no Origin header still pass — `TestClient` sends none.)

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/security.py genefoundry_router/server.py tests/unit/test_security.py tests/integration/test_server.py
git commit -m "feat: build_server/build_app assembly with Origin validation"
```

---

### Task 13: typer CLI — `run` + `doctor` (v0.1 PoC complete)

**Files:**
- Create: `genefoundry_router/cli.py`
- Test: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test** (CLI wiring; uses typer's `CliRunner`, mocks the server run)

`tests/unit/test_cli.py`:
```python
from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def test_run_invokes_uvicorn(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")
    called = {}

    def fake_run(app_obj, host, port, **kw):
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr("genefoundry_router.cli.uvicorn.run", fake_run)
    result = runner.invoke(
        app, ["run", "--servers-file", str(yaml), "--host", "0.0.0.0", "--port", "8123"]
    )
    assert result.exit_code == 0, result.output
    assert called == {"host": "0.0.0.0", "port": 8123}


def test_doctor_reports_unreachable(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text(
        "servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n"
    )
    monkeypatch.setenv("GF_GNOMAD_URL", "https://unreachable.invalid/mcp")

    async def fake_probe(backend):
        return {"name": backend.name, "reachable": False, "tools": 0, "error": "boom"}

    monkeypatch.setattr("genefoundry_router.cli._probe_backend", fake_probe)
    result = runner.invoke(app, ["doctor", "--servers-file", str(yaml)])
    assert result.exit_code == 1  # at least one backend unreachable -> non-zero
    assert "gnomad" in result.output
    assert "unreachable" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: FAIL — cannot import `genefoundry_router.cli`.

- [ ] **Step 3: Write minimal implementation**

`genefoundry_router/cli.py`:
```python
"""Typer CLI for the GeneFoundry router."""

from __future__ import annotations

import asyncio
import os
import sys

import typer
import uvicorn
from rich.console import Console

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

app = typer.Typer(help="GeneFoundry Router — federate the -link MCP fleet.", no_args_is_help=True)
console = Console()

DEFAULT_SERVERS = "servers.yaml"


@app.command()
def run(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    transport: str = typer.Option("http", help="Transport (only 'http' supported)."),
    log_level: str = typer.Option("INFO", help="Log level."),
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Start the router over Streamable HTTP."""
    if transport != "http":
        console.print(f"[red]Unsupported transport {transport!r}; only 'http' is offered.[/red]")
        raise typer.Exit(2)
    settings = RouterSettings(GF_LOG_LEVEL=log_level, GF_SERVERS_FILE=servers_file)
    registry = load_registry(servers_file, os.environ)
    application = build_app(settings, registry)
    uvicorn.run(application, host=host, port=port, log_level=log_level.lower())


async def _probe_backend(backend: BackendDef) -> dict[str, object]:
    """Connect to a backend's /mcp URL and count its tools."""
    from fastmcp import Client

    if backend.url is None:
        return {"name": backend.name, "reachable": False, "tools": 0, "error": "no URL"}
    try:
        async with Client(backend.url) as client:
            tools = await client.list_tools()
        return {"name": backend.name, "reachable": True, "tools": len(tools), "error": None}
    except Exception as exc:  # noqa: BLE001 - report any connection failure
        return {"name": backend.name, "reachable": False, "tools": 0, "error": str(exc)}


@app.command()
def doctor(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Ping each enabled backend and report reachability + tool counts."""
    registry = load_registry(servers_file, os.environ)
    enabled = [b for b in registry if b.enabled]
    results = asyncio.run(_gather_probes(enabled))
    unreachable = 0
    for r in results:
        if r["reachable"]:
            console.print(f"[green]OK[/green]   {r['name']}: {r['tools']} tools")
        else:
            unreachable += 1
            console.print(f"[red]FAIL[/red] {r['name']}: unreachable ({r['error']})")
    if unreachable:
        raise typer.Exit(1)


async def _gather_probes(backends: list[BackendDef]) -> list[dict[str, object]]:
    return [await _probe_backend(b) for b in backends]


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    sys.exit(app())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Phase-2 gate — full CI**

Run: `make ci-local`
Expected: all green. **v0.1 PoC milestone reached**: config + namespaced mount/proxy + Streamable HTTP + `/health` + `doctor`, auth `none`.

- [ ] **Step 6: Manual smoke (optional, requires a reachable backend)**

```bash
GF_GNOMAD_URL=https://<your-gnomad-link>/mcp uv run genefoundry-router run --port 8000 &
curl -s localhost:8000/health | python -m json.tool
```
Expected: `"status": "healthy"`, gnomad in namespaces. Kill the server when done.

- [ ] **Step 7: Commit**

```bash
git add genefoundry_router/cli.py tests/unit/test_cli.py
git commit -m "feat: typer CLI with run and doctor commands (v0.1 PoC)"
```

---

# Phase 3 — Normalization, cache_ttl, tool-search, metrics, list-tools (v0.2)

### Task 14: Configurable per-backend `cache_ttl` via `ProxyProvider`

**Files:**
- Modify: `genefoundry_router/composition.py`
- Test: `tests/integration/test_cache_ttl.py`

> Rationale (Convention notes §2): `create_proxy` cannot take `cache_ttl`. When a backend's `cache_ttl` differs from the 300 s default, register it through `add_provider(ProxyProvider(client_factory, cache_ttl=…), namespace=…)` so the configured TTL is honored. Names still surface as `<namespace>_<tool>`.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_cache_ttl.py`:
```python
from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.registry import BackendDef


async def test_non_default_cache_ttl_still_namespaces(gnomad_fake):
    gateway = FastMCP("genefoundry")
    backend = BackendDef(name="gnomad", url_env="X", namespace="gnomad", cache_ttl=600)
    register_backend(gateway, backend, proxy_target=gnomad_fake)
    async with Client(gateway) as client:
        names = {t.name for t in await client.list_tools()}
    assert "gnomad_get_variant_details" in names
    assert "gnomad_search_genes" in names
```

- [ ] **Step 2: Run test to verify it fails (or passes trivially)**

Run: `uv run pytest tests/integration/test_cache_ttl.py -v`
Expected: with the Task-10 `register_backend` (always `mount(create_proxy())` at default TTL) this passes by namespacing but ignores `cache_ttl=600`. To make the TTL path real and asserted, extend the test to verify the provider path is used:

Append to the test file:
```python
def test_register_uses_proxy_provider_for_non_default_ttl(monkeypatch, gnomad_fake):
    from genefoundry_router import composition

    captured = {}
    orig = composition._register_via_provider

    def spy(server, backend, target):
        captured["ttl"] = backend.cache_ttl
        return orig(server, backend, target)

    monkeypatch.setattr(composition, "_register_via_provider", spy)
    gateway = FastMCP("genefoundry")
    register_backend(gateway, BackendDef(name="gnomad", url_env="X", namespace="gnomad", cache_ttl=600),
                     proxy_target=gnomad_fake)
    assert captured["ttl"] == 600
```
Run again; expect FAIL (`_register_via_provider` does not exist yet).

- [ ] **Step 3: Update `composition.py`** — branch on TTL

Replace `register_backend` and add helpers:
```python
from fastmcp.server.providers.proxy import ProxyClient, ProxyProvider

DEFAULT_CACHE_TTL = 300


def _register_via_mount(server: FastMCP, backend: BackendDef, target: Any | None) -> None:
    proxy = build_proxy(backend, target=target)
    server.mount(proxy, namespace=backend.namespace)


def _register_via_provider(server: FastMCP, backend: BackendDef, target: Any | None) -> None:
    proxy_target = target if target is not None else backend.url
    if proxy_target is None:
        raise ValueError(f"backend {backend.name!r} has no URL to proxy")
    provider = ProxyProvider(
        client_factory=lambda: ProxyClient(proxy_target),
        cache_ttl=float(backend.cache_ttl),
    )
    server.add_provider(provider, namespace=backend.namespace)


def register_backend(
    server: FastMCP,
    backend: BackendDef,
    proxy_target: Any | None = None,
) -> None:
    """Mount a backend under its namespace, honoring a non-default cache_ttl."""
    if backend.cache_ttl == DEFAULT_CACHE_TTL:
        _register_via_mount(server, backend, proxy_target)
    else:
        _register_via_provider(server, backend, proxy_target)
    log.info("backend_mounted", backend=backend.name, namespace=backend.namespace,
             cache_ttl=backend.cache_ttl)
```

> If `add_provider`/`ProxyProvider` names differ in the installed build (run the Task-2 import smoke), fall back to `_register_via_mount` for all backends and record a follow-up: "per-backend cache_ttl not honored — verify ProxyProvider API." Namespacing (the locked requirement) is unaffected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_cache_ttl.py tests/integration/test_composition.py -v`
Expected: PASS (both the 300 s mount path and the 600 s provider path).

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/composition.py tests/integration/test_cache_ttl.py
git commit -m "feat: honor per-backend cache_ttl via ProxyProvider"
```

---

### Task 15: Normalization (async) — `strip_prefix`/rename/arg-remap + tag injection

**Files:**
- Create: `genefoundry_router/normalization.py`
- Test: `tests/unit/test_normalization.py`
- Test: `tests/integration/test_strip_prefix.py`

> **R1.2/R1.8:** normalization is applied **after** all backends are mounted, in an **async** pass that enumerates tools via the public `await server.list_tools()` (there is **no** `server._tool_manager`). It (a) renames double-prefixed/non-compliant tools and (b) injects backend `tags` so `BM25SearchTransform` indexes them. `register_backend` stays mount-only; this pass runs from the lifespan (Task 23). If a backend is unreachable at startup, its transforms are skipped with a warning and re-applied on the next poll (Task 22).

- [ ] **Step 1: Write the unit failing test** (pure builders)

`tests/unit/test_normalization.py`:
```python
from genefoundry_router.normalization import strip_prefix_name, build_tool_transform
from genefoundry_router.registry import BackendDef, TransformConfig


def test_strip_prefix_name():
    assert strip_prefix_name("pubtator_search_literature", "pubtator_") == "search_literature"
    assert strip_prefix_name("search_genes", "pubtator_") == "search_genes"


def test_build_tool_transform_none_when_no_transform():
    b = BackendDef(name="gnomad", url_env="X", namespace="gnomad")
    assert build_tool_transform(b, present_tools=["get_variant_details"]) is None


def test_build_tool_transform_strips_prefix_for_namespaced_names():
    b = BackendDef(name="pubtator", url_env="X", namespace="pubtator",
                   transform=TransformConfig(strip_prefix="pubtator_"))
    # gateway names after namespacing are pubtator_pubtator_<tool>
    transform = build_tool_transform(b, present_tools=["pubtator_pubtator_search_literature"])
    assert transform is not None
    # the mapping renames the double-prefixed name back to single-prefixed
    assert "pubtator_pubtator_search_literature" in transform.transforms
    cfg = transform.transforms["pubtator_pubtator_search_literature"]
    assert cfg.name == "pubtator_search_literature"
```

- [ ] **Step 2: Run unit test to verify it fails**

Run: `uv run pytest tests/unit/test_normalization.py -v`
Expected: FAIL — cannot import `genefoundry_router.normalization`.

- [ ] **Step 3: Implement `normalization.py`**

```python
"""Stopgap normalization transforms for non-compliant backends.

These exist only until each -link repo adopts the Tool-Naming Standard v1; when a
source fix lands, delete the matching ``transform`` block from servers.yaml.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

from genefoundry_router.registry import BackendDef, qualified_name


def strip_prefix_name(tool_name: str, prefix: str) -> str:
    """Remove ``prefix`` from the start of ``tool_name`` if present."""
    return tool_name[len(prefix):] if tool_name.startswith(prefix) else tool_name


def build_tool_transform(
    backend: BackendDef,
    present_tools: Iterable[str],
) -> ToolTransform | None:
    """Build a ToolTransform for a backend's gateway-visible (namespaced) tools.

    ``present_tools`` are the already-namespaced names (``<ns>_<leaf>``). Returns
    None when the backend declares no transform.
    """
    tc = backend.transform
    if tc is None:
        return None

    ns = backend.namespace
    transforms: dict[str, ToolTransformConfig] = {}
    for current in present_tools:
        leaf = current[len(ns) + 1:] if current.startswith(f"{ns}_") else current
        new_leaf = leaf
        if tc.strip_prefix:
            new_leaf = strip_prefix_name(new_leaf, tc.strip_prefix)
        if leaf in tc.rename:
            new_leaf = tc.rename[leaf]
        args = {
            old: ArgTransformConfig(name=new)
            for old, new in tc.arg_rename.get(leaf, {}).items()
        }
        if new_leaf == leaf and not args:
            continue  # nothing to change for this tool
        transforms[current] = ToolTransformConfig(
            name=qualified_name(ns, new_leaf),
            arguments=args or None,
        )
    return ToolTransform(transforms) if transforms else None
```

- [ ] **Step 4: Run unit test to verify it passes**

Run: `uv run pytest tests/unit/test_normalization.py -v`
Expected: PASS.

- [ ] **Step 5: Write the integration failing test** (pubtator round-trip after strip + tag injection)

`tests/integration/test_strip_prefix.py`:
```python
from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.normalization import apply_normalizations
from genefoundry_router.registry import BackendDef, TransformConfig


async def test_pubtator_prefix_stripped_at_gateway(pubtator_fake):
    gateway = FastMCP("genefoundry")
    backend = BackendDef(
        name="pubtator", url_env="X", namespace="pubtator",
        tags=["literature", "entity"],
        transform=TransformConfig(strip_prefix="pubtator_"),
    )
    register_backend(gateway, backend, proxy_target=pubtator_fake)
    await apply_normalizations(gateway, [backend])  # async post-mount pass
    async with Client(gateway) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    # leaf was pubtator_search_literature -> namespaced pubtator_pubtator_search_literature
    # -> stripped back to pubtator_search_literature (single, correct prefix)
    assert "pubtator_search_literature" in names
    assert "pubtator_pubtator_search_literature" not in names
    # tags injected so BM25 can index them
    stripped = next(t for t in tools if t.name == "pubtator_search_literature")
    assert {"literature", "entity"} <= set(stripped.tags or [])
```

- [ ] **Step 6: Run integration test — expect FAIL** (`apply_normalizations` not implemented yet)

Run: `uv run pytest tests/integration/test_strip_prefix.py -v`
Expected: FAIL — cannot import `apply_normalizations`.

- [ ] **Step 7: Add `apply_normalizations` to `normalization.py`** (public async API — no `_tool_manager`)

```python
import structlog
from fastmcp import FastMCP

log = structlog.get_logger(__name__)


async def apply_normalizations(server: FastMCP, registry: list[BackendDef]) -> None:
    """Async post-mount pass: rename non-compliant tools, then inject backend tags.

    Enumerates with the PUBLIC ``await server.list_tools()``. Two passes so tag
    injection sees post-rename names. Resilient: a backend that fails to enumerate
    (unreachable proxy) is skipped and retried on the next poll (Task 22).
    """
    # Pass 1 — name/arg transforms
    try:
        present = [t.name for t in await server.list_tools()]
    except Exception as exc:  # noqa: BLE001 - tolerate an unreachable backend at startup
        log.warning("normalization_list_failed", error=str(exc))
        return
    for backend in registry:
        if backend.transform is None:
            continue
        scoped = [n for n in present if n.startswith(f"{backend.namespace}_")]
        transform = build_tool_transform(backend, scoped)
        if transform is not None:
            server.add_transform(transform)
            log.info("normalized", backend=backend.name, tools=len(scoped))

    # Pass 2 — tag injection on post-rename names (union with any existing tags)
    by_ns = {b.namespace: b for b in registry if b.tags}
    if not by_ns:
        return
    for tool in await server.list_tools():
        ns = tool.name.split("_", 1)[0]
        backend = by_ns.get(ns)
        if backend is None:
            continue
        merged = sorted(set(tool.tags or []) | set(backend.tags))
        server.add_tool_transformation(tool.name, ToolTransformConfig(tags=set(merged)))
```

> The renames use a catalog-level `ToolTransform` via `server.add_transform(...)`; tags use per-tool `server.add_tool_transformation(name, ToolTransformConfig(tags=...))`. Both are **public** 3.4.2 APIs (verified). The integration test is the contract — if `add_tool_transformation` replaces rather than merges tags, the union above already supplies the full set. If `ToolTransformConfig` rejects `tags=set(...)`, pass a `list` instead.

- [ ] **Step 8: Run integration test to verify it passes**

Run: `uv run pytest tests/integration/test_strip_prefix.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add genefoundry_router/normalization.py tests/unit/test_normalization.py tests/integration/test_strip_prefix.py
git commit -m "feat: async normalization (strip_prefix/rename/arg-remap) + tag injection"
```

---

### Task 16: Tool-search — `BM25SearchTransform` + `always_visible`

**Files:**
- Create: `genefoundry_router/tool_search.py`
- Modify: `genefoundry_router/server.py` (apply search after all backends mounted)
- Test: `tests/integration/test_tool_search.py`

> Pinned essentials (spec §19 Q4 default): `resolve_variant_id`, `search_genes` (+ the search surface `search_tools`/`call_tool`). At the gateway these are namespaced, so pin the **namespaced** names that exist (`gnomad_resolve_variant_id`, `gnomad_search_genes`). The pinned set is configurable.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_tool_search.py`:
```python
from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server
from genefoundry_router.tool_search import DEFAULT_ALWAYS_VISIBLE, apply_tool_search


async def test_search_surface_hides_bulk_but_keeps_pinned(gnomad_fake, gtex_fake):
    settings = RouterSettings(_env_file=None)
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        BackendDef(name="gtex", url_env="Y", namespace="gtex"),
    ]
    server = build_server(
        settings, registry,
        proxy_targets={"gnomad": gnomad_fake, "gtex": gtex_fake},
    )
    apply_tool_search(server, settings, always_visible=["gnomad_search_genes"])
    async with Client(server) as client:
        listed = {t.name for t in await client.list_tools()}
    # the BM25 surface is present
    assert "search_tools" in listed
    assert "call_tool" in listed
    # pinned essential remains directly listed
    assert "gnomad_search_genes" in listed
    # a non-pinned bulk tool is hidden from the default listing
    assert "gtex_get_gene_information" not in listed


def test_default_always_visible_is_documented():
    assert "search_tools" not in DEFAULT_ALWAYS_VISIBLE  # search_tools is synthetic
    assert DEFAULT_ALWAYS_VISIBLE  # non-empty default pinned set
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_tool_search.py -v`
Expected: FAIL — cannot import `genefoundry_router.tool_search`.

- [ ] **Step 3: Implement `tool_search.py`**

```python
"""BM25 tool-search surface to control tool overload across the fleet."""

from __future__ import annotations

import structlog
from fastmcp import FastMCP
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform

from genefoundry_router.config import RouterSettings

log = structlog.get_logger(__name__)

# Pinned, always-listed essentials (namespaced gateway names). Spec §19 Q4.
DEFAULT_ALWAYS_VISIBLE: list[str] = [
    "gnomad_resolve_variant_id",
    "gnomad_search_genes",
]


def apply_tool_search(
    server: FastMCP,
    settings: RouterSettings,
    always_visible: list[str] | None = None,
) -> None:
    """Replace the full tool listing with search_tools + call_tool + pinned tools."""
    pinned = always_visible if always_visible is not None else DEFAULT_ALWAYS_VISIBLE
    server.add_transform(
        BM25SearchTransform(
            max_results=settings.GF_SEARCH_MAX_RESULTS,
            always_visible=pinned,
        )
    )
    log.info("tool_search_enabled", max_results=settings.GF_SEARCH_MAX_RESULTS, pinned=pinned)
```

- [ ] **Step 4: Wire it into `build_server`** (apply after the mount loop)

In `server.py`, add an `enable_search: bool = True` parameter to `build_server` and call `apply_tool_search` at the end:
```python
def build_server(settings, registry, proxy_targets=None, enable_search=True):
    ...
    for backend in registry:
        ...
    if enable_search:
        from genefoundry_router.tool_search import apply_tool_search
        apply_tool_search(server, settings)
    return server
```
Update the Task-12 `test_built_server_exposes_namespaced_tools` test to call `build_server(..., enable_search=False)` so it still asserts raw namespaced names (search would otherwise hide them).

> **Transform ordering (important):** normalization (renames + tags, Task 15) must run **before** `BM25SearchTransform` so the index reflects the final names/tags. In the standalone `test_tool_search.py` (no normalization) applying search inside `build_server` is fine. But in the **app/production path**, `build_app` calls `build_server(..., enable_search=False)` and the **composed lifespan (Task 23)** applies `apply_normalizations` → `apply_tool_search` in that order. Change `build_app` (Task 12) to pass `enable_search=False`; Task 23 supplies the ordered lifespan. (BM25 also lazily re-indexes on tool-set hash change, but ordering avoids an unindexed first-list window.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_tool_search.py tests/integration/test_server.py -v`
Expected: PASS.

> If `always_visible` names that don't exist cause an error, filter `pinned` to names actually present before constructing the transform (enumerate via `await server.list_tools()`, the API confirmed in Task 15).

- [ ] **Step 6: Commit**

```bash
git add genefoundry_router/tool_search.py genefoundry_router/server.py tests/integration/test_tool_search.py tests/integration/test_server.py
git commit -m "feat: BM25 tool-search surface with pinned essentials"
```

---

### Task 17: `/metrics` + instrumentation middleware + `/health` reachability (R1.7)

**Files:**
- Modify: `genefoundry_router/observability.py`
- Modify: `genefoundry_router/server.py`
- Test: `tests/unit/test_metrics.py`
- Test: `tests/integration/test_metrics_middleware.py`

> R1.7: counters were dead (defined, never incremented) and `/health` was shallow (no reachability). This task adds a FastMCP `MetricsMiddleware` (`on_call_tool`/`on_list_tools`) that actually increments tool-call/search/latency, and enriches `/health` with a cached per-backend reachability map.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_metrics.py`:
```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import BACKEND_UP, register_health, register_metrics, set_backend_up
from genefoundry_router.registry import BackendDef


def test_metrics_endpoint_exposes_prometheus_text():
    app = FastAPI()
    register_metrics(app)
    BACKEND_UP.labels(backend="gnomad").set(1)
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_health_reports_cached_reachability():
    app = FastAPI()
    backends = [BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp")]
    set_backend_up(backends[0], up=True)
    register_health(app, backends)
    body = TestClient(app).get("/health").json()
    assert body["backends"]["reachable"]["gnomad"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_metrics.py -v`
Expected: FAIL — cannot import `register_metrics` / `BACKEND_UP`.

- [ ] **Step 3: Add metrics to `observability.py`**

```python
from prometheus_client import (
    CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest,
)
from starlette.responses import Response

METRICS_REGISTRY = CollectorRegistry()

BACKEND_UP = Gauge(
    "genefoundry_backend_up", "Backend reachability (1=up, 0=down)",
    ["backend"], registry=METRICS_REGISTRY,
)
TOOL_CALLS = Counter(
    "genefoundry_tool_calls_total", "Federated tool-call count",
    ["namespace"], registry=METRICS_REGISTRY,
)
SEARCH_HITS = Counter(
    "genefoundry_search_hits_total", "search_tools invocations",
    registry=METRICS_REGISTRY,
)
TOOL_LATENCY = Histogram(
    "genefoundry_tool_latency_seconds", "Federated tool-call latency",
    ["namespace"], registry=METRICS_REGISTRY,
)

# Cached reachability for /health (updated by startup probe + polling, Tasks 22/23).
BACKEND_STATUS: dict[str, bool] = {}


def set_backend_up(backend: "BackendDef", up: bool) -> None:
    BACKEND_UP.labels(backend=backend.name).set(1 if up else 0)
    BACKEND_STATUS[backend.namespace] = up


def register_metrics(app: FastAPI) -> None:
    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)
```

Update `register_health` to surface the cached reachability map:
```python
    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "service": "genefoundry",
            "backends": {
                "total": len(backends),
                "enabled": len(enabled),
                "namespaces": [b.namespace for b in enabled],
                "reachable": {b.namespace: BACKEND_STATUS.get(b.namespace) for b in enabled},
            },
        }
```

- [ ] **Step 4: Add a FastMCP `MetricsMiddleware`** (the part that makes counters non-dead)

```python
import time

from fastmcp.server.middleware import Middleware, MiddlewareContext


class MetricsMiddleware(Middleware):
    """Increment tool-call/search counters + latency. on_call_tool/on_list_tools verified."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):  # type: ignore[no-untyped-def]
        name = getattr(context.message, "name", "") or ""
        namespace = name.split("_", 1)[0] if "_" in name else "_root"
        if name in ("search_tools", "call_tool"):
            SEARCH_HITS.inc()
        start = time.perf_counter()
        try:
            return await call_next(context)
        finally:
            TOOL_CALLS.labels(namespace=namespace).inc()
            TOOL_LATENCY.labels(namespace=namespace).observe(time.perf_counter() - start)
```

> `time.perf_counter()` is allowed (it is not `Date.now`/`random`). Confirm the `on_call_tool` signature against the installed `Middleware` base (Task 2 smoke listed the `on_*` hooks); if the message attribute differs, read `context.message` once and adjust the name extraction. The integration test is the contract.

- [ ] **Step 5: Write the middleware integration test**

`tests/integration/test_metrics_middleware.py`:
```python
from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.observability import MetricsMiddleware, TOOL_CALLS
from genefoundry_router.registry import BackendDef


async def test_tool_call_increments_counter(gnomad_fake):
    gateway = FastMCP("genefoundry")
    gateway.add_middleware(MetricsMiddleware())
    register_backend(gateway, BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
                     proxy_target=gnomad_fake)
    before = TOOL_CALLS.labels(namespace="gnomad")._value.get()
    async with Client(gateway) as client:
        await client.call_tool("gnomad_get_variant_details", {"value": "x"})
    after = TOOL_CALLS.labels(namespace="gnomad")._value.get()
    assert after == before + 1
```

- [ ] **Step 6: Wire into `build_server` + `build_app`**

In `build_server`, `server.add_middleware(MetricsMiddleware())` before applying transforms. In `build_app`, call `register_metrics(app)` after `register_health(app, registry)`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_metrics.py tests/integration/test_metrics_middleware.py -v`
Expected: PASS. (If `._value.get()` is unavailable in your prometheus-client build, assert via `generate_latest(METRICS_REGISTRY)` text containing the incremented sample instead.)

- [ ] **Step 8: Commit**

```bash
git add genefoundry_router/observability.py genefoundry_router/server.py tests/unit/test_metrics.py tests/integration/test_metrics_middleware.py
git commit -m "feat: metrics middleware, latency histogram, /health reachability"
```

---

### Task 18: CLI `list-tools` (enumerate + flag >64-char names)

**Files:**
- Modify: `genefoundry_router/cli.py`
- Test: `tests/unit/test_cli_list_tools.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_cli_list_tools.py`:
```python
from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def test_list_tools_filters_namespace_and_flags_long(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")

    async def fake_list(settings, registry):
        return [
            "gnomad_get_variant_details",
            "gnomad_" + "x" * 60,  # 67 chars -> over limit
            "gtex_get_gene_information",
        ]

    monkeypatch.setattr("genefoundry_router.cli._list_federated_tools", fake_list)
    result = runner.invoke(app, ["list-tools", "--servers-file", str(yaml), "--namespace", "gnomad"])
    assert result.exit_code == 0, result.output
    assert "gnomad_get_variant_details" in result.output
    assert "gtex_get_gene_information" not in result.output  # filtered out
    assert "OVER 64" in result.output  # long name flagged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_list_tools.py -v`
Expected: FAIL — no `list-tools` command.

- [ ] **Step 3: Add `list_tools` to `cli.py`**

```python
from genefoundry_router.registry import MAX_QUALIFIED_NAME_LEN


async def _list_federated_tools(settings: RouterSettings, registry: list[BackendDef]) -> list[str]:
    """Build the gateway (search disabled) and return all namespaced tool names."""
    from fastmcp import Client

    from genefoundry_router.server import build_server

    server = build_server(settings, registry, enable_search=False)
    async with Client(server) as client:
        return [t.name for t in await client.list_tools()]


@app.command("list-tools")
def list_tools(
    namespace: str = typer.Option(None, help="Filter to a single namespace."),
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Enumerate federated tools (post-namespace, post-transform); flag >64-char names."""
    settings = RouterSettings(GF_SERVERS_FILE=servers_file)
    registry = load_registry(servers_file, os.environ)
    names = asyncio.run(_list_federated_tools(settings, registry))
    if namespace:
        names = [n for n in names if n.startswith(f"{namespace}_")]
    for name in sorted(names):
        flag = "  [red]OVER 64[/red]" if len(name) > MAX_QUALIFIED_NAME_LEN else ""
        console.print(f"{name}{flag}")
    console.print(f"\n[bold]{len(names)} tools[/bold]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_list_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Phase-3 gate**

Run: `make ci-local`
Expected: all green. **v0.2 components complete**: BM25 search, async normalization (pubtator strip_prefix) + tag injection, metrics middleware, structured logs, `list-tools`. (These are component-tested here; the running `genefoundry-router run` applies normalization + search end-to-end once the composed lifespan lands in **Task 23**.)

- [ ] **Step 6: Commit**

```bash
git add genefoundry_router/cli.py tests/unit/test_cli_list_tools.py
git commit -m "feat: list-tools CLI command with 64-char flagging"
```

---

# Phase 4 — Auth & validate (v0.3 logic)

### Task 19: `auth.build_auth()` — dispatch + MCP-compliant jwt/oauth (R1.5)

**Files:**
- Create: `genefoundry_router/auth.py`
- Test: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_auth.py`:
```python
import pytest

from genefoundry_router.auth import build_auth
from genefoundry_router.config import RouterSettings
from genefoundry_router.exceptions import ConfigurationError


def test_none_mode_returns_none():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    assert build_auth(s) is None


def test_jwt_mode_requires_issuer_jwks_audience():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="jwt")  # no issuer/jwks/audience
    with pytest.raises(ConfigurationError):
        build_auth(s)


def test_oauth_mode_requires_config():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="oauth")
    with pytest.raises(ConfigurationError):
        build_auth(s)


def test_oauth_without_jwt_verifier_is_rejected():
    # R1.5: OAuthProxy.token_verifier is REQUIRED — never construct it with None.
    s = RouterSettings(
        _env_file=None, GF_AUTH_MODE="oauth",
        GF_OAUTH_CLIENT_ID="id", GF_OAUTH_CLIENT_SECRET="secret",
        GF_OAUTH_AUTHORIZE_URL="https://idp/authorize", GF_OAUTH_TOKEN_URL="https://idp/token",
        GF_PUBLIC_BASE_URL="https://genefoundry.example.org/mcp",
        # deliberately omit GF_JWT_JWKS_URL/ISSUER/AUDIENCE
    )
    with pytest.raises(ConfigurationError):
        build_auth(s)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auth.py -v`
Expected: FAIL — cannot import `genefoundry_router.auth`.

- [ ] **Step 3: Implement `auth.py`** (dispatch + validation; builders filled next task)

```python
"""Pluggable auth assembly for the router (GF_AUTH_MODE = none|jwt|oauth)."""

from __future__ import annotations

from typing import Any

import structlog

from genefoundry_router.config import RouterSettings
from genefoundry_router.exceptions import ConfigurationError

log = structlog.get_logger(__name__)


def build_auth(settings: RouterSettings) -> Any | None:
    """Return a FastMCP auth provider for the configured mode, or None for 'none'."""
    mode = settings.GF_AUTH_MODE
    if mode == "none":
        log.info("auth_mode", mode="none")
        return None
    if mode == "jwt":
        return _build_jwt(settings)
    if mode == "oauth":
        return _build_oauth(settings)
    raise ConfigurationError(f"unknown GF_AUTH_MODE: {mode!r}")  # pragma: no cover


def _build_jwt(settings: RouterSettings) -> Any:
    # MCP auth (2025-11-25): audience binding is a MUST for a protected resource.
    if not (settings.GF_JWT_ISSUER and settings.GF_JWT_JWKS_URL and settings.GF_JWT_AUDIENCE):
        raise ConfigurationError(
            "jwt mode requires GF_JWT_ISSUER, GF_JWT_JWKS_URL, and GF_JWT_AUDIENCE (audience MUST)"
        )
    from fastmcp.server.auth.providers.jwt import JWTVerifier

    log.info("auth_mode", mode="jwt", issuer=settings.GF_JWT_ISSUER)
    return JWTVerifier(
        jwks_uri=settings.GF_JWT_JWKS_URL,
        issuer=settings.GF_JWT_ISSUER,
        audience=settings.GF_JWT_AUDIENCE,  # reject tokens not minted for this server
        base_url=settings.GF_PUBLIC_BASE_URL,  # canonical public resource URI (PRM)
    )


def _build_oauth(settings: RouterSettings) -> Any:
    # R1.5: OAuthProxy.token_verifier is REQUIRED — so the JWT verifier inputs are
    # mandatory in oauth mode too (no None verifier). base_url MUST be the public URL.
    required = {
        "GF_OAUTH_CLIENT_ID": settings.GF_OAUTH_CLIENT_ID,
        "GF_OAUTH_CLIENT_SECRET": settings.GF_OAUTH_CLIENT_SECRET,
        "GF_OAUTH_AUTHORIZE_URL": settings.GF_OAUTH_AUTHORIZE_URL,
        "GF_OAUTH_TOKEN_URL": settings.GF_OAUTH_TOKEN_URL,
        "GF_PUBLIC_BASE_URL": settings.GF_PUBLIC_BASE_URL,
        "GF_JWT_ISSUER": settings.GF_JWT_ISSUER,
        "GF_JWT_JWKS_URL": settings.GF_JWT_JWKS_URL,
        "GF_JWT_AUDIENCE": settings.GF_JWT_AUDIENCE,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ConfigurationError(f"oauth mode requires: {', '.join(missing)}")
    from fastmcp.server.auth import MultiAuth, OAuthProxy

    verifier = _build_jwt(settings)  # always a real TokenVerifier
    oauth = OAuthProxy(
        upstream_authorization_endpoint=settings.GF_OAUTH_AUTHORIZE_URL,
        upstream_token_endpoint=settings.GF_OAUTH_TOKEN_URL,
        upstream_client_id=settings.GF_OAUTH_CLIENT_ID,
        upstream_client_secret=settings.GF_OAUTH_CLIENT_SECRET,
        token_verifier=verifier,  # REQUIRED — never None
        base_url=settings.GF_PUBLIC_BASE_URL,  # this server's public URL (redirects + PRM)
        resource_base_url=settings.GF_PUBLIC_BASE_URL,  # canonical resource URI for audience
    )
    log.info("auth_mode", mode="oauth", provider=settings.GF_OAUTH_PROVIDER)
    # MultiAuth lets M2M JWT + interactive OAuth coexist (spec §9).
    return MultiAuth(server=oauth, verifiers=[verifier])
```

> The router auto-serves Protected-Resource-Metadata + `WWW-Authenticate` on 401 once this provider is attached (`JWTVerifier.get_well_known_routes` / `RemoteAuthProvider` — verified) — asserted in **Task 25**. Behind nginx-proxy-manager, ensure forwarded headers (`X-Forwarded-Proto`/`-Host`) reach the app so generated absolute URLs use `GF_PUBLIC_BASE_URL`, not the bind socket.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/auth.py tests/unit/test_auth.py
git commit -m "feat: pluggable auth assembly (none/jwt/oauth dispatch)"
```

---

### Task 20: Wire auth into `build_server` + assert provider construction

**Files:**
- Modify: `genefoundry_router/server.py`
- Test: `tests/unit/test_auth_wiring.py`

- [ ] **Step 1: Write the failing test** (jwt mode constructs a verifier; OAuthProxy/JWT need a fake JWKS so use monkeypatch)

`tests/unit/test_auth_wiring.py`:
```python
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server


def test_server_built_with_none_auth():
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    server = build_server(s, [BackendDef(name="hgnc", url_env="X", namespace="hgnc", enabled=False)],
                          enable_search=False)
    assert server.auth is None


def test_server_built_with_jwt_auth(monkeypatch):
    captured = {}

    def fake_build_auth(settings):
        captured["mode"] = settings.GF_AUTH_MODE
        return object()  # stand-in auth provider

    monkeypatch.setattr("genefoundry_router.server.build_auth", fake_build_auth)
    s = RouterSettings(_env_file=None, GF_AUTH_MODE="jwt")
    server = build_server(s, [BackendDef(name="hgnc", url_env="X", namespace="hgnc", enabled=False)],
                          enable_search=False)
    assert captured["mode"] == "jwt"
    assert server.auth is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_auth_wiring.py -v`
Expected: FAIL — `build_server` does not yet build/attach auth (and `server.auth` is None for jwt).

- [ ] **Step 3: Attach auth in `build_server`**

In `server.py`, import and use `build_auth`:
```python
from genefoundry_router.auth import build_auth

def build_server(settings, registry, proxy_targets=None, enable_search=True):
    auth = build_auth(settings)
    server: FastMCP = FastMCP("genefoundry", auth=auth)
    ...
```
> Confirm the attribute is `server.auth` in the installed build; if it's stored elsewhere (e.g. `server._auth`), adjust the test's accessor. The contract is "auth provider is attached when mode != none."
>
> **R1.6 invariant (document in the module docstring):** the gateway authenticates the *caller* at this edge; it MUST NOT forward the caller's token to the 13 backends (confused-deputy). Backend proxies use the router's own connection (Task 10). Never wire the incoming `Authorization` header into `ProxyClient`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_auth_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/server.py tests/unit/test_auth_wiring.py
git commit -m "feat: attach configured auth provider to the gateway"
```

---

### Task 21: CLI `validate` command

**Files:**
- Modify: `genefoundry_router/cli.py`
- Test: `tests/unit/test_cli_validate.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_cli_validate.py`:
```python
from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def test_validate_flags_missing_url_for_enabled(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text(
        "servers:\n"
        "  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n"
        "  - { name: hgnc, url_env: GF_HGNC_URL, namespace: hgnc, enabled: false }\n"
    )
    monkeypatch.delenv("GF_GNOMAD_URL", raising=False)
    result = runner.invoke(app, ["validate", "--servers-file", str(yaml)])
    assert result.exit_code == 1  # enabled gnomad has no URL
    assert "gnomad" in result.output
    assert "missing URL" in result.output


def test_validate_passes_when_all_enabled_have_urls(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")
    monkeypatch.setenv("GF_GNOMAD_URL", "https://gnomad-link.example.org/mcp")
    result = runner.invoke(app, ["validate", "--servers-file", str(yaml)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_validate.py -v`
Expected: FAIL — no `validate` command.

- [ ] **Step 3: Add `validate` to `cli.py`**

```python
@app.command()
def validate(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Validate servers.yaml + env; report missing URLs and invalid namespaces."""
    registry = load_registry(servers_file, os.environ)
    problems: list[str] = []
    for b in registry:
        if b.enabled and b.url is None:
            problems.append(f"{b.name}: missing URL (set {b.url_env})")
    if problems:
        for p in problems:
            console.print(f"[red]FAIL[/red] {p}")
        raise typer.Exit(1)
    console.print(f"[green]OK[/green] {len(registry)} backends valid "
                  f"({sum(b.enabled for b in registry)} enabled)")
```

> `load_registry` already enforces namespace charset and duplicate-namespace rules (raising `RegistryError`); let that surface as a typer error for malformed files. Optionally wrap the `load_registry` call in try/except to print a friendly message and `raise typer.Exit(1)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_validate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/cli.py tests/unit/test_cli_validate.py
git commit -m "feat: validate CLI command"
```

---

# Phase 4.5 — Security & observability hardening (v0.3+)

### Task 22: `discovery.py` — polling re-list fallback

**Files:**
- Create: `genefoundry_router/discovery.py`
- Test: `tests/unit/test_discovery.py`

> Per Convention notes §5, proxy freshness is TTL-based. The polling fallback periodically forces a re-list of the gateway's tools so the BM25 index reflects upstream changes within `GF_POLL_INTERVAL` even when `cache_ttl` is high. `GF_POLL_INTERVAL=0` disables it. This task builds the refresher; **Task 23 wires it into the app lifespan** (R1.7 — it was previously never started).

- [ ] **Step 1: Write the failing test**

`tests/unit/test_discovery.py`:
```python
import asyncio

import pytest

from genefoundry_router.discovery import PollingRefresher


async def test_refresher_calls_relist_each_interval():
    calls = {"n": 0}

    async def relist():
        calls["n"] += 1

    refresher = PollingRefresher(interval_seconds=0.01, relist=relist)
    await refresher.start()
    await asyncio.sleep(0.05)
    await refresher.stop()
    assert calls["n"] >= 2  # fired multiple times over 50ms at 10ms interval


async def test_zero_interval_is_disabled():
    async def relist():  # pragma: no cover - must never run
        raise AssertionError("should not be called when disabled")

    refresher = PollingRefresher(interval_seconds=0, relist=relist)
    await refresher.start()
    await asyncio.sleep(0.02)
    await refresher.stop()
    assert refresher.running is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_discovery.py -v`
Expected: FAIL — cannot import `genefoundry_router.discovery`.

- [ ] **Step 3: Implement `discovery.py`**

```python
"""Polling re-list fallback for proxy freshness (TTL-based backends)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)


class PollingRefresher:
    """Periodically invoke ``relist`` to refresh the federated tool index.

    Disabled when ``interval_seconds <= 0``.
    """

    def __init__(self, interval_seconds: float, relist: Callable[[], Awaitable[None]]) -> None:
        self._interval = interval_seconds
        self._relist = relist
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._interval <= 0:
            log.info("polling_disabled")
            return
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._relist()
                except Exception as exc:  # noqa: BLE001 - polling must survive errors
                    log.warning("relist_failed", error=str(exc))
        except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
            pass

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/discovery.py tests/unit/test_discovery.py
git commit -m "feat: polling re-list refresher for proxy freshness"
```

---

### Task 23: Compose the app lifespan — async normalization + tool-search + polling (R1.2/R1.7)

**Files:**
- Modify: `genefoundry_router/server.py`
- Test: `tests/integration/test_lifespan.py`

> This is the integration point the review flagged: normalization (Task 15) and the polling refresher (Task 22) existed but nothing ran them. `build_app` now installs a **composed lifespan** that, on startup, enters the MCP app lifespan → runs `apply_normalizations` → applies tool-search (after normalization, per Task 16 ordering) → probes backends once (seeds `/health`) → starts the `PollingRefresher`; on shutdown it stops the refresher.

- [ ] **Step 1: Write the failing test**

`tests/integration/test_lifespan.py`:
```python
from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef, TransformConfig
from genefoundry_router.server import build_app


def test_lifespan_runs_normalization_then_search(pubtator_fake):
    # poll disabled; normalization must still run at startup
    settings = RouterSettings(_env_file=None, GF_POLL_INTERVAL=0)
    registry = [BackendDef(name="pubtator", url_env="X", namespace="pubtator",
                           tags=["literature"], transform=TransformConfig(strip_prefix="pubtator_"))]
    app = build_app(settings, registry, proxy_targets={"pubtator": pubtator_fake})
    with TestClient(app):  # triggers lifespan startup + shutdown
        # health reflects the startup probe seeding BACKEND_STATUS
        body = TestClient(app).get("/health").json()
        assert "pubtator" in body["backends"]["namespaces"]
```

- [ ] **Step 2: Run it (FAIL — composed lifespan not implemented)**

Run: `uv run pytest tests/integration/test_lifespan.py -v`

- [ ] **Step 3: Replace `build_app`'s lifespan with a composed one**

```python
from contextlib import asynccontextmanager

from genefoundry_router.discovery import PollingRefresher
from genefoundry_router.normalization import apply_normalizations
from genefoundry_router.observability import set_backend_up
from genefoundry_router.tool_search import apply_tool_search


def build_app(settings, registry, proxy_targets=None):
    configure_logging(settings.GF_LOG_LEVEL)
    server = build_server(settings, registry, proxy_targets=proxy_targets, enable_search=False)
    mcp_app = server.http_app(path="/")

    async def _relist() -> None:
        await apply_normalizations(server, registry)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.lifespan(app):
            await apply_normalizations(server, registry)   # R1.2 — async, after mount
            apply_tool_search(server, settings)            # ordering: after normalization
            for b in registry:                              # seed /health reachability
                if b.enabled:
                    set_backend_up(b, up=(proxy_targets or {}).get(b.name) is not None or b.url is not None)
            refresher = PollingRefresher(settings.GF_POLL_INTERVAL, _relist)
            await refresher.start()
            try:
                yield
            finally:
                await refresher.stop()

    app = FastAPI(title="GeneFoundry Router", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)
    register_health(app, registry)
    register_metrics(app)
    app.mount(settings.GF_MCP_PATH, mcp_app)
    return app
```

> The earlier `test_build_app_serves_health` (Task 12) still passes (health is independent of lifespan). The startup probe here is a cheap presence check; **Task 24/25** add real reachability and auth contract tests. If `mcp_app.lifespan` is not an async context manager in your build, wrap via `mcp_app.router.lifespan_context`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_lifespan.py tests/integration/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/server.py tests/integration/test_lifespan.py
git commit -m "feat: composed app lifespan (normalization + tool-search + polling)"
```

---

### Task 24: Origin-validation end-to-end through the app (R1.4)

**Files:**
- Test: `tests/integration/test_origin_app.py`

- [ ] **Step 1: Write the test** (no new code — verifies the wired middleware end-to-end)

`tests/integration/test_origin_app.py`:
```python
from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app


def _client(gnomad_fake, origins):
    settings = RouterSettings(_env_file=None, GF_ALLOWED_ORIGINS=origins)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    return TestClient(build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake}))


def test_disallowed_origin_blocked_on_mcp(gnomad_fake):
    c = _client(gnomad_fake, ["https://claude.ai"])
    r = c.post("/mcp/", headers={"origin": "https://evil.example"}, json={})
    assert r.status_code == 403


def test_absent_origin_allowed(gnomad_fake):
    c = _client(gnomad_fake, [])
    assert c.get("/health").status_code == 200  # health check sends no Origin
```

- [ ] **Step 2: Run + confirm PASS**

Run: `uv run pytest tests/integration/test_origin_app.py -v`
Expected: PASS. (If `RouterSettings(GF_ALLOWED_ORIGINS=[...])` rejects a list literal because of the CSV `before` validator, pass a comma-joined string instead — the validator splits it.)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_origin_app.py
git commit -m "test: end-to-end Origin validation on the mounted app"
```

---

### Task 25: OAuth Protected-Resource-Metadata + 401 contract (R1.5)

**Files:**
- Test: `tests/integration/test_auth_contract.py`

> Proves FastMCP auto-serves the MCP authorization discovery surface once a provider is attached. Uses `jwt` mode (no live IdP needed — a static JWKS URL is enough to construct the verifier; no token is presented, so the request is unauthorized).

- [ ] **Step 1: Write the test**

`tests/integration/test_auth_contract.py`:
```python
import pytest
from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app


def _jwt_app(gnomad_fake):
    settings = RouterSettings(
        _env_file=None, GF_AUTH_MODE="jwt",
        GF_JWT_ISSUER="https://idp.example.org/",
        GF_JWT_JWKS_URL="https://idp.example.org/.well-known/jwks.json",
        GF_JWT_AUDIENCE="https://genefoundry.example.org/mcp",
        GF_PUBLIC_BASE_URL="https://genefoundry.example.org/mcp",
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    return TestClient(build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake}))


def test_unauthenticated_mcp_returns_401_with_www_authenticate(gnomad_fake):
    c = _jwt_app(gnomad_fake)
    r = c.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "www-authenticate" in {k.lower() for k in r.headers}


def test_protected_resource_metadata_served(gnomad_fake):
    c = _jwt_app(gnomad_fake)
    # RFC 9728 well-known (root or path-suffixed form); accept either
    for path in ("/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-protected-resource/mcp"):
        r = c.get(path)
        if r.status_code == 200 and "authorization_servers" in r.json():
            return
    pytest.fail("no Protected Resource Metadata document served")
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/integration/test_auth_contract.py -v`
Expected: PASS. **If FastMCP serves the PRM/401 on the MCP sub-app rather than the outer host**, request paths under `/mcp` (e.g. `/mcp/.well-known/...`) or assert against `server.http_app()` directly. Adjust the asserted paths to whatever the attached provider actually exposes — the **contract is: unauthenticated → 401 + `WWW-Authenticate`, and a PRM document with `authorization_servers` is discoverable.** If a 3.4.2 detail makes the exact wiring differ, record the observed behavior and keep the contract assertions.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_auth_contract.py
git commit -m "test: MCP auth discovery (401 + WWW-Authenticate + protected-resource-metadata)"
```

---

### Task 26: `doctor --strict-naming` — fleet Standard-v1 leaf audit (R1.9)

**Files:**
- Modify: `genefoundry_router/cli.py`
- Test: `tests/unit/test_strict_naming.py`

> The router sees every backend's **leaf** names, so it is the natural place to enforce Tool-Naming Standard v1 (unprefixed, `verb_noun`, ≤50 chars, canonical verb). `doctor --strict-naming` reports per-backend violations; exit non-zero if any.

- [ ] **Step 1: Write the failing test**

`tests/unit/test_strict_naming.py`:
```python
from genefoundry_router.cli import check_leaf_name

CANONICAL_VERBS = {"get", "search", "list", "resolve", "find", "compare", "compute"}


def test_compliant_leaf_passes():
    assert check_leaf_name("get_variant_details") == []


def test_violations_detected():
    issues = check_leaf_name("pubtator_searchLiterature")  # prefixed + camelCase + non-verb
    assert any("prefix" in i or "verb" in i or "charset" in i for i in issues)


def test_overlong_leaf_flagged():
    issues = check_leaf_name("get_" + "x" * 60)  # >50 chars
    assert any("50" in i for i in issues)
```

- [ ] **Step 2: Run it (FAIL — no `check_leaf_name`)**

Run: `uv run pytest tests/unit/test_strict_naming.py -v`

- [ ] **Step 3: Add `check_leaf_name` + `--strict-naming` to `cli.py`**

```python
import re

LEAF_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
CANONICAL_VERBS = {"get", "search", "list", "resolve", "find", "compare", "compute"}
# Documented v1.1 action-verb exceptions (spec §19 Q2 — left as exceptions for now).
ACTION_VERB_EXCEPTIONS = {"predict", "analyze", "annotate", "submit", "export", "generate", "download"}


def check_leaf_name(leaf: str) -> list[str]:
    """Return Tool-Naming Standard v1 violations for a single leaf tool name."""
    issues: list[str] = []
    if not LEAF_NAME_RE.match(leaf):
        issues.append(f"charset/length: {leaf!r} must match ^[a-z0-9_]{{1,50}}$ (≤50)")
    verb = leaf.split("_", 1)[0]
    if verb not in CANONICAL_VERBS and verb not in ACTION_VERB_EXCEPTIONS:
        issues.append(f"verb: {leaf!r} starts with non-canonical verb {verb!r}")
    return issues
```

Extend `doctor` with a `--strict-naming` option that, for each reachable backend, lists its leaf tools (strip the namespace prefix) and reports `check_leaf_name` violations; `raise typer.Exit(1)` if any are found.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_strict_naming.py -v`
Expected: PASS.

- [ ] **Step 5: Phase-4.5 gate**

Run: `make ci-local`
Expected: all green. **v0.3 (logic) complete**: auth (none/jwt/oauth, audience-bound), Origin validation, composed lifespan (normalization + tool-search + polling), metrics/health, `validate`, `list-tools`, `doctor --strict-naming`.

- [ ] **Step 6: Commit**

```bash
git add genefoundry_router/cli.py tests/unit/test_strict_naming.py
git commit -m "feat: doctor --strict-naming fleet compliance audit"
```

---

# Phase 5 — Docker & deploy (v0.3)

### Task 27: Dockerfile

**Files:**
- Create: `docker/Dockerfile`
- Test: `tests/unit/docker/test_dockerfile.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/docker/test_dockerfile.py`:
```python
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[3] / "docker" / "Dockerfile"


def test_dockerfile_exists_and_runs_router():
    text = DOCKERFILE.read_text()
    assert "FROM python:3.12-slim" in text
    assert "uv sync --frozen --no-dev" in text
    assert "EXPOSE 8000" in text
    # default command starts the router over http
    assert "genefoundry-router" in text
    assert 'run' in text and "--host" in text and "0.0.0.0" in text
```

Create `tests/unit/docker/__init__.py` (empty).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/docker/test_dockerfile.py -v`
Expected: FAIL — Dockerfile not found.

- [ ] **Step 3: Create `docker/Dockerfile`** (multi-stage, mirrors gnomad-link; entrypoint = router `run`)

```dockerfile
# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY genefoundry_router ./genefoundry_router
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS production
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/build/.venv/bin:$PATH" \
    GF_HOST=0.0.0.0 \
    GF_PORT=8000 \
    GF_MCP_PATH=/mcp
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /home/app/web
COPY --from=builder /build/.venv /build/.venv
COPY genefoundry_router ./genefoundry_router
COPY servers.yaml pyproject.toml README.md ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["genefoundry-router", "run", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/docker/test_dockerfile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docker/Dockerfile tests/unit/docker/__init__.py tests/unit/docker/test_dockerfile.py
git commit -m "feat: multi-stage Dockerfile for the router"
```

---

### Task 28: Compose overlays (base / prod / dev / npm)

**Files:**
- Create: `docker/docker-compose.yml`
- Create: `docker/docker-compose.prod.yml`
- Create: `docker/docker-compose.dev.yml`
- Create: `docker/docker-compose.npm.yml`
- Test: `tests/unit/docker/test_compose.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/docker/test_compose.py`:
```python
from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[3] / "docker"


def test_base_compose_defines_service_and_healthcheck():
    data = yaml.safe_load((DOCKER / "docker-compose.yml").read_text())
    svc = data["services"]["genefoundry-router"]
    assert svc["healthcheck"]["test"][-1].endswith("/health")
    assert "8000" in str(svc["ports"])


def test_prod_overlay_hardens():
    data = yaml.safe_load((DOCKER / "docker-compose.prod.yml").read_text())
    svc = data["services"]["genefoundry-router"]
    assert svc["read_only"] is True
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["cap_drop"] == ["ALL"]


def test_npm_overlay_joins_external_network():
    data = yaml.safe_load((DOCKER / "docker-compose.npm.yml").read_text())
    assert data["networks"]["npm-network"]["external"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/docker/test_compose.py -v`
Expected: FAIL — compose files not found.

- [ ] **Step 3: Create the four compose files**

`docker/docker-compose.yml`:
```yaml
services:
  genefoundry-router:
    build:
      context: ..
      dockerfile: docker/Dockerfile
      target: production
    container_name: genefoundry_router
    env_file:
      - path: ../.env
        required: false
      - path: ../.env.docker
        required: false
    environment:
      GF_HOST: 0.0.0.0
      GF_PORT: 8000
      GF_MCP_PATH: /mcp
      GF_LOG_LEVEL: INFO
    ports:
      - "${GENEFOUNDRY_ROUTER_HOST_PORT:-8010}:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
    restart: unless-stopped
```

`docker/docker-compose.prod.yml`:
```yaml
services:
  genefoundry-router:
    environment:
      GF_LOG_LEVEL: INFO
    read_only: true
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=64m,mode=1777
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 256
    init: true
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: "1.0"
    restart: on-failure
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"
```

`docker/docker-compose.dev.yml`:
```yaml
services:
  genefoundry-router:
    build:
      target: builder
    environment:
      GF_LOG_LEVEL: DEBUG
    volumes:
      - ../genefoundry_router:/home/app/web/genefoundry_router:delegated
      - ../servers.yaml:/home/app/web/servers.yaml:ro
    command: ["genefoundry-router", "run", "--host", "0.0.0.0", "--port", "8000", "--log-level", "DEBUG"]
```

`docker/docker-compose.npm.yml`:
```yaml
services:
  genefoundry-router:
    ports: []
    expose:
      - "8000"
    networks:
      - npm-network

networks:
  npm-network:
    external: true
    name: ${NPM_NETWORK_NAME:-npm_network}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/docker/test_compose.py -v`
Expected: PASS.

- [ ] **Step 5: Verify compose renders (requires docker)**

Run: `make docker-prod-config` and `make docker-npm-config`
Expected: both render without error. (Skip if docker is unavailable in this environment; note it as a manual deploy check.)

- [ ] **Step 6: Commit**

```bash
git add docker/docker-compose.yml docker/docker-compose.prod.yml docker/docker-compose.dev.yml docker/docker-compose.npm.yml tests/unit/docker/test_compose.py
git commit -m "feat: docker-compose overlays (base/prod/dev/npm)"
```

---

### Task 29: `uv.lock` freeze + Docker build sanity

**Files:** none new.

- [ ] **Step 1: Freeze the lock**

Run: `uv lock`
Expected: `uv.lock` up to date (already created in Task 2; confirm no drift).

- [ ] **Step 2: Build the image (requires docker)**

Run: `make docker-build`
Expected: image builds. If docker is unavailable, record this as a manual pre-deploy step in the README (Task 30).

- [ ] **Step 3: Commit (if lock changed)**

```bash
git add uv.lock
git commit -m "chore: freeze uv.lock"
```

---

# Phase 6 — Docs & v1.0 gate

### Task 30: README (expand stub), AGENTS.md, CLAUDE.md

**Files:**
- Modify: `README.md` (expand the Task-1 stub)
- Create: `AGENTS.md`
- Create: `CLAUDE.md`
- Test: `tests/unit/test_docs_presence.py`

- [ ] **Step 1: Write the failing test** (keep docs honest about commands/structure)

`tests/unit/test_docs_presence.py`:
```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_core_commands():
    txt = (ROOT / "README.md").read_text()
    for token in ["genefoundry-router run", "/health", "/mcp", "servers.yaml",
                  "GF_AUTH_MODE", "GF_ALLOWED_ORIGINS", "search_tools"]:
        assert token in txt, f"README missing {token!r}"


def test_claude_md_references_agents_md():
    assert "@AGENTS.md" in (ROOT / "CLAUDE.md").read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_docs_presence.py -v`
Expected: FAIL — the Task-1 stub lacks the required tokens.

- [ ] **Step 3: Expand `README.md`** (mirror gnomad-link section structure)

Sections: `# GeneFoundry Router` → **Core Purpose** → **Key Features** → **Quick Start** (Install `uv sync --group dev`; configure `.env` from `.env.example`; `genefoundry-router run`; verify `curl localhost:8000/health`; add to Claude via the `/mcp` URL) → **Configuration** (env table incl. `GF_AUTH_MODE`, `GF_ALLOWED_ORIGINS`, `GF_PUBLIC_BASE_URL`, backend `GF_*_URL` vars; `servers.yaml` structure) → **CLI** (`run`/`validate`/`list-tools`/`doctor [--strict-naming]`) → **MCP Integration** (Streamable HTTP at `/mcp`) → **Tool discovery** (the router exposes synthetic `search_tools`/`call_tool` + pinned essentials — R1.10: this is the MCP-server-side equivalent of, and independent from, Anthropic's API-level tool-search; works with any MCP client) → **Client compatibility** (Claude connector + Gemini Remote MCP; Gemini requires snake_case ≤64-char names — the gateway already emits these) → **Architecture** (one-endpoint federation, namespacing, BM25 search) → **Security** (Origin validation, auth modes; `none` is local/PoC only; the gateway never forwards your token to backends) → **Deployment** (Docker overlays, nginx-proxy-manager; set `GF_PUBLIC_BASE_URL` + forwarded headers) → **Status caveats** (hgnc disabled until deployment fixed; pubtator strip_prefix until source lands) → **Research-use-only disclaimer**. Include the literal tokens the test checks for.

- [ ] **Step 4: Write `AGENTS.md`** (adapt gnomad-link's; key differences below)

Cover: project = thin FastMCP router (not a data server); primary area `genefoundry_router/`; source-of-truth = `servers.yaml` + `.env`, `uv.lock`; required check `make ci-local`; the full `make` command list; coding standards (uv, modern typing, ruff, mypy py3.12); **600-LOC file discipline**; spec at `docs/specs/2026-06-13-genefoundry-router-design.md` and plan at `docs/plans/`; research-use-only boundary; "namespacing is the gateway's job — keep transforms minimal and delete them as source repos adopt Standard v1."

- [ ] **Step 5: Write `CLAUDE.md`**

```markdown
# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff (runs `lint-loc`, the 600-LOC budget).
- FastMCP 3.x symbols are post-training-cutoff and fast-moving — verify imports
  against the installed package before relying on them (see the import smoke in
  `docs/plans/2026-06-13-genefoundry-router-implementation.md`, Task 2).
- When a backend's source repo adopts Tool-Naming Standard v1, delete its
  `transform` block from `servers.yaml` rather than adding router-side workarounds.
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_docs_presence.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add README.md AGENTS.md CLAUDE.md tests/unit/test_docs_presence.py
git commit -m "docs: README, AGENTS.md, CLAUDE.md"
```

---

### Task 31: Full coverage gate + v1.0 follow-up checklist

**Files:**
- Create: `docs/plans/V1.0-GATE.md`

- [ ] **Step 1: Run the coverage gate**

Run: `make test-cov`
Expected: coverage ≥ 70% (fleet floor). If below, add focused unit tests for any uncovered branch in `config.py`, `composition.py`, `normalization.py`, `auth.py`, `cli.py` until the floor is met.

- [ ] **Step 2: Run the full local CI**

Run: `make ci-local`
Expected: all green.

- [ ] **Step 3: Write `docs/plans/V1.0-GATE.md`** — items blocked on external state (not code):

```markdown
# v1.0 Gate — external blockers

- [ ] hgnc-link live deployment fixed (currently serves mgi-link binary) → flip
      `enabled: true` for hgnc in servers.yaml + uncomment GF_HGNC_URL.
- [ ] pubtator-link drops self-prefix (Standard v1) → delete the
      `transform: { strip_prefix: "pubtator_" }` block from servers.yaml.
- [ ] All 13 backends adopt Tool-Naming Standard v1 → remove any remaining
      transform blocks; confirm `genefoundry-router list-tools` shows zero
      OVER-64 names across the full fleet.
- [ ] Spec §19 Q2 (Standard v1.1 verbs) decided fleet-wide.
- [ ] First public deploy auth mode chosen (none vs jwt) per spec §19 Q3.
- [ ] `always_visible` pinned set confirmed against the live fleet (spec §19 Q4).
- [ ] Landing-page "Add to Claude" button wired to the deployed /mcp endpoint.
```

- [ ] **Step 4: Commit**

```bash
git add docs/plans/V1.0-GATE.md
git commit -m "docs: v1.0 external-blocker gate checklist"
```

---

## Self-review (re-run after R1 code-review revisions)

**Spec coverage:**
- §1–4 architecture → Tasks 10, 12 (proxy/mount/namespace; FastAPI host). ✓
- §5 registry/config → Tasks 4 (+`transport` field, R1.1), 6, 7, 8 (`servers.yaml`, `.env`, defaults merge, env URLs). ✓
- §6 namespacing + 64-char → Tasks 5 (+client-safe charset, R1.10), 10, 18. ✓
- §7 normalization (two layers) → Task 15 (async router stopgap + tags) + Task 31/V1.0-GATE (source layer). ✓
- §8 BM25 search → Task 16 (+ ordering after normalization). ✓
- §9 auth (none/jwt/oauth, MultiAuth) → Tasks 19 (audience + public base, R1.5), 20 (+no-passthrough, R1.6), 25 (PRM/401 contract). ✓
- §10 discovery/freshness → Task 14 (cache_ttl) + Task 22 (polling) + Task 23 (lifespan wiring, R1.7). Push-subscription consciously deferred (Convention notes §5). ✓
- §11 transport (HTTP only, /mcp) → Tasks 4 (reject SSE), 12, 13. ✓
- §12 observability (/health reachability, /metrics + MetricsMiddleware, structlog, correlation-id) → Tasks 11, 17 (R1.7). ✓
- §13 layout → all modules + new `security.py` created across Tasks 2–26. ✓
- §14 CLI (run/validate/list-tools/doctor[+--strict-naming]) → Tasks 13, 18, 21, 26. ✓
- §15 env reference → Task 6 (`RouterSettings` + security env) + Task 8 (`.env.example`). ✓
- §16 testing (unit/integration/contract, fakes, ≥70) → Tasks 9, 31 + per-task TDD. ✓
- §17 deployment (Docker overlays, npm) → Tasks 27–29. ✓
- §18 milestones → Phase gates at Tasks 13 (v0.1), 18 (v0.2), 26 (v0.3 logic), 28 (v0.3 deploy). ✓
- §19 open questions → defaults applied (Q1 URL pattern Task 8; Q2 v1.1 verbs as documented exceptions in Task 26 + V1.0-GATE; Q3 none Task 6/8; Q4 pinned set Task 16). ✓
- **MCP 2025-11-25 transport/auth security** (beyond original spec) → Origin MUST (Tasks 12/24), audience/PRM/401 (Tasks 19/25), no token passthrough (Tasks 10/20). ✓

**Placeholder scan:** every code step carries complete code. The post-cutoff API-risk spots — async tool enumeration (Task 15, now public `list_tools()`), `server.auth` accessor (Task 20), `add_provider`/`ProxyProvider` (Task 14), `MetricsMiddleware.on_call_tool` signature (Task 17), PRM/401 wiring (Task 25), `add_tool_transformation` tag merge (Task 15) — each carries an explicit "verify against installed build; the test is the contract" fallback, not a vague TODO. The broken `server._tool_manager` accessor (R1.2) is removed.

**Type consistency:** `BackendDef`(+`transport`)/`TransformConfig` fields, `load_registry(path, environ)`, `register_backend(server, backend, proxy_target)`, `build_proxy(backend, target)`, `build_server(settings, registry, proxy_targets, enable_search)`, `build_app(settings, registry, proxy_targets)`, `apply_normalizations(server, registry)` (async), `build_auth(settings)`, `qualified_name`/`exceeds_name_limit`/`is_client_safe_name`, `configure_logging`/`register_health`/`register_metrics`/`set_backend_up`/`MetricsMiddleware`, `add_origin_validation(app, allowed_origins)`, `apply_tool_search(server, settings, always_visible)`, `build_tool_transform(backend, present_tools)`, `PollingRefresher(interval_seconds, relist)`, `check_leaf_name(leaf)` — names/signatures consistent across all referencing tasks. ✓

---

## Execution handoff

After review/approval, recommended execution = **subagent-driven** (fresh subagent per task, two-stage review between tasks). Phase gates (`make ci-local` at Tasks 13/18/26 + coverage at 31) are the natural review checkpoints.
