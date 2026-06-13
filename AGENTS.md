# AGENTS.md

Shared instructions for AI coding agents working in this repository.

## What this project is

`genefoundry-router` is a **thin FastMCP 3.x aggregator** (a router/gateway), NOT a data
server. It federates the GeneFoundry `*-link` MCP fleet behind one Streamable-HTTP endpoint
(`genefoundry`) with collision-free namespacing, BM25 tool-search, pluggable auth, and a
config-driven registry. It is a *client* to each backend and a *server* to hosts.

- Primary code area: `genefoundry_router/`
- Source of truth for backends: `servers.yaml` (structure) + `.env` (URLs/secrets) + `uv.lock`
- Design spec: `docs/specs/2026-06-13-genefoundry-router-design.md`
- Implementation plan: `docs/plans/2026-06-13-genefoundry-router-implementation.md`

## Required check before handoff

```bash
make ci-local      # format-check, lint, lint-loc (600-LOC budget), mypy, unit + integration tests
```

Other useful targets: `make test`, `make test-integration`, `make test-cov` (coverage ≥70),
`make lint`, `make typecheck`, `make run`, `make validate`, `make doctor`, `make list-tools`,
`make docker-build`, `make docker-prod-config`, `make docker-npm-config`.

## Coding standards

- Python **3.12+**; dependency + venv management via **uv** (`uv sync --group dev`, `uv run`).
- Modern typing (`X | None`, builtin generics); `ruff` (lint + format) and `mypy` must pass.
- **600-LOC per module** budget, enforced by `scripts/check_file_size.py` (`make lint-loc`).
- TDD: write a failing test, see it fail, implement minimally, see it pass; one atomic commit
  per change.
- FastMCP 3.x symbols are post-training-cutoff and fast-moving — **verify imports against the
  installed package** before relying on them (see the import smoke in the implementation plan,
  Task 2). Adapt to the installed API; the integration test is the contract.

## Project-specific guidance

- **Namespacing is the gateway's job.** Keep per-backend `transform` blocks minimal and delete
  them from `servers.yaml` as source repos adopt Tool-Naming Standard v1.
- **No token passthrough**: never forward the caller's `Authorization` header to backends.
- Streamable HTTP only (`transport="http"`); SSE is not offered.

## Boundary

Research use only. Not clinical decision support. Mirror the backends' disclaimers.
