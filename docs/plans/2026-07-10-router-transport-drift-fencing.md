# Router Transport, Runtime Drift, and Untrusted-Content Fencing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close router issues #31 and #36 by enforcing one outer Host/Origin boundary, checking the reviewed tool catalog at startup and every poll, defining a structural untrusted-text contract with a PubTator reference implementation, and rolling FastMCP 3.4.4 strict transport guards across standalone fleet backends.

**Architecture:** The router owns one outer ASGI Host/Origin guard covering MCP, health, metrics, and OAuth routes; its mounted FastMCP app explicitly disables the new inner guard to prevent conflicting policy. A packaged, reviewed baseline is compared with the same post-normalization catalog used for reachability and search, with changed definitions failing startup and surfacing critically on polls while additions/removals degrade without automatic re-pinning. Returned external prose is fenced by backends as typed `untrusted_text` data; the router treats those subtrees as opaque and never legitimizes embedded tool-like fields.

**Tech Stack:** Python 3.12+, FastMCP 3.4.4, MCP Python SDK, FastAPI/Starlette ASGI middleware, Pydantic Settings, structlog, prometheus-client, pytest/pytest-asyncio, uv, Ruff, mypy, Docker Compose.

---

## Global Constraints

- Streamable HTTP only; do not add SSE.
- The router remains the edge-auth boundary and never forwards caller authorization to backends.
- One router-level Host/Origin guard protects every outer FastAPI route. Pass `host_origin_protection=False` to the router's mounted FastMCP app; do not double-guard `/mcp`.
- Standalone `*-link` backends use FastMCP 3.4.4's native strict guard because they do not have the router's outer FastAPI policy layer.
- Host allowlists are explicit hostnames/IP literals. No `*` wildcard is accepted by GeneFoundry configuration.
- Missing `Origin` remains valid for non-browser clients. A present Origin must be explicitly allowed.
- Runtime drift never performs a second fleet network sweep and never writes or refreshes the reviewed baseline.
- `changed` definitions are the high-signal rug-pull class. Startup enforcement fails before
  accepting traffic. Polling changes and additions are quarantined with
  `ToolTransformConfig(enabled=False)`; unaffected reviewed tools keep serving.
- `removed` remains visible operational drift but does not fail router startup.
- Structural fencing is defense in depth, not a sandbox. The typed JSON object is authoritative; text delimiters are advisory.
- Unicode normalization is NFC, not NFKC. Preserve tab, LF, and CR; remove the precisely enumerated control/zero-width/bidi characters in Task 9.
- No production merge, image publication, deployment, baseline re-pin, or issue closure occurs without the explicit gates in Tasks 12 and 14.

## File and interface map

### Router repository

- Modify `pyproject.toml` and `uv.lock`: require FastMCP `>=3.4.4,<4.0.0` and package the baseline JSON.
- Modify `genefoundry_router/config.py`: add `GF_ALLOWED_HOSTS`, `GF_DRIFT_MODE`, and `GF_DRIFT_BASELINE`.
- Replace `genefoundry_router/security.py`: one pure-ASGI `HostOriginValidationMiddleware` plus configuration validation.
- Modify `genefoundry_router/server.py`: explicitly disable FastMCP's inner guard and call the shared normalized-catalog drift stage at startup/poll.
- Modify `genefoundry_router/normalization.py`: return the post-normalization catalog instead of making callers enumerate it independently.
- Modify `genefoundry_router/drift.py`: fingerprint the full reviewed definition and compare qualified normalized tools.
- Create `genefoundry_router/runtime_drift.py`: load the packaged baseline, classify runtime drift, expose state, health, logs, and metrics.
- Move `ci/fleet-baseline.json` to `genefoundry_router/data/fleet-baseline.json`: one canonical reviewed runtime/CI artifact.
- Modify `.github/workflows/drift.yml`, `Makefile`, and `scripts/snapshot_fleet.py`: consume/update the canonical packaged baseline.
- Modify `genefoundry_router/hints.py`: stop recursion at typed untrusted-content objects.
- Create `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md`: normative untrusted-content amendment and adoption criteria; frozen v1 remains unchanged.
- Create `docs/conformance/untrusted-text-inventory.yml`: machine-readable fleet tool/field inventory, limits, compatibility, and test vectors.
- Add/modify tests listed in Tasks 1-8 and 10.

### PubTator reference repository

- Create `../pubtator-link/pubtator_link/mcp/untrusted_content.py`: sanitizer, provenance, typed wrapper, and raw digest.
- Modify `../pubtator-link/pubtator_link/models/publication_passages.py`: make `PublicationPassage.text` a typed untrusted object.
- Modify `../pubtator-link/pubtator_link/mcp/service_adapters.py`: fence passage text at the MCP serialization boundary.
- Add `../pubtator-link/tests/unit/mcp/test_untrusted_content.py` and update passage adapter/tool contract tests.

### Fleet transport rollout

For each of the 21 repositories, modify `pyproject.toml`, `uv.lock`, its package-specific `config.py` and `server_manager.py` from Task 11's table, `.env.example`, and create `tests/unit/test_host_origin_guard.py`.

---

### Task 1: Upgrade the Router to FastMCP 3.4.4 and Pin the Guard API

**Files:**
- Modify: `pyproject.toml:25-40`
- Modify: `uv.lock`
- Create: `tests/unit/test_fastmcp_transport_guard_api.py`

- [ ] **Step 1: Write the failing import/signature contract test**

```python
import inspect

import fastmcp
from fastmcp import FastMCP


def test_fastmcp_344_host_origin_guard_api_is_available() -> None:
    assert tuple(map(int, fastmcp.__version__.split(".")[:3])) >= (3, 4, 4)
    parameters = inspect.signature(FastMCP.http_app).parameters
    assert "host_origin_protection" in parameters
    assert "allowed_hosts" in parameters
    assert "allowed_origins" in parameters
```

- [ ] **Step 2: Run the test and verify the installed 3.4.2 API fails**

Run: `uv run pytest tests/unit/test_fastmcp_transport_guard_api.py -q`

Expected: FAIL because FastMCP is `3.4.2` and `http_app` lacks `host_origin_protection`.

- [ ] **Step 3: Raise the dependency floor and refresh the lock**

Change the dependency to:

```toml
"fastmcp>=3.4.4,<4.0.0",
```

Run: `uv lock --upgrade-package fastmcp && uv sync --group dev`

Expected: `uv.lock` resolves FastMCP 3.4.4 or newer within 3.x.

- [ ] **Step 4: Run the focused test and import smoke**

Run: `uv run pytest tests/unit/test_fastmcp_transport_guard_api.py -q && uv run python -c 'from fastmcp import FastMCP; import inspect; assert "host_origin_protection" in inspect.signature(FastMCP.http_app).parameters'`

