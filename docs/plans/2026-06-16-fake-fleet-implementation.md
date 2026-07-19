# Hermetic Fake-Fleet Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third local-testing tier — run the real router against impersonated backends over real Streamable-HTTP, fully offline and deterministic, no Docker.

**Architecture:** A committed JSON manifest (record/replay) describes each backend's real tool surface (name + description + inputSchema + tags). A `devtools` package builds one echo-`FastMCP` per backend (exact schema via `Tool.parameters` override), mounts them onto one Starlette app on one localhost port (path-routed `/<ns>/mcp`, every child lifespan entered via `AsyncExitStack`), and the unmodified router federates them via a dev registry. An on-demand snapshot script refreshes the manifest online.

**Tech Stack:** Python 3.12, uv, FastMCP 3.4.2, Starlette/uvicorn, pydantic, pytest (asyncio_mode=auto), Typer (existing CLI), ruff + mypy.

**Spec:** `docs/specs/2026-06-16-fake-fleet-local-testing-design.md`

---

## Conventions for this plan

- Branch `feat/fake-fleet` is already checked out. Commit after every task.
- Run a single test: `uv run pytest <path>::<test> -v`. Run a dir: `uv run pytest tests/unit -q`.
- Keep every module < 600 LOC (`make lint-loc`). Code must pass `ruff` + `mypy` (`make ci-local`).
- Tests use `RouterSettings(_env_file=None)` to avoid reading `.env`.
- `tests/` uses `--import-mode=importlib`; do **not** add `__init__.py` to test dirs.

## File structure

- Create `genefoundry_router/devtools/__init__.py` — package marker.
- Create `genefoundry_router/devtools/fakes.py` — manifest models, `build_fake_tool`, `make_fake_backend` (moved from conftest), `make_backend_from_spec`, `load_manifest`.
- Create `genefoundry_router/devtools/fake_fleet.py` — `build_fleet_app`, `url_map`, `check_dev_config`, `main()` CLI.
- Create `scripts/snapshot_fleet.py` — online refresh; pure `merge_backend` helper + `main()`.
- Create `tests/fixtures/fleet_manifest.json` — committed deterministic snapshot fixture.
- Create `servers.dev.yaml` — full-mirror dev registry (localhost url_envs).
- Create `.env.dev` — localhost backend URLs (un-ignored via `!.env.dev`).
- Create `tests/e2e/conftest.py` — free-port + uvicorn-thread serve helpers + fleet fixture.
- Create `tests/e2e/test_fake_fleet_federation.py` — full-catalog (search-off) assertions.
- Create `tests/e2e/test_fake_fleet_search.py` — search + serving + call round-trip.
- Create `tests/unit/test_fakes.py`, `tests/unit/test_fleet_app.py`, `tests/unit/test_dev_config.py`, `tests/unit/test_snapshot_merge.py`, `tests/unit/test_makefile_targets.py`.
- Modify `tests/integration/conftest.py` — re-export `make_fake_backend` from `devtools.fakes`.
- Modify `.gitignore` — add `!.env.dev`.
- Modify `Makefile` — `dev-fleet`, `run-dev`, `test-e2e`, `snapshot-fleet`, `ci-full`.
- Modify `README.md` — document the local fake-fleet workflow.

---

## Task 1: Schema-exact fake tool (the §6.1 de-risk)

**Files:**
- Create: `genefoundry_router/devtools/__init__.py`
- Create: `genefoundry_router/devtools/fakes.py`
- Test: `tests/unit/test_fakes.py`

- [ ] **Step 1: Write the failing test** (proves `.parameters` override is visible over a real client and the tool is callable)

Create `tests/unit/test_fakes.py`:

```python
from fastmcp import Client, FastMCP

from genefoundry_router.devtools.fakes import build_fake_tool

GENE_SCHEMA = {
    "type": "object",
    "properties": {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol"},
        "limit": {"type": "integer", "description": "max rows"},
    },
    "required": ["gene_symbol"],
}


async def test_build_fake_tool_advertises_exact_input_schema():
    server = FastMCP("probe")
    server.add_tool(build_fake_tool("search_genes", "Search genes by symbol", GENE_SCHEMA, ["gene"]))
    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool("search_genes", {"gene_symbol": "PKD1"})
    tool = next(t for t in tools if t.name == "search_genes")
    # exact schema reproduction -> BM25 will index gene_symbol/limit + their descriptions
    assert tool.inputSchema["properties"].keys() == {"gene_symbol", "limit"}
    assert tool.inputSchema["properties"]["gene_symbol"]["description"] == "HGNC gene symbol"
    # tags surface under meta (client has no .tags) and the echo body round-trips args
    assert "gene" in (tool.meta or {}).get("fastmcp", {}).get("tags", [])
    assert result.data["args"] == {"gene_symbol": "PKD1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fakes.py::test_build_fake_tool_advertises_exact_input_schema -v`
