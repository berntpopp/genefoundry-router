# GeneFoundry Logging & CLI Standard v1 + gnomad-link 3.0.0 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the GeneFoundry Logging & CLI Standard v1 and bring `gnomad-link` into compliance as the exemplar — `typer` CLI (`serve` command), `structlog` logging, HTTP-only (stdio removed), version `3.0.0`.

**Architecture:** Two repos. The **standard doc** lands in `genefoundry-router/docs/` (the standards hub, next to `TOOL-NAMING-STANDARD-v1.md`). The **code work** is entirely in `../gnomad-link`: replace `logging_config.py` internals with the fleet structlog canon (copied from `mgi-link`/`uniprot-link`), replace the argparse `cli.py` with a `typer` app, delete the root `server.py`/`mcp_server.py` entry scripts + the dead `transports/` package, and strip stdio. The MCP facade, services, GraphQL, `/health`, `/mcp` are untouched — so the router (which talks HTTP to `/mcp`) is unaffected.

**Tech Stack:** Python 3.12+, `uv`+hatchling, `typer`, `rich`, `structlog`, `asgi-correlation-id`, `pydantic-settings`, `pytest` (+ `typer.testing.CliRunner`), ruff + mypy. Pre-alpha: breaking changes OK, no shims/aliases.

---

## Working directories

- **Standard doc:** `genefoundry-router/docs/` (run from the `genefoundry-router` repo).
- **All code tasks:** `../gnomad-link` (a sibling repo). Run `make`/`uv`/`git` **inside `gnomad-link`**, e.g. `cd ../gnomad-link && make ci-local`. Each gnomad-link task commits in the gnomad-link repo.

## File map (gnomad-link unless noted)

| File | Change |
|------|--------|
| `genefoundry-router/docs/GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md` | **Create** — the standard (Task 1) |
| `gnomad_link/logging_config.py` | **Rewrite** — stdlib → structlog canon |
| `gnomad_link/config.py` | **Modify** — add `LOG_FORMAT`; drop `stdio` from transport Literals |
| `gnomad_link/__init__.py` | **Modify** — `__version__ = "3.0.0"` |
| `gnomad_link/server_manager.py` | **Modify** — new logging calls; delete `start_stdio_server` + stdio dispatch |
| `gnomad_link/transports/` | **Delete** — dead package (`base.py`, `factory.py`, `__init__.py`) |
| `gnomad_link/cli.py` | **Rewrite** — argparse → typer app (`serve`/`config`/`health`/`cache`/`version`) |
| `server.py`, `mcp_server.py` (repo root) | **Delete** — entry scripts gone |
| `pyproject.toml` | **Modify** — scripts → `gnomad_link.cli:app`; drop hatch `include`; `version="3.0.0"`; drop `gnomad-link-mcp` |
| `docker/Dockerfile`, `docker/docker-compose*.yml` | **Modify** — `CMD`/command → `gnomad-link serve …`; drop stdio env |
| `Makefile` | **Modify** — `dev`/`run-prod` → `gnomad-link serve …`; drop `mcp-serve`/`run-mcp`; fix file lists |
| `README.md`, `CHANGELOG.md` | **Modify** — serve usage; 3.0.0 breaking note; remove stdio |
| `tests/conftest.py` | **Modify** — new logging imports |
| `tests/unit/test_cli.py`, `tests/unit/test_cli_cache_commands.py` | **Rewrite** — typer `CliRunner` |

---

# Phase 0 — Publish the Standard

### Task 1: Write `GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md`

**Files:**
- Create: `genefoundry-router/docs/GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md`

- [ ] **Step 1: Write the standard doc** (run from the `genefoundry-router` repo)

