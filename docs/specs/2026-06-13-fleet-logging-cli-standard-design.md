# GeneFoundry Logging & CLI Standard v1 + gnomad-link Adoption — Design Spec

- **Date:** 2026-06-13
- **Status:** Approved for planning
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Owner:** Bernt Popp
- **Standards hub:** `genefoundry-router/docs/` (sibling to `TOOL-NAMING-STANDARD-v1.md`)
- **Exemplar repo:** `berntpopp/gnomad-link` (→ `3.0.0`)

## 1. Summary

The GeneFoundry `*-link` fleet is inconsistent in two cross-cutting concerns: **CLI framework** and **logging**. Most repos already use `typer` (7/14) and `structlog` (10/14, and 100% of the current generation), but a few outliers still use `argparse` and/or stdlib `logging` — notably `gnomad-link`, which is also the de-facto reference template. This spec defines a **GeneFoundry Logging & CLI Standard v1** (a short canonical doc, like the Tool-Naming Standard v1) and applies it to `gnomad-link` as the exemplar adoption. The remaining outliers get per-repo tracking issues.

This is a developer-experience / fleet-consistency effort. It is **independent of `genefoundry-router`** — the router federates backends over Streamable HTTP `/mcp` and is unaffected by a backend's CLI or logging internals. The payoff is uniform tooling across the fleet and an exemplary reference repo.

The fleet is **pre-alpha**: breaking changes are acceptable, no backward-compat shims, no deprecation windows.

## 2. Goals / Non-goals

**Goals**
- A canonical `GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md` covering CLI (typer) + logging (structlog), HTTP-only transport, and a per-repo Definition of Done.
- `gnomad-link 3.0.0`: full adoption (argparse→typer, stdlib→structlog), behavior-preserving for the surviving command surface.
- Tracking issues for the remaining outliers.

**Non-goals (v1)**
- No changes to any repo's **MCP tool surface**, services, data clients, or HTTP endpoints (`/health`, `/mcp`). This is front-end (CLI) + logging plumbing only.
- No stdio transport anywhere (the fleet is Streamable-HTTP-only, matching the router).
- No backward compatibility (pre-alpha): no console-script aliases, no deprecation period.
- Not a behavior redesign of `serve`/`config`/`health`/`cache` — same effects, new front-end.

## 3. The Standard v1 (canonical content)

Derived from the fleet's actual current-generation repos (`mgi-link`, `uniprot-link`, `stringdb-link`, `litvar-link`), not invented.

### 3.1 CLI
- Framework: **`typer`**. One app per package: `app = typer.Typer(name="<name>", help="…", add_completion=False, no_args_is_help=True)`.
- Entry point: `[project.scripts] <name> = "<pkg>.cli:app"`. **No** root-level `server.py`/`mcp_server.py` entry scripts.
- Console output: **`rich`** (`Console`, `Table`) for human-facing output.
- Canonical commands (include those that apply to the repo):
  - **`serve`** — start the server. Options: `--transport {unified,http}` (default `unified`), `--host`, `--port`, `--mcp-path`, `--log-level`, `--disable-docs`, `--dev`. **No bare-serve** — server start is always `<name> serve …`.
  - `config [--validate]` — show/validate resolved configuration.
  - `health [--url]` — probe a running server's `/health`.
  - `cache stats|clear` — in-process cache management (where the server has one).
  - `version` — show version + API version.
- `no_args_is_help=True`: bare `<name>` prints help.

### 3.2 Logging
- Library: **`structlog`**, configured in `<pkg>/logging_config.py` via `configure_logging(...)`.
- Canonical processor chain (verbatim from `mgi-link`/`uniprot-link`):
  - `structlog.contextvars.merge_contextvars`
  - `structlog.stdlib.add_log_level`
  - `structlog.processors.TimeStamper(fmt="iso")`
  - `structlog.processors.StackInfoRenderer()`
  - `structlog.processors.format_exc_info`
- Format branch:
  - **prod (default):** `structlog.processors.dict_tracebacks` → `structlog.processors.JSONRenderer()`
  - **dev (`--dev`):** `structlog.dev.ConsoleRenderer(colors=True)`
- **Correlation IDs:** bind `asgi-correlation-id` into every event (a small processor reading `correlation_id.get()`), as in `autopvs1-link`/`gtex-link`.
- Module loggers obtained via `structlog.get_logger(__name__)`.

### 3.3 Transport
- **Streamable HTTP only.** No stdio. `serve --transport` accepts `unified` (REST host `/health` + MCP `/mcp`) and `http` (REST host only). MCP at `/mcp`.

### 3.4 Definition of Done (per repo)
- [ ] `typer` app at `<pkg>/cli:app`; `serve`/`config`/`health`/`cache`/`version` as applicable; `no_args_is_help`.
- [ ] Single console script `<name> = "<pkg>.cli:app"`; no root entry scripts; no stdio entry.
- [ ] `<pkg>/logging_config.py` with the canonical structlog chain; JSON prod / Console dev; correlation-id bound.
- [ ] No stdio transport anywhere (config, server manager, docker, docs).
- [ ] Docker `CMD`/Makefile/README updated to `<name> serve …`.
- [ ] CLI + logging covered by tests (typer `CliRunner`); coverage floor held; ruff + mypy clean.
- [ ] MAJOR version bump + one-line `CHANGELOG` note.