Expected: 1 passed and exit 0.

- [ ] **Step 5: Commit the dependency/API contract**

```bash
git add pyproject.toml uv.lock tests/unit/test_fastmcp_transport_guard_api.py
git commit -m "build: require FastMCP 3.4.4 transport guard API"
```

---

### Task 2: Add One Outer Host/Origin Guard

**Files:**
- Modify: `genefoundry_router/config.py:57-100`
- Replace: `genefoundry_router/security.py`
- Modify: `genefoundry_router/server.py:95-142`
- Modify: `.env.example:20-25`
- Modify: `.env.docker.example:55-66`
- Replace: `tests/unit/test_security.py`
- Modify: `tests/integration/test_origin_app.py`
- Create: `tests/integration/test_host_origin_app.py`

- [ ] **Step 1: Write settings tests for CSV hosts and wildcard rejection**

Add to `tests/unit/test_settings.py`:

```python
import pytest
from pydantic import ValidationError

from genefoundry_router.config import RouterSettings


def test_allowed_hosts_csv_is_split() -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_HOSTS="genefoundry.org,localhost,127.0.0.1,::1",
    )
    assert settings.GF_ALLOWED_HOSTS == ["genefoundry.org", "localhost", "127.0.0.1", "::1"]


def test_allowed_hosts_rejects_wildcard() -> None:
    with pytest.raises(ValidationError, match="GF_ALLOWED_HOSTS must not contain wildcard"):
        RouterSettings(_env_file=None, GF_ALLOWED_HOSTS="*")
```

- [ ] **Step 2: Write middleware unit tests before implementation**

Replace `tests/unit/test_security.py` with tests covering one combined guard:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.security import add_host_origin_validation


def _client(hosts: list[str], origins: list[str]) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    add_host_origin_validation(app, allowed_hosts=hosts, allowed_origins=origins)
    return TestClient(app)


def test_allowed_host_with_port_passes() -> None:
    assert _client(["genefoundry.org"], []).get(
        "/health", headers={"host": "GeneFoundry.org:443"}
    ).status_code == 200


def test_disallowed_host_returns_421() -> None:
    response = _client(["genefoundry.org"], []).get(
        "/health", headers={"host": "rebind.example"}
    )
    assert response.status_code == 421
    assert response.json() == {"error": "misdirected request"}


def test_ipv6_loopback_host_passes() -> None:
    assert _client(["::1"], []).get(
        "/health", headers={"host": "[::1]:8000"}
    ).status_code == 200


def test_absent_origin_passes_for_non_browser_client() -> None:
    assert _client(["testserver"], []).get("/health").status_code == 200


