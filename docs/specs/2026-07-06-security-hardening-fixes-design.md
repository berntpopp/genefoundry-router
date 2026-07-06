# Security Hardening Fixes - Design Spec

- **Date:** 2026-07-06
- **Status:** Approved design decisions; implementation plan is separate
- **Owner:** Bernt Popp
- **Scope:** Five router-side hardening fixes covering inbound rate limiting, streaming body limits,
  metrics exposure, middleware ordering, and Docker example defaults.
- **Boundary:** Research use only; not clinical decision support.

## 1. Summary

The router is a thin FastMCP 3.x aggregator that sits behind nginx-proxy-manager and exposes one
Streamable-HTTP MCP endpoint. A security review found five small but important hardening gaps in the
outer FastAPI/ASGI shell: spoofable rate-limit client keys, unbounded rate-limit state, body-size
enforcement that trusts only `Content-Length`, unauthenticated `/metrics`, correlation IDs missing
from short-circuit rejections, and Docker example defaults that make an accidental public unauthenticated
deployment too easy. This spec locks the approved fixes while preserving the router's existing
operational defaults: no caller-token passthrough, no SSE transport, no new dependencies, and no
change to backend auth or drift/conformance surfaces.

## 2. Background / findings recap

| # | Severity | Anchor | Finding |
|---|---|---|---|
| F1 | WARNING | `genefoundry_router/limits.py:22`, `genefoundry_router/limits.py:44` | `_client_key` trusts the leftmost `X-Forwarded-For` hop while nginx appends the real peer on the right; the same middleware also keeps `_hits` forever, so spoofed keys bypass `GF_RATE_LIMIT_RPM` and grow memory without bound. |
| F2 | SUGGESTION | `genefoundry_router/limits.py:48` | The body cap only rejects an oversized `Content-Length`; a chunked request with no `Content-Length` can stream past `GF_MAX_BODY_BYTES`. |
| F3 | SUGGESTION | `genefoundry_router/observability.py:111`, `genefoundry_router/server.py:124`, `genefoundry_router/server.py:125` | `/health` and `/metrics` are registered on the outer app at root, outside auth; `/metrics` leaks namespace counts, latencies, and backend up/down, while `/health` intentionally remains public for container checks. |
| F4 | SUGGESTION | `genefoundry_router/server.py:121`, `genefoundry_router/server.py:122`, `genefoundry_router/server.py:123` | `CorrelationIdMiddleware` is added first, which makes it innermost under Starlette's last-added-is-outermost semantics; 403/413/429 short-circuits can be emitted before request ID binding. |
| F5 | SUGGESTION | `.env.docker.example:14`, `.env.docker.example:15` | `.env.docker.example` ships `GF_AUTH_MODE=none` and `GF_ALLOW_INSECURE=true` uncommented, so a verbatim public Docker deployment starts as an unauthenticated MCP endpoint. |

The deployed topology matters for F1 and F5: the router is expose-only behind nginx-proxy-manager
(`docker/docker-compose.npm.yml:7`), and nginx appends the real peer to the right side of
`X-Forwarded-For`.

## 3. Non-goals / out of scope

- The `rewrite_tool_refs` recursion-depth concern is out of scope. Backends are operator-trusted, and
  the practical risk is negligible compared with the inbound boundary issues above.
- No change to the no-token-passthrough invariant. Caller `Authorization` headers must still never be
  forwarded to backends.
- No change to the main auth flow, OAuth/JWT semantics, insecure-bind guard logic, drift detection,
  conformance checks, tool search, or response-envelope behavior.
- No new transport mode. The router remains Streamable HTTP only.
- No new third-party dependencies. The metrics token check uses stdlib `hmac`.
- No source-code implementation or implementation plan in this document.

## 4. Config surface

Both settings default to operational no-ops: existing deployments do not need new environment values
to keep running. The defaults are still security-improving in the current nginx-proxy-manager topology.

