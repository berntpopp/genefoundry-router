# GeneFoundry Versioning Standard v1

> Canonical reference for the GeneFoundry `-link` MCP fleet and the
> `genefoundry-router` aggregator. Adopted 2026-07-03. Part of the
> **GeneFoundry MCP router** initiative: every server advertises its own
> package version consistently so hosts, the drift baseline, and operators can
> trust `serverInfo.version`.

## Why

Two problems this standard eliminates:

1. **`serverInfo.version` leaked the framework version.** A `FastMCP(name=...)`
   built without a `version=` argument defaults `serverInfo.version` to
   FastMCP's own version (e.g. `3.4.2`), not the server's. Hosts and the
   router's drift baseline then recorded the framework version fleet-wide.
2. **Version drift between sources.** When `__version__` is a hardcoded literal
   separate from `pyproject.toml`, the two silently diverge (observed: a repo
   advertising `0.1.0` at runtime while shipping `0.1.1`).

## The rule — one source, everything derives

1. **`pyproject.toml [project].version` is the single source of truth.** It is
   the only place a version number is written. Bump it, and everything below
   follows automatically. No second literal anywhere.

2. **`<pkg>/__init__.py` derives `__version__` from installed metadata:**

   ```python
   from importlib.metadata import PackageNotFoundError, version

   try:
       __version__ = version("<dist-name>")
   except PackageNotFoundError:  # pragma: no cover - source checkout without install
       __version__ = "0.0.0"

   __all__ = ["__version__"]
   ```

3. **The MCP server advertises it.** Pass `version=__version__` to the
   `FastMCP(...)` constructor. Servers built via `FastMCP.from_fastapi(...)`
   forward it through `**settings` straight into `FastMCP(version=...)`:
   `FastMCP.from_fastapi(app=..., name=..., version=__version__, ...)`.

4. **`/health` and any `buildinfo` derive from `__version__`** (or installed
   metadata) — never a separate literal.

## Enforcement — automatic, not manual

Every repo carries `tests/unit/test_version_single_source.py`, which asserts the
whole chain is one value and **fails CI on any drift**:

```python
assert version(DIST) == pyproject_version          # metadata == pyproject
assert __version__ == version(DIST)                 # dunder derives from metadata
assert create_<x>_mcp().version == __version__      # serverInfo == package version
```

That test is the standard: adding it to a repo and turning it green *is*
compliance. There is no separate release automation to run — a version bump is
a one-line edit to `pyproject.toml` (then `uv lock && uv sync`); the guard test
proves the rest still agrees.

## Releasing

1. Edit `pyproject.toml [project].version` (PATCH for fixes, MINOR/MAJOR per
   SemVer + the Response-Envelope breaking-change rules).
2. `uv lock && uv sync --group dev` so installed metadata reflects the bump.
3. Add a `CHANGELOG.md` entry.
4. `make ci-local` — the guard test confirms metadata → `__version__` →
   `serverInfo` → `/health` all agree.

## Adoption

Adopted fleet-wide 2026-07-03 alongside the `serverInfo.version` fix. All 21
`-link` backends and the router derive `__version__` from metadata, pass
`version=__version__` to their server constructor, and carry the guard test.