## 4. gnomad-link 3.0.0 — adoption scope

### 4.1 Added / rewritten
- **`gnomad_link/cli.py`** — typer `app` replacing argparse. Commands:
  - `serve` (transports `unified`|`http`; same options as today minus `stdio`), dispatching to `UnifiedServerManager` exactly as the current `server.py`/`create_config_from_args` flow does.
  - `config [--validate]`, `health [--url]`, `cache stats|clear`, `version` — ported 1:1 from the current `handle_*_command` functions (same output semantics; rendered via `rich`).
- **`gnomad_link/logging_config.py`** — internals replaced with the §3.2 structlog canon. Public surface migrates to `structlog.get_logger`; the existing `get_server_logger`/`get_mcp_logger`/`get_api_logger` helpers and `TransportAwareFormatter` are removed (call sites updated to `structlog.get_logger(__name__)`).

### 4.2 Removed (pre-alpha, no shims)
- Root **`server.py`** and **`mcp_server.py`**.
- Console script **`gnomad-link-mcp`**.
- **stdio** everywhere: `ServerConfig.transport` Literal drops `"stdio"`; `UnifiedServerManager.start_stdio_server` and its dispatch are deleted; stdio logging branch removed; stdio references in docs/Makefile removed.

### 4.3 Changed (config / packaging / ops)
- `pyproject.toml`: `[project.scripts]` → `gnomad-link = "gnomad_link.cli:app"` (remove the `-mcp` script); `[tool.hatch.build.targets.wheel]` drops the `include = ["server.py","mcp_server.py"]`; version `2.0.0 → 3.0.0`; add `rich` if not already (it is). `structlog` already declared.
- `docker/Dockerfile`: `CMD ["gnomad-link","serve","--transport","unified","--host","0.0.0.0","--port","8000"]`; drop `MCP_TRANSPORT=stdio` paths (none); keep unified defaults.
- `Makefile`: `dev`/`run-prod` → `uv run gnomad-link serve --transport unified …`; remove `mcp-serve`/`run-mcp`; update `format`/`lint`/`typecheck` target file lists (drop `server.py mcp_server.py`, they no longer exist).
- `README.md`: update Quick Start / MCP integration to `gnomad-link serve …`; remove stdio/Claude-Desktop-stdio instructions.
- `CHANGELOG`: one line — "3.0.0: typer CLI (`serve` command), structlog logging, stdio removed (HTTP-only). Breaking."

### 4.4 Preserved (untouched)
- `gnomad_link/mcp/` facade (tools, resources, response modes), services, GraphQL, models.
- `/health` + `/mcp` HTTP surface, `UnifiedServerManager` (minus stdio), `ServerConfig`/`Settings` (minus stdio).
- The router and any HTTP MCP client are unaffected.

## 5. Testing strategy

- **CLI:** rewrite `tests/unit/test_cli.py` + `tests/unit/test_cli_cache_commands.py` to typer `CliRunner`. Assert: `serve` builds the right `ServerConfig` and invokes the manager (mock `UnifiedServerManager`); `config --validate` exit codes; `health` against a mocked `httpx.get`; `cache stats|clear` against a mocked service; `version` output; bare invocation prints help.
- **Logging:** new test asserting `configure_logging` in prod mode emits JSON with `level`/`timestamp`/`event` and a bound `correlation_id` when one is set; dev mode uses the console renderer.
- **Regression:** existing MCP facade/service tests must pass unchanged (proves behavior preservation). Any test importing `server`/`mcp_server` or `transport="stdio"` is updated/removed.
- Gates: `make ci-local` green; coverage ≥ 70 (current floor); ruff + mypy clean; `make lint-loc` (600-LOC) holds.

## 6. Rollout

1. Write `genefoundry-router/docs/GENEFOUNDRY-LOGGING-CLI-STANDARD-v1.md`.
2. Implement `gnomad-link 3.0.0` as the exemplar (this spec's plan).
3. File per-repo tracking issues "Adopt GeneFoundry Logging & CLI Standard v1":
   - **logging:** `clingen-link`, `spliceailookup-link`, `omim-link`.
   - **CLI:** `clingen-link`, `gtex-link`, `pubtator-link`, `spliceailookup-link`, `uniprot-link`, `omim-link`.
   - (Issues link to the standard doc; filed only after step 1, and only with explicit go-ahead since they touch public repos.)
4. **Bonus alignment:** update the `genefoundry-router` implementation plan's `configure_logging` to the §3.2 canon so the router is born compliant.

## 7. Open questions

1. Should `serve --transport http` (REST-only, no MCP) survive, or collapse to a single `serve` that always runs unified? (Default: keep both; `unified` is the default.)
2. Standard doc home confirmed as `genefoundry-router/docs/` (standards hub) — vs a dedicated `geneFoundry-standards` repo later. (Default: router/docs for now.)
