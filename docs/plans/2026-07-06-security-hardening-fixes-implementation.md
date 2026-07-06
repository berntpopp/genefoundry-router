# Security Hardening Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the five approved router-side security hardening fixes for inbound limits, streaming body caps, metrics access, correlation IDs, and Docker example defaults.

**Architecture:** Keep the router a thin FastMCP 3.x aggregator with all inbound hardening in the outer FastAPI/Starlette ASGI shell. Replace the request limiter with pure ASGI so client attribution, rate state, and streaming body enforcement happen before the mounted MCP app without changing backend composition or auth semantics.

**Tech Stack:** FastMCP 3.x, FastAPI/Starlette ASGI, pydantic-settings, structlog, prometheus-client, pytest, uv.

## Global Constraints
- Python 3.12+.
- Dependency and venv management via uv (`uv run`).
- ruff (lint + format) and mypy must pass.
- 600-LOC per module budget via `scripts/check_file_size.py`.
- TDD: write a failing test, see it fail, implement minimally, see it pass.
- One atomic commit per task.
- No new third-party dependencies.
- No caller-token passthrough.
- Streamable HTTP only; SSE is not offered.
- Per-call AUDIT logs from `AuditLogMiddleware` record only tool, namespace, outcome, elapsed time, and correlation ID; they never record tool arguments, tool results, or exception text.
- `make ci-local` must be green before handoff.

---

## File Structure
- `docs/plans/2026-07-06-security-hardening-fixes-implementation.md` - this executable plan.
- `genefoundry_router/config.py` - adds `GF_TRUSTED_PROXY_HOPS` and `GF_METRICS_TOKEN` settings, including blank-token normalization.
- `genefoundry_router/limits.py` - pure-ASGI request limit middleware, trusted-hop client keying, bounded per-window rate state, and streaming body cap.
- `genefoundry_router/observability.py` - optional bearer-token guard for `/metrics` while keeping `/health` public.
- `genefoundry_router/server.py` - wires the new settings and reorders middleware so correlation IDs wrap short-circuit responses.
- `.env.docker.example` - fails closed by requiring an explicit public unauthenticated choice and documents new env vars.
- `.env.example` - documents trusted proxy hops and optional metrics token for local/dev use.
- `tests/unit/test_settings.py` - setting defaults, env parsing, and blank metrics-token normalization.
- `tests/unit/test_limits.py` - client-key selection, rate-limit state bounds, content-length 413, 429 behavior, and raw-ASGI streaming body cap.
- `tests/unit/test_metrics.py` - `/metrics` public/protected matrix and `/health` public behavior.
- `tests/integration/test_server.py` - full `build_app` wiring for metrics token and `X-Request-ID` on 403/413/429.
- `tests/e2e/test_dev_config_consistency.py` - env example hardening checks for `.env.example` and `.env.docker.example` only.

### Task 0: Preflight Branch
**Files:**
- Create: none.
- Modify: none.
- Test/Verify: repository root.

**Interfaces:**
- Consumes: current `main` branch and the existing `make ci-local` target.
- Produces: working branch `fix/security-hardening-2026-07-06` for Tasks 1-7.

- [ ] **Step 1: Create the branch**

Run:

```bash
git switch -c fix/security-hardening-2026-07-06
```

Expected: branch switches from `main` to `fix/security-hardening-2026-07-06`.

- [ ] **Step 2: Confirm the branch**

Run:

```bash
git branch --show-current
```

Expected: prints `fix/security-hardening-2026-07-06`.

- [ ] **Step 3: Run the baseline CI target**

Run:

```bash
make ci-local
```

Expected: PASS before security changes, or capture any pre-existing failures before modifying files.

- [ ] **Step 4: Inspect workspace state**

Run:

```bash
git status --short
```

Expected: no modified tracked files from Task 0.

- [ ] **Step 5: Commit**

No commit for Task 0 because it only creates a branch and runs verification.

### Task 1: Config Settings
**Files:**
- Modify: `genefoundry_router/config.py:45-98`.
- Test: `tests/unit/test_settings.py:4-32`.

**Interfaces:**
- Consumes: `RouterSettings(_env_file=None)` and existing `@field_validator("GF_ALLOWED_ORIGINS", mode="before")` style.
- Produces: `RouterSettings.GF_TRUSTED_PROXY_HOPS: int`, `RouterSettings.GF_METRICS_TOKEN: str | None`, and `RouterSettings._blank_metrics_token(v: object) -> object`.

- [ ] **Step 1: Write the failing test**

Replace `tests/unit/test_settings.py` with:

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
    assert s.GF_ALLOWED_ORIGINS == []  # R1.4 - empty = reject any present Origin
    assert s.GF_PUBLIC_BASE_URL is None  # R1.5 - public URL for OAuth metadata
    assert s.GF_TRUSTED_PROXY_HOPS == 1
    assert s.GF_METRICS_TOKEN is None