```markdown
# GeneFoundry Logging & CLI Standard v1

> Canonical reference for the GeneFoundry `-link` MCP fleet. Adopted 2026-06-13.
> Sibling to `TOOL-NAMING-STANDARD-v1.md`. A tracking issue "Adopt GeneFoundry
> Logging & CLI Standard v1" exists in each non-compliant repo.

All `*-link` servers share one CLI framework and one logging setup so the fleet is
uniform to operate and develop. Derived from the current-generation repos
(`mgi-link`, `uniprot-link`, `stringdb-link`, `litvar-link`).

## Rules

1. **CLI = `typer`.** One app per package: `app = typer.Typer(name="<name>",
   help="…", add_completion=False, no_args_is_help=True)`. Human output via `rich`.
2. **Single entry point:** `[project.scripts] <name> = "<pkg>.cli:app"`. No root-level
   `server.py`/`mcp_server.py` scripts; no per-transport console scripts.
3. **Canonical commands** (include those that apply): `serve` (start the server),
   `config [--validate]`, `health [--url]`, `cache stats|clear`, `version`.
   Server start is **always** `<name> serve …` — no bare-serve.
4. **`serve` options:** `--transport {unified,http}` (default `unified`), `--host`,
   `--port`, `--mcp-path`, `--log-level`, `--disable-docs`, `--dev`.
5. **Logging = `structlog`**, configured in `<pkg>/logging_config.py`. Canonical
   processor chain: `merge_contextvars` (or stdlib equivalent) → `add_log_level` →
   `TimeStamper(fmt="iso")` → `StackInfoRenderer` → exc info → a static-fields
   processor adding `service` + `version`. Format branch: **JSON in prod**
   (`dict_tracebacks` + `JSONRenderer`), **`ConsoleRenderer` in dev**. Bind the
   active `asgi-correlation-id` into every event.
6. **Transport = Streamable HTTP only.** No stdio anywhere (config, server manager,
   docker, docs). MCP at `/mcp`, health at `/health`.
7. **Breaking changes ship immediately** (fleet is pre-alpha): no shims, no aliases,
   no deprecation windows. MAJOR version bump + one-line `CHANGELOG` note.

## Definition of Done (per repo)
- [ ] `typer` app at `<pkg>.cli:app`; `serve`/`config`/`health`/`cache`/`version` as applicable
- [ ] Single console script; no root entry scripts; no stdio entry/transport
- [ ] `<pkg>/logging_config.py` with the canonical structlog chain (JSON prod / console dev) + correlation-id
- [ ] Docker `CMD`/Makefile/README use `<name> serve …`
- [ ] CLI + logging covered by `CliRunner` tests; coverage floor held; ruff + mypy clean
- [ ] MAJOR version bump + `CHANGELOG` note

## References
- FastMCP / fleet repos: `stringdb-link`, `litvar-link`, `mgi-link`, `uniprot-link`
- `TOOL-NAMING-STANDARD-v1.md` (companion standard)
```

- [ ] **Step 2: Commit** (in the `genefoundry-router` repo)

```bash
git add docs/GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md
git commit -m "docs: GeneFoundry Logging & CLI Standard v1"
```

---

# Phase 1 — gnomad-link: structlog logging + stdio-free server layer

> All tasks run in `../gnomad-link`.

### Task 2: Add `LOG_FORMAT` setting + bump version

**Files:**
- Modify: `gnomad_link/config.py`
- Modify: `gnomad_link/__init__.py`
- Test: `tests/unit/test_log_format_setting.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_log_format_setting.py`:
```python
from gnomad_link.config import Settings


def test_log_format_defaults_to_json():
    s = Settings(_env_file=None)
    assert s.LOG_FORMAT == "json"


def test_log_format_accepts_console(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "console")
    s = Settings(_env_file=None)
    assert s.LOG_FORMAT == "console"
```

- [ ] **Step 2: Run it (FAIL — no LOG_FORMAT)**

Run: `cd ../gnomad-link && uv run pytest tests/unit/test_log_format_setting.py -v`

- [ ] **Step 3: Add `LOG_FORMAT` to `Settings`** (in `gnomad_link/config.py`, in the Logging block)

```python
    # Logging Configuration
    LOG_LEVEL: str = "INFO"
    MCP_LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"
```
(Delete the now-unused `STDIO_LOG_LEVEL` line.)

- [ ] **Step 4: Bump version** in `gnomad_link/__init__.py`

```python
__version__ = "3.0.0"
```

- [ ] **Step 5: Run test (PASS) + commit**

```bash
cd ../gnomad-link
uv run pytest tests/unit/test_log_format_setting.py -v
git add gnomad_link/config.py gnomad_link/__init__.py tests/unit/test_log_format_setting.py
git commit -m "feat: add LOG_FORMAT setting; bump to 3.0.0"
```

---

### Task 3: Rewrite `logging_config.py` to the structlog canon

