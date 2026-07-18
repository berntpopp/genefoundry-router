# P0 Policy Boundaries Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PubTator writes reachable only through an authenticated and writer-authorized router, and make every AutoPVS1 external transfer explicit, allowlisted, and disabled by default.

**Architecture:** PubTator separates user authorization at the router from a backend-only service credential; its public fleet profile becomes read-only while a canonical write-tool inventory drives scope checks. AutoPVS1 routes all BGI, Ensembl, health-probe, and redirect traffic through one exact-origin policy, while production settings and logging fail closed independently. Rollout is staged so the router sends a PubTator credential before the backend requires it, and AutoPVS1 production origins are configured before allowlist enforcement is deployed.

**Tech Stack:** Python 3.12+, FastMCP 3.4.4 middleware and `StreamableHttpTransport`, FastAPI/Starlette ASGI middleware, Pydantic Settings 2.x, HTTPX, Docker Compose, pytest, Ruff, mypy, uv.

---

## Global Constraints

- Work in separate branches/worktrees for `pubtator-link`, `autopvs1-link`, and
  `genefoundry-router`; never mix commits across repositories.
- Fetch first and branch from the current `origin/main`. Several fleet worktrees may lag by one
  compatibility commit.
- Follow TDD for every behavioral change: run the named test and see the stated failure before
  implementation, then rerun it and see it pass.
- Run `make ci-local` in every changed repository before pushing. Do not substitute a narrower
  pytest invocation for this handoff gate.
- Keep caller OAuth tokens at the router. `forward_incoming_headers=False` remains invariant;
  the PubTator backend receives only its separately configured service token.
- Keep `/health` unauthenticated for container probes. Never log bearer values, variant IDs,
  HGVS strings, search queries, client IPs, User-Agents, or redirect URLs containing identifiers.
- Preserve research-only disclaimers. Do not describe scraped AutoPVS1 output as authenticated,
  signed, or clinical-grade.
- Update the version from its single source only after behavior is merged and verified. Use a
  minor bump for new configuration/interfaces and a major bump if the PubTator export tool drops
  `export_path` without a compatibility release.
- Obtain Claude Code adversarial review of each security PR after local CI and before merge.

## File Map

### `pubtator-link`

- `pubtator_link/mcp/profiles.py`: canonical `WRITE_TOOLS` and derived profile inventories.
- `pubtator_link/mcp/tools/publications.py`: keep genuinely read-only full-surface tools in the
  readonly profile.
- `pubtator_link/config.py`: secure profile default and backend service-token settings.
- `pubtator_link/security.py`: constant-time `/mcp` service-token ASGI middleware.
- `pubtator_link/server_manager.py`: install backend authentication without protecting health.
- `pubtator_link/mcp/service_adapters.py`: server-generated audit filenames and race-resistant
  file creation.
- `pubtator_link/mcp/tools/review/export.py`: replace caller-controlled paths with `save_to_file`.
- `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`,
  `docker/docker-compose.npm.yml`: explicit local-write versus production-readonly posture.
- `docs/SECURITY.md`, `.env.example`, `.env.docker.example`: operator contract.

### `genefoundry-router`

- `genefoundry_router/registry.py`, `genefoundry_router/config.py`: resolve optional backend
  service-token environment variables.
- `genefoundry_router/composition.py`: inject a backend-only Authorization header.
- `genefoundry_router/authorization.py`: canonical PubTator write-scope middleware.
- `genefoundry_router/server.py`: install write authorization ahead of proxy dispatch.
- `servers.yaml`, `.env.example`, `.env.docker.example`: declare PubTator service credential.
- `docker/.env.patient-data.example`: router profile that intentionally omits AutoPVS1.

### `autopvs1-link`

- `autopvs1_link/config.py`: effective prefixed production settings and egress configuration.
- `autopvs1_link/logging_config.py`: defense-in-depth client metadata redaction.
- `autopvs1_link/api/egress.py`: exact-origin validation and bounded manual redirects.
- `autopvs1_link/api/autopvs1_client.py`: guarded BGI requests.
- `autopvs1_link/api/variant_recoder.py`: guarded Ensembl requests.
- `autopvs1_link/mcp/tools/health_tool.py`: guarded upstream probe.
- `autopvs1_link/mcp/tools/mode_errors.py`: structured `external_egress_disabled` response.
- `docker/docker-compose.prod.yml`, `.env.example`, `docs/configuration.md`: explicit public
  research origins and disabled/patient configuration.

### Task 1: Make PubTator Readonly Inventory Canonical

**Files:**
- Modify: `pubtator_link/mcp/profiles.py`
- Modify: `pubtator_link/mcp/tools/publications.py`
- Test: `tests/unit/mcp/test_mcp_profiles.py`
- Test: `tests/unit/mcp/test_review_tool_inventory.py`

- [ ] **Step 1: Write the failing inventory tests**

Add these assertions to `tests/unit/mcp/test_mcp_profiles.py`:

```python
from pubtator_link.mcp.profiles import WRITE_TOOLS


EXPECTED_WRITE_TOOLS = {
    "index_review_evidence",
    "ground_question",
    "record_review_context",
    "stage_research_session",
    "review_quickstart",
    "add_evidence_certainty",
    "submit_text_annotation",
    "export_review_audit_bundle",
}


def test_write_tool_inventory_is_exact() -> None:
    assert set(WRITE_TOOLS) == EXPECTED_WRITE_TOOLS


def test_readonly_preserves_full_surface_read_tools() -> None:
    full_names = _tool_names("full")
    readonly_names = _tool_names("readonly")
    assert EXPECTED_WRITE_TOOLS <= full_names
    assert readonly_names == full_names - EXPECTED_WRITE_TOOLS
    assert {
        "get_publication_annotations",
        "get_pmc_annotations",
        "build_topic_literature_map",
    } <= readonly_names
```

The inventory is source-backed, not inferred from names. Before freezing it, inspect the tool
annotations and implementations in `mcp/tools/review/research.py` (stage/review quickstart and
grounding), `mcp/tools/review/retrieval.py` (review context),
`mcp/tools/review/evidence_certainty.py` (certainty writes), `mcp/tools/review/indexes.py`
(evidence indexing), `mcp/tools/text_annotations.py` (remote job submission), and the audit export
implementation. Add a test that the registered tools carrying `readOnlyHint=False` are exactly
`EXPECTED_WRITE_TOOLS`; a mismatch fails rather than silently widening the hosted surface.

Change the default-profile assertion to:

```python
def test_normalize_mcp_profile_defaults_to_readonly() -> None:
    assert normalize_mcp_profile(None) == "readonly"
    assert normalize_mcp_profile("") == "readonly"
```

- [ ] **Step 2: Run the tests and verify red state**

Run:

```bash
uv run pytest tests/unit/mcp/test_mcp_profiles.py tests/unit/mcp/test_review_tool_inventory.py -q
```