def test_allowed_origins_parses_csv(monkeypatch):
    monkeypatch.setenv("GF_ALLOWED_ORIGINS", "https://claude.ai, https://cursor.sh")
    s = RouterSettings(_env_file=None)
    assert s.GF_ALLOWED_ORIGINS == ["https://claude.ai", "https://cursor.sh"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.setenv("GF_PORT", "9001")
    monkeypatch.setenv("GF_TRUSTED_PROXY_HOPS", "2")
    monkeypatch.setenv("GF_METRICS_TOKEN", "scrape-secret")
    s = RouterSettings(_env_file=None)
    assert s.GF_AUTH_MODE == "jwt"
    assert s.GF_PORT == 9001
    assert s.GF_TRUSTED_PROXY_HOPS == 2
    assert s.GF_METRICS_TOKEN == "scrape-secret"


def test_metrics_token_blank_normalizes_to_none(monkeypatch):
    monkeypatch.setenv("GF_METRICS_TOKEN", "   ")
    s = RouterSettings(_env_file=None)
    assert s.GF_METRICS_TOKEN is None


def test_invalid_auth_mode_rejected(monkeypatch):
    monkeypatch.setenv("GF_AUTH_MODE", "bogus")
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RouterSettings(_env_file=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py -q`

Expected: FAIL with `AttributeError` for missing `GF_TRUSTED_PROXY_HOPS` or `GF_METRICS_TOKEN`.

- [ ] **Step 3: Write minimal implementation**

In `genefoundry_router/config.py`, add the two fields under the inbound request limits block and add the validator after `_split_origins`:

```python
    # Inbound request limits (DoS/abuse guard). <=0 disables that limit.
    GF_MAX_BODY_BYTES: int = 4_000_000  # 4 MB cap on request bodies (413 over)
    GF_RATE_LIMIT_RPM: int = 0  # per-client requests/min (429 over); 0 = off, enable in prod
    GF_TRUSTED_PROXY_HOPS: int = 1  # trusted hops at the tail of X-Forwarded-For
    GF_METRICS_TOKEN: str | None = None  # optional bearer token for GET /metrics
```

```python
    @field_validator("GF_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept a comma-separated string from env and split into a list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("GF_METRICS_TOKEN", mode="before")
    @classmethod
    def _blank_metrics_token(cls, v: object) -> object:
        """Treat blank scrape-token env values as unset."""
        if isinstance(v, str) and not v.strip():
            return None
        return v
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_settings.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/config.py tests/unit/test_settings.py
git commit -m "feat(config): add proxy hops and metrics token settings"
```

### Task 2: Rate Limiter Pure ASGI, Trusted-Hop Client Key, and Bounded State
**Files:**
- Modify: `genefoundry_router/limits.py:10-77`.
- Modify: `genefoundry_router/server.py:120-125`.
- Test: `tests/unit/test_limits.py:1-40`.

**Interfaces:**
- Consumes: `RouterSettings.GF_TRUSTED_PROXY_HOPS: int` from Task 1.
- Produces: `_client_key(scope: Scope, trusted_proxy_hops: int) -> str`, `RequestLimitMiddleware.__call__(scope: Scope, receive: Receive, send: Send) -> None`, `RequestLimitMiddleware._increment(key: str, now: float) -> bool`, and `add_request_limits(app: FastAPI, max_body_bytes: int, rate_limit_rpm: int, trusted_proxy_hops: int = 1) -> None`.

- [ ] **Step 1: Write the failing test**

Replace `tests/unit/test_limits.py` with:

```python
"""Inbound request limits: body-size cap (413) + per-client rate limit (429)."""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Scope

import genefoundry_router.limits as limits_mod
from genefoundry_router.limits import RequestLimitMiddleware, _client_key


async def _noop_app(_scope, _receive, _send) -> None:
    return None


def _scope(
    headers: list[tuple[bytes, bytes]] | None = None,
    method: str = "GET",
    path: str = "/x",
    client_host: str = "10.0.0.9",
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": (client_host, 12345),
        "server": ("testserver", 80),
    }


def _client(**kw) -> TestClient:
    async def ok(_request: Request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/x", ok, methods=["GET", "POST"])])
    app.add_middleware(RequestLimitMiddleware, **kw)
    return TestClient(app)


class _SpyLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))


def test_body_cap_rejects_oversized() -> None:
    c = _client(max_body_bytes=10, rate_limit_rpm=0)
    assert c.post("/x", content=b"x" * 100).status_code == 413


def test_body_cap_allows_small() -> None:
    c = _client(max_body_bytes=1000, rate_limit_rpm=0)
    assert c.post("/x", content=b"hi").status_code == 200


def test_rate_limit_returns_429_after_limit() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=2)
    assert c.get("/x").status_code == 200
    assert c.get("/x").status_code == 200
    resp = c.get("/x")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"


def test_spoofed_leftmost_xff_values_share_bucket() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=1, trusted_proxy_hops=1)
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-a, 198.51.100.10"}).status_code == 200
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-b, 198.51.100.10"}).status_code == 429