**Files:**
- Rewrite: `gnomad_link/logging_config.py`
- Test: `tests/unit/test_logging_config.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_logging_config.py`:
```python
import json

import structlog

from gnomad_link.logging_config import configure_logging


def test_json_logging_emits_structured_event(capsys, monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging(level="INFO")
    structlog.get_logger("gnomad_test").info("hello", widget="x")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(out)
    assert record["event"] == "hello"
    assert record["widget"] == "x"
    assert record["level"] == "info"
    assert record["service"] == "gnomad-link"
    assert "timestamp" in record and "version" in record


def test_correlation_id_is_bound(capsys, monkeypatch):
    from asgi_correlation_id.context import correlation_id

    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging(level="INFO")
    token = correlation_id.set("abc-123")
    try:
        structlog.get_logger("gnomad_test").info("with-cid")
    finally:
        correlation_id.reset(token)
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["correlation_id"] == "abc-123"
```

- [ ] **Step 2: Run it (FAIL — new API / structlog not configured)**

Run: `cd ../gnomad-link && uv run pytest tests/unit/test_logging_config.py -v`

- [ ] **Step 3: Replace `gnomad_link/logging_config.py`** (canon copied from mgi-link/uniprot-link + correlation-id)

```python
"""Structured logging for gnomAD Link (GeneFoundry Logging & CLI Standard v1)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any

import structlog
from asgi_correlation_id.context import correlation_id

from . import __version__
from .config import settings

if TYPE_CHECKING:
    from structlog.typing import FilteringBoundLogger


def _add_static_fields(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.setdefault("service", "gnomad-link")
    event_dict.setdefault("version", __version__)
    return event_dict


def _bind_correlation_id(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    cid = correlation_id.get()
    if cid is not None:
        event_dict["correlation_id"] = cid
    return event_dict


def _configure_stdlib(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper()))
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(getattr(logging, level.upper()))
    root.addHandler(handler)
    is_debug = level.upper() == "DEBUG"
    for name, lvl in {
        "httpx": "WARNING", "httpcore": "WARNING",
        "uvicorn.access": "INFO" if is_debug else "WARNING",
        "uvicorn.error": "INFO",
        "fastmcp": "INFO" if is_debug else "WARNING",
        "mcp": "INFO" if is_debug else "WARNING",
    }.items():
        logging.getLogger(name).setLevel(getattr(logging, lvl))


def configure_logging(level: str | None = None) -> FilteringBoundLogger:
    """Configure stdlib + structlog. ``level`` overrides ``settings.LOG_LEVEL``."""
    level = level or settings.LOG_LEVEL
    _configure_stdlib(level)
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _bind_correlation_id,
        _add_static_fields,
    ]
    if settings.LOG_FORMAT == "json":
        processors = [*shared, structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        processors = [*shared, structlog.dev.ConsoleRenderer(colors=level.upper() == "DEBUG")]
    structlog.configure(
        processors=processors,  # type: ignore[arg-type]
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger("gnomad_link")  # type: ignore[no-any-return]
```

> The old `TransportAwareFormatter`, `get_transport_logger`, `get_server_logger`, `get_mcp_logger`, `get_api_logger` are **removed**. Call sites (Task 4) switch to `structlog.get_logger(__name__)`.

- [ ] **Step 4: Run test (PASS) + commit**

```bash
cd ../gnomad-link
uv run pytest tests/unit/test_logging_config.py -v
git add gnomad_link/logging_config.py tests/unit/test_logging_config.py
git commit -m "feat: structlog logging config (Logging & CLI Standard v1)"
```

---

### Task 4: Update logging call sites; delete dead `transports/`; strip stdio from the server layer

**Files:**
- Modify: `gnomad_link/server_manager.py`
- Modify: `gnomad_link/config.py`
- Modify: `tests/conftest.py`
- Delete: `gnomad_link/transports/` (whole package)

- [ ] **Step 1: Update `server_manager.py`** — imports + logging + remove stdio

Replace the import (line ~19):
```python
import structlog
```
(remove `from gnomad_link.logging_config import configure_logging, get_server_logger`; keep a direct import where needed):
```python
from gnomad_link.logging_config import configure_logging
```
In `start_unified_server` (was ~122–123):
```python
            configure_logging(config.log_level)
            self.logger = structlog.get_logger("gnomad_server")
```
Delete the entire `start_stdio_server` method (was ~153–163) and the stdio dispatch branch in `start_server`:
```python
    async def start_server(self, config: ServerConfig) -> None:
        if config.transport in ("unified", "http"):
            await self.start_unified_server(config)
        else:
            raise StartupError(f"Unsupported transport: {config.transport}", config.transport)
```