| Setting | Type | Default | Meaning |
|---|---|---|---|
| `GF_TRUSTED_PROXY_HOPS` | `int` | `1` | Number of trusted proxy hops at the tail of `X-Forwarded-For`. `1` means "trust nginx-proxy-manager's appended peer"; `0` or insufficient hops falls back to `request.client.host`. |
| `GF_METRICS_TOKEN` | `str \| None` | `None` | Optional bearer token required for `GET /metrics`. `None` means public metrics, preserving current behavior; when set, clients must send `Authorization: Bearer <token>`. |

`GF_METRICS_TOKEN` should be documented as commented-out in example env files. Blank or whitespace-only
values should normalize to `None` so examples cannot accidentally enable an impossible empty-token
requirement.

## 5. Design

### 5.1 Fix 1 - Rate limiter client key and bounded state

**Problem.** The current `_client_key` returns the leftmost `X-Forwarded-For` value. That is safe only
when the immediate proxy overwrites the header. nginx-proxy-manager appends the real peer to the
right, so a caller can send a different leftmost spoof per request and get a fresh rate-limit bucket.
The current `_hits: dict[str, tuple[int, int]]` also retains every historical key forever.

**Approved approach.**

- Add `GF_TRUSTED_PROXY_HOPS` to `RouterSettings` and pass it into `RequestLimitMiddleware`.
- Parse `X-Forwarded-For` into trimmed non-empty parts.
- Select `parts[-GF_TRUSTED_PROXY_HOPS]`.
- If `GF_TRUSTED_PROXY_HOPS == 0`, if the header is missing, or if `len(parts) < GF_TRUSTED_PROXY_HOPS`,
  fall back to `request.client.host` / the ASGI `scope["client"]` host.
- Replace per-key `(window, count)` tuples with one fixed-window map that is cleared when the global
  fixed-window index advances.
- Add a hard `_MAX_TRACKED` ceiling, for example `100_000` keys per window. If the ceiling is reached
  mid-window, fail open for new keys: log one warning, skip tracking those new keys, and resume normal
  tracking after the next window clear.

**Key code shape.**

```python
_MAX_TRACKED = 100_000


def _client_key(scope: Scope, trusted_proxy_hops: int) -> str:
    client_host = _scope_client_host(scope)
    values = Headers(scope=scope).getlist("x-forwarded-for")
    parts = [part.strip() for value in values for part in value.split(",") if part.strip()]
    if trusted_proxy_hops > 0 and len(parts) >= trusted_proxy_hops:
        return parts[-trusted_proxy_hops]
    return client_host
```

```python
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
        return True  # fail open for this new key

    count = self._hits.get(key, 0) + 1
    self._hits[key] = count
    return count <= self._rpm
```

**XFF hop-selection examples.**

| Case | `X-Forwarded-For` | `GF_TRUSTED_PROXY_HOPS` | Resulting key | Why |
|---|---|---:|---|---|
| Honest client behind nginx | `198.51.100.10` | `1` | `198.51.100.10` | Single trusted tail hop is the peer nginx actually saw. |
| Spoofed leftmost value | `1.2.3.4, 198.51.100.10` | `1` | `198.51.100.10` | The attacker-controlled leftmost value is ignored. |
| Two trusted proxies | `1.2.3.4, 203.0.113.55, 198.51.100.7` | `2` | `203.0.113.55` | Trust the two-hop tail and key on the client seen by the first trusted proxy. |
| Misconfigured hop count | `198.51.100.10` | `2` | `request.client.host` | Insufficient header depth falls back rather than trusting the wrong hop. |
| XFF disabled | any value | `0` | `request.client.host` | Operators can opt out of XFF trust entirely. |

**Fail-open ceiling rationale.** `_MAX_TRACKED` is a memory safety valve, not an authentication or abuse
policy. Failing closed after the ceiling would let one attacker fill the per-window key map and force
a global 429 for every new client behind the proxy. Failing open caps memory deterministically while
preserving availability: already tracked clients still enforce their counters, oversized bodies still
hit the 413 path, and the limiter automatically recovers at the next fixed-window clear.

**Edge cases.**

- Missing, empty, or whitespace-only `X-Forwarded-For` falls back to the ASGI client host.
- IPv4, IPv6, and opaque proxy-provided strings are treated as keys after trimming; this fix does not
  add IP validation.