def test_xff_rotation_no_longer_bypasses_rate_limit() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=2, trusted_proxy_hops=1)
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-1, 203.0.113.9"}).status_code == 200
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-2, 203.0.113.9"}).status_code == 200
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-3, 203.0.113.9"}).status_code == 429


def test_trusted_proxy_hops_two_selects_second_from_right() -> None:
    scope = _scope(
        headers=[(b"x-forwarded-for", b"1.2.3.4, 203.0.113.55, 198.51.100.7")],
        client_host="10.0.0.5",
    )
    assert _client_key(scope, trusted_proxy_hops=2) == "203.0.113.55"


def test_insufficient_hop_depth_and_zero_hops_fall_back_to_scope_client() -> None:
    scope = _scope(
        headers=[(b"x-forwarded-for", b"198.51.100.10")],
        client_host="10.0.0.42",
    )
    assert _client_key(scope, trusted_proxy_hops=2) == "10.0.0.42"
    assert _client_key(scope, trusted_proxy_hops=0) == "10.0.0.42"


def test_hits_clear_across_windows(monkeypatch) -> None:
    middleware = RequestLimitMiddleware(_noop_app, max_body_bytes=0, rate_limit_rpm=10)

    monkeypatch.setattr(limits_mod.time, "monotonic", lambda: 1.0)
    assert middleware._increment("198.51.100.1", limits_mod.time.monotonic()) is True
    assert middleware._hits == {"198.51.100.1": 1}

    monkeypatch.setattr(limits_mod.time, "monotonic", lambda: 61.0)
    assert middleware._increment("198.51.100.2", limits_mod.time.monotonic()) is True
    assert middleware._hits == {"198.51.100.2": 1}


def test_max_tracked_ceiling_fails_open_once_per_window(monkeypatch) -> None:
    spy = _SpyLog()
    monkeypatch.setattr(limits_mod, "_MAX_TRACKED", 2)
    monkeypatch.setattr(limits_mod, "log", spy)
    middleware = RequestLimitMiddleware(_noop_app, max_body_bytes=0, rate_limit_rpm=1)

    assert middleware._increment("a", 1.0) is True
    assert middleware._increment("b", 1.0) is True
    assert len(middleware._hits) == 2
    assert middleware._increment("c", 1.0) is True
    assert middleware._increment("d", 1.0) is True
    assert len(middleware._hits) == 2
    assert [event for event, _ in spy.events].count("rate_limit_tracking_ceiling") == 1
    assert spy.events[0][1]["max_tracked"] == 2
    assert middleware._increment("a", 1.0) is False

    assert middleware._increment("e", 61.0) is True
    assert middleware._increment("f", 61.0) is True
    assert middleware._increment("g", 61.0) is True
    assert middleware._hits == {"e": 1, "f": 1}
    assert [event for event, _ in spy.events].count("rate_limit_tracking_ceiling") == 2


def test_limits_disabled_by_zero() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=0)
    for _ in range(6):
        assert c.get("/x").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_limits.py -q`

Expected: FAIL with `TypeError` for the old `_client_key` signature or `RequestLimitMiddleware.__init__()` not accepting `trusted_proxy_hops`.

- [ ] **Step 3: Write minimal implementation**

Replace `genefoundry_router/limits.py` with:

```python
"""Inbound request limits: body-size cap + per-client rate limit (DoS / abuse guard).

A read-only reference gateway still needs back-pressure: without it, an open or buggy
client can exhaust the router or use it to hammer upstream APIs (OWASP LLM10 - unbounded
consumption). Both limits are opt-in via settings; ``<= 0`` disables that limit.
"""

from __future__ import annotations

import time

import structlog
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = structlog.get_logger(__name__)

_MAX_TRACKED = 100_000


def _scope_client_host(scope: Scope) -> str:
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        host = client[0]
        if isinstance(host, str) and host:
            return host
    return "unknown"


def _client_key(scope: Scope, trusted_proxy_hops: int) -> str:
    """Identify the caller from trusted X-Forwarded-For tail hops, else ASGI client."""
    client_host = _scope_client_host(scope)
    values = Headers(scope=scope).getlist("x-forwarded-for")
    parts = [part.strip() for value in values for part in value.split(",") if part.strip()]
    if trusted_proxy_hops > 0 and len(parts) >= trusted_proxy_hops:
        return parts[-trusted_proxy_hops]
    return client_host