- [ ] **Step 2: Drop `stdio` from the transport Literals** in `gnomad_link/config.py`

```python
    transport: Literal["unified", "http"] = "unified"
```
and
```python
    MCP_TRANSPORT: Literal["unified", "http"] = "unified"
```

- [ ] **Step 3: Delete the dead `transports/` package**

```bash
cd ../gnomad-link && git rm -r gnomad_link/transports
```
(Confirmed unused: nothing imports it except a docstring mention.)

- [ ] **Step 4: Fix `tests/conftest.py`** (line ~11)

Replace:
```python
from gnomad_link.logging_config import configure_logging, get_server_logger
```
with:
```python
import structlog

from gnomad_link.logging_config import configure_logging
```
and replace any `manager.logger = get_server_logger("http")` with:
```python
    manager.logger = structlog.get_logger("gnomad_server")
```

- [ ] **Step 5: Run the suite (expect mostly green; CLI tests still fail — fixed in Phase 2)**

Run: `cd ../gnomad-link && uv run pytest tests/unit -q -k "not cli"`
Expected: PASS (server_manager, mcp, services unaffected; stdio path gone).

- [ ] **Step 6: Commit**

```bash
cd ../gnomad-link
git add gnomad_link/server_manager.py gnomad_link/config.py tests/conftest.py
git commit -m "refactor: structlog call sites; remove stdio + dead transports package"
```

---

# Phase 2 — gnomad-link: typer CLI + entry points + packaging/ops

### Task 5: Rewrite `cli.py` as a typer app

**Files:**
- Rewrite: `gnomad_link/cli.py`
- Test: `tests/unit/test_cli.py` (rewrite)

- [ ] **Step 1: Rewrite `tests/unit/test_cli.py`** for typer `CliRunner`

```python
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from gnomad_link.cli import app

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "3.0.0" in result.output


def test_bare_invocation_shows_help():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "serve" in result.output


def test_serve_builds_config_and_starts(monkeypatch):
    captured = {}

    class FakeManager:
        async def start_server(self, config):
            captured["transport"] = config.transport
            captured["port"] = config.port

    monkeypatch.setattr("gnomad_link.cli.UnifiedServerManager", FakeManager)
    result = runner.invoke(app, ["serve", "--transport", "unified", "--port", "8123"])
    assert result.exit_code == 0, result.output
    assert captured == {"transport": "unified", "port": 8123}


def test_serve_rejects_stdio():
    result = runner.invoke(app, ["serve", "--transport", "stdio"])
    assert result.exit_code != 0  # stdio no longer a valid choice


def test_health_command_reports_healthy(monkeypatch):
    response = Mock(status_code=200)
    response.json.return_value = {"status": "healthy", "transport": "unified"}
    with patch("gnomad_link.cli.httpx.get", return_value=response):
        result = runner.invoke(app, ["health", "--url", "http://127.0.0.1:8000"])
    assert result.exit_code == 0
    assert "healthy" in result.output.lower()
```

- [ ] **Step 2: Run it (FAIL — argparse app, no typer)**

Run: `cd ../gnomad-link && uv run pytest tests/unit/test_cli.py -v`

- [ ] **Step 3: Rewrite `gnomad_link/cli.py`** as a typer app