def test_present_disallowed_origin_returns_403() -> None:
    response = _client(["testserver"], ["https://claude.ai"]).get(
        "/health", headers={"origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json() == {"error": "forbidden origin"}
```

- [ ] **Step 3: Run the new tests and verify they fail**

Run: `uv run pytest tests/unit/test_settings.py tests/unit/test_security.py -q`

Expected: FAIL because `GF_ALLOWED_HOSTS` and `add_host_origin_validation` do not exist.

- [ ] **Step 4: Add settings and the pure-ASGI combined guard**

Add to `RouterSettings` beside `GF_ALLOWED_ORIGINS`:

```python
GF_ALLOWED_HOSTS: Annotated[list[str], NoDecode] = []

@field_validator("GF_ALLOWED_HOSTS", "GF_ALLOWED_ORIGINS", mode="before")
@classmethod
def _split_csv_allowlist(cls, value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value

@field_validator("GF_ALLOWED_HOSTS")
@classmethod
def _reject_host_wildcards(cls, value: list[str]) -> list[str]:
    if any("*" in item for item in value):
        raise ValueError("GF_ALLOWED_HOSTS must not contain wildcard entries")
    return value
```

Implement `HostOriginValidationMiddleware` in `security.py` as pure ASGI. Normalize Host with `ipaddress.ip_address`, bracket-aware IPv6 handling, lowercase DNS labels, and optional port removal. Validate Host first, then Origin. Empty host list preserves compatibility by skipping Host validation; empty Origin list rejects every present Origin.

The public interface is exact:

```python
def add_host_origin_validation(
    app: FastAPI,
    allowed_hosts: list[str],
    allowed_origins: list[str],
) -> None:
    app.add_middleware(
        HostOriginValidationMiddleware,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
```

- [ ] **Step 5: Wire only the outer guard and explicitly disable the inner guard**

Change `server.http_app` to:

```python
mcp_app = server.http_app(
    path=settings.GF_MCP_PATH,
    stateless_http=True,
    json_response=True,
    host_origin_protection=False,
)
```

Replace the current `add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)` call with:

```python
add_host_origin_validation(
    app,
    allowed_hosts=settings.GF_ALLOWED_HOSTS,
    allowed_origins=settings.GF_ALLOWED_ORIGINS,
)
```

Keep `CorrelationIdMiddleware` last-added and therefore outermost.

- [ ] **Step 6: Add whole-app route and middleware-order tests**

In `tests/integration/test_host_origin_app.py`, build the real app and assert:

```python
def test_host_guard_covers_health_mcp_metrics_and_correlation_id(gnomad_fake) -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_HOSTS=["genefoundry.test"],
        GF_METRICS_TOKEN="metrics-secret",
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    with TestClient(build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})) as client:
        for method, path in (("get", "/health"), ("get", "/metrics"), ("post", "/mcp/")):
            response = getattr(client, method)(path, headers={"host": "evil.example"}, json={} if method == "post" else None)
            assert response.status_code == 421
            assert response.headers["x-request-id"]
```

Also inspect `app.user_middleware` and assert exactly one `HostOriginValidationMiddleware`; assert the mounted FastMCP application was created with inner protection disabled via the Task 1 signature-compatible call.

- [ ] **Step 7: Document loopback and public values**

Add:

```dotenv
# Optional comma-separated Host allowlist. Loopback: localhost,127.0.0.1,::1.
# Public/proxied: include the GF_PUBLIC_BASE_URL hostname and the explicit health-check Host.
GF_ALLOWED_HOSTS=localhost,127.0.0.1,::1
```

For `.env.docker.example`, use:

```dotenv
GF_ALLOWED_HOSTS=genefoundry.org
```

Update the Docker healthcheck in Task 12 to send `Host: genefoundry.org`; do not add `localhost` to the public allowlist merely for health checking.

- [ ] **Step 8: Run focused and full router checks**

Run: `uv run pytest tests/unit/test_settings.py tests/unit/test_security.py tests/integration/test_origin_app.py tests/integration/test_host_origin_app.py -q`

Expected: all tests pass.

Run: `make ci-local`

Expected: format, Ruff, LOC, mypy, unit, and integration checks pass.

- [ ] **Step 9: Commit the router transport boundary**

```bash
git add .env.example .env.docker.example genefoundry_router/config.py genefoundry_router/security.py genefoundry_router/server.py tests/unit/test_settings.py tests/unit/test_security.py tests/integration/test_origin_app.py tests/integration/test_host_origin_app.py
git commit -m "feat(security): enforce one outer Host and Origin boundary (#36)"
```

---

### Task 3: Ship One Canonical Reviewed Baseline

**Files:**
- Move: `ci/fleet-baseline.json` -> `genefoundry_router/data/fleet-baseline.json`
- Create: `genefoundry_router/data/__init__.py`
- Modify: `pyproject.toml:65-67`
- Modify: `genefoundry_router/config.py`
- Modify: `genefoundry_router/cli.py:395-433`
- Modify: `.github/workflows/drift.yml:42-49`
- Modify: `Makefile:120-130`
- Modify: `scripts/snapshot_fleet.py:76-82`
- Modify: `tests/unit/test_ci_fleet_baseline.py`
- Create: `tests/unit/test_packaged_baseline.py`

- [ ] **Step 1: Write the packaged-resource test**

```python
from importlib.resources import files

from genefoundry_router.devtools.fakes import load_manifest


def test_reviewed_baseline_is_packaged_and_parseable() -> None:
    baseline = files("genefoundry_router.data").joinpath("fleet-baseline.json")
    with baseline.open("rb") as handle:
        manifest = load_manifest(handle)
    assert len(manifest.backends) == 21
    assert all(backend.tools for backend in manifest.backends.values())
```

Adjust `load_manifest` to accept `Path | BinaryIO` if the failing test proves it currently accepts only `Path`; keep this interface in `devtools/fakes.py` and cover both inputs.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/unit/test_packaged_baseline.py -q`

Expected: FAIL because `genefoundry_router.data` does not exist.

- [ ] **Step 3: Move the baseline and make it package data**

Run:

```bash
mkdir -p genefoundry_router/data
touch genefoundry_router/data/__init__.py
git mv ci/fleet-baseline.json genefoundry_router/data/fleet-baseline.json
```

Add to the wheel configuration:

```toml
[tool.hatch.build.targets.wheel.force-include]
"genefoundry_router/data/fleet-baseline.json" = "genefoundry_router/data/fleet-baseline.json"
```

Add settings:

```python
GF_DRIFT_MODE: Literal["off", "warn", "enforce"] = "warn"
GF_DRIFT_BASELINE: str | None = None
```

Add `bundled_baseline()` in `runtime_drift.py` in Task 5; until then make CLI's default resolve through `importlib.resources.as_file` rather than a repository-relative test fixture.

- [ ] **Step 4: Point CI and snapshot commands at the canonical artifact**

Change the workflow drift command to omit `--manifest`, so it uses the packaged default. Change `make snapshot-baseline` and `scripts/snapshot_fleet.py` defaults to `genefoundry_router/data/fleet-baseline.json`.

- [ ] **Step 5: Verify source and built-wheel presence**

Run:

```bash
uv run pytest tests/unit/test_ci_fleet_baseline.py tests/unit/test_packaged_baseline.py tests/unit/test_cli_drift.py -q
rm -rf dist
uv build --wheel
unzip -l dist/*.whl | grep 'genefoundry_router/data/fleet-baseline.json'
```

Expected: tests pass and `unzip` prints exactly one packaged baseline path.

- [ ] **Step 6: Commit the canonical baseline move**

```bash
git add genefoundry_router/data pyproject.toml genefoundry_router/config.py genefoundry_router/cli.py genefoundry_router/devtools/fakes.py .github/workflows/drift.yml Makefile scripts/snapshot_fleet.py tests/unit/test_ci_fleet_baseline.py tests/unit/test_packaged_baseline.py tests/unit/test_cli_drift.py
git commit -m "feat(drift): ship the reviewed fleet baseline with the router (#36)"
```

---

### Task 4: Fingerprint the Full Security-Relevant Tool Definition

**Files:**
- Modify: `genefoundry_router/drift.py`
- Modify: `genefoundry_router/devtools/fakes.py:53-63`
- Modify: `scripts/snapshot_fleet.py:40-49`
- Modify: `tests/unit/test_drift.py`
- Modify: `tests/unit/test_cli_drift.py`

- [ ] **Step 1: Write failing fingerprint sensitivity tests**

Add parameterized cases asserting that each of these changes alters the digest: `description`, `inputSchema`, `outputSchema`, `annotations`, and `execution`. Also assert JSON key ordering does not alter it.

```python
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", "tampered"),
        ("inputSchema", {"type": "object", "properties": {"x": {"type": "string"}}}),
        ("outputSchema", {"type": "object", "properties": {"result": {"type": "string"}}}),
        ("annotations", {"readOnlyHint": False}),
        ("execution", {"taskSupport": "required"}),
    ],
)
def test_fingerprint_covers_security_relevant_definition(field: str, value: object) -> None:
    base = ToolDefinition(name="get_gene", description="safe")
    changed = base.model_copy(update={field: value})
    assert tool_fingerprint(base) != tool_fingerprint(changed)
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/unit/test_drift.py -q`

Expected: FAIL because `ToolDefinition` does not exist and the current digest omits three fields.

- [ ] **Step 3: Add the exact definition model and fingerprint interface**

```python
class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(default_factory=dict)
    outputSchema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None


def tool_fingerprint(tool: ToolDefinition) -> str:
    payload = tool.model_dump(mode="json", by_alias=True, exclude_none=False)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

Extend `ToolSpec` and the live snapshot to preserve `execution`. Preserve backwards parsing for baselines where it is absent by defaulting it to `None`.

- [ ] **Step 4: Qualify baseline names before comparison**

Implement:

```python
def manifest_fingerprints(manifest: Manifest) -> dict[str, str]:
    return {
        f"{namespace}_{tool.name}": tool_fingerprint(
            ToolDefinition(
                **{
                    **tool.model_dump(mode="json"),
                    "name": f"{namespace}_{tool.name}",
                }
            )
        )
        for namespace, backend in manifest.backends.items()
        for tool in backend.tools
    }
```

The current production registry has no active transform blocks, so baseline qualification is deterministic. Add a guard test that fails if a future `servers.yaml` transform is added without explicit baseline normalization support.

- [ ] **Step 5: Run drift tests**

Run: `uv run pytest tests/unit/test_drift.py tests/unit/test_cli_drift.py tests/unit/test_servers_yaml.py -q`

Expected: all pass.

- [ ] **Step 6: Commit the stronger fingerprint**

```bash
git add genefoundry_router/drift.py genefoundry_router/devtools/fakes.py scripts/snapshot_fleet.py tests/unit/test_drift.py tests/unit/test_cli_drift.py tests/unit/test_servers_yaml.py
git commit -m "feat(drift): fingerprint complete normalized tool definitions (#36)"
```

---

### Task 5: Run Drift at Startup and Every Poll

**Files:**
- Create: `genefoundry_router/runtime_drift.py`
- Modify: `genefoundry_router/normalization.py:62-99`
- Modify: `genefoundry_router/server.py:80-183`
- Modify: `genefoundry_router/observability.py`
- Modify: `genefoundry_router/discovery.py:14-50`
- Create: `tests/unit/test_runtime_drift.py`
- Modify: `tests/integration/test_lifespan.py`
- Create: `tests/integration/test_polling_drift.py`

- [ ] **Step 1: Write policy unit tests**

```python
def test_changed_fails_startup_in_enforce_mode() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "old"}, mode="enforce")
    with pytest.raises(StartupError, match="changed tool definition"):
        guard.evaluate({"gnomad_get_gene": "new"}, phase="startup", unreachable=set())