Expected: FAIL — `ModuleNotFoundError: genefoundry_router.devtools`.

- [ ] **Step 3: Write minimal implementation**

Create `genefoundry_router/devtools/__init__.py`:

```python
"""Developer tooling: offline fake-fleet harness for local router testing."""
```

Create `genefoundry_router/devtools/fakes.py`:

```python
"""Manifest models and FastMCP fakes for the offline fleet harness."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool


def build_fake_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    tags: list[str] | None = None,
) -> Tool:
    """Build an echo tool that advertises ``input_schema`` verbatim.

    The captured JSON Schema is assigned to ``Tool.parameters`` so the gateway's
    BM25 index sees the same parameter names/descriptions as production (the search
    text is name + description + param names + param descriptions). The echo body
    accepts any args and returns them.
    """

    async def _echo(**kwargs: Any) -> dict[str, Any]:
        return {"tool": name, "args": kwargs}

    tool = Tool.from_function(_echo, name=name, description=description, tags=set(tags or []))
    tool.parameters = input_schema
    return tool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fakes.py -v`
Expected: PASS. (If the `.parameters` override is not reflected in `inputSchema`, fall back to constructing `FunctionTool(name=..., description=..., parameters=input_schema, fn=_echo, tags=...)` directly and re-run — but the override is expected to work in fastmcp 3.4.2.)

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/devtools/__init__.py genefoundry_router/devtools/fakes.py tests/unit/test_fakes.py
git commit -m "feat(devtools): schema-exact fake MCP tool builder"
```

---

## Task 2: Manifest models + spec-driven backend builder

**Files:**
- Modify: `genefoundry_router/devtools/fakes.py`
- Test: `tests/unit/test_fakes.py`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_fakes.py`)

```python
from genefoundry_router.devtools.fakes import Manifest, make_backend_from_spec


def test_manifest_parses_and_builds_backend():
    raw = {
        "snapshot_meta": {"captured_at": "2026-06-16T00:00:00Z", "source": "local",
                          "router_servers_file": "servers.yaml"},
        "backends": {
            "gnomad": {"version": "5.0.0", "tools": [
                {"name": "search_genes", "description": "Search genes", "tags": ["gene"],
                 "inputSchema": {"type": "object",
                                 "properties": {"gene_symbol": {"type": "string"}}}},
            ]},
        },
    }
    manifest = Manifest.model_validate(raw)
    assert manifest.backends["gnomad"].version == "5.0.0"
    backend = make_backend_from_spec("gnomad", manifest.backends["gnomad"])
    assert backend.name == "gnomad"


async def test_make_backend_from_spec_exposes_tools():
    from fastmcp import Client

    spec = Manifest.model_validate({
        "snapshot_meta": {"captured_at": "x", "source": "local", "router_servers_file": "s"},
        "backends": {"gtex": {"version": "1.0.0", "tools": [
            {"name": "get_gene_information", "description": "d", "tags": [],
             "inputSchema": {"type": "object", "properties": {}}},
        ]}},
    }).backends["gtex"]
    async with Client(make_backend_from_spec("gtex", spec)) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"get_gene_information"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fakes.py -v`
Expected: FAIL — `ImportError: cannot import name 'Manifest'`.

- [ ] **Step 3: Write minimal implementation** (append to `genefoundry_router/devtools/fakes.py`)

```python
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    outputSchema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class BackendSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)


class SnapshotMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    captured_at: str
    source: str
    router_servers_file: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_meta: SnapshotMeta
    backends: dict[str, BackendSpec]


def make_backend_from_spec(namespace: str, spec: BackendSpec) -> FastMCP:
    """Build a FastMCP fake for one backend from its manifest spec."""
    server = FastMCP(f"{namespace}-link")
    for tool in spec.tools:
        server.add_tool(build_fake_tool(tool.name, tool.description, tool.inputSchema, tool.tags))
    return server


def load_manifest(path: str | Path) -> Manifest:
    """Load and validate the committed fleet manifest."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest.model_validate(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fakes.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/devtools/fakes.py tests/unit/test_fakes.py
git commit -m "feat(devtools): manifest models + spec-driven fake backend builder"
```

---

## Task 3: Move `make_fake_backend` into devtools; re-export from conftest

**Files:**
- Modify: `genefoundry_router/devtools/fakes.py`
- Modify: `tests/integration/conftest.py`
- Test: existing `tests/integration/*` (regression)

- [ ] **Step 1: Add the shared builder** (append to `genefoundry_router/devtools/fakes.py`, verbatim behavior from the old conftest)