```python
"""Typer CLI for gnomAD Link (GeneFoundry Logging & CLI Standard v1)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import typer
from rich.console import Console

from . import __version__
from .config import ServerConfig, settings
from .server_manager import UnifiedServerManager

app = typer.Typer(
    name="gnomad-link",
    help="gnomAD Link: unified FastAPI host + MCP HTTP server for gnomAD data.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


@app.command()
def serve(
    transport: str = typer.Option("unified", help="unified (REST+MCP) or http (REST only)."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    mcp_path: str = typer.Option("/mcp"),
    log_level: str = typer.Option("INFO"),
    disable_docs: bool = typer.Option(False, "--disable-docs"),
    dev: bool = typer.Option(False, "--dev", help="Development mode (auto-reload)."),
) -> None:
    """Start the gnomAD Link server over Streamable HTTP."""
    if transport not in ("unified", "http"):
        console.print(f"[red]Unsupported transport {transport!r} (use unified|http).[/red]")
        raise typer.Exit(2)
    config = ServerConfig(
        transport=transport, host=host, port=port, mcp_path=mcp_path,
        enable_docs=not disable_docs, log_level=log_level,
    )
    asyncio.run(UnifiedServerManager().start_server(config))


@app.command()
def config(validate: bool = typer.Option(False, "--validate")) -> None:
    """Show (and optionally validate) the resolved configuration."""
    cfg = ServerConfig.from_env()
    console.print("[bold]gnomAD Link configuration[/bold]")
    for k, v in vars(cfg).items():
        console.print(f"  {k}: {v}")
    console.print(f"  GNOMAD_API_URL: {settings.GNOMAD_API_URL}")
    if validate:
        if not 1 <= cfg.port <= 65535 or not cfg.mcp_path.startswith("/"):
            console.print("[red]Invalid configuration[/red]")
            raise typer.Exit(1)
        console.print("[green]Configuration is valid[/green]")


@app.command()
def health(url: str = typer.Option("http://127.0.0.1:8000")) -> None:
    """Probe a running server's /health."""
    try:
        resp = httpx.get(f"{url}/health", timeout=5)
    except httpx.HTTPError as exc:
        console.print(f"[red]Failed to connect: {exc}[/red]")
        raise typer.Exit(1) from exc
    if resp.status_code != 200:
        console.print(f"[red]Server returned {resp.status_code}[/red]")
        raise typer.Exit(1)
    data = resp.json()
    console.print(f"[green]healthy[/green] transport={data.get('transport')} status={data.get('status')}")


cache_app = typer.Typer(help="In-process cache management.")
app.add_typer(cache_app, name="cache")


@cache_app.command("stats")
def cache_stats() -> None:
    """Show cache statistics."""
    stats: dict[str, Any] = UnifiedServerManager()._create_frequency_service().get_cache_stats()
    console.print(f"hits={stats['hits']} misses={stats['misses']} "
                  f"total={stats['total']} hit_rate={stats['hit_rate']}")


@cache_app.command("clear")
def cache_clear() -> None:
    """Clear all caches and reset counters."""
    UnifiedServerManager()._create_frequency_service().clear_cache()
    console.print("Cache cleared and statistics reset.")


@app.command()
def version() -> None:
    """Show version information."""
    console.print(f"gnomad-link {__version__}")
```

- [ ] **Step 4: Run test (PASS) + commit**

```bash
cd ../gnomad-link
uv run pytest tests/unit/test_cli.py -v
git add gnomad_link/cli.py tests/unit/test_cli.py
git commit -m "feat: typer CLI with serve/config/health/cache/version"
```

---

### Task 6: Rewrite the cache-command test for typer

**Files:**
- Rewrite: `tests/unit/test_cli_cache_commands.py`

- [ ] **Step 1: Rewrite the test**

```python
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from gnomad_link.cli import app

runner = CliRunner()


def _fake_service(stats=None):
    svc = Mock()
    svc.get_cache_stats.return_value = stats or {
        "hits": 3, "misses": 1, "total": 4, "hit_rate": 0.75, "cache_info": {}
    }
    return svc


def test_cache_stats_prints_metrics():
    svc = _fake_service()
    with patch("gnomad_link.cli.UnifiedServerManager") as mgr:
        mgr.return_value._create_frequency_service.return_value = svc
        result = runner.invoke(app, ["cache", "stats"])
    assert result.exit_code == 0
    assert "hits=3" in result.output and "hit_rate=0.75" in result.output


def test_cache_clear_resets():
    svc = _fake_service()
    with patch("gnomad_link.cli.UnifiedServerManager") as mgr:
        mgr.return_value._create_frequency_service.return_value = svc
        result = runner.invoke(app, ["cache", "clear"])
    assert result.exit_code == 0
    svc.clear_cache.assert_called_once()
    assert "cleared" in result.output.lower()
```

- [ ] **Step 2: Run (PASS) + commit**

```bash
cd ../gnomad-link
uv run pytest tests/unit/test_cli_cache_commands.py -v
git add tests/unit/test_cli_cache_commands.py
git commit -m "test: typer cache command tests"
```

---

### Task 7: Delete root entry scripts + repoint packaging

**Files:**
- Delete: `server.py`, `mcp_server.py` (repo root)
- Modify: `pyproject.toml`

- [ ] **Step 1: Delete the entry scripts**

```bash
cd ../gnomad-link && git rm server.py mcp_server.py
```

- [ ] **Step 2: Update `pyproject.toml`**