def test_added_and_removed_degrade_without_startup_failure() -> None:
    guard = RuntimeDriftGuard(
        pinned={"gnomad_old": "a"},
        mode="enforce",
    )
    report = guard.evaluate({"gnomad_new": "b"}, phase="startup", unreachable=set())
    assert report.added == ["gnomad_new"]
    assert report.removed == ["gnomad_old"]
    assert guard.degraded is True
    assert guard.quarantined == frozenset({"gnomad_new"})


def test_unreachable_namespace_is_excluded_from_both_sides() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "a"}, mode="enforce")
    report = guard.evaluate({}, phase="startup", unreachable={"gnomad"})
    assert report.has_drift is False
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/unit/test_runtime_drift.py -q`

Expected: FAIL because `RuntimeDriftGuard` does not exist.

- [ ] **Step 3: Implement runtime policy and packaged baseline loading**

Implement the runtime policy with this concrete core:

```python
class RuntimeDriftGuard:
    def __init__(self, pinned: dict[str, str], mode: DriftMode) -> None:
        self.pinned = dict(pinned)
        self.mode = mode
        self.last_report = DriftReport(added=[], removed=[], changed=[])
        self.degraded = False
        self.quarantined: frozenset[str] = frozenset()

    def evaluate(
        self,
        current: dict[str, str],
        *,
        phase: Literal["startup", "poll"],
        unreachable: set[str],
    ) -> DriftReport:
        def reachable(definitions: dict[str, str]) -> dict[str, str]:
            return {
                name: digest
                for name, digest in definitions.items()
                if name.split("_", 1)[0] not in unreachable
            }

        report = detect_drift(reachable(current), reachable(self.pinned))
        self.last_report = report
        self.degraded = report.has_drift
        self.quarantined = frozenset(report.added) | (
            frozenset(report.changed) if phase == "poll" else frozenset()
        )
        if self.mode == "enforce" and phase == "startup" and report.changed:
            names = ", ".join(report.changed)
            raise StartupError(f"changed tool definition: {names}")
        return report


def load_runtime_guard(settings: RouterSettings) -> RuntimeDriftGuard:
    if settings.GF_DRIFT_MODE == "off":
        return RuntimeDriftGuard({}, "off")
    if settings.GF_DRIFT_BASELINE is not None:
        manifest = load_manifest(Path(settings.GF_DRIFT_BASELINE))
    else:
        resource = files("genefoundry_router.data").joinpath("fleet-baseline.json")
        with as_file(resource) as path:
            manifest = load_manifest(path)
    return RuntimeDriftGuard(manifest_fingerprints(manifest), settings.GF_DRIFT_MODE)


def _model_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=False)
    return dict(value)


def definitions_from_tools(tools: Sequence[Tool]) -> dict[str, ToolDefinition]:
    return {
        tool.name: ToolDefinition(
            name=tool.name,
            description=tool.description or "",
            inputSchema=tool.parameters or {},
            outputSchema=tool.output_schema,
            annotations=_model_dict(tool.annotations),
            execution=_model_dict(tool.execution),
        )
        for tool in tools
    }


def fingerprint_definitions(
    definitions: dict[str, ToolDefinition],
) -> dict[str, str]:
    return {name: tool_fingerprint(tool) for name, tool in definitions.items()}
```

`load_runtime_guard` uses `GF_DRIFT_BASELINE` when set; otherwise it opens
`genefoundry_router.data/fleet-baseline.json` through `importlib.resources`. `off` skips loading.
`warn` logs every class. `enforce` raises `StartupError` only for `changed` during startup. Added
tools and polling changes enter `quarantined`; poll evaluation does not kill the polling task.

- [ ] **Step 4: Make normalization return the shared catalog**

Change the signature to:

```python
async def apply_normalizations(server: FastMCP, registry: list[BackendDef]) -> list[Tool]:
```

After installing transforms, return `list(await server._list_tools())`. On total enumeration failure, return `[]` and let reachability/drift classify namespaces as unreachable. Do not call `_list_tools()` again in `_seed_reachability`; change it to accept `tools: Sequence[Tool]`.

- [ ] **Step 5: Wire the same stage into startup and polling**

In `build_app`, construct one guard before the lifespan. Add:

```python
async def _refresh_catalog(phase: Literal["startup", "poll"]) -> None:
    tools = await apply_normalizations(server, registry)
    unreachable = _seed_reachability(registry, tools)
    report = guard.evaluate(
        fingerprint_definitions(definitions_from_tools(tools)),
        phase=phase,
        unreachable=unreachable,
    )
    quarantine = {
        name: ToolTransformConfig(enabled=False)
        for name in guard.quarantined
    }
    if quarantine:
        server.add_transform(ToolTransform(quarantine))