Expected: collection fails because `WRITE_TOOLS` is not defined, and the old default/profile
inventory assertions disagree with the new contract.

- [ ] **Step 3: Define one write inventory and derive readonly tools**

In `pubtator_link/mcp/profiles.py`, replace the default and manual exclusion with:

```python
DEFAULT_MCP_PROFILE: MCPToolProfile = "readonly"

WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "index_review_evidence",
        "ground_question",
        "record_review_context",
        "stage_research_session",
        "review_quickstart",
        "add_evidence_certainty",
        "submit_text_annotation",
        "export_review_audit_bundle",
    }
)

READONLY_TOOLS: tuple[str, ...] = tuple(
    name for name in (*LEAN_TOOLS, *FULL_ONLY_TOOLS) if name not in WRITE_TOOLS
)
```

In `pubtator_link/mcp/tools/publications.py`, change both conditions that currently read
`if profile == "full":` around `get_publication_annotations`, `build_topic_literature_map`, and
`get_pmc_annotations` to:

```python
if profile in ("full", "readonly"):
```

Those are the only two line replacements in this file; do not change any decorated handler or
move `submit_text_annotation` into either block.

- [ ] **Step 4: Run focused and full profile tests**

Run:

```bash
uv run pytest tests/unit/mcp/test_mcp_profiles.py tests/unit/mcp/test_review_tool_inventory.py -q
```

Expected: PASS, with the exact eight-tool write inventory excluded from readonly. The completeness
equality proves every non-write tool in the full profile remains available.

- [ ] **Step 5: Commit the profile boundary**

```bash
git add pubtator_link/mcp/profiles.py pubtator_link/mcp/tools/publications.py \
  tests/unit/mcp/test_mcp_profiles.py tests/unit/mcp/test_review_tool_inventory.py
git commit -m "fix(security): make hosted PubTator profile read-only"
```

### Task 2: Require a PubTator Backend Service Credential

**Files:**
- Create: `pubtator_link/security.py`
- Modify: `pubtator_link/config.py`
- Modify: `pubtator_link/server_manager.py`
- Test: `tests/unit/test_service_auth.py`
- Create: `tests/unit/test_security_config.py`

- [ ] **Step 1: Write failing middleware and settings tests**

Create `tests/unit/test_service_auth.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pubtator_link.security import MCPServiceAuthMiddleware


def _client(token: str | None = "service-secret") -> TestClient:
    app = FastAPI()
    if token is not None:
        app.add_middleware(MCPServiceAuthMiddleware, token=token)

    @app.post("/mcp")
    async def mcp() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_mcp_service_auth_rejects_missing_and_wrong_token() -> None:
    client = _client()
    assert client.post("/mcp").status_code == 401
    assert client.post("/mcp/", follow_redirects=False).status_code == 401
    assert client.get("/mcp").status_code == 401
    assert client.delete("/mcp").status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_mcp_service_auth_accepts_backend_token_and_leaves_health_public() -> None:
    client = _client()
    response = client.post("/mcp", headers={"Authorization": "Bearer service-secret"})
    assert response.status_code == 200
    assert client.get("/health").status_code == 200
```

Create `tests/unit/test_security_config.py`:

```python
import pytest
from pydantic import ValidationError

from pubtator_link.config import ServerSettings


def test_write_profile_requires_service_token_or_explicit_local_exception() -> None:
    with pytest.raises(ValidationError, match="write-capable MCP profile requires"):
        ServerSettings(
            _env_file=None,
            mcp_profile="full",
            mcp_service_token=None,
            allow_unauthenticated_writes=False,
        )


def test_readonly_profile_does_not_require_service_token() -> None:
    settings = ServerSettings(
        _env_file=None,
        mcp_profile="readonly",
        mcp_service_token=None,
    )
    assert settings.mcp_profile == "readonly"


def test_unauthenticated_write_exception_is_loopback_only() -> None:
    with pytest.raises(ValidationError, match="loopback"):
        ServerSettings(
            _env_file=None,
            host="0.0.0.0",
            mcp_profile="full",
            mcp_service_token=None,
            allow_unauthenticated_writes=True,
        )
```

- [ ] **Step 2: Run the tests and verify red state**

Run:

```bash
uv run pytest tests/unit/test_service_auth.py tests/unit/test_security_config.py -q
```

Expected: FAIL because `pubtator_link.security`, `mcp_service_token`, and
`allow_unauthenticated_writes` do not exist.

- [ ] **Step 3: Implement constant-time transport authentication**

Create `pubtator_link/security.py`:

```python
from __future__ import annotations

import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class MCPServiceAuthMiddleware:
    def __init__(self, app: ASGIApp, *, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") != "/mcp":
            await self.app(scope, receive, send)
            return
        authorization = Headers(scope=scope).get("authorization", "")
        scheme, separator, credential = authorization.partition(" ")
        valid = (
            separator == " "
            and scheme.lower() == "bearer"
            and secrets.compare_digest(credential, self.token)
        )
        if not valid:
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
```

In `pubtator_link/config.py`, import `model_validator`, default to readonly, and add:

```python
mcp_profile: Literal["lean", "full", "readonly"] = Field(
    default="readonly", description="MCP tool registration profile"
)
mcp_service_token: str | None = Field(
    default=None, description="Router-owned bearer token required by /mcp"
)
allow_unauthenticated_writes: bool = Field(
    default=False,
    description="Explicit loopback-development exception for write-capable profiles",
)

@model_validator(mode="after")
def validate_write_boundary(self) -> "ServerSettings":
    local_exception = self.allow_unauthenticated_writes and self.host in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if self.allow_unauthenticated_writes and not local_exception:
        raise ValueError("unauthenticated writes are restricted to a loopback bind")
    if (
        self.mcp_profile != "readonly"
        and not self.mcp_service_token
        and not local_exception
    ):
        raise ValueError(
            "write-capable MCP profile requires PUBTATOR_LINK_MCP_SERVICE_TOKEN "
            "or the explicit loopback-development exception"
        )
    return self
```

In `pubtator_link/server_manager.py`, install the middleware only when configured:

```python
from pubtator_link.security import MCPServiceAuthMiddleware

# After FastAPI construction and before mounting the MCP app:
if settings.mcp_service_token:
    app.add_middleware(MCPServiceAuthMiddleware, token=settings.mcp_service_token)
```

- [ ] **Step 4: Run authentication tests**

Run:

```bash
uv run pytest tests/unit/test_service_auth.py tests/unit/test_security_config.py -q
```

Expected: PASS; `/mcp` is protected and `/health` remains public.

- [ ] **Step 5: Commit backend authentication**

```bash
git add pubtator_link/security.py pubtator_link/config.py pubtator_link/server_manager.py \
  tests/unit/test_service_auth.py tests/unit/test_security_config.py
git commit -m "feat(security): require PubTator backend service auth"
```