class RequestLimitMiddleware:
    """Reject oversized bodies (413) and rate-limit per client (429, fixed window)."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = 0,
        rate_limit_rpm: int = 0,
        trusted_proxy_hops: int = 1,
        window_seconds: int = 60,
    ) -> None:
        self.app = app
        self._max_body = max_body_bytes
        self._rpm = rate_limit_rpm
        self._trusted_proxy_hops = trusted_proxy_hops
        self._window = window_seconds
        self._hits: dict[str, int] = {}
        self._window_index: int | None = None
        self._ceiling_warned = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._content_length_exceeds(scope):
            await JSONResponse({"error": "request entity too large"}, status_code=413)(
                scope, receive, send
            )
            return

        if self._rpm > 0 and not self._rate_allowed(scope):
            await JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(self._window)},
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _content_length_exceeds(self, scope: Scope) -> bool:
        if self._max_body <= 0:
            return False
        content_length = Headers(scope=scope).get("content-length")
        if content_length is None or not content_length.isdigit():
            return False
        length = int(content_length)
        if length <= self._max_body:
            return False
        log.warning("request_too_large", content_length=length, limit=self._max_body)
        return True

    def _rate_allowed(self, scope: Scope) -> bool:
        key = _client_key(scope, self._trusted_proxy_hops)
        allowed = self._increment(key, time.monotonic())
        if not allowed:
            log.warning("rate_limited", limit=self._rpm)
        return allowed

    def _increment(self, key: str, now: float) -> bool:
        window = int(now // self._window)
        if self._window_index != window:
            self._hits.clear()
            self._window_index = window
            self._ceiling_warned = False

        if key not in self._hits and len(self._hits) >= _MAX_TRACKED:
            if not self._ceiling_warned:
                log.warning("rate_limit_tracking_ceiling", max_tracked=_MAX_TRACKED)
                self._ceiling_warned = True
            return True

        count = self._hits.get(key, 0) + 1
        self._hits[key] = count
        return count <= self._rpm


def add_request_limits(
    app: FastAPI,
    max_body_bytes: int,
    rate_limit_rpm: int,
    trusted_proxy_hops: int = 1,
) -> None:
    """Attach the request-limit middleware (no-op for whichever limit is <= 0)."""
    app.add_middleware(
        RequestLimitMiddleware,
        max_body_bytes=max_body_bytes,
        rate_limit_rpm=rate_limit_rpm,
        trusted_proxy_hops=trusted_proxy_hops,
    )
```

In `genefoundry_router/server.py`, replace only the existing `add_request_limits(...)` line; leave the correlation/origin lines for Task 5:

```python
    add_request_limits(
        app,
        settings.GF_MAX_BODY_BYTES,
        settings.GF_RATE_LIMIT_RPM,
        trusted_proxy_hops=settings.GF_TRUSTED_PROXY_HOPS,
    )  # DoS guard
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_limits.py tests/integration/test_server.py::test_build_app_serves_health -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/limits.py genefoundry_router/server.py tests/unit/test_limits.py
git commit -m "fix(limits): harden rate limiter client attribution"
```

### Task 3: Streaming Body Cap
**Files:**
- Modify: `genefoundry_router/limits.py:1-130` after Task 2.
- Test: `tests/unit/test_limits.py:1-166` after Task 2.

**Interfaces:**
- Consumes: pure-ASGI `RequestLimitMiddleware.__call__(scope: Scope, receive: Receive, send: Send) -> None` and `add_request_limits(...)` from Task 2.
- Produces: `_read_body_until_limit(receive: Receive, limit: int) -> bytes | None` and `_replay_receive(body: bytes, receive: Receive) -> Receive`.

- [ ] **Step 1: Write the failing test**

Update the imports in `tests/unit/test_limits.py` to include `Response`, `ASGIApp`, `Message`, `Receive`, and `Send`, then append the raw-ASGI harness and tests:

```python
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
```

```python
async def _echo_body_app(scope: Scope, receive: Receive, send: Send) -> None:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        elif message["type"] == "http.disconnect":
            break
    await Response(b"".join(chunks), media_type="application/octet-stream")(scope, receive, send)


async def _run_asgi(
    app: ASGIApp,
    messages: list[Message],
    headers: list[tuple[bytes, bytes]] | None = None,
) -> list[Message]:
    sent: list[Message] = []
    pending = list(messages)

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(_scope(headers=headers, method="POST"), receive, send)
    return sent


def _status(sent: list[Message]) -> int:
    for message in sent:
        if message["type"] == "http.response.start":
            return int(message["status"])
    raise AssertionError("http.response.start was not emitted")


def _body(sent: list[Message]) -> bytes:
    return b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )


async def test_chunked_body_without_content_length_over_cap_returns_413() -> None:
    middleware = RequestLimitMiddleware(_echo_body_app, max_body_bytes=5, rate_limit_rpm=0)

    sent = await _run_asgi(
        middleware,
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )

    assert _status(sent) == 413
    assert b"request entity too large" in _body(sent)


async def test_legal_streaming_body_replays_byte_for_byte() -> None:
    middleware = RequestLimitMiddleware(_echo_body_app, max_body_bytes=10, rate_limit_rpm=0)

    sent = await _run_asgi(
        middleware,
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )

    assert _status(sent) == 200
    assert _body(sent) == b"abcdef"


async def test_disconnect_before_complete_body_aborts_without_downstream_call() -> None:
    called = False

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True
        await _echo_body_app(scope, receive, send)

    middleware = RequestLimitMiddleware(downstream, max_body_bytes=10, rate_limit_rpm=0)

    sent = await _run_asgi(
        middleware,
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": True},
            {"type": "http.disconnect"},
        ],
    )

    assert called is False
    assert sent == []


async def test_empty_streaming_body_passes() -> None:
    middleware = RequestLimitMiddleware(_echo_body_app, max_body_bytes=10, rate_limit_rpm=0)

    sent = await _run_asgi(
        middleware,
        [{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert _status(sent) == 200
    assert _body(sent) == b""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_limits.py -q`

Expected: FAIL because the no-`Content-Length` over-cap raw-ASGI request currently reaches the downstream echo app and returns 200, and the early-disconnect raw-ASGI request is replayed downstream instead of aborting.

- [ ] **Step 3: Write minimal implementation**

Replace `genefoundry_router/limits.py` with:

```python
"""Inbound request limits: body-size cap + per-client rate limit (DoS / abuse guard).