```

Startup order is `_refresh_catalog("startup")`, then `apply_tool_search`. Polling calls
`_refresh_catalog("poll")`. Apply quarantine before tool-search indexing so quarantined names are
neither listed, searchable, nor callable. The search transform already reindexes lazily when its
catalog hash changes; do not create a second search transform per poll.

- [ ] **Step 6: Expose drift in health and metrics**

Add aggregate health fields:

```json
"drift": {"status": "ok|degraded", "changed": [], "added": [], "removed": []}
```

Add Prometheus gauges `genefoundry_drift_changed`, `genefoundry_drift_added`, `genefoundry_drift_removed`, and `genefoundry_drift_last_check_timestamp_seconds`. Logs contain qualified tool names only, never descriptions or schemas.

- [ ] **Step 7: Write lifespan and poll tests**

Test startup match, startup changed failure, additions/removals degraded, unreachable exclusion,
added-tool list/search/call quarantine, poll-changed list/search/call quarantine, unaffected-tool
calls, and poll task survival. In `test_polling_drift.py`, use a mutable fake catalog and a
0.01-second interval; wait with a bounded async event rather than a fixed long sleep.

- [ ] **Step 8: Run focused tests and performance assertion**

Run: `uv run pytest tests/unit/test_runtime_drift.py tests/integration/test_lifespan.py tests/integration/test_polling_drift.py tests/unit/test_metrics.py -q`

Expected: all pass.

Add a spy assertion that one refresh performs no more than the normalization-required enumerations and never opens an HTTP client outside the mounted proxy providers.

- [ ] **Step 9: Run router CI and commit**

Run: `make ci-local`

Expected: all gates pass and every Python module remains under 600 LOC.

```bash
git add genefoundry_router/runtime_drift.py genefoundry_router/normalization.py genefoundry_router/server.py genefoundry_router/observability.py genefoundry_router/discovery.py tests/unit/test_runtime_drift.py tests/integration/test_lifespan.py tests/integration/test_polling_drift.py tests/unit/test_metrics.py
git commit -m "feat(drift): check normalized catalog at startup and poll (#36)"
```

---

### Task 6: Add Container and Operator Configuration for #36

**Files:**
- Modify: `docker/Dockerfile:37-45`
- Modify: `docker/docker-compose.yml:21-33`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `.env.example`
- Modify: `.env.docker.example`
- Modify: `README.md`
- Modify: `tests/unit/docker/test_compose.py`
- Modify: `tests/unit/test_dockerfile_digest_pinned.py`
- Create: `tests/unit/test_runtime_security_docs.py`

- [ ] **Step 1: Write failing container/config assertions**

Assert the production image contains `genefoundry_router/data/fleet-baseline.json`, Compose passes `GF_ALLOWED_HOSTS`, `GF_DRIFT_MODE`, and `GF_DRIFT_BASELINE`, and the healthcheck sends the configured public Host.

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/unit/docker/test_compose.py tests/unit/test_dockerfile_digest_pinned.py tests/unit/test_runtime_security_docs.py -q`

Expected: FAIL on missing runtime settings and health Host.

- [ ] **Step 3: Configure production enforcement without weakening local development**

Set in the production overlay/example:

```yaml
environment:
  GF_ALLOWED_HOSTS: ${GF_ALLOWED_HOSTS:?set the public router hostname}
  GF_DRIFT_MODE: enforce
```

Change the healthcheck to:

```yaml
test: ["CMD", "sh", "-c", "curl -f -H \"Host: $${GF_HEALTHCHECK_HOST}\" http://localhost:8000/health"]
```

Set `GF_HEALTHCHECK_HOST=genefoundry.org` in the production example. Local compose keeps `GF_DRIFT_MODE=warn` and `GF_ALLOWED_HOSTS=localhost,127.0.0.1,::1`.

- [ ] **Step 4: Document drift responses and re-pin discipline**

Document exact operator actions: changed startup failure means review live definitions; additions/removals mean degraded service; `make snapshot-baseline` is allowed only after code review of the diff; never re-pin merely to restore green status.

- [ ] **Step 5: Verify Docker and router checks**

Run: `make docker-prod-config && make docker-npm-config && make ci-local`

Expected: both Compose renders succeed with required test environment values and router CI passes.

- [ ] **Step 6: Commit deployment configuration**

```bash
git add docker .env.example .env.docker.example README.md tests/unit/docker/test_compose.py tests/unit/test_dockerfile_digest_pinned.py tests/unit/test_runtime_security_docs.py
git commit -m "docs(security): deploy Host and runtime drift enforcement (#36)"
```

---

### Task 7: Ratify the Untrusted-Content Contract and Fleet Inventory

**Files:**
- Create: `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md`
- Create: `docs/conformance/untrusted-text-inventory.yml`
- Modify: `tests/unit/test_docs_presence.py`
- Create: `tests/unit/test_untrusted_content_standard.py`

- [ ] **Step 1: Write the failing normative-doc test**

```python
def test_untrusted_content_standard_is_normative_and_structural() -> None:
    text = Path("docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md").read_text()
    for required in (
        '"kind": "untrusted_text"',
        '"raw_sha256"',
        '"provenance"',
        "Unicode NFC",
        "defense in depth",
        "MUST NOT duplicate",
    ):
        assert required in text
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/unit/test_untrusted_content_standard.py -q`

Expected: FAIL because the standard has no fencing section.

- [ ] **Step 3: Add the normative schema and sanitation table**

Add this authoritative shape:

```json
{
  "kind": "untrusted_text",
  "text": "NFC-normalized external content",
  "provenance": {
    "source": "pubtator",
    "record_id": "PMID:12345678",
    "retrieved_at": "2026-07-10T12:00:00Z"
  },
  "raw_sha256": "64 lowercase hexadecimal characters"
}
```

Specify removal of C0 `U+0000-U+0008`, `U+000B-U+000C`, `U+000E-U+001F`; C1 `U+007F-U+009F`; zero-width `U+200B-U+200D`, `U+2060`, `U+FEFF`; bidi controls `U+202A-U+202E`, `U+2066-U+2069`. Preserve tab, LF, CR, ordinary Unicode, and scientific symbols. State that structural typing and provenance are primary; mirrored delimiters are advisory defense in depth.

- [ ] **Step 4: Create the explicit 21-backend inventory**

Create YAML inventory entries with `backend`, `tool`, `json_pointers`, `max_text_bytes`,
`max_objects`, `compatibility`, and `test_vector`. Seed every backend below; source audits replace
the sentinel tool/pointer only when the PR includes exact source evidence:

```yaml
- backend: pubtator
  tool: search_literature
  json_pointers: [/data/items/*/text]
  max_text_bytes: 2097152
  max_objects: 128
  compatibility: additive-v1.1
  test_vector: hostile-literature-v1
```

Add equivalent source-verified rows for AutoPVS1, ClinGen, ClinVar, GenCC, GeneReviews, gnomAD,
GTEx, HGNC, HPO, LitVar, MaveDB, MetaDome, MGI, Mondo, Orphanet, PanelApp, SpliceAI Lookup,
STRING, UniProt, and VEP. A backend with no externally sourced free-text field records an explicit
`classification: no-untrusted-text` row and evidence path rather than being omitted.

- [ ] **Step 5: Run doc tests and commit**

Run: `uv run pytest tests/unit/test_docs_presence.py tests/unit/test_untrusted_content_standard.py -q`

Expected: all pass.

```bash
git add docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md docs/conformance/untrusted-text-inventory.yml tests/unit/test_docs_presence.py tests/unit/test_untrusted_content_standard.py
git commit -m "docs(security): define structural untrusted-content fencing (#31)"
```