```python
def make_fake_backend(name: str, tool_names: list[str]) -> FastMCP:
    """Build a FastMCP server exposing trivial echo tools with the given names.

    Back-compat builder for the in-process integration fixtures (no schemas/tags).
    The richer, manifest-driven path is ``make_backend_from_spec``.
    """
    server = FastMCP(name)
    for tool_name in tool_names:

        def _make(tn: str):
            async def _tool(value: str = "") -> dict[str, str]:
                return {"tool": tn, "server": name, "value": value}

            _tool.__name__ = tn
            return _tool

        server.tool(name=tool_name)(_make(tool_name))
    return server
```

- [ ] **Step 2: Re-export from conftest** — replace the body of `tests/integration/conftest.py` so the fixtures use the moved builder:

```python
"""In-process FastMCP fake backends for integration tests (no network)."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP

from genefoundry_router.devtools.fakes import make_fake_backend

__all__ = ["make_fake_backend"]


@pytest.fixture
def gnomad_fake() -> FastMCP:
    return make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])


@pytest.fixture
def gtex_fake() -> FastMCP:
    return make_fake_backend("gtex-link", ["get_gene_information", "search_genes"])


@pytest.fixture
def pubtator_fake() -> FastMCP:
    return make_fake_backend(
        "pubtator-link", ["pubtator_search_literature", "pubtator_get_passages"]
    )
```

- [ ] **Step 3: Run the integration suite to verify no regression**

Run: `uv run pytest tests/integration -q`
Expected: PASS (same tests as before, now sourcing the builder from devtools).

- [ ] **Step 4: Commit**

```bash
git add genefoundry_router/devtools/fakes.py tests/integration/conftest.py
git commit -m "refactor(devtools): single source for make_fake_backend; conftest re-exports"
```

---

## Task 4: Committed manifest fixture

**Files:**
- Create: `tests/fixtures/fleet_manifest.json`
- Test: `tests/unit/test_fakes.py`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_fakes.py`)

```python
from pathlib import Path

from genefoundry_router.devtools.fakes import load_manifest

FIXTURE = Path("tests/fixtures/fleet_manifest.json")


