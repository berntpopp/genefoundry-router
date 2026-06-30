# autopvs1-link #41 — Stop PII Logging, Harden Prod Env, Honest UA, Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Bring the AutoPVS1-Link REST request path to the same data-minimization bar the MCP path already meets (no patient-derived genomic data in default logs), close the production debug/log-level footgun, replace the browser-spoofing outbound User-Agent with an honest one, and surface upstream-provenance plus parsed-shape validation so silent HTML-scrape drift cannot ship as fact.

**Architecture:** autopvs1-link is a thin FastMCP/FastAPI `-link` backend that scrapes `autopvs1.bgi.com` HTML and re-shapes it into typed MCP envelopes. The fixes are localized: a Starlette `BaseHTTPMiddleware` (`RequestLoggingMiddleware`) that logs every REST request, a pydantic-settings config layer, the outbound `httpx` client header, the response-envelope builder (`mcp/envelope.py`), and the MCP presenter (`mcp/presenters/variant.py`). No transport, auth, or routing changes — backends stay unauthenticated-by-design behind the router/proxy.

**Tech Stack:** Python 3.12+, uv, FastAPI/Starlette, pydantic + pydantic-settings, structlog, httpx, FastMCP 3.x, pytest, PyYAML (already a transitive dependency), ruff, mypy.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

**Modified**
- `autopvs1_link/middleware/logging_middleware.py` — data-minimize the per-request log; add an opt-in `log_client_ip` gate and a pure `_request_log_context` builder.
- `autopvs1_link/server_manager.py` — wire `RequestLoggingMiddleware(log_client_ip=settings.debug)` so raw IP logging follows the debug flag (off in production).
- `docker/docker-compose.prod.yml` — set `AUTOPVS1_LINK_ENVIRONMENT: production`; drop the dead `AUTOPVS1_LINK_LOG_LEVEL: INFO` line (the production preset forces WARNING).
- `autopvs1_link/config.py` — replace the browser-spoofing default `user_agent` with an honest `autopvs1-link/<version> (+url)` token built from `__version__`.
- `autopvs1_link/mcp/presenters/variant.py` — emit an `upstream_format_unrecognized` warning when the scraped `final_strength` is empty or not in the recognized set (parsed-shape validation).
- `autopvs1_link/mcp/envelope.py` — add an `UpstreamProvenance` model and a `meta.upstream` note populated only on scrape-tier envelopes; strip it when absent.
- `tests/unit/test_logging_middleware.py` — add redaction tests.
- `tests/unit/test_config_settings.py` — keep existing (untouched unless noted); UA covered in a new module.
- `tests/unit/mcp/test_envelope.py` — add provenance tests.
- `CHANGELOG.md` — `[Unreleased] / Security` notes per task.

**Created**
- `tests/unit/test_docker_compose_prod.py` — assert the prod compose sets `AUTOPVS1_LINK_ENVIRONMENT=production` and that the production config preset yields `debug=False` + `level=WARNING`.
- `tests/unit/test_user_agent.py` — assert the default UA identifies the tool and reaches the outbound httpx client header.
- `tests/unit/mcp/test_presenter_shape_validation.py` — assert the parsed-shape drift warning fires on unrecognized `final_strength` and not on recognized values.

---

### Task 1: Data-minimize REST request logging (#41 core)

Variant IDs travel in the REST path/query (e.g. `GET /variant/17-43045712-G-A?genome=hg38`) and may be patient-derived genomic data — GDPR Art. 9 special-category data, subject to the Art. 5(1)(c) data-minimization principle (IP addresses are themselves personal data; every logged field must map to a documented purpose). The current `dispatch` binds `query_params`, `client_ip`, and `user_agent` then logs at INFO (`logging_middleware.py:42-60`). This task removes them from the default bind and gates the raw client IP behind an opt-in debug flag, mirroring structlog's best practice of never letting sensitive fields enter the event dict at the call site.

**Files**
- Modify `autopvs1_link/middleware/logging_middleware.py:17-31` (constructor) and `:42-60` (dispatch); add a `_request_log_context` helper.
- Modify `autopvs1_link/server_manager.py:53` (wire the gate).
- Test: `tests/unit/test_logging_middleware.py` (extend).