- Window clearing uses `time.monotonic()` as today; wall-clock changes do not affect counters.
- `GF_RATE_LIMIT_RPM <= 0` still disables only the rate limit; body capping remains independent.
- Logs remain PII-minimal: no request body, headers, or bearer tokens are logged.

### 5.2 Fix 2 - Streaming body cap without `Content-Length`

**Problem.** The current middleware checks only `Content-Length` before delegating to the app. A
client can omit that header and stream a chunked body that exceeds `GF_MAX_BODY_BYTES`.

**Approved approach.**

Convert `RequestLimitMiddleware` from `BaseHTTPMiddleware` to pure ASGI. The middleware should:

- pass through non-HTTP scopes unchanged;
- reject an oversized numeric `Content-Length` immediately with 413;
- run the rate-limit check before delegating to the app;
- when `GF_MAX_BODY_BYTES > 0`, read `http.request` messages from `receive`, accumulating at most
  `GF_MAX_BODY_BYTES + 1` bytes;
- return 413 as soon as the accumulated body exceeds the cap, regardless of `Content-Length`;
- otherwise replay the fully buffered body to the downstream app through a wrapped `receive`.

MCP requests are POST JSON and the configured cap is about 4 MB, so bounded buffering is acceptable.
GET requests have no request body in the supported Streamable-HTTP flow, and SSE is not offered by
the router.

**Key code shape.**

```python
class RequestLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int = 0, rate_limit_rpm: int = 0, ...) -> None:
        self.app = app
        self._max_body = max_body_bytes
        self._rpm = rate_limit_rpm

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

        buffered = await self._read_body_until_limit(receive)
        if buffered is None:
            await JSONResponse({"error": "request entity too large"}, status_code=413)(
                scope, receive, send
            )
            return
        await self.app(scope, _replay_receive(buffered), send)
```

**Edge cases.**

- Invalid or non-numeric `Content-Length` does not bypass the cap; the streaming path still enforces
  the real body size.
- A legal body is replayed byte-for-byte to the app, including the empty final `http.request`.
- On over-cap streaming requests, the middleware stops reading once the cap is exceeded and emits
  raw-ASGI `JSONResponse`.
- `GF_MAX_BODY_BYTES <= 0` preserves the current disabled-body-cap behavior.
- The body cap and rate limiter remain one middleware so ordering is explicit and no extra
  middleware interaction is introduced.

### 5.3 Fix 3 - Optional `/metrics` bearer guard

**Problem.** `/health` and `/metrics` are registered on the outer FastAPI app at root before the MCP
sub-app mount. They are outside router auth and can be proxied publicly. `/metrics` leaks operational
details such as per-namespace tool-call counts, latencies, and backend up/down state.

**Approved approach.**

- Change `register_metrics(app)` to `register_metrics(app, token: str | None = None)`.
- If `token is None`, keep `/metrics` public to preserve existing deployments.
- If `token` is set, require `Authorization: Bearer <token>`.
- Compare the supplied token with `hmac.compare_digest`.
- Return 401 for missing, malformed, or wrong credentials.
- Leave `/health` public because the container healthcheck curls `http://localhost:8000/health`
  without auth, and health visibility is already relied on by Docker/NPM operations.
- Wire `settings.GF_METRICS_TOKEN` from `server.py`.

**Key code shape.**