def test_committed_manifest_is_valid_and_has_pinned_essentials():
    manifest = load_manifest(FIXTURE)
    gnomad = manifest.backends["gnomad"]
    leaves = {t.name for t in gnomad.tools}
    # pinned essentials (tool_search.DEFAULT_ALWAYS_VISIBLE) must exist as leaves
    assert {"resolve_variant_id", "search_genes"} <= leaves
    # a cross-backend leaf collision exists to prove namespacing resolves it
    gtex_leaves = {t.name for t in manifest.backends["gtex"].tools}
    assert "search_genes" in gtex_leaves
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest "tests/unit/test_fakes.py::test_committed_manifest_is_valid_and_has_pinned_essentials" -v`
Expected: FAIL — `FileNotFoundError`.

- [ ] **Step 3: Create the fixture** `tests/fixtures/fleet_manifest.json`:

```json
{
  "snapshot_meta": {
    "captured_at": "2026-06-16T00:00:00Z",
    "source": "local",
    "router_servers_file": "servers.yaml"
  },
  "backends": {
    "gnomad": {
      "version": "5.0.0",
      "tools": [
        {"name": "resolve_variant_id", "description": "Resolve a variant to a canonical gnomAD id",
         "tags": ["variant"],
         "inputSchema": {"type": "object",
           "properties": {"variant_id": {"type": "string", "description": "rsID or chr-pos-ref-alt"}},
           "required": ["variant_id"]}},
        {"name": "search_genes", "description": "Search genes by symbol",
         "tags": ["gene"],
         "inputSchema": {"type": "object",
           "properties": {"gene_symbol": {"type": "string", "description": "HGNC gene symbol"}},
           "required": ["gene_symbol"]}},
        {"name": "get_variant_details", "description": "Variant frequencies across populations",
         "tags": ["variant", "frequency"],
         "inputSchema": {"type": "object",
           "properties": {"variant_id": {"type": "string", "description": "canonical variant id"}},
           "required": ["variant_id"]}}
      ]
    },
    "gtex": {
      "version": "1.0.0",
      "tools": [
        {"name": "get_gene_information", "description": "Gene metadata from GTEx",
         "tags": ["expression"],
         "inputSchema": {"type": "object",
           "properties": {"gene_id": {"type": "string", "description": "symbol or GENCODE id"}},
           "required": ["gene_id"]}},
        {"name": "search_genes", "description": "Search GTEx genes",
         "tags": ["gene"],
         "inputSchema": {"type": "object",
           "properties": {"query": {"type": "string", "description": "free-text gene query"}},
           "required": ["query"]}}
      ]
    },
    "pubtator": {
      "version": "2.0.0",
      "tools": [
        {"name": "search_literature", "description": "Search biomedical literature",
         "tags": ["literature"],
         "inputSchema": {"type": "object",
           "properties": {"query": {"type": "string", "description": "literature search query"}},
           "required": ["query"]}},
        {"name": "get_passages", "description": "Fetch passages for a PMID",
         "tags": ["literature"],
         "inputSchema": {"type": "object",
           "properties": {"pmid": {"type": "string", "description": "PubMed id"}},
           "required": ["pmid"]}}
      ]
    }
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest "tests/unit/test_fakes.py::test_committed_manifest_is_valid_and_has_pinned_essentials" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/fleet_manifest.json tests/unit/test_fakes.py
git commit -m "test(devtools): committed fake-fleet manifest fixture"
```

---

## Task 5: Multi-mount fleet app with composed lifespan

**Files:**
- Create: `genefoundry_router/devtools/fake_fleet.py`
- Test: `tests/unit/test_fleet_app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_fleet_app.py`:

```python
from starlette.routing import Mount

from genefoundry_router.devtools.fakes import load_manifest
from genefoundry_router.devtools.fake_fleet import build_fleet_app, url_map


def test_fleet_app_mounts_every_backend_path():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    app = build_fleet_app(manifest)
    mounts = {r.path for r in app.routes if isinstance(r, Mount)}
    assert mounts == {"/gnomad", "/gtex", "/pubtator"}


async def test_fleet_app_lifespan_enters_all_children():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    app = build_fleet_app(manifest)
    # entering the composed lifespan must initialize every child session manager
    async with app.router.lifespan_context(app):
        pass  # no exception == all child lifespans entered+exited cleanly


def test_url_map_is_localhost_paths():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    urls = url_map(manifest, "127.0.0.1", 9100)
    assert urls["gnomad"] == "http://127.0.0.1:9100/gnomad/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fleet_app.py -v`
Expected: FAIL — `ModuleNotFoundError: ...fake_fleet`.

- [ ] **Step 3: Write minimal implementation**

Create `genefoundry_router/devtools/fake_fleet.py`:

```python
"""Offline multi-backend fake fleet: one Starlette app, path-routed MCP mounts."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.routing import Mount

from genefoundry_router.devtools.fakes import Manifest, make_backend_from_spec


def url_map(manifest: Manifest, host: str, port: int) -> dict[str, str]:
    """Map each namespace to its localhost MCP URL."""
    return {ns: f"http://{host}:{port}/{ns}/mcp" for ns in manifest.backends}


def build_fleet_app(manifest: Manifest) -> Starlette:
    """Mount one fake FastMCP per backend at /<ns>/mcp on a single Starlette app.

    Each child ``http_app`` has its own lifespan; FastMCP requires it to be entered
    or the session manager never initializes. The outer lifespan enters every child
    via an AsyncExitStack.
    """
    children = {
        ns: make_backend_from_spec(ns, spec).http_app(path="/mcp")
        for ns, spec in manifest.backends.items()
    }

    @asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        async with AsyncExitStack() as stack:
            for child in children.values():
                await stack.enter_async_context(child.lifespan(app))
            yield

    routes = [Mount(f"/{ns}", app=child) for ns, child in children.items()]
    return Starlette(routes=routes, lifespan=lifespan)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fleet_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/devtools/fake_fleet.py tests/unit/test_fleet_app.py
git commit -m "feat(devtools): multi-mount fleet app with composed lifespan"
```

---

## Task 6: Dev registry config (`servers.dev.yaml`, `.env.dev`) + consistency check

**Files:**
- Create: `servers.dev.yaml`
- Create: `.env.dev`
- Modify: `.gitignore`
- Modify: `genefoundry_router/devtools/fake_fleet.py`
- Test: `tests/unit/test_dev_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dev_config.py`:

```python
from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fake_fleet import check_dev_config
from genefoundry_router.devtools.fakes import load_manifest


def _dev_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in open(".env.dev", encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def test_dev_registry_resolves_to_localhost():
    registry = load_registry("servers.dev.yaml", _dev_env())
    gnomad = next(b for b in registry if b.namespace == "gnomad")
    assert gnomad.url == "http://127.0.0.1:9100/gnomad/mcp"


def test_check_dev_config_passes_for_matching_manifest():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    registry = load_registry("servers.dev.yaml", _dev_env())
    enabled = [b for b in registry if b.enabled and b.namespace in manifest.backends]
    assert check_dev_config(enabled, manifest, "127.0.0.1", 9100) == []


def test_check_dev_config_reports_mismatch():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    registry = load_registry("servers.dev.yaml", _dev_env())
    enabled = [b for b in registry if b.enabled and b.namespace in manifest.backends]
    problems = check_dev_config(enabled, manifest, "127.0.0.1", 9999)  # wrong port
    assert problems  # at least one URL mismatch reported
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dev_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_dev_config'` (and missing files).

- [ ] **Step 3: Create the config files and the checker.**

Create `.env.dev` (localhost; safe to commit — no secrets):

```bash
# Local fake-fleet dev env. Backend URLs point at `make dev-fleet` (port 9100).
GF_HOST=127.0.0.1
GF_PORT=8000
GF_AUTH_MODE=none
GF_GNOMAD_URL=http://127.0.0.1:9100/gnomad/mcp
GF_GTEX_URL=http://127.0.0.1:9100/gtex/mcp
GF_PUBTATOR_URL=http://127.0.0.1:9100/pubtator/mcp
```

Create `servers.dev.yaml` (only the namespaces present in the committed manifest, so
`build_server` mounts exactly what the fleet serves):

```yaml
# servers.dev.yaml — dev registry for the offline fake fleet (URLs from .env.dev)
defaults:
  transport: http
  enabled: true
  cache_ttl: 300
  tags: []
servers:
  - { name: gnomad,   url_env: GF_GNOMAD_URL,   namespace: gnomad,   tags: [variant, gene, frequency] }
  - { name: gtex,     url_env: GF_GTEX_URL,     namespace: gtex,     tags: [expression, tissue] }
  - { name: pubtator, url_env: GF_PUBTATOR_URL, namespace: pubtator, tags: [literature, entity] }
```

Add to `.gitignore` immediately after the existing `!.env.docker.example` line:

```
!.env.dev
```

Append to `genefoundry_router/devtools/fake_fleet.py`:

```python
from genefoundry_router.registry import BackendDef


def check_dev_config(
    registry: list[BackendDef],
    manifest: Manifest,
    host: str,
    port: int,
) -> list[str]:
    """Return human-readable mismatches between the registry URLs and the fleet URLs."""
    expected = url_map(manifest, host, port)
    problems: list[str] = []
    for backend in registry:
        want = expected.get(backend.namespace)
        if want is None:
            problems.append(f"{backend.namespace}: not served by the fleet manifest")
        elif backend.url != want:
            problems.append(f"{backend.namespace}: url {backend.url!r} != expected {want!r}")
    return problems
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_dev_config.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add servers.dev.yaml .env.dev .gitignore genefoundry_router/devtools/fake_fleet.py tests/unit/test_dev_config.py
git commit -m "feat(devtools): dev registry config + URL consistency check"
```

---

## Task 7: Fleet CLI entrypoint (`python -m ... fake_fleet`)

**Files:**
- Modify: `genefoundry_router/devtools/fake_fleet.py`
- Test: `tests/unit/test_fleet_app.py`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_fleet_app.py`)

```python
from genefoundry_router.devtools.fake_fleet import build_parser


def test_cli_parser_defaults():
    args = build_parser().parse_args([])
    assert args.port == 9100
    assert args.manifest == "tests/fixtures/fleet_manifest.json"


def test_cli_parser_overrides():
    args = build_parser().parse_args(["--port", "9200", "--manifest", "x.json"])
    assert args.port == 9200 and args.manifest == "x.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_fleet_app.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 3: Write minimal implementation** (append to `genefoundry_router/devtools/fake_fleet.py`)

```python
import argparse

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline fake MCP fleet.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--manifest", default="tests/fixtures/fleet_manifest.json")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = load_manifest(args.manifest)
    for ns, url in url_map(manifest, args.host, args.port).items():
        print(f"  {ns:<10} -> {url}")
    app = build_fleet_app(manifest)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_fleet_app.py -v`
Expected: PASS.

- [ ] **Step 5: Smoke the runnable module manually (optional but recommended)**

Run: `timeout 3 uv run python -m genefoundry_router.devtools.fake_fleet || true`
Expected: prints the `gnomad/gtex/pubtator -> http://127.0.0.1:9100/...` map, then serves until the timeout.

- [ ] **Step 6: Commit**

```bash
git add genefoundry_router/devtools/fake_fleet.py tests/unit/test_fleet_app.py
git commit -m "feat(devtools): fake_fleet CLI entrypoint"
```

---

## Task 8: E2E harness — serve helpers + full-catalog federation (search off)

**Files:**
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_fake_fleet_federation.py`
- Test: itself

- [ ] **Step 1: Write the serve helpers + fixture** in `tests/e2e/conftest.py`:

```python
"""E2E fixtures: serve ASGI apps over real HTTP in a background thread."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from genefoundry_router.devtools.fake_fleet import build_fleet_app
from genefoundry_router.devtools.fakes import Manifest, load_manifest
from genefoundry_router.registry import BackendDef

FIXTURE = "tests/fixtures/fleet_manifest.json"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve(app: object) -> tuple[uvicorn.Server, str]:
    """Serve an ASGI app on a free port in a daemon thread; return (server, base_url)."""
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start in time")
        time.sleep(0.02)
    return server, f"http://127.0.0.1:{port}"


def dev_registry(manifest: Manifest, base_url: str) -> list[BackendDef]:
    """Build a registry whose backends point at the served fleet."""
    return [
        BackendDef(
            name=ns,
            namespace=ns,
            url_env=f"GF_{ns.upper()}_URL",
            tags=[],
            url=f"{base_url}/{ns}/mcp",
        )
        for ns in manifest.backends
    ]


@pytest.fixture(scope="session")
def fleet() -> Iterator[tuple[Manifest, str]]:
    manifest = load_manifest(FIXTURE)
    server, base_url = serve(build_fleet_app(manifest))
    try:
        yield manifest, base_url
    finally:
        server.should_exit = True
        time.sleep(0.2)
```

- [ ] **Step 2: Write the failing federation test** in `tests/e2e/test_fake_fleet_federation.py`:

```python
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.server import build_server

from .conftest import dev_registry


async def test_full_catalog_matches_manifest_projection(fleet):
    manifest, base_url = fleet
    settings = RouterSettings(_env_file=None)
    registry = dev_registry(manifest, base_url)
    # search OFF so the raw federated catalog is listable (the gateway->fake hop is real HTTP)
    server = build_server(settings, registry, enable_search=False)
    async with Client(server) as client:
        names = [t.name for t in await client.list_tools()]

    expected = {
        f"{ns}_{tool.name}"
        for ns, spec in manifest.backends.items()
        for tool in spec.tools
    }
    assert set(names) == expected            # no transform: names match v1 exactly
    assert len(names) == len(set(names))     # no collisions after namespacing
    assert "gnomad_search_genes" in names and "gtex_search_genes" in names  # collision resolved
```

- [ ] **Step 3: Run test to verify it fails first, then passes**

Run: `uv run pytest tests/e2e/test_fake_fleet_federation.py -v`
Expected: PASS once the fixture + helpers exist. (If it errors before the assertion, that is the genuine red — the harness wiring — fix and re-run until the assertion itself is what passes.)

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/conftest.py tests/e2e/test_fake_fleet_federation.py
git commit -m "test(e2e): full-catalog federation over real HTTP (search off)"
```

---

## Task 9: E2E — search + serving + call round-trip (production build)

**Files:**
- Create: `tests/e2e/test_fake_fleet_search.py`
- Test: itself

- [ ] **Step 1: Write the failing test** in `tests/e2e/test_fake_fleet_search.py`:

```python
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.server import build_app

from .conftest import dev_registry, serve


async def test_search_and_call_over_the_wire(fleet):
    manifest, base_url = fleet
    settings = RouterSettings(_env_file=None)
    app = build_app(settings, dev_registry(manifest, base_url))  # search ON via lifespan
    server, router_url = serve(app)  # client -> gateway -> fake, all on the wire
    try:
        async with Client(f"{router_url}/mcp") as client:
            listed = {t.name for t in await client.list_tools()}
            # pinned essentials are always visible despite search hiding the bulk catalog
            assert {"gnomad_resolve_variant_id", "gnomad_search_genes"} <= listed
            assert "search_tools" in listed and "call_tool" in listed

            # BM25 indexes description + parameter text (NOT tags): query param/desc words
            hits = await client.call_tool("search_tools", {"query": "literature query pmid"})
            found = " ".join(str(hits.data)).lower()
            assert "pubtator_search_literature" in found or "pubtator_get_passages" in found

            # end-to-end invocation round-trips through the gateway to the fake
            result = await client.call_tool(
                "call_tool",
                {"name": "gnomad_search_genes", "arguments": {"gene_symbol": "PKD1"}},
            )
            assert "PKD1" in str(result.data)
    finally:
        server.should_exit = True
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/e2e/test_fake_fleet_search.py -v`
Expected: PASS. Notes if it fails:
- If `call_tool` argument shape differs in fastmcp 3.4.2, introspect the synthetic `call_tool` schema with a quick `list_tools()`/`inputSchema` dump and adjust the argument keys (`name`/`arguments`) to match — the test is the contract; adapt the call, never weaken the assertion.
- If `search_tools` result shape differs, assert against the actual returned structure (the names must appear somewhere in the payload).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_fake_fleet_search.py
git commit -m "test(e2e): search discovery + call round-trip over real HTTP"
```

---

## Task 10: Snapshot refresh script

**Files:**
- Create: `scripts/snapshot_fleet.py`
- Test: `tests/unit/test_snapshot_merge.py`

- [ ] **Step 1: Write the failing test** in `tests/unit/test_snapshot_merge.py`:

```python
from genefoundry_router.devtools.fakes import BackendSpec, ToolSpec
from scripts.snapshot_fleet import merge_backend


def test_merge_keeps_prior_when_new_is_none():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    assert merge_backend(prior, None) is prior


def test_merge_prefers_new_when_present():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    fresh = BackendSpec(version="2.0.0", tools=[ToolSpec(name="get_y")])
    merged = merge_backend(prior, fresh)
    assert merged.version == "2.0.0"
    assert [t.name for t in merged.tools] == ["get_y"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_snapshot_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: scripts.snapshot_fleet`.

- [ ] **Step 3: Write minimal implementation** in `scripts/snapshot_fleet.py`:

```python
"""Refresh tests/fixtures/fleet_manifest.json from live (or local) backends.

Online, on-demand only — never run in tests. Per-backend resilient: an unreachable
backend keeps its prior manifest entry instead of being clobbered.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fakes import (
    BackendSpec,
    Manifest,
    SnapshotMeta,
    ToolSpec,
    load_manifest,
)


def merge_backend(prior: BackendSpec | None, fresh: BackendSpec | None) -> BackendSpec | None:
    """Prefer a fresh snapshot; fall back to the prior entry when unreachable."""
    return fresh if fresh is not None else prior


async def _snapshot_backend(url: str) -> BackendSpec | None:
    from fastmcp import Client

    try:
        async with Client(url) as client:
            tools = await client.list_tools()
            version = None
            init = getattr(client, "initialize_result", None)
            if init is not None and getattr(init, "serverInfo", None) is not None:
                version = init.serverInfo.version  # MCP initialize handshake
        specs = [
            ToolSpec(
                name=t.name,
                description=t.description or "",
                inputSchema=t.inputSchema or {"type": "object", "properties": {}},
                tags=list((t.meta or {}).get("fastmcp", {}).get("tags", [])),
            )
            for t in tools
        ]
        return BackendSpec(version=version, tools=specs)
    except Exception as exc:  # noqa: BLE001 - report + keep prior
        print(f"  WARN unreachable: {url} ({exc})")
        return None


async def _run(servers_file: str, out: Path, captured_at: str) -> None:
    prior = load_manifest(out) if out.exists() else None
    registry = [b for b in load_registry(servers_file, os.environ) if b.enabled and b.url]
    backends: dict[str, BackendSpec] = {}
    for b in registry:
        assert b.url is not None
        fresh = await _snapshot_backend(b.url)
        prior_spec = prior.backends.get(b.namespace) if prior else None
        merged = merge_backend(prior_spec, fresh)
        if merged is not None:
            backends[b.namespace] = merged
    manifest = Manifest(
        snapshot_meta=SnapshotMeta(
            captured_at=captured_at, source="live", router_servers_file=servers_file
        ),
        backends=backends,
    )
    out.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(backends)} backends)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the fake-fleet manifest.")
    parser.add_argument("--servers-file", default="servers.yaml")
    parser.add_argument("--out", default="tests/fixtures/fleet_manifest.json")
    parser.add_argument("--captured-at", required=True, help="ISO timestamp (date -u +%FT%TZ)")
    args = parser.parse_args()
    asyncio.run(_run(args.servers_file, Path(args.out), args.captured_at))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_snapshot_merge.py -v`
Expected: PASS.

Note (online-only): the `serverInfo.version` attribute path is exercised only when you run
`make snapshot-fleet` against real backends. If a live run shows a different attribute name,
adjust `_snapshot_backend` — this code is off the offline test path.

- [ ] **Step 5: Commit**

```bash
git add scripts/snapshot_fleet.py tests/unit/test_snapshot_merge.py
git commit -m "feat(devtools): on-demand fleet snapshot refresh script"
```

---

## Task 11: Make targets + targets test

**Files:**
- Modify: `Makefile`
- Test: `tests/unit/test_makefile_targets.py`

- [ ] **Step 1: Write the failing test** in `tests/unit/test_makefile_targets.py`:

```python
from pathlib import Path