**Interfaces**
- Consumes: `starlette.requests.Request`, `correlation_id: str`.
- Produces: `RequestLoggingMiddleware.__init__(self, app, exclude_paths: list[str] | None = None, log_client_ip: bool = False)` and `RequestLoggingMiddleware._request_log_context(self, request: Request, correlation_id: str) -> dict[str, str]`.

Steps:

- [ ] (1) Write the failing tests — append to `tests/unit/test_logging_middleware.py`:
```python
def test_request_log_context_omits_pii_by_default() -> None:
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/variant/17-43045712-G-A"
    request.query_params = "genome=hg38"
    request.headers = {"user-agent": "secret-agent", "x-forwarded-for": "8.8.8.8"}
    request.client.host = "10.0.0.1"

    mw = RequestLoggingMiddleware(app=MagicMock())
    ctx = mw._request_log_context(request, "cid-123")

    assert ctx == {
        "correlation_id": "cid-123",
        "method": "GET",
        "path": "/variant/17-43045712-G-A",
    }
    assert "query_params" not in ctx
    assert "client_ip" not in ctx
    assert "user_agent" not in ctx


def test_request_log_context_includes_client_ip_when_opted_in() -> None:
    request = MagicMock()
    request.method = "GET"
    request.url.path = "/variant/17-43045712-G-A"
    request.headers = {"user-agent": "ua-x", "x-forwarded-for": "8.8.8.8"}
    request.client = None

    mw = RequestLoggingMiddleware(app=MagicMock(), log_client_ip=True)
    ctx = mw._request_log_context(request, "cid-123")

    assert ctx["client_ip"] == "8.8.8.8"
    assert ctx["user_agent"] == "ua-x"
```
- [ ] (2) Run it, expect FAIL (`AttributeError: 'RequestLoggingMiddleware' object has no attribute '_request_log_context'`):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/test_logging_middleware.py -q
```
- [ ] (3) Minimal implementation in `autopvs1_link/middleware/logging_middleware.py`. Update the constructor signature/body (line 17) to accept and store the gate:
```python
    def __init__(self, app, exclude_paths: list[str] | None = None, log_client_ip: bool = False):
        """Initialize the middleware.

        Args:
            app: The FastAPI application
            exclude_paths: List of paths to exclude from logging
            log_client_ip: When True, bind the raw client IP and user agent
                into request logs. Off by default (GDPR Art. 5(1)(c) data
                minimization); wired from ``settings.debug`` so production
                never logs raw IPs.
        """
        super().__init__(app)
        self.log_client_ip = log_client_ip
        self.exclude_paths = exclude_paths or [
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
        ]
```
Replace the request-context extraction + bind block (current lines 42-60) with a minimized bind that never carries `query_params`:
```python
        # Bind a data-minimized logging context. Variant IDs ride in the
        # REST path/query and may be patient-derived genomic data
        # (GDPR Art. 9); query_params is therefore NEVER logged, and the
        # raw client_ip/user_agent (personal data, Art. 5(1)(c)) are bound
        # only when the opt-in debug gate is set. The MCP body-arg path
        # already meets this bar; this brings REST to parity.
        bound_logger = logger.bind(**self._request_log_context(request, correlation_id))

        # Log incoming request
        bound_logger.info("Incoming request")
```
Add the pure helper method (place directly above `_extract_client_ip`, current line 96):
```python
    def _request_log_context(self, request: Request, correlation_id: str) -> dict[str, str]:
        """Build the data-minimized bind context for request logs.

        Default level emits only ``correlation_id``/``method``/``path``.
        The raw client IP and user agent are personal data; they are added
        only when ``log_client_ip`` is opted in. ``query_params`` is never
        bound because variant IDs in the query string may be patient-derived
        genomic data (GDPR Art. 9 / Art. 5(1)(c) data minimization).
        """
        context: dict[str, str] = {
            "correlation_id": correlation_id,
            "method": request.method,
            "path": request.url.path,
        }
        if self.log_client_ip:
            context["client_ip"] = self._extract_client_ip(request)
            context["user_agent"] = request.headers.get("user-agent", "")
        return context
```
Then wire the gate in `autopvs1_link/server_manager.py:53`, changing:
```python
    app.add_middleware(RequestLoggingMiddleware)
```
to:
```python
    app.add_middleware(RequestLoggingMiddleware, log_client_ip=settings.debug)