A read-only reference gateway still needs back-pressure: without it, an open or buggy
client can exhaust the router or use it to hammer upstream APIs (OWASP LLM10 - unbounded
consumption). Both limits are opt-in via settings; ``<= 0`` disables that limit.
"""

from __future__ import annotations

import time

import structlog
from fastapi import FastAPI
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger(__name__)

_MAX_TRACKED = 100_000


def _scope_client_host(scope: Scope) -> str:
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        host = client[0]
        if isinstance(host, str) and host:
            return host
    return "unknown"


def _client_key(scope: Scope, trusted_proxy_hops: int) -> str:
    """Identify the caller from trusted X-Forwarded-For tail hops, else ASGI client."""
    client_host = _scope_client_host(scope)
    values = Headers(scope=scope).getlist("x-forwarded-for")
    parts = [part.strip() for value in values for part in value.split(",") if part.strip()]
    if trusted_proxy_hops > 0 and len(parts) >= trusted_proxy_hops:
        return parts[-trusted_proxy_hops]
    return client_host


class _ClientDisconnected(Exception):
    """Raised when the client disconnects before the request body completes."""


async def _read_body_until_limit(receive: Receive, limit: int) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise _ClientDisconnected
        if message["type"] != "http.request":
            continue
        body = message.get("body", b"")
        if body:
            total += len(body)
            if total > limit:
                return None
            chunks.append(body)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay_receive(body: bytes, receive: Receive) -> Receive:
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


class RequestLimitMiddleware:
    """Reject oversized bodies (413) and rate-limit per client (429, fixed window)."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int = 0,
        rate_limit_rpm: int = 0,
        trusted_proxy_hops: int = 1,
        window_seconds: int = 60,
    ) -> None:
        self.app = app
        self._max_body = max_body_bytes
        self._rpm = rate_limit_rpm
        self._trusted_proxy_hops = trusted_proxy_hops
        self._window = window_seconds
        self._hits: dict[str, int] = {}
        self._window_index: int | None = None
        self._ceiling_warned = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._content_length_exceeds(scope):
            await JSONResponse({"error": "request entity too large"}, status_code=413)(
                scope, receive, send
            )
            return

        if self._rpm > 0 and not self._rate_allowed(scope):
            await JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(self._window)},
            )(scope, receive, send)
            return

        if self._max_body <= 0:
            await self.app(scope, receive, send)
            return

        try:
            buffered = await _read_body_until_limit(receive, self._max_body)
        except _ClientDisconnected:
            return

        if buffered is None:
            log.warning("request_too_large", limit=self._max_body)
            await JSONResponse({"error": "request entity too large"}, status_code=413)(
                scope, receive, send
            )
            return

        await self.app(scope, _replay_receive(buffered, receive), send)

    def _content_length_exceeds(self, scope: Scope) -> bool:
        if self._max_body <= 0:
            return False
        content_length = Headers(scope=scope).get("content-length")
        if content_length is None or not content_length.isdigit():
            return False
        length = int(content_length)
        if length <= self._max_body:
            return False
        log.warning("request_too_large", content_length=length, limit=self._max_body)
        return True

    def _rate_allowed(self, scope: Scope) -> bool:
        key = _client_key(scope, self._trusted_proxy_hops)
        allowed = self._increment(key, time.monotonic())
        if not allowed:
            log.warning("rate_limited", limit=self._rpm)
        return allowed

    def _increment(self, key: str, now: float) -> bool:
        window = int(now // self._window)
        if self._window_index != window:
            self._hits.clear()
            self._window_index = window
            self._ceiling_warned = False

        if key not in self._hits and len(self._hits) >= _MAX_TRACKED:
            if not self._ceiling_warned:
                log.warning("rate_limit_tracking_ceiling", max_tracked=_MAX_TRACKED)
                self._ceiling_warned = True
            return True

        count = self._hits.get(key, 0) + 1
        self._hits[key] = count
        return count <= self._rpm


def add_request_limits(
    app: FastAPI,
    max_body_bytes: int,
    rate_limit_rpm: int,
    trusted_proxy_hops: int = 1,
) -> None:
    """Attach the request-limit middleware (no-op for whichever limit is <= 0)."""
    app.add_middleware(
        RequestLimitMiddleware,
        max_body_bytes=max_body_bytes,
        rate_limit_rpm=rate_limit_rpm,
        trusted_proxy_hops=trusted_proxy_hops,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_limits.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/limits.py tests/unit/test_limits.py
git commit -m "fix(limits): cap streaming request bodies"
```

### Task 4: `/metrics` Bearer Guard
**Files:**
- Modify: `genefoundry_router/observability.py:5-20` and `genefoundry_router/observability.py:111-117`.
- Modify: `genefoundry_router/server.py:124-125`.
- Test: `tests/unit/test_metrics.py:1-29`.
- Test: `tests/integration/test_server.py:49-54`.

**Interfaces:**
- Consumes: `RouterSettings.GF_METRICS_TOKEN: str | None` from Task 1.
- Produces: `_metrics_authorized(authorization: str | None, token: str) -> bool` and `register_metrics(app: FastAPI, token: str | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

Replace `tests/unit/test_metrics.py` with:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import (
    BACKEND_UP,
    register_health,
    register_metrics,
    set_backend_up,
)
from genefoundry_router.registry import BackendDef


def test_metrics_endpoint_exposes_prometheus_text():
    app = FastAPI()
    register_metrics(app)
    BACKEND_UP.labels(backend="gnomad").set(1)
    client = TestClient(app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_metrics_without_authorization_returns_401_when_token_set():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_metrics_wrong_bearer_token_returns_401():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")
    resp = TestClient(app).get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_metrics_correct_bearer_token_returns_200():
    app = FastAPI()
    register_metrics(app, token="scrape-secret")
    resp = TestClient(app).get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_metrics_public_when_token_is_none():
    app = FastAPI()
    register_metrics(app, token=None)
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text


def test_health_reports_cached_reachability():
    app = FastAPI()
    backends = [BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp")]
    set_backend_up(backends[0], up=True)
    register_health(app, backends)
    body = TestClient(app).get("/health").json()
    assert body["backends"]["reachable"]["gnomad"] is True


def test_health_remains_public_when_metrics_token_set():
    app = FastAPI()
    backends = [BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp")]
    register_metrics(app, token="scrape-secret")
    register_health(app, backends)
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "genefoundry"
```

Append this integration test to `tests/integration/test_server.py`:

```python
def test_build_app_metrics_token_from_settings(gnomad_fake):
    settings = RouterSettings(_env_file=None, GF_METRICS_TOKEN="scrape-secret")
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    client = TestClient(app)

    assert client.get("/metrics").status_code == 401
    resp = client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert resp.status_code == 200
    assert "genefoundry_backend_up" in resp.text
    assert client.get("/health").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_metrics.py tests/integration/test_server.py::test_build_app_metrics_token_from_settings -q`

Expected: FAIL with `TypeError: register_metrics() got an unexpected keyword argument 'token'`.

- [ ] **Step 3: Write minimal implementation**

In `genefoundry_router/observability.py`, update imports and replace `register_metrics` with:

```python
import hmac
import logging
import time

import structlog
from fastapi import FastAPI, Request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import JSONResponse, Response
```

```python
def _metrics_authorized(authorization: str | None, token: str) -> bool:
    parts = (authorization or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].encode("utf-8"), token.encode("utf-8"))


def register_metrics(app: FastAPI, token: str | None = None) -> None:
    """Attach GET /metrics exposing the Prometheus text exposition format."""

    @app.get("/metrics")
    async def metrics(request: Request) -> Response:
        if token is not None and not _metrics_authorized(
            request.headers.get("authorization"), token
        ):
            return JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Response(generate_latest(METRICS_REGISTRY), media_type=CONTENT_TYPE_LATEST)
```

In `genefoundry_router/server.py`, replace `register_metrics(app)` with:

```python
    register_metrics(app, token=settings.GF_METRICS_TOKEN)  # R1.7 - /metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_metrics.py tests/integration/test_server.py::test_build_app_metrics_token_from_settings -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/observability.py genefoundry_router/server.py tests/unit/test_metrics.py tests/integration/test_server.py
git commit -m "fix(metrics): require optional bearer token"
```

### Task 5: Middleware Order
**Files:**
- Modify: `genefoundry_router/server.py:120-125`.
- Test: `tests/integration/test_server.py:49-54` plus Task 4 additions.

**Interfaces:**
- Consumes: `add_request_limits(app, max_body_bytes, rate_limit_rpm, trusted_proxy_hops=...)` from Task 2 and `register_metrics(app, token=...)` from Task 4.
- Produces: `build_app(...) -> FastAPI` middleware order `CorrelationIdMiddleware -> OriginValidationMiddleware -> RequestLimitMiddleware -> routes`.

- [ ] **Step 1: Write the failing test**

Append these tests to `tests/integration/test_server.py`:

```python
def test_request_id_present_on_origin_rejection(gnomad_fake):
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_ORIGINS=["https://allowed.example"],
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})

    resp = TestClient(app).get("/health", headers={"Origin": "https://bad.example"})

    assert resp.status_code == 403
    assert resp.headers.get("X-Request-ID")


def test_request_id_present_on_body_cap_rejection(gnomad_fake):
    settings = RouterSettings(_env_file=None, GF_MAX_BODY_BYTES=2)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})

    resp = TestClient(app).post("/health", content=b"abcd")

    assert resp.status_code == 413
    assert resp.headers.get("X-Request-ID")


def test_request_id_present_on_rate_limit_rejection(gnomad_fake):
    settings = RouterSettings(_env_file=None, GF_RATE_LIMIT_RPM=1)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    resp = client.get("/health")

    assert resp.status_code == 429
    assert resp.headers.get("X-Request-ID")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_server.py -q`

Expected: FAIL because at least the 403, 413, or 429 short-circuit response lacks `X-Request-ID` while `CorrelationIdMiddleware` is innermost.

- [ ] **Step 3: Write minimal implementation**

In `genefoundry_router/server.py`, reorder the middleware block to:

```python
    app = FastAPI(title="GeneFoundry Router", lifespan=lifespan)
    add_request_limits(
        app,
        settings.GF_MAX_BODY_BYTES,
        settings.GF_RATE_LIMIT_RPM,
        trusted_proxy_hops=settings.GF_TRUSTED_PROXY_HOPS,
    )  # DoS guard
    add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)  # R1.4 - MCP Origin MUST
    app.add_middleware(CorrelationIdMiddleware)
    register_health(app, registry)
    register_metrics(app, token=settings.GF_METRICS_TOKEN)  # R1.7 - /metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_server.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add genefoundry_router/server.py tests/integration/test_server.py