### Task 3: Send the PubTator Service Credential from the Router

**Files:**
- Modify: `genefoundry_router/registry.py`
- Modify: `genefoundry_router/config.py`
- Modify: `genefoundry_router/composition.py`
- Modify: `servers.yaml`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_no_token_passthrough.py`

- [ ] **Step 1: Write failing registry and transport tests**

Add to `tests/unit/test_no_token_passthrough.py`:

```python
def test_backend_service_token_is_injected_without_caller_forwarding() -> None:
    client = make_proxy_client(
        "https://backend.example.org/mcp",
        service_token="router-owned-secret",
    )
    assert client.transport.headers["Authorization"] == "Bearer router-owned-secret"
    assert client.transport.forward_incoming_headers is False
```

Add to `tests/unit/test_config.py`:

```python
def test_registry_resolves_optional_backend_service_token(tmp_path) -> None:
    registry = tmp_path / "servers.yaml"
    registry.write_text(
        """
defaults: {transport: http}
servers:
  - name: pubtator
    namespace: pubtator
    url_env: GF_PUBTATOR_URL
    service_token_env: GF_PUBTATOR_TOKEN
""",
        encoding="utf-8",
    )
    backend = load_registry(
        registry,
        {
            "GF_PUBTATOR_URL": "https://pubtator.example/mcp",
            "GF_PUBTATOR_TOKEN": "service-secret",
        },
    )[0]
    assert backend.service_token == "service-secret"
```

- [ ] **Step 2: Run the tests and verify red state**

Run:

```bash
uv run pytest tests/unit/test_config.py \
  tests/unit/test_no_token_passthrough.py -q
```

Expected: FAIL because registry and proxy interfaces have no service-token fields.

- [ ] **Step 3: Resolve the secret and build an explicit HTTP transport**

Add to `BackendDef` in `genefoundry_router/registry.py`:

```python
service_token_env: str | None = None
service_token: str | None = None
```

In `load_registry` in `genefoundry_router/config.py`, immediately after URL resolution:

```python
backend.url = environ.get(backend.url_env)
if backend.service_token_env is not None:
    backend.service_token = environ.get(backend.service_token_env)
```

In `genefoundry_router/composition.py`, import the installed FastMCP transport and change the
client factory interface:

```python
from fastmcp.client.transports import StreamableHttpTransport


def make_proxy_client(
    target: Any,
    timeout: float | None = None,
    service_token: str | None = None,
) -> ProxyClient:
    if isinstance(target, str) and service_token is not None:
        transport = StreamableHttpTransport(
            target,
            headers={"Authorization": f"Bearer {service_token}"},
        )
        client = ProxyClient(transport) if timeout is None else ProxyClient(transport, timeout=timeout)
    else:
        client = ProxyClient(target) if timeout is None else ProxyClient(target, timeout=timeout)
    transport = getattr(client, "transport", None)
    if transport is not None and hasattr(transport, "forward_incoming_headers"):
        transport.forward_incoming_headers = False
    return client
```

Pass `backend.service_token` in both URL client factories in `build_proxy` and
`_register_via_provider`:

```python
client_factory=lambda: make_proxy_client(
    proxy_target,
    timeout,
    service_token=backend.service_token,
)
```

Change the PubTator entry in `servers.yaml` to include:

```yaml
service_token_env: GF_PUBTATOR_TOKEN
```

Use block form for that entry so the credential field remains reviewable; do not put a token in
YAML.

- [ ] **Step 4: Run registry, transport, and integration tests**

Run:

```bash
uv run pytest tests/unit/test_config.py tests/unit/test_no_token_passthrough.py \
  tests/integration/test_pubtator_no_transform.py -q
```

Expected: PASS, including both the injected service header and caller-header suppression.

- [ ] **Step 5: Commit router service credentials**

```bash
git add genefoundry_router/registry.py genefoundry_router/config.py \
  genefoundry_router/composition.py servers.yaml tests/unit/test_config.py \
  tests/unit/test_no_token_passthrough.py
git commit -m "feat(security): support backend service credentials"
```

### Task 4: Authorize PubTator Writes by Caller Scope

**Files:**
- Create: `genefoundry_router/authorization.py`
- Modify: `genefoundry_router/server.py`
- Test: `tests/unit/test_write_authorization.py`
- Test: `tests/integration/test_write_authorization.py`

- [ ] **Step 1: Write failing middleware tests**

Create `tests/unit/test_write_authorization.py`:

```python
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

import genefoundry_router.authorization as authorization
from genefoundry_router.authorization import WriteAuthorizationMiddleware


@dataclass
class _Context:
    message: object


async def _ok(_context: object) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_pubtator_write_requires_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_index_review_evidence"))
    monkeypatch.setattr(
        authorization,
        "get_access_token",
        lambda: AccessToken(token="x", client_id="c", scopes=["mcp:read"]),
    )
    with pytest.raises(ToolError, match="pubtator:write"):
        await middleware.on_call_tool(context, _ok)


