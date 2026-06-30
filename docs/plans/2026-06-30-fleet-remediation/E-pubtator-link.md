# pubtator-link Write-Surface Hardening (#85) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Close issue #85 in `pubtator-link` by jailing audit-bundle `export_path` to a configured base directory, capping the unbounded `pmids`/`curated_urls` review-index inputs, keying inbound rate limiting on a trusted-proxy `X-Forwarded-For` when directly reachable, binding the default compose to loopback, and documenting the write/full profile as gateway-only.

**Architecture:** `pubtator-link` is a thin FastMCP 3.x backend with a FastAPI/ASGI shell (`server_manager.py`) that mounts the MCP Streamable-HTTP app and wraps it in pure-ASGI middleware. The write surface is concentrated in the `full` MCP tool profile (`export_review_audit_bundle`, `index_review_evidence`, text-annotation submit) and is unauthenticated by design, so safety relies on (a) input validation in the pydantic request models and adapter sinks, and (b) the backend being reachable only through the router/reverse proxy. This plan tightens (a) at the filesystem sink and request models, and hardens (b) so a directly reachable instance still degrades safely.

**Tech Stack:** Python 3.12+, FastMCP 3.x, FastAPI/Starlette ASGI, pydantic v2 + pydantic-settings, pytest + pytest-asyncio, uv, ruff, mypy, Docker Compose.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-`Authorization` passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

Created:
- `docs/SECURITY.md` — documents the unauthenticated-by-design posture, the write/full profile, loopback-default deployment, `review_export_base_dir`, and `trust_proxy_headers`; references issue #85.

Modified:
- `pubtator_link/config.py` — add `review_export_base_dir: str | None` and `trust_proxy_headers: bool` settings.
- `pubtator_link/mcp/service_adapters.py` — import `settings`; add `export_base_dir` param + `_audit_export_base_error()` base-dir containment guard to `export_review_audit_bundle_impl`.
- `pubtator_link/models/review_rerag.py` — add `max_length=200` to `IndexReviewEvidenceRequest.pmids` and `.curated_urls`.
- `pubtator_link/server_manager.py` — `InboundRateLimitMiddleware` derives client key from trusted-proxy XFF when enabled; wire `trust_proxy_headers` at construction.
- `docker/docker-compose.yml` — publish the app port to `127.0.0.1` only.
- `tests/unit/mcp/test_mcp_service_adapters.py` — base-dir traversal tests; thread `export_base_dir` through existing write tests.
- `tests/unit/test_review_rerag_models.py` — list-cap test.
- `tests/unit/test_server_manager.py` — trusted-proxy XFF rate-limit tests.
- `tests/unit/test_docker_compose_postgres.py` — loopback-bind assertion.

---

### Task 1: Jail `export_path` to a configured base directory

OWASP's path-traversal guidance is to canonicalize the user-supplied path (resolving `..` and symlinks) and then assert it is contained within an allowed base directory; in Python this is `Path.expanduser().resolve()` + `Path.is_relative_to(base)` (see [OWASP Path Traversal](https://owasp.org/www-community/attacks/Path_Traversal) and [Preventing Directory Traversal Vulnerabilities in Python](https://salvatoresecurity.com/preventing-directory-traversal-vulnerabilities-in-python/)). The current guard (`_audit_export_path_error`, `service_adapters.py:1633-1648`) checks symlink/exists/is-dir/parent-writable but never canonicalizes or enforces containment, so `open(output_path, "x")` at `:1606` can create a file anywhere writable. We add a secure-by-default base: when `review_export_base_dir` is unset, file export is disabled (inline/compact still work); when set, the resolved path must stay within it.

**Files**
- Modify: `pubtator_link/config.py` (add setting after the inbound-rate-limit block, ~`:85`)
- Modify: `pubtator_link/mcp/service_adapters.py:14` (import), `:1569-1592` (signature + wiring), new helper near `:1633`
- Test: `tests/unit/mcp/test_mcp_service_adapters.py` (new tests + update existing write tests ~`:572-694`)