git commit -m "fix(server): make correlation ids outermost"
```

### Task 6: Docker Example Fails Closed and New Env Vars Are Documented
**Files:**
- Modify: `.env.docker.example:12-15` and `.env.docker.example:62-64`.
- Modify: `.env.example:10-12`.
- Test: `tests/e2e/test_dev_config_consistency.py:1-8`.
- Test: `tests/unit/test_settings.py` has no Task 6 change because it does not assert example-file contents.

**Interfaces:**
- Consumes: `RouterSettings.GF_AUTH_MODE == "none"` and `RouterSettings.GF_ALLOW_INSECURE is False` defaults, plus `cli.is_insecure_public_bind(auth_mode: str, host: str, allow_insecure: bool) -> bool`.
- Produces: env examples documenting `GF_TRUSTED_PROXY_HOPS=1` and commented `# GF_METRICS_TOKEN=`, with `.env.docker.example` requiring an explicit opt-in before public unauthenticated startup.

- [ ] **Step 1: Write the failing test**

Replace `tests/e2e/test_dev_config_consistency.py` with:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_example_requires_explicit_public_auth_choice():
    text = (ROOT / ".env.docker.example").read_text(encoding="utf-8")
    assert "\n# GF_AUTH_MODE=none\n" in text
    assert "\n# GF_ALLOW_INSECURE=true\n" in text
    assert "\nGF_AUTH_MODE=none\n" not in text
    assert "\nGF_ALLOW_INSECURE=true\n" not in text