@pytest.mark.asyncio
async def test_pubtator_write_scope_allows_call(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_record_review_context"))
    monkeypatch.setattr(
        authorization,
        "get_access_token",
        lambda: AccessToken(token="x", client_id="c", scopes=["pubtator:write"]),
    )
    assert await middleware.on_call_tool(context, _ok) == "ok"


@pytest.mark.asyncio
async def test_readonly_pubtator_call_needs_no_write_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_search_literature"))
    monkeypatch.setattr(authorization, "get_access_token", lambda: None)
    assert await middleware.on_call_tool(context, _ok) == "ok"
```

- [ ] **Step 2: Run the test and verify red state**

Run:

```bash
uv run pytest tests/unit/test_write_authorization.py -q
```

Expected: import failure because `genefoundry_router.authorization` does not exist.

- [ ] **Step 3: Implement scope enforcement before proxy dispatch**

Create `genefoundry_router/authorization.py`:

```python
from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext

PUBTATOR_WRITE_TOOLS = frozenset(
    {
        "pubtator_index_review_evidence",
        "pubtator_ground_question",
        "pubtator_record_review_context",
        "pubtator_stage_research_session",
        "pubtator_review_quickstart",
        "pubtator_add_evidence_certainty",
        "pubtator_submit_text_annotation",
        "pubtator_export_review_audit_bundle",
    }
)


class WriteAuthorizationMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        name = getattr(context.message, "name", "")
        if name in PUBTATOR_WRITE_TOOLS:
            token = get_access_token()
            scopes = set(token.scopes) if token is not None else set()
            if "pubtator:write" not in scopes:
                raise ToolError("This tool requires the pubtator:write scope")
        return await call_next(context)
```

In `genefoundry_router/server.py`, add it before observability and hint middleware so denied
calls never reach a backend:

```python
from genefoundry_router.authorization import WriteAuthorizationMiddleware

server.add_middleware(WriteAuthorizationMiddleware())
```

The router and PubTator constants intentionally duplicate a cross-repository contract. Add a
release-gate comparison in Task 9 rather than introducing a shared runtime package.

- [ ] **Step 4: Run focused authorization tests**

Run:

```bash
uv run pytest tests/unit/test_write_authorization.py tests/integration/test_auth_contract.py -q
```

Expected: PASS; read-only calls are unchanged and write calls require scope.

- [ ] **Step 5: Commit caller authorization**

```bash
git add genefoundry_router/authorization.py genefoundry_router/server.py \
  tests/unit/test_write_authorization.py
git commit -m "feat(auth): require scope for PubTator writes"
```

### Task 5: Remove PubTator Export Path Races

**Files:**
- Modify: `pubtator_link/mcp/service_adapters.py`
- Modify: `pubtator_link/mcp/tools/review/export.py`
- Test: `tests/unit/mcp/test_mcp_service_adapters.py`
- Test: `tests/unit/mcp/test_mcp_profiles.py`

- [ ] **Step 1: Write failing generated-file tests**

Replace caller-path tests in `tests/unit/mcp/test_mcp_service_adapters.py` with:

```python
@pytest.mark.asyncio
async def test_export_writes_server_generated_leaf_with_private_mode(tmp_path) -> None:
    from pubtator_link.mcp.service_adapters import export_review_audit_bundle_impl

    result = await export_review_audit_bundle_impl(
        service=_FakeReviewAuditBundleService(),
        review_id="rev_123",
        save_to_file=True,
        export_base_dir=str(tmp_path),
    )
    output = Path(result["export_path"])
    assert output.parent == tmp_path.resolve()
    assert output.name.startswith("review-audit-rev_123-")
    assert output.suffix == ".json"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_export_save_is_disabled_without_base_directory() -> None:
    from pubtator_link.mcp.service_adapters import export_review_audit_bundle_impl

    result = await export_review_audit_bundle_impl(
        service=_FakeReviewAuditBundleService(),
        review_id="rev_123",
        save_to_file=True,
        export_base_dir=None,
    )
    assert result["success"] is False
    assert "file export is disabled" in result["error"]["field_errors"][0]["reason"]
```

Import `stat` and `Path` at the top of the test module.

- [ ] **Step 2: Run the tests and verify red state**

Run:

```bash
uv run pytest tests/unit/mcp/test_mcp_service_adapters.py -k 'export_' -q
```

Expected: FAIL because `save_to_file` is not accepted and the adapter still trusts a caller path.

- [ ] **Step 3: Generate and create a leaf beneath an opened directory**

In `pubtator_link/mcp/service_adapters.py`, replace `export_path` with `save_to_file: bool = False`
and use this helper:

```python
import re
import uuid

_SAFE_EXPORT_STEM = re.compile(r"[^A-Za-z0-9_.-]+")


def _write_audit_export(base_dir: str, review_id: str, serialized: str) -> Path:
    base = Path(base_dir).expanduser().resolve(strict=True)
    if not base.is_dir():
        raise OSError("export base is not a directory")
    stem = _SAFE_EXPORT_STEM.sub("_", review_id).strip("._") or "review"
    filename = f"review-audit-{stem}-{uuid.uuid4().hex}.json"
    directory_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        file_fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
        with os.fdopen(file_fd, "w", encoding="utf-8") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(directory_fd)
    return base / filename
```

Use it only when `save_to_file` is true:

```python
if not save_to_file:
    if response_mode == "compact":
        return McpReviewAuditBundleResponse(
            audit_bundle_summary=compact_audit_bundle_summary(bundle_json)
        ).model_dump(mode="json", exclude_none=True)
    return McpReviewAuditBundleResponse(audit_bundle=bundle).model_dump(
        mode="json",
        exclude_none=True,
    )
if export_base_dir is None and settings.review_export_base_dir is None:
    return McpReviewAuditBundleResponse(
        success=False,
        error=_audit_export_path_field_error(
            "file export is disabled; set PUBTATOR_LINK_REVIEW_EXPORT_BASE_DIR"
        ),
    ).model_dump(mode="json", exclude_none=True)
base_dir = export_base_dir or settings.review_export_base_dir
assert base_dir is not None
output_path = _write_audit_export(base_dir, review_id, serialized)
```

In `pubtator_link/mcp/tools/review/export.py`, expose `save_to_file: bool = False`, remove
`export_path`, and pass `save_to_file` to the adapter. This is a schema-breaking change; record a
major version bump unless the current unpublished release policy permits removal.

- [ ] **Step 4: Run export and profile tests**

Run:

```bash
uv run pytest tests/unit/mcp/test_mcp_service_adapters.py \
  tests/unit/mcp/test_mcp_profiles.py -q
```

Expected: PASS; no caller path reaches `open`, and generated files are mode `0600`.

- [ ] **Step 5: Commit export hardening**

```bash
git add pubtator_link/mcp/service_adapters.py pubtator_link/mcp/tools/review/export.py \
  tests/unit/mcp/test_mcp_service_adapters.py tests/unit/mcp/test_mcp_profiles.py
git commit -m "fix(security): remove caller-selected audit export paths"
```

### Task 6: Fix AutoPVS1 Production Settings and PII Logging

**Files:**
- Modify: `autopvs1_link/config.py`
- Modify: `autopvs1_link/logging_config.py`
- Test: `tests/unit/test_config_env_prefix.py`
- Test: `tests/unit/test_logging_redaction.py`
- Test: `tests/unit/test_docker_compose_prod.py`

- [ ] **Step 1: Write failing effective-environment and redaction tests**

Add to `tests/unit/test_config_env_prefix.py`:

```python
def test_prefixed_production_environment_activates_secure_preset() -> None:
    code = """
from autopvs1_link.config import settings
print(settings.environment, settings.debug, settings.logging.level)
"""
    env = os.environ.copy()
    env["AUTOPVS1_LINK_ENVIRONMENT"] = "production"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "production False WARNING"
```

Add to `tests/unit/test_logging_redaction.py`:

```python
def test_redactor_scrubs_client_network_metadata() -> None:
    result = redact_sensitive_fields(
        None,
        "info",
        {
            "event": "Incoming request",
            "client_ip": "203.0.113.7",
            "user_agent": "patient-workstation/1",
        },
    )
    assert result["client_ip"] == "<redacted>"
    assert result["user_agent"] == "<redacted>"
```

Add `os`, `subprocess`, and `sys` imports where missing.

- [ ] **Step 2: Run the tests and verify red state**

Run:

```bash
uv run pytest tests/unit/test_config_env_prefix.py \
  tests/unit/test_logging_redaction.py tests/unit/test_docker_compose_prod.py -q
```

Expected: FAIL: the subprocess prints `development True INFO`, and client metadata survives.

- [ ] **Step 3: Make prefixed environment authoritative and redact client metadata**

Change root settings configuration in `autopvs1_link/config.py`:

```python
model_config = {
    "env_prefix": "AUTOPVS1_LINK_",
    "env_file": ".env",
    "env_file_encoding": "utf-8",
}
```

Add these fields to `_SENSITIVE_FIELDS` in `autopvs1_link/logging_config.py`:

```python
"client_ip",
"user_agent",
```

Retain the production preset that forces `debug=False`, reload off, WARNING, and JSON logs. Do
not rely on log-level suppression as the redaction control.

- [ ] **Step 4: Run the tests and verify green state**

Run:

```bash
uv run pytest tests/unit/test_config_env_prefix.py \
  tests/unit/test_logging_redaction.py tests/unit/test_docker_compose_prod.py -q
```

Expected: PASS, including the clean-process environment assertion.

- [ ] **Step 5: Commit production logging correction**

```bash
git add autopvs1_link/config.py autopvs1_link/logging_config.py \
  tests/unit/test_config_env_prefix.py tests/unit/test_logging_redaction.py \
  tests/unit/test_docker_compose_prod.py
git commit -m "fix(security): activate AutoPVS1 production privacy preset"
```

### Task 7: Add a Central AutoPVS1 Egress Policy

**Files:**
- Create: `autopvs1_link/api/egress.py`
- Modify: `autopvs1_link/config.py`
- Test: `tests/unit/test_egress_policy.py`

- [ ] **Step 1: Write failing exact-origin and redirect tests**

Create `tests/unit/test_egress_policy.py`:

```python
import httpx
import pytest

from autopvs1_link.api.egress import EgressDeniedError, EgressPolicy, guarded_request


def test_policy_denies_by_default_and_matches_exact_origin() -> None:
    disabled = EgressPolicy(mode="disabled", allowed_origins=frozenset())
    with pytest.raises(EgressDeniedError):
        disabled.require_allowed("https://autopvs1.bgi.com/variant/hg38/1-1-A-G")
    policy = EgressPolicy(
        mode="allowlist",
        allowed_origins=frozenset({"https://autopvs1.bgi.com"}),
    )
    policy.require_allowed("https://autopvs1.bgi.com/search")
    for url in (
        "https://evil.autopvs1.bgi.com/search",
        "https://autopvs1.bgi.com.evil.example/search",
        "https://user@autopvs1.bgi.com/search",
        "http://autopvs1.bgi.com/search",
    ):
        with pytest.raises(EgressDeniedError):
            policy.require_allowed(url)


@pytest.mark.asyncio
async def test_redirect_is_validated_before_second_request() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/collect"})

    policy = EgressPolicy(
        mode="allowlist",
        allowed_origins=frozenset({"https://autopvs1.bgi.com"}),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EgressDeniedError):
            await guarded_request(
                client,
                policy,
                "GET",
                "https://autopvs1.bgi.com/search",
            )
    assert requests == ["https://autopvs1.bgi.com/search"]
```

- [ ] **Step 2: Run the test and verify red state**

Run:

```bash
uv run pytest tests/unit/test_egress_policy.py -q
```

Expected: import failure because `autopvs1_link.api.egress` does not exist.

- [ ] **Step 3: Implement exact-origin validation and bounded redirects**

Create `autopvs1_link/api/egress.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx

EgressMode = Literal["disabled", "allowlist"]
_REDIRECTS = {301, 302, 303, 307, 308}


class EgressDeniedError(RuntimeError):
    """Configured policy rejected an outbound destination before network I/O."""


def normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("allowed upstream origin must be a bare HTTPS origin")
    port = f":{parsed.port}" if parsed.port not in (None, 443) else ""
    return f"https://{parsed.hostname.lower()}{port}"


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    mode: EgressMode
    allowed_origins: frozenset[str]

    def require_allowed(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise EgressDeniedError("outbound URL is not an authenticated HTTPS destination")
        origin = normalize_origin(f"{parsed.scheme}://{parsed.netloc}")
        if self.mode != "allowlist" or origin not in self.allowed_origins:
            raise EgressDeniedError("outbound origin is not allowlisted")


async def guarded_request(
    client: httpx.AsyncClient,
    policy: EgressPolicy,
    method: str,
    url: str,
    *,
    max_redirects: int = 5,
    **kwargs: Any,
) -> httpx.Response:
    current = url
    request_kwargs = dict(kwargs)
    for hop in range(max_redirects + 1):
        policy.require_allowed(current)
        response = await client.request(
            method,
            current,
            follow_redirects=False,
            **request_kwargs,
        )
        if response.status_code not in _REDIRECTS:
            return response
        if hop == max_redirects:
            raise EgressDeniedError("outbound redirect limit exceeded")
        location = response.headers.get("location")
        if not location:
            raise EgressDeniedError("redirect response omitted Location")
        next_url = urljoin(str(response.url), location)
        policy.require_allowed(next_url)
        if urlsplit(current).scheme == "https" and urlsplit(next_url).scheme != "https":
            raise EgressDeniedError("HTTPS redirect downgrade rejected")
        current = next_url
        request_kwargs.pop("params", None)
        if response.status_code == 303:
            method = "GET"
            request_kwargs.pop("content", None)
            request_kwargs.pop("data", None)
            request_kwargs.pop("json", None)
    raise AssertionError("redirect loop terminated unexpectedly")
```

In `APIConfig`, add CSV configuration and a property that normalizes once at startup:

```python
egress_mode: Literal["disabled", "allowlist"] = "disabled"
allowed_upstream_origins: str = ""

@property
def egress_policy(self) -> EgressPolicy:
    origins = frozenset(
        normalize_origin(item.strip())
        for item in self.allowed_upstream_origins.split(",")
        if item.strip()
    )
    if self.egress_mode == "allowlist" and not origins:
        raise ValueError("allowlist egress mode requires at least one exact origin")
    return EgressPolicy(mode=self.egress_mode, allowed_origins=origins)
```

Import `EgressPolicy` and `normalize_origin` from `autopvs1_link.api.egress`.

- [ ] **Step 4: Run policy tests**

Run:

```bash
uv run pytest tests/unit/test_egress_policy.py tests/unit/test_config_settings.py -q
```

Expected: PASS with only the first request observed on a rejected redirect.

- [ ] **Step 5: Commit the policy primitive**

```bash
git add autopvs1_link/api/egress.py autopvs1_link/config.py \
  tests/unit/test_egress_policy.py tests/unit/test_config_settings.py
git commit -m "feat(security): add default-deny outbound policy"
```

### Task 8: Route Every AutoPVS1 Outbound Call Through the Policy

**Files:**
- Modify: `autopvs1_link/api/autopvs1_client.py`
- Modify: `autopvs1_link/api/variant_recoder.py`
- Modify: `autopvs1_link/mcp/tools/health_tool.py`
- Modify: `autopvs1_link/mcp/tools/mode_errors.py`
- Modify: `autopvs1_link/mcp/tools/_pvs1_runners.py`
- Modify: `autopvs1_link/mcp/tools/variant_tool.py`
- Modify: `autopvs1_link/mcp/tools/cnv_tool.py`
- Modify: `autopvs1_link/mcp/tools/search_tool.py`
- Modify: `autopvs1_link/mcp/presenters/variant.py`
- Test: `tests/unit/test_egress_integration.py`
- Test: `tests/unit/mcp/test_tool_runtime.py`
- Test: `tests/unit/mcp/test_presenter_shape_validation.py`

- [ ] **Step 1: Write failing client and tool-envelope tests**

Create `tests/unit/test_egress_integration.py`:

```python
import httpx
import pytest

from autopvs1_link.api.autopvs1_client import AutoPVS1Client
from autopvs1_link.api.egress import EgressDeniedError
from autopvs1_link.api.variant_recoder import VariantRecoderClient
from autopvs1_link.config import settings


@pytest.mark.asyncio
async def test_disabled_policy_blocks_bgi_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def forbidden(*args: object, **kwargs: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("network must not be reached")

    monkeypatch.setattr(settings.api, "egress_mode", "disabled")
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    client = AutoPVS1Client()
    try:
        with pytest.raises(EgressDeniedError):
            await client.get_variant_data("hg38", "1-1-A-G")
    finally:
        await client.close()
    assert calls == 0


@pytest.mark.asyncio
async def test_disabled_policy_blocks_ensembl_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.api, "egress_mode", "disabled")
    with pytest.raises(EgressDeniedError):
        await VariantRecoderClient().recode("rs80357906", "hg38")
```

Add a tool-runtime assertion using the existing server facade in
`tests/unit/mcp/test_tool_runtime.py`:

```python
@pytest.mark.asyncio
async def test_disabled_egress_returns_structured_error(monkeypatch) -> None:
    monkeypatch.setattr(settings.api, "egress_mode", "disabled")
    mcp = build_mcp_server()
    result = await mcp.call_tool(
        "get_variant_pvs1_data",
        {"variant_id": "1-1-A-G", "genome_build": "hg38"},
    )
    assert result.structured_content["error_code"] == "external_egress_disabled"
    assert result.structured_content["retryable"] is False


def test_unknown_scraped_strength_fails_closed() -> None:
    with pytest.raises(UpstreamFormatError, match="unrecognized final strength"):
        present_variant(_variant("Bananas"), source_url=None)
```

Import `UpstreamFormatError` and `present_variant` from
`autopvs1_link.mcp.presenters.variant`; place the final test in
`tests/unit/mcp/test_presenter_shape_validation.py`, where `_variant` already exists.

- [ ] **Step 2: Run focused tests and verify red state**

Run:

```bash
uv run pytest tests/unit/test_egress_integration.py \
  tests/unit/mcp/test_tool_runtime.py -k 'disabled_egress' -q
```

Expected: FAIL because existing clients call HTTPX directly and tools do not map the policy error.

- [ ] **Step 3: Replace every direct external request**

In `AutoPVS1Client.__init__`, retain pooling but disable automatic redirects:

```python
self.policy = settings.api.egress_policy
self.client = httpx.AsyncClient(
    timeout=settings.api.request_timeout,
    headers=headers,
    follow_redirects=False,
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)
```

Update every existing happy-path client/tool fixture to set `settings.api.egress_mode` to
`"allowlist"` and configure only the exact origin exercised by that fixture. BGI cases allow
`https://autopvs1.bgi.com`; Ensembl cases allow the matching GRCh37 or GRCh38 origin. Do not add a
global permissive test default: tests that omit policy configuration must continue proving the
default-deny behavior.

Replace each BGI `self.client.get` call with:

```python
response = await guarded_request(self.client, self.policy, "GET", url, params=params)
```

Omit `params` for variant/CNV URLs. In `VariantRecoderClient.recode`, use
`settings.api.egress_policy` and `guarded_request` around the Ensembl URL. In
`health_tool._probe_upstream`, use the same helper with method `HEAD`; do not set
`follow_redirects=True`.

In `mode_errors.py`, add:

```python
def external_egress_disabled_error() -> dict[str, object]:
    return {
        "code": "external_egress_disabled",
        "message": "External variant transfer is disabled by deployment policy.",
        "retryable": False,
        "suggestions": [
            "Use a deployment with an explicitly approved AutoPVS1 upstream.",
            "Do not submit patient-derived variants to a public research instance.",
        ],
    }
```

Catch only `EgressDeniedError` before generic HTTPX handlers in `_pvs1_runners.py`,
`variant_tool.py`, `cnv_tool.py`, and `search_tool.py`, returning that structured error. Log only
the operation and `error_type`, never the denied URL or identifier.

In `autopvs1_link/mcp/presenters/variant.py`, replace the non-fatal unknown-strength warning with:

```python
class UpstreamFormatError(ValueError):
    """Scraped HTML does not satisfy the reviewed AutoPVS1 result contract."""


if final_strength not in _KNOWN_FINAL_STRENGTHS:
    raise UpstreamFormatError(
        f"unrecognized final strength from AutoPVS1: {final_strength!r}"
    )
```

Catch `UpstreamFormatError` in variant/CNV runners and return the existing non-retryable
`parse_error` envelope without including the scraped value in logs.

- [ ] **Step 4: Run all AutoPVS1 network and MCP tests**

Run:

```bash
uv run pytest tests/unit/test_egress_policy.py tests/unit/test_egress_integration.py \
  tests/unit/test_hgvs_redirect_functionality.py tests/unit/mcp/test_tool_runtime.py \
  tests/unit/mcp/test_tools.py tests/unit/mcp/test_presenter_shape_validation.py -q
```

Expected: PASS; disabled mode produces zero requests, and allowed test fixtures still parse.

- [ ] **Step 5: Commit policy enforcement**

```bash
git add autopvs1_link/api/autopvs1_client.py autopvs1_link/api/variant_recoder.py \
  autopvs1_link/mcp/tools/health_tool.py autopvs1_link/mcp/tools/mode_errors.py \
  autopvs1_link/mcp/tools/_pvs1_runners.py autopvs1_link/mcp/tools/variant_tool.py \
  autopvs1_link/mcp/tools/cnv_tool.py autopvs1_link/mcp/tools/search_tool.py \
  autopvs1_link/mcp/presenters/variant.py tests/unit/test_egress_integration.py \
  tests/unit/mcp/test_tool_runtime.py tests/unit/mcp/test_presenter_shape_validation.py
git commit -m "fix(security): enforce AutoPVS1 egress policy everywhere"
```

### Task 9: Pin Deployment Profiles and Operator Contracts

**Files:**
- Modify: `pubtator-link/docker/docker-compose.yml`
- Modify: `pubtator-link/docker/docker-compose.prod.yml`
- Modify: `pubtator-link/docker/docker-compose.npm.yml`
- Modify: `pubtator-link/.env.example`
- Modify: `pubtator-link/.env.docker.example`
- Modify: `pubtator-link/docs/SECURITY.md`
- Modify: `pubtator-link/tests/unit/test_docker_compose_postgres.py`
- Modify: `autopvs1-link/docker/docker-compose.prod.yml`
- Modify: `autopvs1-link/.env.example`
- Modify: `autopvs1-link/docs/configuration.md`
- Modify: `autopvs1-link/tests/unit/test_docker_compose_prod.py`
- Modify: `genefoundry-router/.env.example`
- Modify: `genefoundry-router/.env.docker.example`
- Create: `genefoundry-router/docker/.env.patient-data.example`
- Test: `genefoundry-router/tests/unit/test_deployment_profiles.py`

- [ ] **Step 1: Write failing merged-Compose and patient-profile tests**

In PubTator `tests/unit/test_docker_compose_postgres.py`, add a subprocess Compose assertion:

```python
def test_merged_production_compose_is_readonly_and_requires_service_token(monkeypatch) -> None:
    monkeypatch.setenv("PUBTATOR_LINK_MCP_SERVICE_TOKEN", "compose-test-secret")
    result = subprocess.run(
        [
            "docker", "compose",
            "-f", "docker/docker-compose.yml",
            "-f", "docker/docker-compose.prod.yml",
            "-f", "docker/docker-compose.npm.yml",
            "config", "--format", "json",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    service = json.loads(result.stdout)["services"]["pubtator-link"]
    assert service["environment"]["PUBTATOR_LINK_MCP_PROFILE"] == "readonly"
    assert service["environment"]["PUBTATOR_LINK_MCP_SERVICE_TOKEN"] == "compose-test-secret"
    assert not service.get("ports")
```

In AutoPVS1 `tests/unit/test_docker_compose_prod.py`, assert:

```python
def test_public_research_compose_explicitly_allows_current_origins() -> None:
    env = _prod_env()
    assert env["AUTOPVS1_LINK_API_EGRESS_MODE"] == "allowlist"
    assert set(env["AUTOPVS1_LINK_API_ALLOWED_UPSTREAM_ORIGINS"].split(",")) == {
        "https://autopvs1.bgi.com",
        "https://rest.ensembl.org",
        "https://grch37.rest.ensembl.org",
    }
```

Create router `tests/unit/test_deployment_profiles.py`:

```python
from pathlib import Path


def test_patient_profile_has_no_autopvs1_backend_url() -> None:
    text = Path("docker/.env.patient-data.example").read_text(encoding="utf-8")
    assert "GF_AUTOPVS1_URL=" not in text
    assert "AUTOPVS1" in text
    assert "disabled" in text.lower()
```

- [ ] **Step 2: Run tests and verify red state**

Run in each repository:

```bash
uv run pytest tests/unit/test_docker_compose_postgres.py -q
uv run pytest tests/unit/test_docker_compose_prod.py -q
uv run pytest tests/unit/test_deployment_profiles.py -q
```

Expected: all three fail because production profiles and documentation do not encode the new
boundaries.

- [ ] **Step 3: Encode production and local exceptions explicitly**

PubTator base/local Compose:

```yaml
environment:
  PUBTATOR_LINK_MCP_PROFILE: full
  PUBTATOR_LINK_ALLOW_UNAUTHENTICATED_WRITES: "true"
```

PubTator production overlay:

```yaml
environment:
  PUBTATOR_LINK_MCP_PROFILE: readonly
  PUBTATOR_LINK_ALLOW_UNAUTHENTICATED_WRITES: "false"
  PUBTATOR_LINK_MCP_SERVICE_TOKEN: "${PUBTATOR_LINK_MCP_SERVICE_TOKEN:?required}"
  PUBTATOR_LINK_ENABLE_INBOUND_RATE_LIMIT: "true"
  PUBTATOR_LINK_TRUST_PROXY_HEADERS: "true"
```

AutoPVS1 public research production overlay:

```yaml
environment:
  AUTOPVS1_LINK_API_EGRESS_MODE: allowlist
  AUTOPVS1_LINK_API_ALLOWED_UPSTREAM_ORIGINS: >-
    https://autopvs1.bgi.com,https://rest.ensembl.org,https://grch37.rest.ensembl.org
```

Add `GF_PUBTATOR_TOKEN=` to router examples as a secret with no committed value. Create
`docker/.env.patient-data.example` by copying the authenticated production router settings and
all approved backend URLs except `GF_AUTOPVS1_URL`; include:

```dotenv
# AutoPVS1 intentionally disabled: public BGI/Ensembl transfer is not approved for patient data.
# Do not set GF_AUTOPVS1_URL in this profile.
```

Document token generation, rotation order, egress modes, exact origins, self-hosted configuration,
and the research-only boundary in the named docs. State that Docker networking is not an egress
firewall and hospital deployments also require a host/network egress policy.

- [ ] **Step 4: Run deployment tests and render Compose**

Run in each repository:

```bash
uv run pytest tests/unit/test_docker_compose_postgres.py -q
PUBTATOR_LINK_MCP_SERVICE_TOKEN=compose-test-secret docker compose \
  -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  -f docker/docker-compose.npm.yml config --quiet

uv run pytest tests/unit/test_docker_compose_prod.py -q
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  -f docker/docker-compose.npm.yml config --quiet

uv run pytest tests/unit/test_deployment_profiles.py -q
```

Expected: PASS and both Compose render commands exit 0.

- [ ] **Step 5: Commit deployment contracts in each repository**

PubTator:

```bash
git add docker/docker-compose.yml docker/docker-compose.prod.yml docker/docker-compose.npm.yml \
  .env.example .env.docker.example docs/SECURITY.md \
  tests/unit/test_docker_compose_postgres.py
git commit -m "docs(security): enforce PubTator deployment boundary"
```

AutoPVS1:

```bash
git add docker/docker-compose.prod.yml .env.example docs/configuration.md \
  tests/unit/test_docker_compose_prod.py
git commit -m "docs(security): declare approved AutoPVS1 egress"
```

Router:

```bash
git add .env.example .env.docker.example docker/.env.patient-data.example \
  tests/unit/test_deployment_profiles.py
git commit -m "docs(security): add patient-data router profile"
```

### Task 10: CI, Adversarial Review, Release, and Staged Deployment

**Files:**
- Modify: `pubtator-link/pyproject.toml`
- Modify: `pubtator-link/CHANGELOG.md`
- Modify: `autopvs1-link/pyproject.toml`
- Modify: `autopvs1-link/CHANGELOG.md`
- Modify: `genefoundry-router/pyproject.toml`
- Modify: `genefoundry-router/CHANGELOG.md`
- Modify: `genefoundry-router/genefoundry_router/data/fleet-baseline.json`
- Modify: GitHub issue comments for `pubtator-link#85`, `autopvs1-link#41`, router `#32/#33`

- [ ] **Step 1: Prove each repository is green before version changes**

Run:

```bash
cd /home/bernt-popp/development/pubtator-link && make ci-local
cd /home/bernt-popp/development/autopvs1-link && make ci-local
cd /home/bernt-popp/development/genefoundry-router && make ci-local
```

Expected: all commands exit 0. Before running, confirm the branches with:

```bash
git -C /home/bernt-popp/development/pubtator-link branch --show-current
git -C /home/bernt-popp/development/autopvs1-link branch --show-current
git -C /home/bernt-popp/development/genefoundry-router branch --show-current
```

Do not run the gates against another checkout.

- [ ] **Step 2: Compare the two canonical write inventories**

Run:

```bash
python - <<'PY'
import ast
from pathlib import Path

pub = ast.parse(Path("../pubtator-link/pubtator_link/mcp/profiles.py").read_text())
router = ast.parse(Path("genefoundry_router/authorization.py").read_text())

def literal_set(tree: ast.Module, name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    call = node.value
                    assert isinstance(call, ast.Call)
                    return set(ast.literal_eval(call.args[0]))
    raise AssertionError(name)

leaf = literal_set(pub, "WRITE_TOOLS")
qualified = literal_set(router, "PUBTATOR_WRITE_TOOLS")
assert {f"pubtator_{name}" for name in leaf} == qualified
PY
```

Expected: exit 0. If worktrees are not siblings, use their exact absolute paths in the two
`Path` constructor calls.

- [ ] **Step 3: Request adversarial review and resolve every material finding**

Run Claude Code independently in each worktree with this prompt:

```text
Adversarially review this security PR. Look specifically for caller-token passthrough,
service-token leakage, middleware ordering bypass, missing PubTator write tools, direct backend
bypass, AutoPVS1 requests that avoid the egress policy, redirect validation after network I/O,
PII in logs, Compose merge surprises, and rollout outage risks. Cite file and line for every
finding. Do not edit.
```

Expected: retain the review transcript in the PR. Fix High/Medium correctness or security findings
with new failing tests and atomic commits; rerun `make ci-local` after each fix series.

- [ ] **Step 4: Bump versions and changelogs only after green review**

Use the repository’s release command or edit its tested single source, then run:

```bash
make ci-local
git add CHANGELOG.md pyproject.toml
git commit -m "chore(release): bump version for policy boundaries"
```

If the version source is not `pyproject.toml`, stage the exact file asserted by
`test_version_single_source.py` instead. Expected: full CI remains green and the changelog names
the new environment variables and breaking export-schema change.

- [ ] **Step 5: Open PRs and merge in dependency order**

1. Open router PR containing backend service-header support and write authorization.
2. Open PubTator PR containing readonly inventory, service auth, export change, and deployment
   profile.
3. Open AutoPVS1 PR containing settings/privacy and egress enforcement.
4. Require green GitHub checks, Claude adversarial review, and branch protection.
5. Merge router first, PubTator second, AutoPVS1 third. Do not deploy yet.

- [ ] **Step 6: Stage the PubTator credential without outage**

Generate a random secret in the deployment secret store. Configure the merged router release with
`GF_PUBTATOR_TOKEN` and deploy it while the old PubTator backend still ignores Authorization.

Verify through the authenticated router:

```bash
genefoundry-router conformance https://genefoundry.org/mcp \
  --name genefoundry --tier stateless --require-auth
```

Expected: PASS and PubTator read calls still succeed. Never print the token in shell history or CI
logs.

- [ ] **Step 7: Deploy PubTator readonly plus service authentication**

Set the same secret as `PUBTATOR_LINK_MCP_SERVICE_TOKEN`, deploy merged production Compose, and
verify:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  https://pubtator-link.genefoundry.org/mcp
```

Expected: `401`.

Using a secret-aware probe from the router host, call `tools/list` and assert none of the eight
write tools is present. Expected: authenticated backend request succeeds and profile is readonly.

- [ ] **Step 8: Configure AutoPVS1 origins before deploying enforcement**

Set these exact deployment values first:

```dotenv
AUTOPVS1_LINK_API_EGRESS_MODE=allowlist
AUTOPVS1_LINK_API_ALLOWED_UPSTREAM_ORIGINS=https://autopvs1.bgi.com,https://rest.ensembl.org,https://grch37.rest.ensembl.org
```

Deploy the new AutoPVS1 release and run one canonical coordinate, one rsID/HGVS resolution, one
CNV, and one upstream health probe. Expected: existing public research behavior succeeds with no
material latency change. Run a denied-origin deployment probe and confirm zero outbound request
reaches the denied destination.

- [ ] **Step 9: Deploy and verify the patient-data profile**

Render `docker/.env.patient-data.example` into the hospital secret/config system without setting
`GF_AUTOPVS1_URL`. Enforce the corresponding host/network egress deny rule for BGI and public
Ensembl. Run `genefoundry-router list-tools` and assert no `autopvs1_` tool exists. Expected:
AutoPVS1 is absent. From the deployed router container, run:

```bash
docker exec genefoundry_router curl -fsS --max-time 5 https://autopvs1.bgi.com/
docker exec genefoundry_router curl -fsS --max-time 5 https://rest.ensembl.org/
docker exec genefoundry_router curl -fsS --max-time 5 https://grch37.rest.ensembl.org/
```

Expected: all three commands exit non-zero because the hospital egress policy denies those
destinations. A DNS failure, connect refusal, or policy-proxy denial is acceptable evidence; an
HTTP response from any destination is a failed gate.

- [ ] **Step 10: Refresh baseline, close issues, and record evidence**

After the deployed readonly catalog is authoritative:

```bash
uv run python scripts/snapshot_fleet.py --servers-file servers.yaml \
  --output genefoundry_router/data/fleet-baseline.json
make ci-local
git add genefoundry_router/data/fleet-baseline.json
git commit -m "chore(ci): refresh fleet baseline after policy rollout"
```

Expected: baseline review shows PubTator read-only inventory and the intended AutoPVS1 presence in
the public-research profile only.

Close `pubtator-link#85` and router `#33` only after direct PubTator MCP returns 401, the general
fleet catalog is readonly, write scope tests pass, export paths are server-generated, and live
router reads succeed. Close `autopvs1-link#41` and router `#32` only after production environment
tests, client metadata redaction, zero-request default deny, every-hop redirect tests, explicit
public origins, patient-profile router omission, and network egress denial are evidenced. Correct
the stale issue comments about already-landed PubTator caps/path confinement and AutoPVS1 honest
User-Agent/provenance when posting closure evidence.