```
(`settings` is already imported and used in this module.)
- [ ] (4) Run it, expect PASS (the two new tests plus the five existing ones):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/test_logging_middleware.py -q
```
- [ ] (5) Commit:
```bash
git commit -am "fix(logging): drop variant IDs/IP from default REST request logs (#41)

Variant IDs ride in the REST path/query and may be patient-derived
genomic data (GDPR Art. 9). Default-level request logs now carry only
correlation_id/method/path; raw client_ip + user_agent are gated behind
an opt-in debug flag wired from settings.debug. Brings REST logging to
the MCP body-arg data-minimization bar.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Close the production debug/log-level footgun

`docker/docker-compose.prod.yml` never sets `AUTOPVS1_LINK_ENVIRONMENT`, so `config.py:182` defaults it to `development`; `config.py:185` then leaves `debug=True` and the per-request INFO logging active (the `if self.is_production:` reset at `config.py:214` never runs). That compounds the PII exposure from Task 1's code path. Setting `environment=production` flips `debug=False`, forces `logging.level=WARNING` (which suppresses the INFO "Incoming request" line entirely), and forces JSON logging. The explicit `AUTOPVS1_LINK_LOG_LEVEL: INFO` line in the compose is dead under the production preset (the `__init__` post-read override wins) and is removed because it is actively misleading.

**Files**
- Modify `docker/docker-compose.prod.yml:6-9` (environment block).
- Test: `tests/unit/test_docker_compose_prod.py` (create).

**Interfaces**
- Consumes: the compose YAML (`docker/docker-compose.prod.yml`) and `autopvs1_link.config.Settings`.
- Produces: regression coverage asserting prod env + preset.

Steps:

- [ ] (1) Write the failing test — create `tests/unit/test_docker_compose_prod.py`:
```python
"""Guard the production compose against the debug/log-level footgun."""

from pathlib import Path

import yaml

