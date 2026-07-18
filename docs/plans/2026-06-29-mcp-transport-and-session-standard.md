# MCP Transport & Session Standard v1 — Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every GeneFoundry `-link` server and the `genefoundry` router serve MCP at a single `/mcp` endpoint with no 307 redirect, in the stateless+JSON tier, with canonical `serverInfo` and `/health`, all enforced by one shared, vendored conformance probe.

**Architecture:** A single self-contained probe (`conformance.py`, httpx-only) is the contract. It is authored and validated once in the router repo (Phase A), then vendored into each repo. Each `-link` server is its **own git repo**, so the migrations are mutually independent — Phase B is a fully parallel fan-out of one PR per repo (≈22 concurrent units), gated only by Phase A. Phase C is a fleet-wide live sweep + close-out after the PRs merge.

**Tech Stack:** Python 3.12+, uv, FastMCP 3.x (`mcp.http_app(path=, stateless_http=, json_response=)`), FastAPI/Starlette host, httpx, pytest, ruff, mypy, GitHub Actions, docker compose.

## Global Constraints

Every task's requirements implicitly include these (verbatim from the spec + AGENTS.md):

- **Spec is the contract:** `docs/specs/2026-06-29-mcp-transport-and-session-standard-design.md`. Deliverable standard doc: `docs/MCP-TRANSPORT-STANDARD-v1.md`.
- **Transport canon:** build the MCP ASGI app with the path baked in — `mcp.http_app(path=<mcp_path>, stateless_http=True, json_response=True)` — and mount it at root — `app.mount("/", mcp_app)`. Never `mount("/mcp", http_app(path="/"))` (that is the 307 source).
- **No `Mcp-Session-Id`** at the transport layer in the stateless tier. Application sessions (pubtator-style) stay at the service layer, addressed by a `session_id` **tool argument** — never touched by this work.
- **serverInfo.name**: backends MUST be `"<namespace>-link"` (lowercase). The **router** MUST be exactly `"genefoundry"` (not `genefoundry-link`).
- **/health** MUST return `200`; **backends** MUST include `{status, version, transport}`; the **router** keeps its aggregate `{status, service, backends:{…}}` shape.
- **No token passthrough:** the router MUST NOT forward the caller's `Authorization` header to backends. Do not change this.
- **Streamable HTTP only** (`transport="http"`); SSE is not offered in the default tier. No new escape-hatch (stateful) servers in v1 — no current tool qualifies.
- **Python 3.12+, uv** (`uv sync --group dev`, `uv run`). `ruff` (lint+format) and `mypy` MUST pass. **600 LOC/module** budget (`make lint-loc`). `make ci-local` before handoff.
- **FastMCP 3.x is post-cutoff** — verify `stateless_http`/`json_response` kwargs against the installed package before relying on them; the integration test is the contract.
- **TDD**: failing test → see it fail → minimal implementation → see it pass → one atomic commit per change.
- **One PR per repo.** Branch first (never commit to a repo's default branch directly). Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Verify against live `main`, not a local checkout** — several working copies are 1 commit behind; `git fetch` and branch off `origin/<default>` before editing.

---

## Parallelization Map

```
Phase A (SEQUENTIAL — the only blocker)
  Task 1  router repo: author + self-validate the probe, publish the standard,
          ship the reusable CI job + vendored-probe template
        │
        ▼
Phase B (FULLY PARALLEL — one subagent / one PR per repo, no inter-dependencies)
  Task 2 ×11  mode-only migrations  (gencc, gtex, hgnc, hpo, mavedb, metadome,
              mgi, mondo, orphanet, panelapp, uniprot)
  Task 3 ×2   path+mode migrations  (genereviews, stringdb)
  Task 4 ×8   conformant repos: add the gate only, no code change
              (autopvs1, clingen, clinvar, gnomad, litvar, pubtator,
               spliceailookup, vep)
  Task 5 ×1   router transport migration (in-process probe, router profile)
        │
        ▼
Phase C (SEQUENTIAL — after Phase B PRs merge)
  Task 6  fleet-wide live conformance sweep + servers.yaml/.env verify +
          mark the standard Adopted
```

Phase B holds up to **22 concurrent PRs**. Repos are independent git repos, so no worktrees are needed — each subagent works in a different `../<repo>` directory. Dispatch Task 2/3/4 rows and Task 5 all at once.

---

## File Structure

**Router repo (`genefoundry-router`), Phase A & Task 5:**
- Create: `genefoundry_router/conformance.py` — the probe (httpx-only, self-contained, < 200 LOC).
- Create: `docs/MCP-TRANSPORT-STANDARD-v1.md` — the normative standard (rendered from the spec).
- Create: `docs/conformance/conformance.py` — canonical vendored copy (byte-identical to the module above) that `-link` repos copy in.
- Create: `docs/conformance/conformance.yml` — reusable GitHub Actions job template.
- Create: `docs/conformance/test_transport_v1.py` — pytest wrapper template repos copy in.
- Create: `tests/conformance/test_router_transport_v1.py` — router's own in-process conformance test (Task 5).
- Modify: `genefoundry_router/server.py:91,127` — bake path + `mount("/")` (Task 5).
- Modify: `Makefile` — add `conformance` target.

**Each `-link` repo (Phase B), per the per-repo tables:**
- Modify: `<pkg>/server_manager.py` — the single `mcp.http_app(...)` call (Tasks 2 & 3) + the `mount(...)` (Task 3 only).
- Modify: the `/health` handler — ensure `{status, version, transport}` (Tasks 2 & 3).
- Create: `tests/conformance/conformance.py` (vendored), `tests/conformance/test_transport_v1.py` (wrapper), `.github/workflows/conformance.yml` (gate) — Tasks 2, 3, 4.

---

## Phase A

### Task 1: Author + self-validate the probe; publish the standard (router repo)

This is the only serialization point. It produces the contract every other task consumes and is **self-validating**: the probe is TDD'd red→green against a *known-non-conformant* live server (a freshly built `gtex` container, pre-migration) and a *known-conformant* one (`autopvs1`, already migrated on `main`).

**Files:**
- Create: `genefoundry_router/conformance.py`
- Create: `docs/conformance/conformance.py` (identical copy for vendoring)
- Create: `docs/conformance/test_transport_v1.py`
- Create: `docs/conformance/conformance.yml`
- Create: `docs/MCP-TRANSPORT-STANDARD-v1.md`
- Modify: `Makefile`
- Test: validated against live `autopvs1` + `gtex` docker containers (manual red/green), no committed network test.

**Interfaces:**
- Produces: `run_probe(base_url: str, *, expected_name: str, tier: str, require_auth: bool = False) -> Report` and `main(argv: list[str] | None = None) -> int`. `Report` has `.conformant: bool`, `.passed: list[str]`, `.failed: list[str]`. CLI: `python -m genefoundry_router.conformance <url> --name <n> --tier {stateless,stateful} [--require-auth]`; exit 0=conformant, 1=non-conformant, 2=transport error.

- [ ] **Step 1: Branch off live main**

```bash
cd /home/bernt-popp/development/genefoundry-router
git fetch -q origin
git switch -c feat/mcp-transport-conformance origin/main
```

- [ ] **Step 2: Write the probe**

Create `genefoundry_router/conformance.py`:

```python
"""MCP Transport & Session Standard v1 — conformance probe.

Self-contained (httpx only). Vendored into every -link repo's tests/conformance/
and used by the router. Run against a live server:

    python -m genefoundry_router.conformance http://127.0.0.1:8005 --name gtex-link --tier stateless

Exit code: 0 conformant, 1 non-conformant, 2 transport/probe error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

import httpx

SUPPORTED_PROTOCOL = "2025-06-18"
UNSUPPORTED_PROTOCOL = "1999-01-01"

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": SUPPORTED_PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "mcp-conformance-probe", "version": "1.0.0"},
    },
}
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@dataclass
class Report:
    base_url: str
    name: str
    tier: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.passed.append(label) if ok else self.failed.append(f"{label} — {detail}")
        return ok

    @property
    def conformant(self) -> bool:
        return not self.failed


def _jsonrpc(resp: httpx.Response) -> dict:
    """Return the JSON-RPC payload, tolerating an SSE-framed body."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}
    try:
        return resp.json()
    except json.JSONDecodeError:
        return {}


def run_probe(
    base_url: str, *, expected_name: str, tier: str, require_auth: bool = False
) -> Report:
    base = base_url.rstrip("/")
    rep = Report(base, expected_name, tier)
    is_router = expected_name == "genefoundry"
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        init = client.post(f"{base}/mcp", json=_INIT, headers=_HEADERS)

        if require_auth and init.status_code == 401:
            rep.check(
                "auth: unauthenticated MCP call → 401 + WWW-Authenticate",
                "www-authenticate" in {k.lower() for k in init.headers},
                "missing WWW-Authenticate header",
            )
            return rep

        rep.check(
            "POST /mcp does not 307",
            init.status_code != 307,
            f"got {init.status_code} Location={init.headers.get('location')!r}",
        )
        rep.check("POST /mcp → 200", init.status_code == 200, f"got {init.status_code}")
        rep.check(
            "init Content-Type is application/json",
            init.headers.get("content-type", "").startswith("application/json"),
            init.headers.get("content-type", ""),
        )
        if tier == "stateless":
            rep.check(
                "stateless: no Mcp-Session-Id header",
                "mcp-session-id" not in {k.lower() for k in init.headers},
                "session id assigned",
            )

        result = _jsonrpc(init).get("result", {})
        name = result.get("serverInfo", {}).get("name")
        rep.check(f"serverInfo.name == {expected_name!r}", name == expected_name, f"got {name!r}")

        tl = client.post(f"{base}/mcp", json=_TOOLS_LIST, headers=_HEADERS)
        tools = _jsonrpc(tl).get("result", {}).get("tools", [])
        rep.check("tools/list returns ≥ 1 tool", len(tools) >= 1, f"{len(tools)} tools")

        bad = client.post(
            f"{base}/mcp",
            json=_TOOLS_LIST,
            headers={**_HEADERS, "MCP-Protocol-Version": UNSUPPORTED_PROTOCOL},
        )
        rep.check(
            "unsupported MCP-Protocol-Version → 400 (post-init)",
            bad.status_code == 400,
            f"got {bad.status_code}",
        )

        get = client.get(f"{base}/mcp", headers={"Accept": "text/event-stream"})
        rep.check("GET /mcp does not 307", get.status_code != 307, f"got {get.status_code}")

        health = client.get(f"{base}/health")
        rep.check("GET /health → 200", health.status_code == 200, f"got {health.status_code}")
        body = _jsonrpc(health) if health.status_code == 200 else {}
        rep.check("/health has 'status'", "status" in body, str(body)[:120])
        if not is_router:
            for key in ("version", "transport"):
                rep.check(f"/health has {key!r}", key in body, "missing (backend MUST include it)")
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP Transport Standard v1 conformance probe")
    parser.add_argument("base_url")
    parser.add_argument("--name", required=True, help="expected serverInfo.name")
    parser.add_argument("--tier", choices=["stateless", "stateful"], default="stateless")
    parser.add_argument("--require-auth", action="store_true")
    args = parser.parse_args(argv)
    try:
        rep = run_probe(
            args.base_url,
            expected_name=args.name,
            tier=args.tier,
            require_auth=args.require_auth,
        )
    except httpx.HTTPError as exc:
        print(f"TRANSPORT ERROR: {exc}", file=sys.stderr)
        return 2
    for line in rep.passed:
        print(f"  PASS  {line}")
    for line in rep.failed:
        print(f"  FAIL  {line}")
    verdict = "CONFORMANT" if rep.conformant else "NON-CONFORMANT"
    print(f"\n{verdict}: {rep.name} @ {rep.base_url} "
          f"({len(rep.passed)} pass, {len(rep.failed)} fail)")
    return 0 if rep.conformant else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: RED — run the probe against a non-conformant live server**

Build & run pre-migration `gtex` (stateful default), then probe it:

```bash
cd /home/bernt-popp/development/gtex-link && make docker-build && make docker-up
sleep 8
cd /home/bernt-popp/development/genefoundry-router
uv run python -m genefoundry_router.conformance http://127.0.0.1:8005 --name gtex-link --tier stateless
```

Expected: exit **1**, with `FAIL  stateless: no Mcp-Session-Id header` (and possibly the health version/transport FAILs). This proves the probe detects non-conformance. (If gtex's host port differs, read it from `docker ps`.) Tear down: `cd ../gtex-link && make docker-down`.

- [ ] **Step 4: GREEN — run the probe against a conformant live server**

```bash
cd /home/bernt-popp/development/autopvs1-link && git fetch -q origin && git switch main && git pull -q && make docker-build && make docker-up
sleep 8
cd /home/bernt-popp/development/genefoundry-router
uv run python -m genefoundry_router.conformance http://127.0.0.1:8000 --name autopvs1-link --tier stateless
```

Expected: exit **0** for the transport/session/serverInfo checks. If `/health` lacks `version`/`transport`, note it — those become explicit migration steps (Task 4 adds them even to "conformant" repos). Tear down: `make docker-down`.

> If Step 3 does **not** FAIL or Step 4's transport checks do **not** PASS, STOP and use systematic-debugging — the probe's protocol assumptions (e.g. stateless `tools/list` without a prior session, or the SSE-framing fallback) need correcting before any repo consumes it.

- [ ] **Step 5: Vendor copy + wrapper + CI template + Makefile**

Copy the validated probe and add the reusable artifacts:

```bash
cd /home/bernt-popp/development/genefoundry-router
mkdir -p docs/conformance
cp genefoundry_router/conformance.py docs/conformance/conformance.py
```

Create `docs/conformance/test_transport_v1.py` (the wrapper repos copy into `tests/conformance/`):

```python
"""MCP Transport Standard v1 conformance gate (vendored).

Skips unless CONFORMANCE_MCP_URL points at a running server. The conformance.yml
workflow sets it after `make docker-up`; local `make ci-local` skips it.
"""

from __future__ import annotations

import os

import pytest

from .conformance import run_probe

MCP_URL = os.environ.get("CONFORMANCE_MCP_URL")
EXPECTED_NAME = os.environ.get("CONFORMANCE_NAME", "REPLACE-ME-link")
TIER = os.environ.get("CONFORMANCE_TIER", "stateless")


@pytest.mark.skipif(not MCP_URL, reason="set CONFORMANCE_MCP_URL to run the live probe")
def test_mcp_transport_standard_v1() -> None:
    report = run_probe(MCP_URL, expected_name=EXPECTED_NAME, tier=TIER)
    assert report.conformant, "non-conformant:\n  " + "\n  ".join(report.failed)
```

Create `docs/conformance/conformance.yml` (reusable job — copy to each repo's `.github/workflows/`, fill `NAME`/`PORT`):

```yaml
name: mcp-conformance
on:
  pull_request:
  push:
    branches: [main, master]
permissions:
  contents: read
concurrency:
  group: conformance-${{ github.ref }}
  cancel-in-progress: true
jobs:
  conformance:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      CONFORMANCE_NAME: REPLACE-ME-link   # serverInfo.name
      CONFORMANCE_TIER: stateless
      MCP_PORT: "8000"                    # docker-compose host port
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v8
      - run: uv sync --group dev
      - name: Build & start server
        run: make docker-build && make docker-up
      - name: Wait for health
        run: |
          for i in $(seq 1 30); do
            curl -fsS "http://127.0.0.1:${MCP_PORT}/health" && break || sleep 2
          done
      - name: Run conformance probe
        env:
          CONFORMANCE_MCP_URL: http://127.0.0.1:${{ env.MCP_PORT }}
        run: uv run pytest tests/conformance/test_transport_v1.py -v
      - name: Logs on failure
        if: failure()
        run: make docker-logs || docker compose -f docker/docker-compose.yml logs
      - name: Teardown
        if: always()
        run: make docker-down || true
```

Add to `Makefile` (router):

```makefile
.PHONY: conformance
conformance:  ## Probe a live MCP server: make conformance MCP_URL=... NAME=... TIER=stateless
	uv run python -m genefoundry_router.conformance $(MCP_URL) --name $(NAME) --tier $(or $(TIER),stateless)
```

- [ ] **Step 6: Write the standard doc**

Create `docs/MCP-TRANSPORT-STANDARD-v1.md` — the normative standard rendered from the design spec. It MUST contain, as MUST/SHOULD/MAY clauses: the §3 transport contract (single `/mcp`, no 307, stateless+JSON, protocol-version, Accept, Origin/edge boundary), §4 serverInfo (backend `<ns>-link`, router `genefoundry`), §5 health (backend `{status,version,transport}`, router aggregate), §6 application-session pattern (DB-backed, `session_id` tool arg, pubtator `ResearchSessionService` reference), §7 escape hatch, and §8 conformance (backend vs router profile + the probe command + the per-repo DoD checklist). Link the probe at `docs/conformance/conformance.py`. End with an adoption table seeded from spec §9 (the three migration buckets + router), status column `pending` per repo.

- [ ] **Step 7: Lint, type-check, commit**

```bash
cd /home/bernt-popp/development/genefoundry-router
uv run ruff format genefoundry_router/conformance.py docs/conformance/*.py
uv run ruff check genefoundry_router/conformance.py
uv run mypy genefoundry_router/conformance.py
git add genefoundry_router/conformance.py docs/ Makefile
git commit -m "$(printf 'feat(conformance): MCP Transport Standard v1 probe + standard doc\n\nSelf-contained httpx probe (run_probe/CLI), vendored copy, pytest wrapper,\nreusable conformance.yml, and docs/MCP-TRANSPORT-STANDARD-v1.md. Validated\nred against pre-migration gtex and green against autopvs1.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

- [ ] **Step 8: Open the PR, merge when green**

```bash
git push -u origin feat/mcp-transport-conformance
gh pr create --fill --title "feat(conformance): MCP Transport Standard v1 probe + standard doc" \
  --body "$(printf 'Authors the shared conformance probe and publishes docs/MCP-TRANSPORT-STANDARD-v1.md. Blocks the Phase B migration fan-out.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)')"
```

**Gate:** Phase B does not start until this PR is merged (the vendored probe + standard must exist).

---

## Phase B — parallel fan-out (one PR per repo)

Dispatch all Task 2/3/4 rows and Task 5 concurrently. Each is self-contained: branch off the repo's live default, apply the template, gate on the probe, open a PR.

### Task 2 (template, ×11): Mode-only migration

These 11 repos already mount at `/` with the path baked in; they are stateful only because of the FastMCP default. The fix is one kwarg pair + the health fields + the gate.

**Per-repo table** (each row is one full instance of this task = one PR):

| # | Repo | Package | `http_app(...)` call to edit | serverInfo.name | docker host port |
|---|------|---------|------------------------------|-----------------|------------------|
| 2a | gencc-link | `gencc_link` | `server_manager.py` `start_unified_server` | `gencc-link` | verify via `docker ps` |
| 2b | gtex-link | `gtex_link` | `server_manager.py` `start_unified_server` | `gtex-link` | 8005 |
| 2c | hgnc-link | `hgnc_link` | `server_manager.py` `start_unified_server` | `hgnc-link` | verify |
| 2d | hpo-link | `hpo_link` | `server_manager.py` `start_unified_server` | `hpo-link` | verify |
| 2e | mavedb-link | `mavedb_link` | `server_manager.py` `start_unified_server` | `mavedb-link` | verify |
| 2f | metadome-link | `metadome_link` | `server_manager.py` `start_unified_server` | `metadome-link` | verify |
| 2g | mgi-link | `mgi_link` | `server_manager.py` `start_unified_server` | `mgi-link` | verify |
| 2h | mondo-link | `mondo_link` | `server_manager.py` `start_unified_server` | `mondo-link` | verify |
| 2i | orphanet-link | `orphanet_link` | `server_manager.py` `start_unified_server` | `orphanet-link` | verify |
| 2j | panelapp-link | `panelapp_link` | `server_manager.py` `start_unified_server` (line ~148) | `panelapp-link` | verify |
| 2k | uniprot-link | `uniprot_link` | `server_manager.py` `start_unified_server` | `uniprot-link` | verify |

> Note: `orhpanet-link` (the misspelled directory) is a stray clone — ignore it; the federated repo is `orphanet-link`. Confirm the `serverInfo.name` against the repo's MCP facade before asserting; if a repo still has a free-form name, fix it in this same PR (the probe enforces `<ns>-link`).

**Files (per row):**
- Modify: `<pkg>/server_manager.py` — the single `mcp.http_app(path=settings.mcp_path)` line.
- Modify: the `/health` handler (grep `def health` / `health_router`) — ensure `{status, version, transport}`.
- Create: `tests/conformance/conformance.py`, `tests/conformance/test_transport_v1.py`, `tests/conformance/__init__.py`, `.github/workflows/conformance.yml`.

**Interfaces:**
- Consumes: the vendored `conformance.py` from Task 1 (`docs/conformance/conformance.py`).
- Produces: a server that passes the **backend stateless** probe profile.

- [ ] **Step 1: Branch off live main**

```bash
cd /home/bernt-popp/development/<repo>
git fetch -q origin
git switch -c feat/mcp-stateless-transport "origin/$(git remote show origin | sed -n 's/.*HEAD branch: //p')"
```

- [ ] **Step 2: Vendor the probe + wrapper + workflow**

```bash
R=/home/bernt-popp/development/genefoundry-router
mkdir -p tests/conformance
cp "$R/docs/conformance/conformance.py" tests/conformance/conformance.py
cp "$R/docs/conformance/test_transport_v1.py" tests/conformance/test_transport_v1.py
: > tests/conformance/__init__.py
cp "$R/docs/conformance/conformance.yml" .github/workflows/conformance.yml
```

Edit `.github/workflows/conformance.yml`: set `CONFORMANCE_NAME: <serverInfo.name>` and `MCP_PORT` to the repo's docker-compose host port. (Use `master` or `main` in the `push.branches` list to match the repo's default.)

- [ ] **Step 3: RED — add the failing transport unit test**

Add `tests/conformance/test_transport_mode.py` — an in-process assertion that the runtime builds a **stateless** MCP app. This fails before the edit because the kwargs are absent. Use the repo's own facade factory (grep `def create_<x>_mcp`):

```python
"""Stateless-tier construction guard (in-process, no server needed)."""

from __future__ import annotations

import inspect

from <pkg> import server_manager


def test_unified_server_builds_stateless_json_mcp_app() -> None:
    src = inspect.getsource(server_manager.ServerManager.start_unified_server)
    assert "stateless_http=True" in src, "MCP app must be built stateless"
    assert "json_response=True" in src, "MCP app must return JSON responses"
    assert 'mount("/"' in src, "MCP ASGI app must mount at root (no 307)"
```

```bash
uv run pytest tests/conformance/test_transport_mode.py -v
```

Expected: FAIL on `stateless_http=True`.

- [ ] **Step 4: GREEN — apply the one-line transport change**

In `<pkg>/server_manager.py`, change the single MCP-app construction:

```python
# before
mcp_asgi = mcp.http_app(path=settings.mcp_path)
# after
mcp_asgi = mcp.http_app(
    path=settings.mcp_path, stateless_http=True, json_response=True
)
```

(panelapp: the same change on its `mcp.http_app(path=settings.mcp_path)` line inside `start_unified_server`.) Confirm the `mount("/", mcp_asgi)` is already present (it is for all 11 — do not change it).

```bash
uv run pytest tests/conformance/test_transport_mode.py -v
```

Expected: PASS.

- [ ] **Step 5: Ensure `/health` returns `{status, version, transport}`**

Locate the health handler. Make it return all three keys (import the package `__version__`):

```python
return {"status": "ok", "version": __version__, "transport": "streamable-http-stateless"}
```

If a health response model exists, extend it; keep existing keys. Add/extend the repo's health unit test to assert the three keys, run it green.

- [ ] **Step 6: Live probe via docker**

```bash
make docker-build && make docker-up
for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:<PORT>/health" && break || sleep 2; done
CONFORMANCE_MCP_URL=http://127.0.0.1:<PORT> CONFORMANCE_NAME=<name> CONFORMANCE_TIER=stateless \
  uv run pytest tests/conformance/test_transport_v1.py -v
make docker-down
```

Expected: the conformance test PASSES. If it fails on a stale image, the container predates the edit — rebuild.

- [ ] **Step 7: Full local CI, commit, PR**

```bash
make ci-local
git add <pkg>/server_manager.py tests/ .github/workflows/conformance.yml
git commit -m "$(printf 'feat(mcp): stateless+JSON transport + conformance gate (Transport Standard v1)\n\nAdd stateless_http/json_response to the unified MCP app, ensure /health\nreturns {status,version,transport}, and vendor the v1 conformance probe.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
git push -u origin feat/mcp-stateless-transport
gh pr create --fill --title "feat(mcp): adopt MCP Transport Standard v1 (stateless+JSON)" \
  --body "$(printf 'Adopts docs/MCP-TRANSPORT-STANDARD-v1.md: stateless+JSON /mcp, no 307, canonical /health, vendored conformance probe (green in CI).\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)')"
```

---

### Task 3 (template, ×2): Path + mode migration — genereviews, stringdb

These two still build `http_app(path="/")` and mount at `mcp_path`, so they carry **both** the 307 and the stateful default. They need the full gtex pattern *and* the stateless kwargs.

**Per-repo table:**

| # | Repo | Package | `http_app` line | `mount` line | path symbol in scope | serverInfo.name |
|---|------|---------|-----------------|--------------|----------------------|-----------------|
| 3a | genereviews-link | `genereview_link` | `server_manager.py:340` `self.mcp.http_app(path="/")` | `server_manager.py:353` `self.app.mount(config.mcp_path, mcp_app)` | `config.mcp_path` (verify `config` in scope at the call) | `genereviews-link` |
| 3b | stringdb-link | `stringdb_link` | `server_manager.py:58` `mcp_app.http_app(path="/")` | `server_manager.py:68` `app.mount(settings.mcp_path, mcp_http_app)` | `settings.mcp_path` | `stringdb-link` |

**Files (per row):** as Task 2, plus the `mount(...)` edit.

**Interfaces:**
- Consumes: the vendored probe (Task 1).
- Produces: a server passing the backend stateless profile — `POST /mcp` no longer 307s.

- [ ] **Step 1: Branch off live main** — as Task 2 Step 1.

- [ ] **Step 2: Vendor the probe + wrapper + workflow** — as Task 2 Step 2 (set `CONFORMANCE_NAME`, `MCP_PORT`).

- [ ] **Step 3: RED — add the failing construction guard**

```python
import inspect
from <pkg> import server_manager

def test_mcp_app_is_rooted_stateless_json() -> None:
    src = inspect.getsource(server_manager)   # module-level: covers class methods
    assert 'http_app(path="/")' not in src, "must bake the mcp_path, not path='/'"
    assert "stateless_http=True" in src
    assert "json_response=True" in src
    assert 'mount("/"' in src, "mount the MCP app at root"
```

Run it; expected FAIL.

- [ ] **Step 4: GREEN — apply the path + mode fix**

genereviews (`genereview_link/server_manager.py`):

```python
# line ~340 — before
mcp_app = self.mcp.http_app(path="/")
# after
mcp_app = self.mcp.http_app(path=config.mcp_path, stateless_http=True, json_response=True)

# line ~353 — before
self.app.mount(config.mcp_path, mcp_app)
# after
self.app.mount("/", mcp_app)
```

Confirm `config.mcp_path` resolves in that method (it is the `create_mcp_server(self, app, config)` config); if the mount lives in a method without `config`, read `mcp_path` from the same settings/config object that method already holds. The host routes (`/`, `/health`, `/metrics` added in `_add_utility_endpoints`) are registered before the mount — verify they still resolve (they keep precedence under `mount("/")`).

stringdb (`stringdb_link/server_manager.py`):

```python
# line ~58 — before
mcp_http_app = mcp_app.http_app(path="/")
# after
mcp_http_app = mcp_app.http_app(path=settings.mcp_path, stateless_http=True, json_response=True)

# line ~68 — before
app.mount(settings.mcp_path, mcp_http_app)
# after
app.mount("/", mcp_http_app)
```

Run the guard test; expected PASS.

- [ ] **Step 5: Ensure `/health` returns `{status, version, transport}`** — as Task 2 Step 5.

- [ ] **Step 6: Live probe via docker** — as Task 2 Step 6. Pay attention to `GET /mcp does not 307` and `POST /mcp does not 307` PASSing now.

- [ ] **Step 7: Full local CI, commit, PR** — as Task 2 Step 7.

---

### Task 4 (template, ×8): Conformant repos — add the gate only

These already pass the transport/session/serverInfo contract on `main`. No `server_manager` change — add the regression gate and confirm `/health` carries `{status, version, transport}` (add the two fields if missing, as found in Task 1 Step 4).

**Per-repo table:**

| # | Repo | serverInfo.name | docker host port | tier |
|---|------|-----------------|------------------|------|
| 4a | autopvs1-link | `autopvs1-link` | 8000 | stateless |
| 4b | clingen-link | `clingen-link` | verify | stateless |
| 4c | clinvar-link | `clinvar-link` | verify | stateless |
| 4d | gnomad-link | `gnomad-link` | verify | stateless |
| 4e | litvar-link | `litvar-link` | 8000 | stateless |
| 4f | pubtator-link | `pubtator-link` | verify (needs postgres: `docker compose -p docker up -d`) | stateless |
| 4g | spliceailookup-link | `spliceailookup-link` | verify | stateless |
| 4h | vep-link | `vep-link` | verify | stateless |

> litvar's transport fix lives on its `main`; if a feature branch (`fix/litvar-docker-mcp-unified`) is still open, rebase/confirm `main` is the conformant state before branching. pubtator needs its postgres — the conformance.yml must bring up the full compose project (`-p docker`).

**Files (per row):** `tests/conformance/{__init__.py,conformance.py,test_transport_v1.py}`, `.github/workflows/conformance.yml`; optionally the `/health` handler for `{version,transport}`.

- [ ] **Step 1: Branch off live main** — as Task 2 Step 1 (`feat/mcp-conformance-gate`).

- [ ] **Step 2: Vendor the probe + wrapper + workflow** — as Task 2 Step 2.

- [ ] **Step 3: Confirm `/health` carries `{status, version, transport}`**

Probe locally and add the two fields if missing (TDD the health unit test):

```bash
make docker-build && make docker-up   # pubtator: docker compose -p docker up -d
for i in $(seq 1 30); do curl -fsS "http://127.0.0.1:<PORT>/health" && break || sleep 2; done
CONFORMANCE_MCP_URL=http://127.0.0.1:<PORT> CONFORMANCE_NAME=<name> CONFORMANCE_TIER=stateless \
  uv run pytest tests/conformance/test_transport_v1.py -v
make docker-down
```

Expected: PASS. If only the `/health version/transport` checks fail, add those fields (Task 2 Step 5 pattern) and re-probe.

- [ ] **Step 4: Full local CI, commit, PR**

```bash
make ci-local
git add tests/ .github/workflows/conformance.yml <any health file>
git commit -m "$(printf 'test(mcp): add MCP Transport Standard v1 conformance gate\n\nVendor the v1 probe + conformance.yml; server already serves stateless+JSON\n/mcp. Locks in the contract against regressions.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
git push -u origin feat/mcp-conformance-gate
gh pr create --fill --title "test(mcp): add MCP Transport Standard v1 conformance gate" \
  --body "$(printf 'Locks in already-conformant stateless+JSON /mcp behaviour with the vendored v1 probe.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)')"
```

---

### Task 5 (×1): Router transport migration + router conformance profile

The router (`genefoundry`) builds `http_app(path="/")` and mounts at `GF_MCP_PATH` (`server.py:91,127`) → it 307s on `POST /mcp` like the path-split backends. Unlike backends, its `build_app()` mounts MCP in-process, so the router is probed in-process (no docker). Apply the gtex pattern and add the **router profile** gate.

**Files:**
- Modify: `genefoundry_router/server.py:91,127`
- Create: `tests/conformance/__init__.py`, `tests/conformance/test_router_transport_v1.py`
- Verify (do not break): `tests/integration/test_auth_contract.py` (the `401` contract).

**Interfaces:**
- Consumes: `run_probe` (Task 1, same repo) and `build_app(settings, registry, proxy_targets=...)`.
- Produces: a router passing the **router profile** (`serverInfo.name=genefoundry`, aggregate `/health`, `401` when auth on).

- [ ] **Step 1: Branch**

```bash
cd /home/bernt-popp/development/genefoundry-router
git fetch -q origin && git switch -c feat/router-transport-stateless origin/main
```

- [ ] **Step 2: RED — in-process router conformance test**

Create `tests/conformance/test_router_transport_v1.py`. Build the app the way the integration suite does (reuse its registry/proxy fixtures — grep `tests/integration/conftest.py` for the existing `build_app`/`proxy_targets` fixture) and drive `run_probe` against it over `httpx.ASGITransport`. Because `run_probe` opens its own `httpx.Client`, expose the app via an in-process loopback: run it with `uvicorn` on an ephemeral port in a thread, or assert the transport directly. Minimal, fixture-light form using an ASGI-backed client:

```python
"""Router profile conformance (in-process)."""

from __future__ import annotations

import httpx
import pytest

from genefoundry_router.conformance import run_probe  # reuse the checks where possible
from genefoundry_router.server import build_app
from tests.integration.conftest import build_test_settings, build_test_registry  # adjust to actual names


@pytest.fixture
def router_base_url(...):  # start build_app(...) under uvicorn on 127.0.0.1:0, yield the URL
    ...


def test_router_profile(router_base_url: str) -> None:
    report = run_probe(router_base_url, expected_name="genefoundry", tier="stateless")
    assert report.conformant, "\n  ".join(report.failed)
```

Run it; expected FAIL on `POST /mcp does not 307` (current mount produces the redirect) and the stateless session check.

> If wiring a uvicorn-on-ephemeral-port fixture is heavier than the existing integration harness, instead assert the specific contract directly with the suite's existing in-process client: `resp = client.post("/mcp", ...)` and assert `resp.status_code != 307`, no `Mcp-Session-Id`, `serverInfo.name == "genefoundry"`. Reuse whatever app/client fixture `tests/integration/` already provides — do not invent a new harness.

- [ ] **Step 3: GREEN — bake the path + mount at root**

```python
# server.py:91 — before
mcp_app = server.http_app(path="/")  # ASGI sub-app; its lifespan must be entered
# after
mcp_app = server.http_app(
    path=settings.GF_MCP_PATH, stateless_http=True, json_response=True
)

# server.py:127 — before
app.mount(settings.GF_MCP_PATH, mcp_app)
# after
app.mount("/", mcp_app)
```

The health/metrics routes (118–119) and the auth well-known routes (124–126) are registered before the mount and keep precedence under `mount("/")`. Run the conformance test; expected PASS.

- [ ] **Step 4: Verify stateless does not break proxying or auth**

```bash
make ci-local        # full unit + integration suite, incl. test_auth_contract.py
```

Expected: all green. If `stateless_http=True` breaks proxy mounts or the composed lifespan (the router enters `mcp_app.lifespan` in `build_app`), STOP and use systematic-debugging. Fallback only after investigation: keep the path fix (`mount("/")`, baked path) and document a justified escape-hatch (§7) for the router — but the target is stateless; do not silently revert.

- [ ] **Step 5: Confirm the auth 401 contract**

Confirm `tests/integration/test_auth_contract.py` still asserts an unauthenticated `/mcp` call → `401` + `WWW-Authenticate`. If the route move changed the path, update the test to hit `/mcp` (root-mounted) and re-run.

- [ ] **Step 6: Commit + PR**

```bash
git add genefoundry_router/server.py tests/conformance/
git commit -m "$(printf 'feat(mcp): router serves /mcp stateless+JSON, no 307 (Transport Standard v1)\n\nBake GF_MCP_PATH into http_app and mount at root so POST /mcp returns 200\ndirectly. Add the in-process router-profile conformance test. Auth 401 and\nproxy behaviour unchanged (ci-local green).\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
git push -u origin feat/router-transport-stateless
gh pr create --fill --title "feat(mcp): router adopts Transport Standard v1 (stateless+JSON, no 307)" \
  --body "$(printf 'Router serves /mcp directly (no 307), stateless+JSON, router conformance profile green; auth 401 + proxy unchanged.\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)')"
```

---

## Phase C

### Task 6: Fleet-wide live sweep + servers.yaml/.env verify + mark Adopted

After the Phase B PRs merge, prove the whole fleet is conformant end-to-end and close the standard.

**Files:**
- Modify: `docs/MCP-TRANSPORT-STANDARD-v1.md` (adoption table → `adopted`).
- Verify: `servers.yaml` / `.env` URL targets, fleet conformance memory.

- [ ] **Step 1: Sweep every backend's live container**

For each `-link` repo: pull merged `main`, `make docker-build && make docker-up`, wait for `/health`, run the probe in small batches (≤4 stacks — Docker's default address pools exhaust beyond ~18; `docker network prune` between batches). pubtator: `docker compose -p docker up -d`.

```bash
cd /home/bernt-popp/development/genefoundry-router
make conformance MCP_URL=http://127.0.0.1:<PORT> NAME=<ns>-link TIER=stateless
```

Record pass/fail per repo. Every backend MUST exit 0.

- [ ] **Step 2: Sweep the router edge**

Run the router locally (`make run` or docker) and probe with the router profile, both unauthenticated (expect the `401` contract when auth is enabled) and authenticated:

```bash
make conformance MCP_URL=http://127.0.0.1:<ROUTER_PORT> NAME=genefoundry TIER=stateless
```

- [ ] **Step 3: Verify routing targets**

Confirm `servers.yaml` / `.env` backend URLs target `/mcp` (no trailing slash) and that the router federates each migrated backend. Run the router's own integration suite (`make test-integration`) against the live fleet if available, else the in-memory proxy suite.

- [ ] **Step 4: Mark the standard Adopted + record the result**

Flip each repo's row in `docs/MCP-TRANSPORT-STANDARD-v1.md` to `adopted` with the sweep date. Commit on a short branch + PR. Update the `fleet-docker-mcp-validation` memory: gap #1 (MCP path/transport split) RESOLVED — fleet on Transport Standard v1, conformance probe gating CI.

```bash
git switch -c docs/mcp-transport-standard-adopted
git add docs/MCP-TRANSPORT-STANDARD-v1.md
git commit -m "$(printf 'docs(standard): mark MCP Transport Standard v1 adopted fleet-wide\n\nLive conformance sweep green across all -link backends + the router.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
git push -u origin docs/mcp-transport-standard-adopted
gh pr create --fill
```

---

## Self-Review

**1. Spec coverage** (design spec §1–§11 → task):
- §3.1 single endpoint / no 307 → Task 3 (genereviews/stringdb), Task 5 (router); already-true verified by Task 4. ✓
- §3.2 stateless+JSON → Task 2, Task 3, Task 5; probe asserts no `Mcp-Session-Id`. ✓
- §3.3 protocol-version 400 → probe Step (Task 1) `unsupported MCP-Protocol-Version → 400`. ✓
- §3.4 Accept/JSON content-type → probe `init Content-Type application/json`. ✓
- §3.5 Origin/edge → enforced at the router (existing `add_origin_validation`); per AGENTS.md backends sit behind the proxy, so the probe does not fail backends on Origin in v1. **Documented in the standard doc (Task 1 Step 6); not a per-backend code task.** ✓ (intentional scope boundary)
- §4 serverInfo (`<ns>-link` / router `genefoundry`) → probe `serverInfo.name` check; router carve-out in Task 5 + probe `is_router`. ✓
- §5 health (`{status,version,transport}` / router aggregate) → Task 2/3/4 Step 5; probe gates backend keys, skips for router. ✓
- §6 application-session pattern → documented in the standard (Task 1 Step 6); no code change (pubtator already implements it). ✓
- §7 escape hatch → documented; no v1 server qualifies. ✓
- §8 conformance probe + DoD (backend & router profiles) → Task 1 (probe), `conformance.yml` gate, router profile in Task 5. ✓
- §9 rollout buckets (8 conformant / 11 mode-only / 2 path+mode / router) → Tasks 4 / 2 / 3 / 5 exactly. ✓ (21 backends + router accounted for)

**2. Placeholder scan:** the only deliberate "verify/`<PORT>`/`<repo>`/`<pkg>`" markers are per-repo instantiation parameters in the fan-out tables (each row names the concrete repo, package, line, and serverInfo name) — not content gaps. The probe, wrapper, workflow, Makefile target, and every server_manager edit are shown in full. Router Step 2 names the exact fixture-reuse instruction rather than inventing a harness.

**3. Type/name consistency:** `run_probe(base_url, *, expected_name, tier, require_auth=False) -> Report` and the CLI flags (`--name/--tier/--require-auth`) are identical in Task 1 (definition), the `test_transport_v1.py` wrapper, the `conformance.yml` env (`CONFORMANCE_NAME/TIER/MCP_URL`), the `make conformance` target, and Tasks 4/5/6 (consumers). `Report.conformant`/`.failed` used consistently. The transport edit (`stateless_http=True, json_response=True`, `mount("/")`) is the same string the in-process guard tests assert.