---

### Task 8: Make Router Hint Rewriting Opaque Below Untrusted Text

**Files:**
- Modify: `genefoundry_router/hints.py:46-66`
- Modify: `tests/unit/test_hints.py`
- Modify: `tests/integration/test_hint_rewriting.py`

- [ ] **Step 1: Write the malicious-subtree regression test**

```python
def test_untrusted_text_is_opaque_to_tool_reference_rewriting() -> None:
    payload = {
        "next_commands": [{"tool": "search_genes"}],
        "evidence": {
            "kind": "untrusted_text",
            "text": "external text",
            "tool": "delete_everything",
            "nested": {"fallback_tool": "search_genes"},
        },
    }
    count = rewrite_tool_refs(payload, "clingen", {"clingen"})
    assert count == 1
    assert payload["next_commands"][0]["tool"] == "clingen_search_genes"
    assert payload["evidence"]["tool"] == "delete_everything"
    assert payload["evidence"]["nested"]["fallback_tool"] == "search_genes"
```

- [ ] **Step 2: Run and verify the current recursive implementation fails**

Run: `uv run pytest tests/unit/test_hints.py::test_untrusted_text_is_opaque_to_tool_reference_rewriting -q`

Expected: FAIL because the nested `fallback_tool` is rewritten.

- [ ] **Step 3: Add the minimal opaque-subtree guard**

At the start of the dictionary branch:

```python
if isinstance(obj, dict) and obj.get("kind") == "untrusted_text":
    return 0
```

Do not add fuzzy key detection or inspect the `text` value.

- [ ] **Step 4: Verify direct and synthetic call paths**

Run: `uv run pytest tests/unit/test_hints.py tests/integration/test_hint_rewriting.py -q`

Expected: all existing hint behavior passes and untrusted subtrees remain byte-for-byte unchanged.

- [ ] **Step 5: Commit the router exclusion**

```bash
git add genefoundry_router/hints.py tests/unit/test_hints.py tests/integration/test_hint_rewriting.py
git commit -m "fix(security): keep untrusted text opaque to hint rewriting (#31)"
```

---

### Task 9: Implement the PubTator Reference Fence

**Repository:** `/home/bernt-popp/development/pubtator-link`

**Files:**
- Create: `pubtator_link/mcp/untrusted_content.py`
- Modify: `pubtator_link/models/publication_passages.py:53-63`
- Modify: `pubtator_link/mcp/service_adapters.py:249-277`
- Create: `tests/unit/mcp/test_untrusted_content.py`
- Modify: `tests/unit/mcp/test_mcp_service_adapters.py`
- Modify: `tests/unit/mcp/test_mcp_facade.py`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml` and `uv.lock` for the required major version bump

- [ ] **Step 1: Write sanitizer/model tests first**

```python
def test_fence_normalizes_and_removes_forbidden_controls() -> None:
    raw = "Cafe\u0301\x00\u200b\u202e\nBRCA1"
    fenced = fence_untrusted_text(raw, source="pubtator", record_id="PMID:1")
    assert fenced.kind == "untrusted_text"
    assert fenced.text == "Caf\u00e9\nBRCA1"
    assert fenced.raw_sha256 == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert fenced.provenance.source == "pubtator"
    assert fenced.provenance.record_id == "PMID:1"


def test_fence_preserves_tabs_newlines_and_scientific_symbols() -> None:
    raw = "p.Gly12Asp\tΔG = −1.2 kcal/mol\r\n"
    assert fence_untrusted_text(raw, source="pubtator", record_id="PMID:2").text == raw
```

- [ ] **Step 2: Run and verify failure**

Run: `uv run pytest tests/unit/mcp/test_untrusted_content.py -q`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement typed fencing**

```python
class UntrustedTextProvenance(BaseModel):
    source: str
    record_id: str
    retrieved_at: datetime