def test_env_examples_document_hardening_settings():
    for relative_path in (".env.example", ".env.docker.example"):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "GF_TRUSTED_PROXY_HOPS=1" in text
        assert "# GF_METRICS_TOKEN=" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/e2e/test_dev_config_consistency.py -q`

Expected: FAIL because `.env.docker.example` still has uncommented `GF_AUTH_MODE=none` and `GF_ALLOW_INSECURE=true`, and the example files do not document both new env vars.

- [ ] **Step 3: Write minimal implementation**

In `.env.docker.example`, change option (A) to:

```dotenv
# (A) Deliberately-public, no login - the fleet's current posture. Honest and
#     explicit: acknowledge the open endpoint with the escape hatch.
# GF_AUTH_MODE=none
# GF_ALLOW_INSECURE=true
```

In `.env.docker.example`, insert the hardening settings after `GF_ALLOWED_ORIGINS=https://claude.ai,https://cursor.sh`:

```dotenv
GF_ALLOWED_ORIGINS=https://claude.ai,https://cursor.sh
# Inbound request limits / proxy attribution.
GF_TRUSTED_PROXY_HOPS=1
# Optional Prometheus scrape token. Unset means public metrics.
# GF_METRICS_TOKEN=
GENEFOUNDRY_ROUTER_HOST_PORT=8010
```