```python
def _metrics_authorized(authorization: str | None, token: str) -> bool:
    parts = (authorization or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].encode("utf-8"), token.encode("utf-8"))


def register_metrics(app: FastAPI, token: str | None = None) -> None:
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

**Edge cases.**

- Wrong auth scheme, missing header, empty bearer value, and wrong token all return 401.
- Tokens are never logged.
- `/health` remains public in all modes.
- `GF_METRICS_TOKEN` is a scrape credential only; it is not caller auth and is never forwarded
  upstream.

### 5.4 Fix 4 - Correlation ID middleware order

**Problem.** Starlette wraps middleware in reverse addition order: the last-added middleware is the
outermost. `build_app` currently adds `CorrelationIdMiddleware` first, then origin validation, then
request limits. As a result, origin/body/rate short-circuits can return before the request ID is bound
into contextvars and before `X-Request-ID` is attached.

**Approved approach.**

Reorder only the middleware additions so correlation ID is added last and is therefore outermost.
No validation, rate-limit, body-limit, routing, or auth logic changes.

**Key code shape.**

```python
add_request_limits(
    app,
    settings.GF_MAX_BODY_BYTES,
    settings.GF_RATE_LIMIT_RPM,
    trusted_proxy_hops=settings.GF_TRUSTED_PROXY_HOPS,
)
add_origin_validation(app, settings.GF_ALLOWED_ORIGINS)
app.add_middleware(CorrelationIdMiddleware)
```

Request flow becomes:

```text
CorrelationId -> OriginValidation -> RequestLimitMiddleware -> FastAPI routes / MCP app
```

**Edge cases.**

- 403 origin rejections, 413 body rejections, and 429 rate-limit rejections all carry
  `X-Request-ID`.
- `structlog.contextvars.merge_contextvars` continues to merge the request ID into logs.
- Route registration order stays unchanged: `/health` and `/metrics` remain registered before the
  root MCP mount.

### 5.5 Fix 5 - Docker example fails closed

**Problem.** `.env.docker.example` binds the container to `0.0.0.0` and currently leaves the insecure
auth escape hatch uncommented. A verbatim copy to a public host starts open behind the proxy instead
of requiring an operator to make an explicit auth choice.

**Approved approach.**

- Comment out the option (A) insecure lines in `.env.docker.example`.
- Keep the explanatory text that says option (A) is deliberate-public and requires an explicit
  escape hatch.
- With `GF_AUTH_MODE` and `GF_ALLOW_INSECURE` unset, `RouterSettings` defaults to
  `GF_AUTH_MODE="none"` and `GF_ALLOW_INSECURE=False`; `cli.py` then refuses to start on `0.0.0.0`.
- Document `GF_TRUSTED_PROXY_HOPS` and `GF_METRICS_TOKEN` in both `.env.docker.example` and
  `.env.example`.

**Key code shape.**

```dotenv
# (A) Deliberately-public, no login.
# GF_AUTH_MODE=none
# GF_ALLOW_INSECURE=true

# Inbound request limits / proxy attribution.
GF_TRUSTED_PROXY_HOPS=1