class UntrustedText(BaseModel):
    kind: Literal["untrusted_text"] = "untrusted_text"
    text: str
    provenance: UntrustedTextProvenance
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def fence_untrusted_text(raw: str, *, source: str, record_id: str) -> UntrustedText:
    normalized = unicodedata.normalize("NFC", raw)
    clean = "".join(char for char in normalized if ord(char) not in FORBIDDEN_CODEPOINTS)
    return UntrustedText(
        text=clean,
        provenance=UntrustedTextProvenance(
            source=source,
            record_id=record_id,
            retrieved_at=datetime.now(UTC),
        ),
        raw_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
```

Build `FORBIDDEN_CODEPOINTS` exactly from Task 7's enumerated ranges.

- [ ] **Step 4: Fence publication passages at the MCP boundary**

Change `PublicationPassage.text` from `str` to `UntrustedText`. In `get_publication_passages_impl`, transform each service passage immediately before `model_dump`, using `source=passage.source` and `record_id=f"PMID:{passage.pmid}#{passage.passage_id}"`. Keep internal retrieval/ranking services on plain strings so search performance is unchanged.

- [ ] **Step 5: Assert structured and mirrored MCP results agree**

Call `get_publication_passages` through an in-process FastMCP client. Assert `structured_content` contains typed fenced text, the JSON `TextContent` mirror contains the same object, raw external text is not duplicated elsewhere, and the tool output schema exposes `kind` as the literal `untrusted_text`.

- [ ] **Step 6: Run PubTator focused and full checks**

Run: `uv run pytest tests/unit/mcp/test_untrusted_content.py tests/unit/mcp/test_mcp_service_adapters.py tests/unit/mcp/test_mcp_facade.py -q`

Expected: all pass.

Run: `make ci-local`

Expected: all PubTator gates pass.

- [ ] **Step 7: Bump the breaking version and commit**

Record that `get_publication_passages.text` changed from string to typed object. Apply the repo's single-source major-version procedure.

```bash
git add pubtator_link/mcp/untrusted_content.py pubtator_link/models/publication_passages.py pubtator_link/mcp/service_adapters.py tests/unit/mcp/test_untrusted_content.py tests/unit/mcp/test_mcp_service_adapters.py tests/unit/mcp/test_mcp_facade.py CHANGELOG.md pyproject.toml uv.lock
git commit -m "feat!: fence PubTator passage text as untrusted data (#31)"
```

---

### Task 10: Add a Router-to-Reference Contract Test

**Files:**
- Create: `tests/integration/test_untrusted_content_contract.py`
- Modify: `tests/discoverability/catalog.json` after reviewed snapshot refresh

- [ ] **Step 1: Write an in-process reference contract test**

Create a fake PubTator backend returning a typed untrusted passage with embedded `tool` and `fallback_tool` keys. Call it directly and through synthetic `call_tool`; assert both channels retain the typed subtree unchanged and preserve provenance/digest.

- [ ] **Step 2: Run and verify failure before Task 8 is present**

Run: `uv run pytest tests/integration/test_untrusted_content_contract.py -q`

Expected: FAIL on mutated embedded tool references when run against the pre-Task-8 router.

- [ ] **Step 3: Refresh only reviewed tool metadata**

After the PubTator version is deployed to the test endpoint, run `make snapshot-baseline` and inspect the exact PubTator `outputSchema` diff. Do not accept unrelated backend changes. Refresh `tests/discoverability/catalog.json` with the existing snapshot command only after the output-schema change is approved.

- [ ] **Step 4: Run router CI and commit the contract**

Run: `make ci-local`

Expected: all router tests pass.

```bash
git add tests/integration/test_untrusted_content_contract.py tests/discoverability/catalog.json
git commit -m "test(security): pin PubTator untrusted-content federation contract (#31)"
```

Do not commit a production baseline re-pin in this task; Task 14 gates it after deployment review.

---

### Task 11: Roll FastMCP 3.4.4 Strict Guards Across the Fleet

**Repositories and exact server files:**

| Repository | Config module | Server module |
|---|---|---|
| `autopvs1-link` | `autopvs1_link/config.py` | `autopvs1_link/server_manager.py` |
| `clingen-link` | `clingen_link/config.py` | `clingen_link/server_manager.py` |
| `clinvar-link` | `clinvar_link/config.py` | `clinvar_link/server_manager.py` |
| `gencc-link` | `gencc_link/config.py` | `gencc_link/server_manager.py` |
| `genereviews-link` | `genereview_link/config.py` | `genereview_link/server_manager.py` |
| `gnomad-link` | `gnomad_link/config.py` | `gnomad_link/server_manager.py` |
| `gtex-link` | `gtex_link/config.py` | `gtex_link/server_manager.py` |
| `hgnc-link` | `hgnc_link/config.py` | `hgnc_link/server_manager.py` |
| `hpo-link` | `hpo_link/config.py` | `hpo_link/server_manager.py` |
| `litvar-link` | `litvar_link/config.py` | `litvar_link/server_manager.py` |
| `mavedb-link` | `mavedb_link/config.py` | `mavedb_link/server_manager.py` |
| `metadome-link` | `metadome_link/config.py` | `metadome_link/server_manager.py` |
| `mgi-link` | `mgi_link/config.py` | `mgi_link/server_manager.py` |
| `mondo-link` | `mondo_link/config.py` | `mondo_link/server_manager.py` |
| `orphanet-link` | `orphanet_link/config.py` | `orphanet_link/server_manager.py` |
| `panelapp-link` | `panelapp_link/config.py` | `panelapp_link/server_manager.py` |
| `pubtator-link` | `pubtator_link/config.py` | `pubtator_link/server_manager.py` |
| `spliceailookup-link` | `spliceailookup_link/config.py` | `spliceailookup_link/server_manager.py` |
| `stringdb-link` | `stringdb_link/config.py` | `stringdb_link/server_manager.py` |
| `uniprot-link` | `uniprot_link/config.py` | `uniprot_link/server_manager.py` |
| `vep-link` | `vep_link/config.py` | `vep_link/server_manager.py` |

**Files in every repository:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: config and server modules from the table
- Modify: `.env.example`
- Create: `tests/unit/test_host_origin_guard.py`

- [ ] **Step 1: Create one branch per repository**

Run in each clean repository:

```bash
git switch -c fix/fastmcp-344-strict-host-origin
```

Expected: a new local branch; do not push.

- [ ] **Step 2: Add the same failing API and behavior contract in every repository**

Each `tests/unit/test_host_origin_guard.py` asserts FastMCP >=3.4.4, inspects that the repository's constructed HTTP app uses `host_origin_protection=True`, accepts its configured public backend hostname and loopback test Host, returns 421 for `evil.example`, accepts absent Origin, and returns 403 for a present disallowed Origin.

Use the repository's existing `create_app` factory with `TestClient`; do not test FastMCP internals in isolation.

- [ ] **Step 3: Run each test and verify failure**

Run per repository: `uv run pytest tests/unit/test_host_origin_guard.py -q`

Expected: FAIL because FastMCP is below 3.4.4 or the server does not pass strict guard arguments.

- [ ] **Step 4: Apply the fleet configuration contract**

Each settings model exposes its prefixed environment equivalent of:

```python
allowed_hosts: list[str] = ["localhost", "127.0.0.1", "::1"]
allowed_origins: list[str] = []
```

Reject `*` in allowed hosts. Preserve each repository's existing CORS configuration separately; CORS response headers do not replace request Host/Origin validation.

Each `http_app` call becomes:

```python
mcp.http_app(
    path="/mcp",
    stateless_http=True,
    json_response=True,
    host_origin_protection=True,
    allowed_hosts=settings.allowed_hosts,
    allowed_origins=settings.allowed_origins,
)
```

Adapt only the local settings-object name and existing path/json arguments; keep `host_origin_protection=True` exact.

- [ ] **Step 5: Upgrade and lock FastMCP in each repository**

Change the FastMCP constraint to `>=3.4.4,<4.0.0`, then run:

```bash
uv lock --upgrade-package fastmcp
uv sync --group dev
uv run pytest tests/unit/test_host_origin_guard.py -q
make ci-local
```

Expected: the focused test and repository CI pass.

- [ ] **Step 6: Commit atomically in each repository**

Run this exact package-root mapping from each repository root:

```bash
case "$(basename "$PWD")" in
  autopvs1-link) package=autopvs1_link ;;
  clingen-link) package=clingen_link ;;
  clinvar-link) package=clinvar_link ;;
  gencc-link) package=gencc_link ;;
  genereviews-link) package=genereview_link ;;
  gnomad-link) package=gnomad_link ;;
  gtex-link) package=gtex_link ;;
  hgnc-link) package=hgnc_link ;;
  hpo-link) package=hpo_link ;;
  litvar-link) package=litvar_link ;;
  mavedb-link) package=mavedb_link ;;
  metadome-link) package=metadome_link ;;
  mgi-link) package=mgi_link ;;
  mondo-link) package=mondo_link ;;
  orphanet-link) package=orphanet_link ;;
  panelapp-link) package=panelapp_link ;;
  pubtator-link) package=pubtator_link ;;
  spliceailookup-link) package=spliceailookup_link ;;
  stringdb-link) package=stringdb_link ;;
  uniprot-link) package=uniprot_link ;;
  vep-link) package=vep_link ;;
  *) printf 'unsupported repository: %s\n' "$(basename "$PWD")" >&2; exit 2 ;;