In `.env.example`, add the new settings under the inbound request limits block:

```dotenv
# Inbound request limits (DoS/abuse guard). <=0 disables that limit.
GF_MAX_BODY_BYTES=4000000      # reject request bodies over 4 MB (413)
GF_RATE_LIMIT_RPM=0            # per-client requests/min (429); 0 = off, enable in production
GF_TRUSTED_PROXY_HOPS=1        # trusted proxy hops at the tail of X-Forwarded-For; 0 = ignore XFF
# GF_METRICS_TOKEN=            # optional bearer token for /metrics; unset = public metrics
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/e2e/test_dev_config_consistency.py tests/unit/test_settings.py -q`

Expected: PASS for env example files only (`.env.example` and `.env.docker.example`) plus settings tests.

- [ ] **Step 5: Commit**

```bash
git add .env.docker.example .env.example tests/e2e/test_dev_config_consistency.py
git commit -m "docs(security): fail closed in docker env example"
```

### Task 7: Final Verification
**Files:**
- Create: none.
- Modify: no production code.
- Test/Verify: all changed files from Tasks 1-6.

**Interfaces:**
- Consumes: completed Tasks 1-6 and their atomic commits.
- Produces: verified branch with `make ci-local` green and all DoD checks satisfied.

- [ ] **Step 1: Run the line-budget check**

Run: `uv run python scripts/check_file_size.py`

Expected: `Line budget OK.` Current `genefoundry_router/limits.py` count before this work is 77 lines; after the pure-ASGI rewrite it must remain under the 600-line module budget.

- [ ] **Step 2: Run local CI**

Run: `make ci-local`

Expected: PASS for `format-check`, `lint-ci`, `lint-loc`, `mypy`, parallel unit tests, and integration tests.

- [ ] **Step 3: Inspect formatting or generated changes**

Run:

```bash
git status --short
```

Expected: clean working tree. If formatting commands changed files during verification, inspect the diff before committing.

- [ ] **Step 4: Commit formatting-only changes when present**

Run only when `git status --short` shows formatting-only changes:

```bash
git add genefoundry_router tests
git commit -m "style: apply security hardening formatting"
```

Expected: no commit when the working tree is already clean.

- [ ] **Step 5: Definition of Done checklist**

Confirm each item:

```text
[ ] make ci-local is green.
[ ] uv run python scripts/check_file_size.py confirms every module is under budget.
[ ] genefoundry_router/limits.py remains below 600 LOC.
[ ] No new dependencies were added to pyproject.toml or uv.lock.
[ ] /health remains public.
[ ] /metrics is public only when GF_METRICS_TOKEN is unset.
[ ] Caller Authorization headers are still never forwarded to backends.
[ ] The router still exposes Streamable HTTP only.
[ ] Per-call AUDIT logs from `AuditLogMiddleware` record only tool, namespace, outcome, elapsed time, and correlation ID; they do not record tool arguments, tool results, or exception text.
```

## Self-Review Notes
- Spec coverage: Fix 1 maps to Tasks 1-2, Fix 2 maps to Task 3, Fix 3 maps to Task 4, Fix 4 maps to Task 5, Fix 5 maps to Task 6, and spec section 8 maps to Task 7.
- Placeholder scan: no placeholder instructions are left in code or test snippets.
- Type consistency: `add_request_limits`, `register_metrics`, `_client_key`, `_increment`, `_read_body_until_limit`, and `_replay_receive` use the same signatures everywhere; `trusted_proxy_hops` matches between `limits.py`, `server.py`, and tests.