Scripts:
```toml
[project.scripts]
gnomad-link = "gnomad_link.cli:app"
```
(remove the `gnomad-link-mcp` line.)

Version:
```toml
version = "3.0.0"
```

Hatch wheel — drop the now-deleted root modules:
```toml
[tool.hatch.build.targets.wheel]
packages = ["gnomad_link"]
```
(remove the `include = ["server.py", "mcp_server.py"]` block.)

- [ ] **Step 3: Re-sync + smoke the console script**

```bash
cd ../gnomad-link
uv sync
uv run gnomad-link version   # -> "gnomad-link 3.0.0"
```
Expected: prints the version (proves the `gnomad_link.cli:app` entry point resolves).

- [ ] **Step 4: Commit**

```bash
cd ../gnomad-link
git add pyproject.toml server.py mcp_server.py uv.lock
git commit -m "refactor: single typer entry point; drop root server scripts and -mcp script"
```

---

### Task 8: Update Makefile, Docker, README, CHANGELOG

**Files:**
- Modify: `Makefile`
- Modify: `docker/Dockerfile`, `docker/docker-compose.yml`, `docker/docker-compose.dev.yml`
- Modify: `README.md`, `CHANGELOG.md`

- [ ] **Step 1: `Makefile`** — fix file lists (no more `server.py mcp_server.py`) and run targets

Replace `server.py mcp_server.py` in `format`/`format-check`/`lint`/`lint-ci`/`lint-fix`/`typecheck*` targets with nothing (just `gnomad_link tests`). Replace the run targets:
```makefile
dev: ## Run unified host (/health) + mounted MCP HTTP locally
	uv run gnomad-link serve --transport unified --host 127.0.0.1 --port 8000

run-prod: ## Run production server
	uv run gnomad-link serve --transport unified --host 0.0.0.0 --port 8000
```
Delete the `mcp-serve`, `mcp-serve-http`, `run-mcp` targets (stdio gone).

- [ ] **Step 2: `docker/Dockerfile`** — update `CMD`

```dockerfile
CMD ["gnomad-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
```
Remove `MCP_TRANSPORT=stdio` references (there are none by default; ensure env stays `unified`).

- [ ] **Step 3: Compose files** — update the `command:` lines

In `docker/docker-compose.yml` (and `.dev.yml` if it sets a command):
```yaml
    command: ["gnomad-link", "serve", "--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: `README.md`** — replace start instructions

Change `uv run python server.py --transport unified …` / `gnomad-link --transport …` to `gnomad-link serve --transport unified …` (and `make dev`). Remove the stdio / `gnomad-link-mcp` / Claude-Desktop-stdio sections.

- [ ] **Step 5: `CHANGELOG.md`** — prepend

```markdown
## 3.0.0
- BREAKING: typer CLI — start with `gnomad-link serve …` (no bare-serve).
- BREAKING: stdio transport removed (Streamable HTTP only); `gnomad-link-mcp` and
  root `server.py`/`mcp_server.py` deleted.
- Logging migrated to structlog (JSON in prod, console in dev, correlation-id).
- Adopts GeneFoundry Logging & CLI Standard v1.
```

- [ ] **Step 6: Validate docker config (if docker available) + commit**

```bash
cd ../gnomad-link
make docker-prod-config >/dev/null && echo "compose renders"   # skip if no docker
git add Makefile docker README.md CHANGELOG.md
git commit -m "docs/ops: serve command in Makefile/Docker/README; 3.0.0 changelog"
```

---

# Phase 3 — Gate

### Task 9: Full `ci-local` + coverage gate

**Files:** none new.

- [ ] **Step 1: Search for stragglers** (must be empty)

```bash
cd ../gnomad-link
grep -rnE "stdio|argparse|get_server_logger|mcp_server|create_parser" gnomad_link tests Makefile docker README.md | grep -viE "unified|http" || echo "clean"
```
Expected: `clean` (or only benign matches). Fix any real straggler before continuing.

- [ ] **Step 2: Run the gate**

Run: `cd ../gnomad-link && make ci-local`
Expected: format, lint, lint-loc, typecheck, tests all green. Fix anything red (e.g., a missed `get_server_logger` call site → `structlog.get_logger`).

- [ ] **Step 3: Coverage**

Run: `cd ../gnomad-link && make test-cov`
Expected: ≥ 70%. If the new `cli.py`/`logging_config.py` drop below, add focused tests (e.g., `config --validate` failure path, `serve` http transport) until the floor holds.

- [ ] **Step 4: Commit any gate fixes**

```bash
cd ../gnomad-link && git add -A && git commit -m "chore: green ci-local for 3.0.0" || echo "nothing to commit"
```

---

# Phase 4 — Rollout prep (no outward actions without approval)

### Task 10: Tracking-issue bodies + router logging alignment

**Files:**
- Create: `genefoundry-router/docs/rollout/logging-cli-standard-issues.md`
- Modify: `genefoundry-router/docs/plans/2026-06-13-genefoundry-router-implementation.md` (Task 11 logging)

- [ ] **Step 1: Draft the tracking-issue text** (do NOT file — outward-facing; wait for explicit go-ahead)

`genefoundry-router/docs/rollout/logging-cli-standard-issues.md`:
```markdown
# Tracking issues — Adopt GeneFoundry Logging & CLI Standard v1