esac
git add pyproject.toml uv.lock .env.example tests/unit/test_host_origin_guard.py
git add "$package/config.py" "$package/server_manager.py"
git commit -m "fix(security): enable FastMCP 3.4.4 strict Host/Origin guard"
```

- [ ] **Step 7: Record the 21 green commit SHAs in the inventory**

Update `docs/UNTRUSTED-CONTENT-INVENTORY.md` with a separate transport-adoption table containing repository, branch, commit SHA, `make ci-local` result, and deployment state. Every row must have a real SHA before Task 12 begins.

---

### Task 12: Local Cross-Repository Verification Gate

**Files:**
- Modify: `docs/UNTRUSTED-CONTENT-INVENTORY.md`
- No production configuration changes

- [ ] **Step 1: Prove every worktree is clean and based on current origin/main**

For the router and all 21 backends, run `git fetch origin`, `git status --short`, and `git rev-list --left-right --count origin/main...HEAD`. Stop if a repository contains unrelated changes or its branch is missing current upstream changes.

- [ ] **Step 2: Run router verification**

Run:

```bash
cd /home/bernt-popp/development/genefoundry-router
make ci-local
make test-cov
make docker-build
GF_ALLOWED_HOSTS=genefoundry.org GF_HEALTHCHECK_HOST=genefoundry.org make docker-prod-config
```

Expected: all commands exit 0; coverage is at least 70%; image build includes the baseline.

- [ ] **Step 3: Run the fleet conformance probe against local strict apps**

For each backend, start its local test app, send allowed/disallowed Host and Origin requests to `/mcp`, `/health`, and any OAuth-independent root routes, and record results. The expected matrix is allowed Host/no Origin -> normal route status; disallowed Host -> 421; allowed Host/disallowed Origin -> 403.

- [ ] **Step 4: Re-run all 21 repository CI targets**

Run `make ci-local` in every backend repository. Record the timestamp and commit SHA in the transport-adoption table. Do not accept a partial green fleet.

- [ ] **Step 5: Review performance evidence**

Compare router startup wall time before/after with the same local fake fleet over 20 runs. The median regression must be under 5% or 100 ms, whichever is larger. Confirm runtime drift opens no extra backend sessions beyond the existing catalog harvest.

- [ ] **Step 6: Commit only the verified inventory evidence**

```bash
cd /home/bernt-popp/development/genefoundry-router
git add docs/UNTRUSTED-CONTENT-INVENTORY.md
git commit -m "docs(security): record verified fleet transport guard rollout"
```

---

### Task 13: Open Reviewable Pull Requests Without Deploying

**Remote mutation gate:** Stop here unless the operator explicitly authorizes pushes and PR creation.

- [ ] **Step 1: Recheck every branch immediately before push**

Require clean worktrees, green CI, no force push, and no commits outside the issue scope.

- [ ] **Step 2: Push and open draft PRs after authorization**

Push each branch with `git push -u origin <branch>` and open a draft PR. Router PR title: `security: Host/Origin guard, runtime drift, and untrusted-content contract (#31, #36)`. Backend PR title: `security: enable FastMCP 3.4.4 strict Host/Origin guard`.

- [ ] **Step 3: Require remote checks**

Each PR must pass quality, typecheck, tests, dependency review, CodeQL, container scan, and SBOM checks present in that repository. Do not merge a PR with skipped required checks.

- [ ] **Step 4: Merge in dependency order after authorization**

Merge order: FastMCP dependency/API contracts; backend strict guards; router outer guard/runtime drift in warn mode; PubTator fencing; router fencing contract. Production enforcement remains gated by Task 14.

---

### Task 14: Deployment, Baseline Re-Pin, and Issue Closure Gate

**Production mutation gate:** Requires explicit operator approval, current backups, rollback image digests, and a maintenance window.

- [ ] **Step 1: Deploy backend strict guards before router enforcement**

Deploy backends behind the reverse proxy with their exact public Host allowlists. Verify router-to-backend MCP initialization, `/health`, and tool listing for all 21 namespaces.

- [ ] **Step 2: Deploy PubTator fencing and inspect its schema drift**

Verify `get_publication_passages` returns typed untrusted text and unchanged passage counts/latency. Confirm only the reviewed output-schema/version changes appear in drift.

- [ ] **Step 3: Re-pin only reviewed definitions**

Run `make snapshot-baseline`, inspect every JSON diff, and reject unrelated changes. Commit the reviewed baseline alone:

```bash
git add genefoundry_router/data/fleet-baseline.json
git commit -m "chore(drift): pin reviewed Host/fencing fleet definitions"
```

- [ ] **Step 4: Deploy router in warn mode**

Set the public Host allowlist and healthcheck Host. Verify `/mcp`, OAuth discovery/callback, `/health`, and `/metrics` through the public proxy. Confirm disallowed Host returns 421 with `X-Request-ID`; disallowed Origin returns 403.

- [ ] **Step 5: Observe one complete poll interval**

Require drift metrics at zero, no unexpected additions/removals, no backend reachability regressions, and no material startup/call latency increase.

- [ ] **Step 6: Enable production drift enforcement**

Set `GF_DRIFT_MODE=enforce`, restart once, and verify startup succeeds against the packaged reviewed baseline. Roll back to the prior image/config if startup fails for an unreviewed change; never auto-re-pin.

- [ ] **Step 7: Close issues only from evidence**

Close #36 after public Host/Origin tests, startup enforcement, poll detection, packaged baseline, and production metrics are verified. Close #31 only after the standard, PubTator reference implementation, router opaque-subtree behavior, and CI contract are merged and deployed. Update fleet tracking issues with the 21 PRs and deployment evidence.

---

## Final verification checklist

- [ ] `make ci-local`, `make test-cov`, `make docker-build`, `make docker-prod-config`, and `make docker-npm-config` pass in the router.
- [ ] Every changed Python module is under the 600-LOC budget.
- [ ] The wheel and production image contain exactly one canonical reviewed baseline.
- [ ] The router has exactly one outer Host/Origin guard and passes `host_origin_protection=False` to FastMCP.
- [ ] Each standalone backend passes `host_origin_protection=True` using FastMCP >=3.4.4.
- [ ] Startup and poll drift use the post-normalization catalog and do not perform an extra network sweep.
- [ ] Changed, added, removed, unreachable, and partial-harvest cases have distinct tests and observability.
- [ ] No code path writes or automatically refreshes the drift baseline.
- [ ] PubTator external passage text is typed, normalized, fenced, and provenance-bearing in structured and mirrored results.
- [ ] Router hint rewriting never descends into `kind: untrusted_text`.
- [ ] Public OAuth, MCP, health, metrics, and container healthcheck workflows remain usable.
- [ ] Remote pushes, merges, deployments, baseline re-pins, and issue closure occurred only after their explicit gates.