**Interfaces**
- Consumes: `export_review_audit_bundle_impl(*, service, review_id, session_id=None, export_path=None, fallback_inline=False, response_mode="compact", export_base_dir: str | None = None)` — new keyword-only `export_base_dir`; when `None`, falls back to `settings.review_export_base_dir`.
- Produces: `_audit_export_base_error(output_path: Path, base_dir: str | None) -> dict[str, Any] | None` — returns an `export_path` field error when `base_dir` is `None` (disabled) or when `output_path.resolve()` escapes the resolved base; `None` when contained.

Steps:

- [ ] Write the failing test — append to `tests/unit/mcp/test_mcp_service_adapters.py`:
```python
@pytest.mark.asyncio
async def test_export_rejects_absolute_path_outside_base(tmp_path) -> None:
    from pubtator_link.mcp.service_adapters import export_review_audit_bundle_impl

    result = await export_review_audit_bundle_impl(
        service=_FakeReviewAuditBundleService(),
        review_id="rev_123",
        export_path="/etc/pubtator_owned.json",
        export_base_dir=str(tmp_path),
    )

    assert result["success"] is False
    assert result["error"]["field_errors"][0]["field"] == "export_path"
    assert not (tmp_path / "pubtator_owned.json").exists()


@pytest.mark.asyncio
async def test_export_rejects_parent_traversal_escape(tmp_path) -> None:
    from pubtator_link.mcp.service_adapters import export_review_audit_bundle_impl

    base = tmp_path / "exports"
    base.mkdir()
    result = await export_review_audit_bundle_impl(
        service=_FakeReviewAuditBundleService(),
        review_id="rev_123",
        export_path=str(base / ".." / ".." / "escape.json"),
        export_base_dir=str(base),
    )

    assert result["success"] is False
    assert result["error"]["field_errors"][0]["field"] == "export_path"
    assert not (tmp_path / "escape.json").exists()


@pytest.mark.asyncio
async def test_export_disabled_when_no_base_configured(tmp_path) -> None:
    from pubtator_link.mcp.service_adapters import export_review_audit_bundle_impl

    result = await export_review_audit_bundle_impl(
        service=_FakeReviewAuditBundleService(),
        review_id="rev_123",
        export_path=str(tmp_path / "audit.json"),
        export_base_dir=None,
    )

    assert result["success"] is False
    assert result["error"]["field_errors"][0]["field"] == "export_path"
    assert not (tmp_path / "audit.json").exists()
```
  Also thread `export_base_dir=str(tmp_path)` into the four existing tests that expect a successful or filesystem-level outcome (they pass a path under `tmp_path`): `test_export_review_audit_bundle_adapter_writes_new_file` (`:572`), `..._refuses_existing_file` (`:589`), `..._refuses_directory` (`:608`, pass `export_base_dir=str(tmp_path)`), `..._returns_field_error_without_inline` (`:623`), and `test_export_review_audit_bundle_oversized_inline_fallback_preserves_field_errors` (`:678`). Leave `..._returns_inline_fallback` (`:641`) unchanged — with no base it now exercises the disabled→inline path and its asserts (`inline_bundle` present, `export_path is None`) still hold.

- [ ] Run it, expect FAIL: `uv run pytest tests/unit/mcp/test_mcp_service_adapters.py -k "outside_base or parent_traversal or disabled_when_no_base" -q`
  Expected: `TypeError: export_review_audit_bundle_impl() got an unexpected keyword argument 'export_base_dir'` (param does not exist yet).

- [ ] Minimal implementation. In `pubtator_link/config.py`, add after `inbound_rate_limit_per_minute` (~`:85`):
```python
    review_export_base_dir: str | None = Field(
        default=None,
        description=(
            "Base directory that export_review_audit_bundle export_path writes must "
            "resolve within (canonicalized). Unset disables file export; inline/compact "
            "responses still work."
        ),
    )
```
  In `pubtator_link/mcp/service_adapters.py:14`, widen the import:
```python
from pubtator_link.config import settings, text_processing_config
```
  Add `export_base_dir` to the signature (`:1569-1577`):
```python
async def export_review_audit_bundle_impl(
    *,
    service: ReviewAuditService,
    review_id: str,
    session_id: str | None = None,
    export_path: str | None = None,
    fallback_inline: bool = False,
    response_mode: Literal["full", "compact"] = "compact",
    export_base_dir: str | None = None,
) -> dict[str, Any]:
```
  Insert the containment check ahead of the existing FS guard (replace `:1590-1592`):