def test_makefile_has_fake_fleet_targets():
    text = Path("Makefile").read_text(encoding="utf-8")
    for target in ("dev-fleet:", "run-dev:", "test-e2e:", "snapshot-fleet:", "ci-full:"):
        assert target in text, f"missing Makefile target: {target}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_makefile_targets.py -v`
Expected: FAIL — targets absent.

- [ ] **Step 3: Add the targets.** Append to `.PHONY` line the new names, and add at the end of `Makefile`:

```makefile
dev-fleet: ## Run the offline fake MCP fleet (port 9100)
	uv run python -m genefoundry_router.devtools.fake_fleet

run-dev: ## Run the router against the fake fleet (exports .env.dev)
	set -a; . ./.env.dev; set +a; uv run genefoundry-router run --servers-file servers.dev.yaml

test-e2e: ## Run the offline end-to-end fake-fleet tests
	uv run pytest tests/e2e -q

snapshot-fleet: ## Refresh the fleet manifest from live backends (online)
	uv run python scripts/snapshot_fleet.py --captured-at $$(date -u +%FT%TZ)

ci-full: ci-local test-e2e ## Fast CI plus the offline e2e suite
```

Also update the `.PHONY:` line (line 1) to include: `dev-fleet run-dev test-e2e snapshot-fleet ci-full`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_makefile_targets.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Makefile tests/unit/test_makefile_targets.py
git commit -m "build: dev-fleet/run-dev/test-e2e/snapshot-fleet/ci-full make targets"
```

---

## Task 12: README + final gate

**Files:**
- Modify: `README.md`
- Test: full suite

- [ ] **Step 1: Document the workflow.** Add a "Local testing (offline fake fleet)" section to `README.md`:

```markdown
## Local testing (offline fake fleet)