# Optional Prometheus scrape token. Unset means public metrics.
# GF_METRICS_TOKEN=
```

**Edge cases.**

- A verbatim Docker copy fails closed on a non-loopback bind until the operator chooses exactly one
  of option (A), (B), or (C).
- Local `.env.example` can keep `GF_AUTH_MODE=none` and `GF_ALLOW_INSECURE=false`; it is loopback by
  default and remains suitable for development.
- Example-token lines should be commented or normalized so a blank token does not accidentally create
  a broken protected metrics endpoint.

## 6. Per-file change list

- `genefoundry_router/config.py`
  - Add `GF_TRUSTED_PROXY_HOPS: int = 1`.
  - Add `GF_METRICS_TOKEN: str | None = None`.
  - Add a small validator if needed so blank `GF_METRICS_TOKEN` values normalize to `None`.
- `genefoundry_router/limits.py`
  - Replace `BaseHTTPMiddleware` with pure ASGI middleware.
  - Add trusted-proxy-hop client-key selection.
  - Replace never-pruned `(window, count)` state with a per-window-cleared `dict[str, int]`.
  - Add `_MAX_TRACKED` fail-open ceiling and one-warning-per-window logging.
  - Enforce streaming body caps for requests without `Content-Length`, then replay legal bodies.
- `genefoundry_router/observability.py`
  - Import stdlib `hmac` and `Request` / `JSONResponse`.
  - Change `register_metrics` to accept an optional token and require bearer auth when set.
  - Keep `register_health` unchanged and public.
- `genefoundry_router/server.py`
  - Pass `settings.GF_TRUSTED_PROXY_HOPS` into `add_request_limits`.
  - Pass `settings.GF_METRICS_TOKEN` into `register_metrics`.
  - Reorder middleware additions so `CorrelationIdMiddleware` is added last.
- `.env.docker.example`
  - Comment out `GF_AUTH_MODE=none` and `GF_ALLOW_INSECURE=true` in option (A).
  - Document `GF_TRUSTED_PROXY_HOPS=1`.
  - Document commented `GF_METRICS_TOKEN`.
- `.env.example`
  - Document `GF_TRUSTED_PROXY_HOPS=1`.
  - Document commented `GF_METRICS_TOKEN`.
- Tests
  - Extend `tests/unit/test_limits.py`, `tests/unit/test_metrics.py`,
    `tests/unit/test_settings.py`, `tests/integration/test_server.py`, and
    `tests/e2e/test_dev_config_consistency.py` as described below.

## 7. Test plan (TDD)

Write the failing tests first, then implement the smallest code change that makes them pass.

- `tests/unit/test_limits.py`
  - XFF hop selection ignores a spoofed leftmost value: requests with
    `X-Forwarded-For: spoof-1, 198.51.100.10` and `spoof-2, 198.51.100.10` share one bucket when
    `trusted_proxy_hops=1`.
  - Rate limit is no longer bypassable by header rotation: with `rate_limit_rpm=2`, the third request
    from the same rightmost trusted hop returns 429 even if the leftmost spoof changes.
  - `GF_TRUSTED_PROXY_HOPS=2` selects the second-from-right hop, and insufficient header depth falls
    back to the ASGI client host.
  - `_hits` clears across fixed-window boundaries. Monkeypatch `genefoundry_router.limits.time.monotonic`
    so the second window starts with an empty map and counters reset.
  - `_MAX_TRACKED` ceiling holds. Monkeypatch the constant low, fill the map, assert `len(_hits)` never
    exceeds the ceiling, assert only one warning is logged, and assert new untracked keys fail open
    until the next window.
  - Chunked/no-`Content-Length` body over the cap returns 413. Use a small raw-ASGI harness rather than
    `TestClient` so the request has multiple `http.request` chunks and no `Content-Length`.
  - Numeric `Content-Length` fast-path still returns 413 without invoking the downstream app.
  - A legal buffered body replays intact to the app; the downstream handler sees exactly the original
    bytes.
  - `max_body_bytes=0` and `rate_limit_rpm=0` still disable their respective limits.
- `tests/unit/test_metrics.py`
  - `register_metrics(app, token="secret")` returns 401 without `Authorization`.
  - The same endpoint returns 401 for the wrong bearer token.
  - The same endpoint returns 200 with `Authorization: Bearer secret`.
  - `register_metrics(app, token=None)` remains public and returns Prometheus text.
  - `/health` remains public; existing cached-reachability assertions stay valid.
- `tests/integration/test_server.py`
  - Build the full app and assert `X-Request-ID` is present on a 403 origin rejection.
  - Build the full app with a low body cap and assert `X-Request-ID` is present on a 413 response.
  - Build the full app with `GF_RATE_LIMIT_RPM=1` and assert `X-Request-ID` is present on the 429
    response.
  - Assert `register_metrics` is wired from settings by checking the 401/200 matrix through `build_app`
    when `GF_METRICS_TOKEN` is set.
- `tests/unit/test_settings.py`
  - Defaults include `GF_TRUSTED_PROXY_HOPS == 1`.
  - Defaults include `GF_METRICS_TOKEN is None`.
  - Env override parses `GF_TRUSTED_PROXY_HOPS` as an int.
  - Env override parses `GF_METRICS_TOKEN` as `str | None`, including blank-to-`None` if that validator
    is added.
- `tests/e2e/test_dev_config_consistency.py`
  - Update consistency expectations so the documented env examples and generated dev config account
    for `GF_TRUSTED_PROXY_HOPS` and `GF_METRICS_TOKEN`.
  - Preserve the existing assertion that committed dev config warnings are empty.

## 8. Verification / Definition of Done

- `make ci-local` is green: format-check, lint, `lint-loc`, mypy, unit tests, and integration tests.
- `make lint-loc` confirms every module remains under the 600-LOC budget. `limits.py` will grow the
  most, so check it explicitly after the pure-ASGI rewrite.
- No new dependencies are added to `pyproject.toml` or `uv.lock`.
- `/health` remains public; `/metrics` is public only when `GF_METRICS_TOKEN` is unset.
- No caller `Authorization` header is forwarded to backends.
- The router still exposes Streamable HTTP only.