File one issue per repo (after the standard doc is on the default branch). Body:

> Adopt the GeneFoundry Logging & CLI Standard v1
> (genefoundry-router/docs/GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md).
> - [ ] typer CLI (`serve`/`config`/`health`/`cache`/`version`), entry `<pkg>.cli:app`
> - [ ] structlog logging_config (JSON prod / console dev, correlation-id)
> - [ ] remove stdio (HTTP-only); delete root entry scripts
> - [ ] MAJOR bump + CHANGELOG; ci-local green
> See `gnomad-link@3.0.0` as the exemplar.

Targets:
- logging: clingen-link, spliceailookup-link, omim-link
- CLI:     clingen-link, gtex-link, pubtator-link, spliceailookup-link, uniprot-link, omim-link
```

- [ ] **Step 2: Align the router plan's logging to the canon** (bonus — keeps the router born-compliant)

In `genefoundry-router/docs/plans/2026-06-13-genefoundry-router-implementation.md`, Task 11's `configure_logging`, swap the minimal JSON config for the §3.2 canon (add `merge_contextvars`, `add_log_level`, `TimeStamper(iso)`, `StackInfoRenderer`, `format_exc_info`, a `service`/`version` static-fields processor, and the correlation-id binder; JSON prod / console dev). One-line edit note in the plan's R1 block.

- [ ] **Step 3: Commit (router repo)**

```bash
cd genefoundry-router  # or your router checkout
git add docs/rollout/logging-cli-standard-issues.md docs/plans/2026-06-13-genefoundry-router-implementation.md
git commit -m "docs: standard rollout issue drafts; align router logging to canon"
```

- [ ] **Step 4: Surface for approval**

Report to the user: standard published, gnomad-link at 3.0.0 (green), issue bodies drafted. Ask before filing the ~7 GitHub issues (outward-facing).

---

## Self-review

**Spec coverage:** §3.1 CLI → Tasks 1, 5, 6; §3.2 logging → Tasks 1, 3; §3.3 HTTP-only → Tasks 4, 5, 7, 8; §3.4 DoD → Task 1 + gate Task 9. §4.1 added/rewritten → Tasks 3, 5; §4.2 removed (stdio, root scripts, -mcp, transports) → Tasks 4, 7; §4.3 config/packaging/ops → Tasks 2, 7, 8; §4.4 preserved → verified by the untouched MCP/service tests passing (Task 4 Step 5, Task 9). §5 testing → Tasks 3, 5, 6, 9. §6 rollout → Task 10. Open Q1 (keep `http` transport): kept — `serve --transport {unified,http}` (Task 5). ✓

**Placeholder scan:** every code/step is concrete; deletions specify exact paths; gate Task 9 has an explicit straggler grep. No TBDs.

**Type consistency:** `configure_logging(level: str | None = None)` (Task 3) is called as `configure_logging(config.log_level)` (Task 4); `ServerConfig(transport in {unified,http})` (Task 2/4) matches `serve`'s validation (Task 5); `UnifiedServerManager().start_server(config)` (Task 5) matches the trimmed dispatch (Task 4); entry `gnomad_link.cli:app` (Task 7) matches the `app` defined in Task 5. ✓

---

## Execution handoff

After approval: **subagent-driven** (fresh subagent per task, review between tasks). Note the cross-repo split — Task 1 + Task 10 act in `genefoundry-router`; Tasks 2–9 act in `../gnomad-link`. Gate at Task 9 (`make ci-local` in gnomad-link). Do not file GitHub issues without explicit go-ahead.