```python
    output_path = Path(export_path).expanduser()
    serialized = json.dumps(bundle_json, separators=(",", ":"), sort_keys=True)
    base_dir = export_base_dir if export_base_dir is not None else settings.review_export_base_dir
    field_error = _audit_export_base_error(output_path, base_dir)
    if field_error is None:
        field_error = _audit_export_path_error(output_path)
```
  Add the helper next to `_audit_export_path_error` (~`:1633`):
```python
def _audit_export_base_error(output_path: Path, base_dir: str | None) -> dict[str, Any] | None:
    if base_dir is None:
        return _audit_export_path_field_error(
            "file export is disabled; set PUBTATOR_LINK_REVIEW_EXPORT_BASE_DIR"
        )
    base = Path(base_dir).expanduser().resolve()
    try:
        resolved = output_path.resolve()
    except (OSError, RuntimeError):
        return _audit_export_path_field_error("export path could not be resolved")
    if not resolved.is_relative_to(base):
        return _audit_export_path_field_error(
            "export path escapes the configured base directory"
        )
    return None
```

- [ ] Run it, expect PASS: `uv run pytest tests/unit/mcp/test_mcp_service_adapters.py -q`
  Expected: all export tests pass, including the three new traversal tests and the threaded existing tests.

- [ ] Commit: `fix(review): jail audit export_path to a configured base dir (#85-B)`

---

### Task 2: Cap `pmids` and `curated_urls` on the review-index request