Run the real router against impersonated backends over real Streamable-HTTP — no Docker, no network:

```bash
make dev-fleet   # terminal 1: fakes on :9100 (driven by tests/fixtures/fleet_manifest.json)
make run-dev     # terminal 2: router on :8000 against the fakes (exports .env.dev)
make test-e2e    # one-shot: boot fleet in-process, assert federation, tear down
```

Refresh the manifest from the live fleet when tool surfaces change (online):

```bash
make snapshot-fleet
```
```

- [ ] **Step 2: Run the offline e2e suite**

Run: `make test-e2e`
Expected: all `tests/e2e` tests PASS.

- [ ] **Step 3: Run the full local gate**

Run: `make ci-local`
Expected: format-check, lint, lint-loc, mypy, unit + integration all green. Fix any ruff/mypy issues (e.g., add `# noqa: S104`/`# noqa: S106` only for genuine test-data false positives, matching the existing pattern in `tests/unit/test_cli.py`).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the offline fake-fleet local-testing workflow"
```

- [ ] **Step 5: Final verification**

Run: `make ci-full`
Expected: `ci-local` + `test-e2e` both green. The fake-fleet harness is complete.

---

## Self-Review

**Spec coverage** (each spec section → task):
- §3 record/replay manifest → Tasks 2, 4, 10. §3.1 validated facts: schema override (T1), lifespan AsyncExitStack (T5), search-off full catalog (T8), search indexes description/params not tags (T1, T9), env export not `--env-file` (T11), `.env.dev` gitignore negation (T6). ✓
- §4 components: fake_fleet (T5, T7), fakes/shared builder (T1–T3), manifest fixture (T4), snapshot script (T10), servers.dev.yaml/.env.dev + check (T6), make targets (T11). ✓
- §6 manifest schema incl inputSchema/outputSchema/annotations/tags → T2 models + T4 fixture. §6.1 reconstruction via `.parameters` → T1. ✓
- §7 run ergonomics → T11 targets + T12 README. ✓
- §8 two-layer e2e (full-catalog search-off; search+serving over HTTP) → T8, T9. Pinned essentials present → T9. ✓
- §9 error handling: missing/invalid manifest (pydantic in `load_manifest`, T2), config mismatch hard-fail in e2e (T6 checker; e2e fixture builds registry from manifest so paths always match; `check_dev_config` available for `dev-fleet` warn / `run-dev`), unreachable snapshot keeps prior (T10). ✓
- §10 decisions → reflected across tasks. §11 future extensions → out of scope (no tasks, correct). §12 boundary → fakes echo synthetic data only (T1). ✓

**Placeholder scan:** no TBD/TODO; every code step shows complete code; commands have expected output. ✓

**Type consistency:** `Manifest`/`BackendSpec`/`ToolSpec`/`SnapshotMeta` defined in T2 and used identically in T5/T6/T8/T9/T10. `build_fake_tool`, `make_backend_from_spec`, `load_manifest`, `build_fleet_app`, `url_map`, `check_dev_config`, `merge_backend`, `dev_registry`, `serve` signatures are consistent across all references. `make_fake_backend(name, tool_names)` keeps its original signature (T3) so existing integration tests are unaffected. ✓

**Note on `serverInfo.version` (T10):** online-only; the plan flags verifying the attribute path against a live backend. Not on the offline test path, so it cannot break `make ci-full`.