from autopvs1_link.config import Settings


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates the Compose ``!reset`` merge tag."""


_ComposeLoader.add_constructor("!reset", lambda loader, node: None)

_COMPOSE = (
    Path(__file__).resolve().parents[2] / "docker" / "docker-compose.prod.yml"
)


def _prod_env() -> dict[str, object]:
    data = yaml.load(_COMPOSE.read_text(), Loader=_ComposeLoader)
    return data["services"]["autopvs1-link"]["environment"]


def test_prod_compose_sets_environment_production() -> None:
    assert _prod_env().get("AUTOPVS1_LINK_ENVIRONMENT") == "production"


def test_prod_compose_does_not_pin_info_request_logging() -> None:
    # The production preset forces WARNING; pinning INFO would re-enable the
    # per-request log line that can carry variant IDs.
    assert _prod_env().get("AUTOPVS1_LINK_LOG_LEVEL") != "INFO"


def test_production_preset_disables_debug_and_raises_log_level() -> None:
    settings = Settings(environment="production")
    assert settings.debug is False
    assert settings.logging.level == "WARNING"
```
- [ ] (2) Run it, expect FAIL (first two asserts fail: `AUTOPVS1_LINK_ENVIRONMENT` absent, `AUTOPVS1_LINK_LOG_LEVEL` is `INFO`):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/test_docker_compose_prod.py -q
```
- [ ] (3) Minimal implementation — edit `docker/docker-compose.prod.yml`. Replace the current environment block (lines 6-9):
```yaml
    environment:
      AUTOPVS1_LINK_LOG_LEVEL: INFO
      AUTOPVS1_LINK_LOG_JSON_FORMAT: "true"
      AUTOPVS1_LINK_PORT: 8000
```
with:
```yaml
    environment:
      # Drives the production preset: debug=False, level=WARNING (suppresses
      # the per-request INFO log line that can carry variant IDs), JSON logs.
      AUTOPVS1_LINK_ENVIRONMENT: production
      AUTOPVS1_LINK_LOG_JSON_FORMAT: "true"
      AUTOPVS1_LINK_PORT: 8000
```
- [ ] (4) Run it, expect PASS:
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/test_docker_compose_prod.py -q
```
- [ ] (5) Commit:
```bash
git commit -am "fix(docker): set AUTOPVS1_LINK_ENVIRONMENT=production in prod compose (#41)

Prod compose never set the environment, so config shipped debug=True and
INFO request logging — compounding the PII exposure. Setting production
flips debug off and forces WARNING (which drops the per-request log line)
+ JSON logs. Drops the dead, misleading LOG_LEVEL=INFO override.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Honest outbound User-Agent

`config.py:71-75` defaults `user_agent` to a spoofed Chrome/120 browser string, consumed at `autopvs1_client.py:42` as the outbound header. Per RFC 9110 §10.1.5 (product token `name/version`) and the RFC 9309 convention of a descriptive identifier plus a contact/info URL, the tool should identify itself honestly so the upstream operator can see exactly what is calling. Build the token from `__version__` so it never drifts.

**Files**
- Modify `autopvs1_link/config.py:71-75` (the `user_agent` field) and add a `_default_user_agent` factory.
- Test: `tests/unit/test_user_agent.py` (create).

**Interfaces**
- Consumes: `autopvs1_link.__version__`.
- Produces: `APIConfig().user_agent == "autopvs1-link/<version> (+https://github.com/berntpopp/autopvs1-link)"`, flowing unchanged into `AutoPVS1Client.client.headers["user-agent"]`.

Steps:

- [ ] (1) Write the failing test — create `tests/unit/test_user_agent.py`:
```python
"""The outbound User-Agent must identify the tool, not spoof a browser."""

import asyncio

from autopvs1_link import __version__
from autopvs1_link.api.autopvs1_client import AutoPVS1Client
from autopvs1_link.config import APIConfig


def test_default_user_agent_identifies_the_tool() -> None:
    ua = APIConfig().user_agent
    assert ua == (
        f"autopvs1-link/{__version__} "
        "(+https://github.com/berntpopp/autopvs1-link)"
    )
    assert "Mozilla" not in ua
    assert "Chrome" not in ua


def test_client_sends_honest_user_agent() -> None:
    client = AutoPVS1Client()
    try:
        header = client.client.headers["user-agent"]
        assert header.startswith("autopvs1-link/")
        assert "Mozilla" not in header
    finally:
        asyncio.run(client.close())
```
- [ ] (2) Run it, expect FAIL (`AssertionError`: default UA is the Mozilla/Chrome string):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/test_user_agent.py -q
```
- [ ] (3) Minimal implementation in `autopvs1_link/config.py`. Add the factory just after `_migrate_legacy_env()` runs (after line 47), before `class APIConfig`:
```python
def _default_user_agent() -> str:
    """Honest outbound User-Agent: product token + version + project URL.

    RFC 9110 product token (``name/version``) plus the RFC 9309 convention
    of a descriptive identifier with a contact/info URL. No browser
    spoofing — the upstream operator sees exactly what is calling.
    """
    from autopvs1_link import __version__

    return f"autopvs1-link/{__version__} (+https://github.com/berntpopp/autopvs1-link)"
```
Replace the `user_agent` field (lines 71-75) with:
```python
    user_agent: str = Field(
        default_factory=_default_user_agent,
        description="Honest outbound User-Agent (tool/version + project URL).",
    )
```
(The lazy `from autopvs1_link import __version__` inside the factory avoids any import cycle: `__version__` is defined at `autopvs1_link/__init__.py:17` and the factory only runs when `APIConfig()` is constructed. `autopvs1_client.py:42` already reads `settings.api.user_agent`, so no client change is needed.)
- [ ] (4) Run it, expect PASS:
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/test_user_agent.py -q
```
- [ ] (5) Commit:
```bash
git commit -am "fix(client): honest outbound User-Agent, stop browser spoofing (#41)

Replace the spoofed Chrome/120 UA with autopvs1-link/<version> (+url),
built from __version__. RFC 9110 product token + RFC 9309 contact-URL
convention so the upstream operator can identify the caller.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Parsed-shape validation — warn on unrecognized scraped strength

PVS1 `final_strength` is parsed from remote HTML (`autopvs1_parsers.py:parse_pvs1_flowchart`) with no upstream-format pin. When the page changes, the parser can silently yield an empty or unrecognized `final_strength` that today ships as fact (the presenter only warns on the two `PVS1_Not_*` sentinels). This adds a parsed-shape check in `mcp/presenters/variant.py:_present_flowchart` that emits an `upstream_format_unrecognized` MCPWarning whenever the value is not in the recognized set — turning silent corruption into a visible, structured signal. The warning flows into both variant and CNV envelopes (both call `_present_flowchart`).

**Files**
- Modify `autopvs1_link/mcp/presenters/variant.py:53-61` (add the recognized-strength constant) and `:100` (add the check inside `_present_flowchart`).
- Test: `tests/unit/mcp/test_presenter_shape_validation.py` (create).

**Interfaces**
- Consumes: `AutoPVS1Data` with a `PVS1Flowchart.final_strength: str`.
- Produces: `present_variant(...) -> tuple[VariantMCPData, list[MCPWarning]]` whose warning list contains `MCPWarning(code="upstream_format_unrecognized", ...)` for empty/unrecognized strengths.

Steps:

- [ ] (1) Write the failing tests — create `tests/unit/mcp/test_presenter_shape_validation.py`:
```python
"""Parsed-shape validation for scraped PVS1 final_strength."""

from autopvs1_link.mcp.presenters.variant import present_variant
from autopvs1_link.models.autopvs1_models import (
    AutoPVS1Data,
    PVS1Flowchart,
    VariantInfo,
)


def _variant(final_strength: str) -> AutoPVS1Data:
    return AutoPVS1Data(
        genome_build="hg38",
        variant_info=VariantInfo(
            variant_id="17-43045712-G-A",
            variant_type="SNV",
            gene_symbol="BRCA1",
        ),
        pvs1_flowchart=PVS1Flowchart(
            preliminary_decision_path="nonsense",
            final_strength=final_strength,
        ),
        disease_mechanisms=[],
    )


def test_empty_final_strength_emits_drift_warning() -> None:
    _data, warnings = present_variant(_variant(""), source_url=None)
    assert any(w.code == "upstream_format_unrecognized" for w in warnings)


def test_unrecognized_final_strength_emits_drift_warning() -> None:
    _data, warnings = present_variant(_variant("Bananas"), source_url=None)
    assert any(w.code == "upstream_format_unrecognized" for w in warnings)


def test_recognized_final_strength_emits_no_drift_warning() -> None:
    _data, warnings = present_variant(_variant("Strong"), source_url=None)
    assert not any(w.code == "upstream_format_unrecognized" for w in warnings)
```
- [ ] (2) Run it, expect FAIL (`assert any(...)` fails: no drift warning emitted today):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/mcp/test_presenter_shape_validation.py -q
```
- [ ] (3) Minimal implementation in `autopvs1_link/mcp/presenters/variant.py`. Add the recognized-strength set next to `_AMBIGUOUS_VERDICT_STRENGTHS` (after line 61). The members mirror `autopvs1_parsers.PVS1_STRENGTH_LABELS` plus the two sentinels the client assigns; keep it local to avoid an api→mcp layer import:
```python
# Mirrors autopvs1_parsers.PVS1_STRENGTH_LABELS plus the two sentinel
# verdicts the client assigns when a section is missing/incompatible.
# Anything outside this set means the scraped HTML shape changed.
_KNOWN_FINAL_STRENGTHS = frozenset(
    {
        "VeryStrong",
        "Strong",
        "Moderate",
        "Supporting",
        "Not applicable",
        "Unmet",
        "Strong_RWS",
        "Moderate_RWS",
        "Supporting_RWS",
        "PVS1_Not_Applicable",
        "PVS1_Not_Determined",
    }
)
```
Then in `_present_flowchart`, immediately after `final_strength = str(raw.get("final_strength") or "")` (line 100) and before the existing sentinel block, add:
```python
    if final_strength not in _KNOWN_FINAL_STRENGTHS:
        warnings.append(
            MCPWarning(
                code="upstream_format_unrecognized",
                message=(
                    "AutoPVS1 returned a final PVS1 strength "
                    f"{final_strength!r} that is not in the recognized set; "
                    "the scraped upstream HTML format may have changed. "
                    "Treat this result as unverified pending a parser review."
                ),
            )
        )
```
(The existing `PVS1_Not_Applicable`/`PVS1_Not_Determined` sentinels are in `_KNOWN_FINAL_STRENGTHS`, so they keep their dedicated `pvs1_not_applicable` warning and do not double-fire.)
- [ ] (4) Run it, expect PASS:
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/mcp/test_presenter_shape_validation.py tests/unit/mcp/test_presenter_variant.py -q
```
- [ ] (5) Commit:
```bash
git commit -am "feat(mcp): warn on unrecognized scraped PVS1 strength (#41)

final_strength is parsed from un-pinned upstream HTML; an empty or
unrecognized value previously shipped silently as fact. Emit a structured
upstream_format_unrecognized warning so HTML-scrape drift is visible to
LLM callers instead of corrupting results.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Upstream-provenance note on scrape-tier envelopes

Scrape-tier outputs originate from `autopvs1.bgi.com` HTML, not an official API. The envelope already carries `recommended_citation`, `source_url`, and `final_strength_source`, but nothing states the retrieval method or warns that the format is un-pinned. Add a compact `meta.upstream` provenance note, populated only for scrape-tier tools (mirroring how `meta.rate_limit_floor_ms` is gated), and stripped when absent so cheap tools stay lean.

**Files**
- Modify `autopvs1_link/mcp/envelope.py`: add `from urllib.parse import urlsplit` (top), an `UpstreamProvenance` model (near `RecommendedCitation`, ~line 67), an `upstream` field on `MCPMeta` (~line 173), a `_upstream_provenance` helper, the population in `ok_envelope` (~line 348-368), and `"upstream"` in `_strip_none_telemetry_fields` (~line 238-250).
- Test: `tests/unit/mcp/test_envelope.py` (extend).

**Interfaces**
- Consumes: `tool_name: str | None`, `settings.api.base_url`, `cost_tier` from `_cost_hints_for`.
- Produces: `ok_envelope(...)["meta"]["upstream"] == {"source": "<host>", "retrieval": "html-scrape", "note": "<drift note>"}` for scrape-tier tools; key absent otherwise.

Steps:

- [ ] (1) Write the failing tests — append to `tests/unit/mcp/test_envelope.py`:
```python
def test_scrape_envelope_carries_upstream_provenance() -> None:
    envelope = ok_envelope({"x": 1}, tool_name="get_variant_pvs1_data")
    prov = envelope["meta"]["upstream"]
    assert prov["retrieval"] == "html-scrape"
    assert "bgi.com" in prov["source"]
    assert "drift" in prov["note"].lower()


def test_cheap_envelope_has_no_upstream_provenance() -> None:
    envelope = ok_envelope({"x": 1}, tool_name="get_server_capabilities")
    assert "upstream" not in envelope["meta"]


def test_envelope_without_tool_name_has_no_upstream_provenance() -> None:
    envelope = ok_envelope({"x": 1})
    assert "upstream" not in envelope["meta"]
```
(If `ok_envelope` is not already imported in this module, add it to the existing envelope import.)
- [ ] (2) Run it, expect FAIL (`KeyError: 'upstream'` on the first test):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/mcp/test_envelope.py -q
```
- [ ] (3) Minimal implementation in `autopvs1_link/mcp/envelope.py`. Add the import near the top:
```python
from urllib.parse import urlsplit
```
Add the model after `RecommendedCitation` (after line 77):
```python
class UpstreamProvenance(BaseModel):
    """Provenance note for HTML-scraped AutoPVS1 outputs.

    Surfaced only on scrape-tier envelopes so callers know the data was
    parsed from upstream HTML (not an official API) and that the format is
    not contractually pinned and may drift silently.
    """

    source: str
    retrieval: str = "html-scrape"
    note: str = (
        "Fields are parsed from upstream AutoPVS1 HTML, which has no "
        "versioned/contractual format; values may drift silently if the "
        "page changes. Cross-check before any interpretation."
    )
```
Add the field to `MCPMeta` (after the `uncached_count` field, line 173):
```python
    upstream: UpstreamProvenance | None = None
```
Add the helper (near `_rate_limit_floor_ms`, ~line 256):
```python
def _upstream_provenance() -> UpstreamProvenance:
    """Build the upstream-provenance note from the configured base URL."""
    netloc = urlsplit(settings.api.base_url).netloc or settings.api.base_url
    return UpstreamProvenance(source=netloc)
```
In `ok_envelope`, after the `cost_tier, rate_limit_floor_ms, next_call_earliest_at = _cost_hints_for(...)` line (line 348), compute the note and pass it into `MCPMeta(...)`:
```python
    upstream = _upstream_provenance() if cost_tier == SCRAPE_TIER else None
```
and add `upstream=upstream,` to the `MCPMeta(...)` constructor call (alongside `next_commands=next_commands,` at line 367). Finally add `"upstream"` to the strip tuple in `_strip_none_telemetry_fields` (the `for key in (...)` block, lines 238-250) so non-scrape envelopes do not ship `"upstream": null`.
- [ ] (4) Run it, expect PASS (new tests plus the existing envelope + runtime-schema suites — the schema test in `test_tool_runtime.py` validates the live scrape payload against `VariantMCPEnvelope.model_json_schema()`, which now includes the optional `upstream` field, so the populated value validates):
```bash
cd /home/bernt-popp/development/autopvs1-link
uv run pytest tests/unit/mcp/test_envelope.py tests/unit/mcp/test_tool_runtime.py -q
```
- [ ] (5) Commit:
```bash
git commit -am "feat(mcp): add upstream-provenance note to scrape-tier envelopes (#41)

Surface meta.upstream {source, retrieval=html-scrape, drift note} on
scrape-tier tools so callers know data is parsed from un-pinned upstream
HTML. Gated to scrape tier and stripped otherwise; cheap tools stay lean.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Acceptance criteria

- **#41 / PII:** `RequestLoggingMiddleware._request_log_context(request, cid)` returns exactly `{correlation_id, method, path}` by default; `client_ip`/`user_agent` appear only with `log_client_ip=True`; `query_params` never appears. Verified by `uv run pytest tests/unit/test_logging_middleware.py -q`.
- **Prod env:** `yaml.load(docker/docker-compose.prod.yml)["services"]["autopvs1-link"]["environment"]["AUTOPVS1_LINK_ENVIRONMENT"] == "production"`, no `LOG_LEVEL=INFO`, and `Settings(environment="production")` yields `debug is False` and `logging.level == "WARNING"`. Verified by `uv run pytest tests/unit/test_docker_compose_prod.py -q`.
- **Honest UA:** `APIConfig().user_agent == "autopvs1-link/<__version__> (+https://github.com/berntpopp/autopvs1-link)"` and the live `AutoPVS1Client.client.headers["user-agent"]` contains no `Mozilla`. Verified by `uv run pytest tests/unit/test_user_agent.py -q`.
- **Provenance + parsed-shape validation:** an empty/unrecognized scraped `final_strength` yields an `upstream_format_unrecognized` warning; scrape-tier envelopes carry `meta.upstream.retrieval == "html-scrape"` with a drift note; cheap/no-tool envelopes omit `meta.upstream`. Verified by `uv run pytest tests/unit/mcp/test_presenter_shape_validation.py tests/unit/mcp/test_envelope.py -q`.
- **Whole gate:** `make ci-local` passes (format-check, lint, lint-loc ≤600 LOC/module, mypy, unit + integration). Every touched module stays under the 600-LOC budget (current sizes: `envelope.py` 454, `presenters/variant.py` 349, `config.py` 228, `logging_middleware.py` 200 — all comfortably within budget after the additions).
- Research-use-only / not-clinical-decision-support disclaimer (`MCPMeta.research_use_only=True`, the AGENTS.md boundary) is preserved; no transport/auth changes; no caller `Authorization` forwarded to the upstream.

## Risk & rollback

- **Not EXECUTION-GATED.** Execution ends at local `git commit`s only — no `git push`, no redeploy, no destructive remote operation. The prod-compose edit is a committed file change; the operator redeploy that consumes it is a separate, later step outside this plan.
- **Behavioral surface:** Task 5 adds a field to `MCPMeta`, which is embedded in the typed `*MCPEnvelope` contracts. The change is additive and optional (`upstream: UpstreamProvenance | None = None`), so `model_json_schema()` keeps `meta` non-required and the runtime-schema validation test in `test_tool_runtime.py` still passes (validated in Task 5 step 4). If any snapshot/contract test asserts an exhaustive `meta` key set, update that fixture — none was found during planning (`grep` for full-`meta` equality returned nothing).
- **Log-volume change:** in `staging` (INFO) the per-request log line loses `query_params`/`client_ip`; any downstream dashboard that keyed on those fields must switch to `correlation_id`. In `production` the line is suppressed entirely by the WARNING preset — intended.
- **Rollback:** each task is an isolated atomic commit; `git revert <sha>` restores prior behavior with no schema migration or state to unwind. Reverting Task 2 alone restores the (insecure) prior compose; prefer reverting the full set if rolling back.

## Effort

~0.5 day for an engineer new to the repo: 5 small, isolated TDD tasks (one atomic commit each), ~10 new tests, no cross-module refactor. Longest pole is running `make ci-local` (unit + integration) at the end.