`IndexReviewEvidenceRequest.pmids` and `.curated_urls` (`models/review_rerag.py:195-196`) are `list[str] = Field(default_factory=list)` with no bound, while sibling list fields in the same module cap at `max_length=200`/`500` (e.g. `selected_pmids` `:900`, `selected_passage_ids` `:906`). Each entry drives a DB write and an outbound source fetch, so an unbounded list is a write/fetch-amplification vector. pydantic v2 enforces item-count limits via `Field(max_length=...)` on list fields (see [pydantic Fields](https://docs.pydantic.dev/latest/concepts/fields/)); we mirror the module's `200` cap.

**Files**
- Modify: `pubtator_link/models/review_rerag.py:195-196`
- Test: `tests/unit/test_review_rerag_models.py`

**Interfaces**
- Produces: `IndexReviewEvidenceRequest(pmids=[...], curated_urls=[...])` rejects either list with > 200 items via `pydantic.ValidationError`.

Steps:

- [ ] Write the failing test — append to `tests/unit/test_review_rerag_models.py`:
```python
def test_index_review_evidence_caps_pmids_and_curated_urls() -> None:
    with pytest.raises(ValidationError):
        IndexReviewEvidenceRequest(pmids=[str(i) for i in range(201)])
    with pytest.raises(ValidationError):
        IndexReviewEvidenceRequest(
            curated_urls=[f"https://example.org/{i}" for i in range(201)]
        )
    ok = IndexReviewEvidenceRequest(
        pmids=[str(i) for i in range(200)],
        curated_urls=[f"https://example.org/{i}" for i in range(200)],
    )
    assert len(ok.pmids) == 200
    assert len(ok.curated_urls) == 200
```

- [ ] Run it, expect FAIL: `uv run pytest tests/unit/test_review_rerag_models.py -k caps_pmids -q`
  Expected: `Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>` (201 items currently accepted).

- [ ] Minimal implementation — `pubtator_link/models/review_rerag.py:195-196`:
```python
    pmids: list[str] = Field(default_factory=list, max_length=200)
    curated_urls: list[str] = Field(default_factory=list, max_length=200)
```

- [ ] Run it, expect PASS: `uv run pytest tests/unit/test_review_rerag_models.py -k caps_pmids -q`
  Expected: 1 passed.

- [ ] Commit: `fix(review): cap index_review_evidence pmids/curated_urls at 200 (#85-C)`

---

### Task 3: Key inbound rate limiting on a trusted-proxy `X-Forwarded-For`

`InboundRateLimitMiddleware` keys on `scope["client"][0]` (`server_manager.py:157-158`), the socket peer. Behind a reverse proxy every request shares the proxy's IP, collapsing all callers into one bucket; if directly reachable, the per-client limit works but ignores XFF. Security guidance is to trust XFF only when the immediate peer is a known proxy and to read the **rightmost** entry (the address the trusted proxy observed), never the client-spoofable leftmost (see [MDN X-Forwarded-For](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Forwarded-For) and [The perils of the "real" client IP](https://adam-p.ca/blog/2022/03/x-forwarded-for/)). We add an opt-in `trust_proxy_headers` flag (default `False`); when on, the limiter keys on the rightmost XFF entry, otherwise it keeps the socket peer.

**Files**
- Modify: `pubtator_link/config.py` (add setting near the rate-limit block, ~`:85`)
- Modify: `pubtator_link/server_manager.py:142-145` (`__init__`), `:155-158` (`__call__`), new `_client_ip` helper, `:267-271` (wiring)
- Test: `tests/unit/test_server_manager.py`

**Interfaces**
- Consumes: `InboundRateLimitMiddleware(app, *, requests_per_minute: int, trust_proxy_headers: bool = False)`.
- Produces: `_client_ip(scope) -> str` — rightmost non-empty XFF entry when `trust_proxy_headers` is set and the header is present, else `scope["client"][0]` (or `"unknown"`).

Steps:

- [ ] Write the failing test — append to `tests/unit/test_server_manager.py`:
```python
def test_inbound_rate_limit_keys_on_trusted_proxy_xff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pubtator_link.server_manager.settings.enable_inbound_rate_limit", True)
    monkeypatch.setattr("pubtator_link.server_manager.settings.inbound_rate_limit_per_minute", 1)
    monkeypatch.setattr("pubtator_link.server_manager.settings.trust_proxy_headers", True)

    manager = UnifiedServerManager(logger=LoggerDouble())
    app = manager.create_app(include_mcp=False)

    @app.get("/limited")
    async def limited() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/limited", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200
    assert client.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429


def test_inbound_rate_limit_ignores_xff_when_proxy_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("pubtator_link.server_manager.settings.enable_inbound_rate_limit", True)
    monkeypatch.setattr("pubtator_link.server_manager.settings.inbound_rate_limit_per_minute", 1)
    monkeypatch.setattr("pubtator_link.server_manager.settings.trust_proxy_headers", False)

    manager = UnifiedServerManager(logger=LoggerDouble())
    app = manager.create_app(include_mcp=False)

    @app.get("/limited")
    async def limited() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    # Spoofed XFF must not buy a fresh bucket when the proxy is untrusted.
    assert client.get("/limited", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429
```

- [ ] Run it, expect FAIL: `uv run pytest tests/unit/test_server_manager.py -k "trusted_proxy_xff or ignores_xff" -q`
  Expected: `AttributeError` on `settings.trust_proxy_headers` (setting absent) — and once that exists, the trusted-proxy test fails because XFF is ignored (third request returns 200, not 429).

- [ ] Minimal implementation. In `pubtator_link/config.py`, add near the rate-limit block (~`:85`):
```python
    trust_proxy_headers: bool = Field(
        default=False,
        description=(
            "Trust the rightmost X-Forwarded-For entry (added by a known reverse proxy) "
            "for inbound rate limiting. Leave False when directly reachable."
        ),
    )
```
  In `pubtator_link/server_manager.py`, update `InboundRateLimitMiddleware.__init__` (`:142-145`):
```python
    def __init__(
        self, app: ASGIApp, *, requests_per_minute: int, trust_proxy_headers: bool = False
    ) -> None:
        self.app = app
        self.requests_per_minute = requests_per_minute
        self.trust_proxy_headers = trust_proxy_headers
        self.requests: defaultdict[str, deque[float]] = defaultdict(deque)
```
  Replace the peer-IP lines in `__call__` (`:157-158`):
```python
        client_ip = self._client_ip(scope)
```
  Add the helper method (rightmost XFF entry; client controls only the leftmost):
```python
    def _client_ip(self, scope: Scope) -> str:
        if self.trust_proxy_headers:
            for raw_name, raw_value in scope.get("headers", []):
                if raw_name == b"x-forwarded-for":
                    parts = [
                        part.strip()
                        for part in raw_value.decode("latin-1").split(",")
                        if part.strip()
                    ]
                    if parts:
                        return parts[-1]
        client = scope.get("client")
        return client[0] if client else "unknown"
```
  Wire the flag where the middleware is added (`:267-271`):
```python
        if settings.enable_inbound_rate_limit:
            app.add_middleware(
                InboundRateLimitMiddleware,
                requests_per_minute=settings.inbound_rate_limit_per_minute,
                trust_proxy_headers=settings.trust_proxy_headers,
            )
```

- [ ] Run it, expect PASS: `uv run pytest tests/unit/test_server_manager.py -q`
  Expected: new tests pass and existing rate-limit tests (`:197`, `:258`, `:324`) still pass.

- [ ] Commit: `fix(server): key inbound rate limit on trusted-proxy XFF (#85-A/D)`

---

### Task 4: Bind the default compose to loopback

The base `docker/docker-compose.yml` publishes the app on `"${PUBTATOR_LINK_PORT:-8000}:8000"` (`:66`), which Docker binds to `0.0.0.0` on the host — exposing the unauthenticated backend to every interface. The hardened `docker-compose.prod.yml` already drops published ports (`ports: !reset []`, expose-only behind the proxy); the base file should at minimum publish to loopback only so a local reverse proxy can reach it while the LAN cannot. The in-container `--host 0.0.0.0` and `PUBTATOR_LINK_HOST: 0.0.0.0` stay unchanged — they are required for Docker's published-port forwarder (which connects from the bridge gateway) to reach the app; loopback isolation is enforced by the host-side bind address in the `ports` mapping.

**Files**
- Modify: `docker/docker-compose.yml:66`
- Test: `tests/unit/test_docker_compose_postgres.py`

**Interfaces**
- Produces: base compose app service maps `"127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000"`.

Steps:

- [ ] Write the failing test — append to `tests/unit/test_docker_compose_postgres.py`:
```python
def test_app_service_publishes_only_to_loopback() -> None:
    app = _base_compose()["services"]["pubtator-link"]
    assert app["ports"] == ["127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000"]
```

- [ ] Run it, expect FAIL: `uv run pytest tests/unit/test_docker_compose_postgres.py -k loopback -q`
  Expected: `AssertionError` — current value is `["${PUBTATOR_LINK_PORT:-8000}:8000"]`.

- [ ] Minimal implementation — `docker/docker-compose.yml:66`:
```yaml
    ports:
      - "127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000"
```

- [ ] Run it, expect PASS: `uv run pytest tests/unit/test_docker_compose_postgres.py -q`
  Expected: all compose tests pass.

- [ ] Commit: `fix(docker): bind default compose app port to loopback (#85-D)`

---

### Task 5: Document the write/full profile as gateway-only

The `full` profile enables the write surface (`export_review_audit_bundle`, `index_review_evidence`, `submit_text_annotation`, `record_review_context`, `stage_research_session`); `readonly` strips it (`mcp/profiles.py:58-75`). The backend is unauthenticated by design, so the deployment contract — gateway-only reachability, loopback default, when to set `review_export_base_dir`/`trust_proxy_headers`, and `mcp_profile=readonly` for any directly reachable instance — must be written down. This is a docs-only task closing the "write/full profile documented" half of the acceptance.

**Files**
- Create: `docs/SECURITY.md`
- Test: doc presence assertion in `tests/unit/test_docker_compose_postgres.py` (cheap guard so the doc cannot silently disappear) — optional but included for TDD parity.

**Interfaces**
- Produces: `docs/SECURITY.md` covering profiles, edge auth, `review_export_base_dir`, `trust_proxy_headers`, loopback default, and issue #85.

Steps:

- [ ] Write the failing test — append to `tests/unit/test_docker_compose_postgres.py`:
```python
def test_security_doc_documents_write_profile_posture() -> None:
    text = Path("docs/SECURITY.md").read_text(encoding="utf-8")
    for token in (
        "review_export_base_dir",
        "trust_proxy_headers",
        "mcp_profile",
        "127.0.0.1",
        "#85",
    ):
        assert token in text
```

- [ ] Run it, expect FAIL: `uv run pytest tests/unit/test_docker_compose_postgres.py -k security_doc -q`
  Expected: `FileNotFoundError: docs/SECURITY.md`.

- [ ] Minimal implementation — create `docs/SECURITY.md`:
```markdown
# Security & Deployment Posture

PubTator-Link is **unauthenticated by design**. Edge authentication is owned by the
GeneFoundry router / reverse proxy at the trust boundary; the backend must be reachable
**only** through that proxy, never published directly to a LAN or the internet.

## MCP tool profiles (`PUBTATOR_LINK_MCP_PROFILE`)

- `lean` (default) — read + the review-index write tool (`index_review_evidence`).
- `readonly` — strips all write tools (`index_review_evidence`, `record_review_context`,
  `submit_text_annotation`, `export_review_audit_bundle`, `stage_research_session`, ...).
  Use this for any instance that could be reached without the proxy in front.
- `full` — enables the complete write surface, including audit-bundle file export.
  Run `full` **only** behind the router/proxy.

## Write-surface hardening (issue #85)

- `PUBTATOR_LINK_REVIEW_EXPORT_BASE_DIR` — base directory that `export_review_audit_bundle`
  `export_path` writes must canonically resolve within. **Unset disables file export**
  (inline/compact responses still work). Set it to a dedicated, mounted export volume.
- `index_review_evidence` caps `pmids` and `curated_urls` at 200 entries each.
- `PUBTATOR_LINK_TRUST_PROXY_HEADERS` — set `true` only when a known reverse proxy sits in
  front; the inbound rate limiter then keys on the rightmost `X-Forwarded-For` entry.
  Leave `false` (default) when directly reachable — the leftmost XFF value is client-spoofable.
- The default `docker/docker-compose.yml` publishes the app port to `127.0.0.1` only;
  `docker-compose.prod.yml` drops published ports entirely (expose-only behind the proxy).

Research use only. Not clinical decision support.
```

- [ ] Run it, expect PASS: `uv run pytest tests/unit/test_docker_compose_postgres.py -q`
  Expected: all pass.

- [ ] Commit: `docs(security): document write/full profile as gateway-only (#85)`

---

## Acceptance criteria

- `uv run pytest tests/unit/mcp/test_mcp_service_adapters.py tests/unit/test_review_rerag_models.py tests/unit/test_server_manager.py tests/unit/test_docker_compose_postgres.py -q` → all pass.
- An absolute `export_path` outside the base (`/etc/...`) and one containing `..` are both rejected with an `export_path` field error and no file is created (Task 1 tests); the mechanism is `Path(export_path).expanduser().resolve().is_relative_to(Path(base).expanduser().resolve())`.
- `IndexReviewEvidenceRequest(pmids=[...201])` and `curated_urls=[...201]` raise `ValidationError`; 200 is accepted.
- With `enable_inbound_rate_limit=True`, `trust_proxy_headers=True`, limit 1: two distinct XFF clients each get `200`, a repeat XFF client gets `429`; with `trust_proxy_headers=False`, a second request with a different XFF still gets `429`.
- `yaml.safe_load(open("docker/docker-compose.yml"))["services"]["pubtator-link"]["ports"] == ["127.0.0.1:${PUBTATOR_LINK_PORT:-8000}:8000"]`.
- `docs/SECURITY.md` exists and documents the four `#85` controls.
- `make ci-local` passes (format-check, ruff, `lint-loc` 600-LOC budget, mypy, unit + integration).

## Risk & rollback

- **Behavior change (intended):** file export now requires `PUBTATOR_LINK_REVIEW_EXPORT_BASE_DIR`; existing callers relying on arbitrary `export_path` get a clear field error directing them to set the base (or use `fallback_inline=True`). Document in the PR body and `upgrade-notes`.
- **Compose change:** loopback bind can surprise an operator who reached `:8000` directly; that is the security fix. Co-located reverse proxies on the same host reach `127.0.0.1:8000` unchanged.
- **Rollback:** each task is one atomic commit; `git revert <sha>` restores prior behavior per concern. No schema/migration changes, no data writes.
- This plan is **NOT execution-gated**: it ends at local commits only — no `git push`, no redeploy, no destructive remote operation. Deployment of the loopback-bind and `full`-profile guarantees is a separate, gated fleet step.

## Effort

~0.5–1 day for an engineer new to the repo: Task 1 (~2–3h incl. threading existing tests), Tasks 2/4/5 (~30–45min each), Task 3 (~1–1.5h). All changes are well within the 600-LOC/module budget (small additions to existing modules; one new ~30-line doc).
