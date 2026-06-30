# litvar-link Entrypoint-Reliability + resolve_rsid (#20) Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal** Land the stranded entrypoint-reliability fixes (percent-encoding, PMID→str, resolve-before-fetch) AND close GitHub #20 so `resolve_rsid` returns a populated `variant_id`/`gene`/`variant_name` that chains into `get_variant_literature`.

**Architecture** `litvar-link` is a thin FastMCP backend over the NCBI LitVar2 REST API; MCP tools (`litvar_link/mcp/tools/*`) call `VariantService` (`services/variant_service.py`), which calls `LitVar2Client` (`api/client.py`). Two independent defects break the entry points: (1) canonical LitVar2 ids like `litvar@rs113993960##` are inserted into URL paths un-encoded, so the `#` is parsed as a URL fragment and the request is silently truncated; (2) the sensor endpoint payload only contains `{pmids_count, rsid, link, logo}`, so `lookup_rsid`'s field map keys (`variant_id`/`gene`/`variant_name`) are always `None`. The fix keeps the existing layering: encode at the client boundary, enrich `resolve_rsid` from the autocomplete endpoint at the service boundary.

**Tech Stack** Python 3.12, FastMCP 3.x, httpx (async), pydantic v2, structlog, pytest + pytest-asyncio, uv, ruff, mypy.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-`Authorization` passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

---

## Verified ground truth (read before starting)

These were confirmed against the live API and current `main` on 2026-06-30. Do NOT trust the old line numbers from the audit; the real ones are below.

- Live sensor payload (`GET https://www.ncbi.nlm.nih.gov/research/litvar2-api/sensor/rs1061170`):
  `{"pmids_count":884,"rsid":"rs1061170","link":"https://www.ncbi.nlm.nih.gov/research/litvar2/docsum?variant=litvar%40rs1061170%23%23&query=rs1061170","logo":"..."}`
  — there is **no** `variant_id`, **no** `gene`, **no** `variant_name`, and the URL key is **`link`**, not `litvar_url`. The canonical id only appears percent-encoded inside `link`.
- Live autocomplete payload (`GET .../variant/autocomplete/?query=rs1061170&limit=2`):
  `[{"_id":"litvar@rs1061170##","rsid":"rs1061170","gene":["CFH"],"name":"p.Y402H","hgvs":"p.Y402H","pmids_count":884,...}]`
  — this is where `variant_id` (`_id`), `gene`, and `variant_name` (`name`) actually live.
- Live sensor for an unknown rsID returns **HTTP 400** with body `{"detail":"Variant not found: litvar@rs999999999##"}` (NOT a `null`/None body).
- `litvar_link/models/endpoint_specific.py:95` already has a correct `SensorItem` model (`pmids_count, rsid, link, logo`) and `PublicationsItem.pmids: list[int]` (line 107) — proof PMIDs are ints upstream while `Publication.pmid: str` (validated 7–8 digits) at `models/variants.py:28`.
- Current key files / sizes: `services/variant_service.py` = 470 LOC, `api/client.py` = 381 LOC (600 budget). The stranded branch `fix/litvar-entrypoint-reliability` (commits `0896a8b`, `a225448`, `24b67ff`, `2a8c070`) is local-only, based on `9e29984` (predates the #34 conformance gate) — **re-land its proven diffs as fresh TDD commits on a new branch off current `main`; do not rebase the stale branch.**

## File Structure

Created:
- (none) — all changes land in existing files.

Modified:
- `litvar_link/api/client.py` — add `_format_endpoint()` (percent-encode path segments); coerce publication PMIDs to `str`.
- `litvar_link/services/variant_service.py` — resolve rsID/HGVS→canonical id before publications; map recoverable upstream not-found; **enrich `resolve_rsid` fields from autocomplete (#20)**; map sensor 400 not-found to `available=False`.
- `litvar_link/mcp/tools/literature.py` — doc-only: state that rsID/HGVS/free-text input is auto-resolved.
- `tests/unit/test_api/test_client.py` — percent-encoding + PMID-coercion tests; loosen the flaky ratelimiter token tolerance (PR #33 content).
- `tests/unit/test_services/test_variant_service.py` — resolve-before-fetch tests; `resolve_rsid` field-population + not-found tests; resolve→fetch chain test.
- `tests/conftest.py` — fix `sample_sensor_data` to the real payload shape (`link`, not `litvar_url`).
- `tests/fixtures/api_responses.py` — fix `sensor_response_available()` to the real payload shape.
- `tests/integration/test_live_litvar2.py` — add an opt-in live resolve→fetch chain test.

---

### Task 1: Percent-encode dynamic URL path segments (client)

Fixes the `#`-truncation bug for `get_variant_publications`, `get_variant_details`, `sensor_lookup`, `get_variants_by_gene`. This is the proven stranded commit `0896a8b`.

**Files**
- Modify `litvar_link/api/client.py:8` (import), add helper after line 38, and change the four `.format(...)` call sites (`:269`, `:283`, `:302`, `:318`).
- Test `tests/unit/test_api/test_client.py` (new test in `TestLitVar2Client`).

**Interfaces**
- Produces `_format_endpoint(template: str, **segments: str) -> str` — substitutes each segment via `urllib.parse.quote(str(v), safe="")` then `template.format(**encoded)`.
- Consumes: `httpx.AsyncClient.request` (mocked in test).

- [ ] (1) Write the failing test in `tests/unit/test_api/test_client.py` inside `class TestLitVar2Client` (imports `json`, `AsyncMock`, `MagicMock`, `patch`, fixtures `api_config`/`mock_logger` all already exist):
  ```python
      @pytest.mark.asyncio
      async def test_variant_publications_percent_encodes_canonical_id(
          self,
          api_config: APIConfig,
          mock_logger: MagicMock,
      ) -> None:
          """Canonical LitVar ids carry '@' and '##'; the path segment must be
          percent-encoded. An unencoded '#' is parsed as a URL fragment, so the
          server receives a truncated id and 400s -- the bug that made
          get_variant_literature fail for every input.
          """
          with patch("httpx.AsyncClient.request") as mock_request:
              mock_response = AsyncMock()
              mock_response.status_code = 200
              mock_response.text = json.dumps({"pmids": [111, 222]})
              mock_response.headers = {"content-type": "application/json"}
              mock_response.json = MagicMock(return_value={"pmids": [111, 222]})
              mock_response.raise_for_status = MagicMock()
              mock_request.return_value = mock_response

              async with LitVar2Client(config=api_config, logger=mock_logger) as client:
                  result = await client.get_variant_publications("litvar@rs113993960##")

          assert result == ["111", "222"]
          url = str(mock_request.call_args[1]["url"])
          assert "litvar%40rs113993960%23%23" in url
          # A raw '#' anywhere would truncate the request URL as a fragment.
          assert "#" not in url
  ```
- [ ] (2) Run it, expect FAIL (the result is currently `[111, 222]` ints and the URL still contains `#`):
  `uv run pytest tests/unit/test_api/test_client.py::TestLitVar2Client::test_variant_publications_percent_encodes_canonical_id -q`
  Expected: `FAILED` (AssertionError on `"#" not in url`; this test also asserts the str-coercion from Task 2 — it stays red until both land).
- [ ] (3) Minimal implementation in `litvar_link/api/client.py`. Change the import on line 8 and add the helper above the class (after line 38), then route the four call sites through it:
  ```python
  from urllib.parse import quote, urljoin
  ```
  ```python
  def _format_endpoint(template: str, **segments: str) -> str:
      """Substitute path segments into an endpoint template, percent-encoding each.

      LitVar2 canonical ids look like ``litvar@rs113993960##``; the ``@`` and the
      trailing ``##`` MUST be percent-encoded, because an unencoded ``#`` is parsed
      as a URL fragment delimiter -- the path is then silently truncated and the
      server 400s ("Variant not found"). Every dynamic segment is quoted with
      ``safe=""`` so reserved characters never leak into the path.
      """
      encoded = {key: quote(str(value), safe="") for key, value in segments.items()}
      return template.format(**encoded)
  ```
  Replace `self.config.endpoints["variant_details"].format(variant_id=variant_id)` (`:269`), `...["variant_publications"].format(variant_id=variant_id)` (`:283`), `...["sensor"].format(rsid=rsid)` (`:302`), and `...["gene_variants"].format(gene_name=gene_name)` (`:318`) with the `_format_endpoint(self.config.endpoints[...], <kw>=<val>)` form. (PMID coercion on `:287` is Task 2.)
- [ ] (4) Run it, expect the `"#"`-assertion to pass once Task 2 lands; for now confirm no regressions in the surrounding client suite:
  `uv run pytest tests/unit/test_api/test_client.py -q -k "encode or url or publications or details or sensor or gene"`
  Expected: the encoding assertions PASS; the int/str assertion still red until Task 2.
- [ ] (5) Commit: `git commit -am "fix(client): percent-encode dynamic path segments (LitVar id '#' bug)"`

---

### Task 2: Coerce LitVar2 PMIDs to `str` in the client

Upstream emits `pmids` as ints (`PublicationsItem.pmids: list[int]`); the declared `list[str]` contract and `Publication.pmid: str` require strings. Proven stranded commit `2a8c070`.

**Files**
- Modify `litvar_link/api/client.py:287` (the `get_variant_publications` return).
- Test `tests/unit/test_api/test_client.py` (new test).

**Interfaces**
- Produces `LitVar2Client.get_variant_publications(variant_id: str) -> list[str]` (now actually `str`).

- [ ] (1) Write the failing test:
  ```python
      @pytest.mark.asyncio
      async def test_variant_publications_coerces_int_pmids_to_str(
          self,
          api_config: APIConfig,
          mock_logger: MagicMock,
      ) -> None:
          """The LitVar2 publications endpoint returns PMIDs as integers, but the
          Publication model (and the declared list[str] contract) require strings.
          The client must coerce, else the literature happy path crashes the moment
          the call actually succeeds.
          """
          payload = {"pmids": [37388288, 18022401]}
          with patch("httpx.AsyncClient.request") as mock_request:
              mock_response = AsyncMock()
              mock_response.status_code = 200
              mock_response.text = json.dumps(payload)
              mock_response.headers = {"content-type": "application/json"}
              mock_response.json = MagicMock(return_value=payload)
              mock_response.raise_for_status = MagicMock()
              mock_request.return_value = mock_response

              async with LitVar2Client(config=api_config, logger=mock_logger) as client:
                  result = await client.get_variant_publications("litvar@rs113993960##")

          assert result == ["37388288", "18022401"]
          assert all(isinstance(pmid, str) for pmid in result)
  ```
- [ ] (2) Run it, expect FAIL (`[37388288, 18022401]` ints != `["37388288", "18022401"]`):
  `uv run pytest tests/unit/test_api/test_client.py::TestLitVar2Client::test_variant_publications_coerces_int_pmids_to_str -q`
  Expected: `FAILED`.
- [ ] (3) Minimal implementation — change `litvar_link/api/client.py:287` from
  `return cast("list[str]", extract_list(response, key="pmids"))`
  to:
  ```python
          # LitVar2 returns PMIDs as integers; coerce to honour the list[str]
          # contract (Publication.pmid is a str).
          return [str(pmid) for pmid in extract_list(response, key="pmids")]
  ```
- [ ] (4) Run both client tests, expect PASS (Task 1's `result == ["111", "222"]` now also green):
  `uv run pytest tests/unit/test_api/test_client.py -q -k "percent_encodes or coerces"`
  Expected: `2 passed`.
- [ ] (5) Commit: `git commit -am "fix(client): coerce LitVar2 PMIDs to str (publications happy path)"`

---

### Task 3: Resolve rsID/HGVS→canonical id before fetch + recoverable not-found (literature)

`get_variant_literature` currently forwards whatever id it is given straight to the publications endpoint, which only accepts `litvar@...##`; an rsID/HGVS therefore 400s. Proven stranded commits `a225448` + `24b67ff`. Introduces helpers reused by Task 4.

**Files**
- Modify `litvar_link/services/variant_service.py` — module constants after line 36; new helpers + rewrite `get_variant_literature` (currently `:291`–`:340`).
- Modify `litvar_link/mcp/tools/literature.py:27` (doc-only) — note auto-resolution.
- Test `tests/unit/test_services/test_variant_service.py` (5 new tests + 2 existing-test edits).

**Interfaces**
- Produces `VariantService._resolve_to_variant_id(raw: str) -> str` (canonical id; raises `ValidationError` when nothing resolves).
- Produces `VariantService._fetch_publication_response(resolved_id: str) -> PublicationResponse`.
- Produces module helpers `_is_canonical_variant_id(value) -> bool`, `_is_variant_not_found(exc: LitVarAPIError) -> bool`.

- [ ] (1) Write the failing tests in `tests/unit/test_services/test_variant_service.py` inside `class TestVariantService` (these are the exact proven tests; `mock_client.search_variants` is already an `AsyncMock` per the fixture at `:32`):
  ```python
      @pytest.mark.asyncio
      async def test_get_variant_literature_resolves_rsid_to_canonical_id(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          mock_client.search_variants.return_value = [
              {"_id": "litvar@rs113993960##", "rsid": "rs113993960",
               "gene": ["CFTR"], "name": "p.F508del", "pmids_count": 2},
          ]
          mock_client.get_variant_publications.return_value = ["37388288", "18022401"]
          result = await service.get_variant_literature("rs113993960")
          mock_client.search_variants.assert_awaited_once()
          mock_client.get_variant_publications.assert_awaited_once_with("litvar@rs113993960##")
          assert result.variant_id == "litvar@rs113993960##"
          assert result.total_count == 2

      @pytest.mark.asyncio
      async def test_get_variant_literature_canonical_id_skips_resolution(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          mock_client.get_variant_publications.return_value = ["37388288"]
          result = await service.get_variant_literature("litvar@rs1061170##")
          mock_client.search_variants.assert_not_awaited()
          mock_client.get_variant_publications.assert_awaited_once_with("litvar@rs1061170##")
          assert result.variant_id == "litvar@rs1061170##"

      @pytest.mark.asyncio
      async def test_get_variant_literature_unresolvable_raises_validation(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          mock_client.search_variants.return_value = []
          with pytest.raises(ValidationError, match="No LitVar2 variant found"):
              await service.get_variant_literature("rs000000000")
          mock_client.get_variant_publications.assert_not_awaited()

      @pytest.mark.asyncio
      async def test_get_variant_literature_upstream_not_found_is_recoverable(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          mock_client.get_variant_publications.side_effect = LitVarAPIError(
              'HTTP 400: {"detail":"Variant not found: litvar@rs0##"}', status_code=400)
          with pytest.raises(ValidationError, match="variant not found"):
              await service.get_variant_literature("litvar@rs0##")

      @pytest.mark.asyncio
      async def test_get_variant_literature_outage_stays_transient(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          from litvar_link.exceptions import ServiceUnavailableError
          mock_client.get_variant_publications.side_effect = ServiceUnavailableError(
              "LitVar2 service error: HTTP 503")
          with pytest.raises(ServiceUnavailableError):
              await service.get_variant_literature("litvar@rs0##")
  ```
  Also update the two existing tests that pass a non-canonical id straight to the publications path so they still exercise that path: in `test_get_variant_literature_exception_handling` (`~:499`) change `"test_variant_id"` → `"litvar@rs1061170##"`, and in the empty-publications case (`~:758`) change `"nonexistent_id"` → `"litvar@rs0000000##"`.
- [ ] (2) Run them, expect FAIL (no resolution today):
  `uv run pytest tests/unit/test_services/test_variant_service.py -q -k "literature"`
  Expected: `FAILED` (rsID forwarded verbatim; `search_variants` never awaited).
- [ ] (3) Minimal implementation in `litvar_link/services/variant_service.py`. Add to the imports (line 8) `LitVarAPIError`:
  `from litvar_link.exceptions import LitVarAPIError, ValidationError`
  Add module-level after line 36:
  ```python
  _CANONICAL_ID_PREFIX = "litvar@"
  _NOT_FOUND_STATUS = (400, 404)


  def _is_canonical_variant_id(value: str) -> bool:
      """True for an already-canonical LitVar id like ``litvar@rs113993960##``."""
      return value.startswith(_CANONICAL_ID_PREFIX)


  def _is_variant_not_found(exc: LitVarAPIError) -> bool:
      """True when an upstream 4xx clearly means 'this variant id does not exist'."""
      return exc.status_code in _NOT_FOUND_STATUS and "not found" in str(exc).lower()
  ```
  Add the two helper methods (place above `get_variant_literature`), then replace the body of `get_variant_literature` (`:309`–`:340`) per stranded commit `a225448`+`24b67ff`:
  ```python
      async def _resolve_to_variant_id(self, raw: str) -> str:
          """Resolve free-text / rsID / HGVS to a canonical LitVar id.

          Already-canonical ids pass through untouched; otherwise the autocomplete
          endpoint resolves the top hit. Raises ``ValidationError`` (a recoverable
          message the tool surfaces verbatim) when nothing matches.
          """
          if _is_canonical_variant_id(raw):
              return raw
          search = await self.search_variants(raw, limit=1)
          if not search.variants:
              msg = (
                  f"No LitVar2 variant found for {raw!r}. "
                  "Use search_genetic_variants to find the variant id."
              )
              raise ValidationError(msg, field="variant_id")
          return search.variants[0].id

      async def _fetch_publication_response(self, resolved_id: str) -> PublicationResponse:
          """Fetch + shape publications for an already-canonical LitVar id."""
          initial_hits = hits_before(self._cached_get_variant_publications)
          pmids = await self._cached_get_variant_publications(resolved_id)
          cached = was_cache_hit(self._cached_get_variant_publications, before=initial_hits)
          from litvar_link.models.variants import Publication
          publications = [Publication(pmid=pmid) for pmid in pmids if pmid]
          return PublicationResponse(
              variant_id=resolved_id, publications=publications,
              total_count=len(publications), pmid_count=len(publications),
              pmc_count=0, format="json", cached=cached,
          )
  ```
  and the new `try` body:
  ```python
          variant_id = variant_id.strip()
          try:
              resolved_id = await self._resolve_to_variant_id(variant_id)
              return await self._fetch_publication_response(resolved_id)
          except ValidationError:
              raise
          except LitVarAPIError as e:
              if _is_variant_not_found(e):
                  msg = (
                      f"LitVar2 has no literature record for {variant_id!r} "
                      "(variant not found). Use search_genetic_variants to find a "
                      "valid variant id."
                  )
                  raise ValidationError(msg, field="variant_id") from e
              self._log_literature_error(e, variant_id)
              raise
          except Exception as e:
              self._log_literature_error(e, variant_id)
              raise
  ```
  Add the tiny `_log_literature_error(self, exc, variant_id)` helper (wraps `log_error_with_context` under `if self.logger`). Update the literature tool docstring at `litvar_link/mcp/tools/literature.py:27` to state input may be a canonical id, rsID, or HGVS/free text (preserve the "Research use only; not clinical decision support" line).
- [ ] (4) Run, expect PASS:
  `uv run pytest tests/unit/test_services/test_variant_service.py -q -k "literature"`
  Expected: all literature tests `passed`.
- [ ] (5) Commit: `git commit -am "fix(literature): resolve rsID/HGVS to canonical id; map upstream not-found as recoverable"`

---

### Task 4: Populate `resolve_rsid` variant_id/gene/variant_name from autocomplete (#20)

**This is the gap the stranded branch never fixed.** `lookup_rsid` maps `sensor_data.get("variant_id"/"gene"/"variant_name")` (`services/variant_service.py:378–382`) and `sensor_data.get("litvar_url")` (`:379`) — none of those keys exist in the real sensor payload (`{pmids_count, rsid, link, logo}`), so they are always `None`. Enrich the three id fields from the autocomplete endpoint and read the URL from `link`. Also map the live sensor's `400 "Variant not found"` to `available=False`.

**Files**
- Modify `litvar_link/services/variant_service.py` — new `_resolve_rsid_record` + `_unavailable_sensor` helpers; rewrite `lookup_rsid` (`:342`–`:394`).
- Modify `tests/conftest.py:160` (`sample_sensor_data`) and `tests/fixtures/api_responses.py:90` (`sensor_response_available`) to the real payload shape.
- Test `tests/unit/test_services/test_variant_service.py` (2 new tests).

**Interfaces**
- Produces `VariantService._resolve_rsid_record(rsid: str) -> AutocompleteVariantItem | None` — best-effort autocomplete enrichment (`.id`/`.gene`/`.name`); returns `None` (never raises) so a transient autocomplete blip cannot break an otherwise-successful sensor lookup.
- Modifies `VariantService.lookup_rsid(rsid: str) -> SensorResponse` so `variant_id`/`gene`/`variant_name`/`litvar_url` are populated when available.

- [ ] (1) Write the failing tests in `class TestVariantService`:
  ```python
      @pytest.mark.asyncio
      async def test_lookup_rsid_populates_canonical_fields_from_autocomplete(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          """resolve_rsid must return populated variant_id/gene/variant_name so the
          result chains downstream. The sensor payload carries only pmids_count +
          link, so the three fields are enriched from autocomplete (issue #20).
          """
          mock_client.sensor_lookup.return_value = {
              "pmids_count": 884, "rsid": "rs1061170",
              "link": "https://www.ncbi.nlm.nih.gov/research/litvar2/docsum?variant=litvar%40rs1061170%23%23&query=rs1061170",
              "logo": "https://www.ncbi.nlm.nih.gov/research/litvar2/assets/litvar-logo-small.png",
          }
          mock_client.search_variants.return_value = [
              {"_id": "litvar@rs1061170##", "rsid": "rs1061170",
               "gene": ["CFH"], "name": "p.Y402H", "hgvs": "p.Y402H", "pmids_count": 884},
          ]
          result = await service.lookup_rsid("rs1061170")
          assert result.available is True
          assert result.variant_id == "litvar@rs1061170##"
          assert result.gene == ["CFH"]
          assert result.variant_name == "p.Y402H"
          assert result.litvar_url is not None
          assert result.pmids_count == 884

      @pytest.mark.asyncio
      async def test_lookup_rsid_upstream_not_found_is_unavailable(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          """The live sensor endpoint 400s with 'Variant not found' for an unknown
          rsID; resolve_rsid maps that to available=False (recoverable), not an
          internal error, and never calls autocomplete.
          """
          mock_client.sensor_lookup.side_effect = LitVarAPIError(
              'HTTP 400: {"detail":"Variant not found: litvar@rs999999999##"}',
              status_code=400)
          result = await service.lookup_rsid("rs999999999")
          assert result.available is False
          assert result.variant_id is None
          mock_client.search_variants.assert_not_awaited()
  ```
- [ ] (2) Run them, expect FAIL (`variant_id`/`gene`/`variant_name` come back `None`; the 400 propagates as `LitVarAPIError`):
  `uv run pytest tests/unit/test_services/test_variant_service.py -q -k "lookup_rsid_populates or lookup_rsid_upstream_not_found"`
  Expected: `FAILED`.
- [ ] (3) Minimal implementation in `litvar_link/services/variant_service.py`. Add the import of the model at the top of the method module-side use and the helpers:
  ```python
      async def _resolve_rsid_record(self, rsid: str) -> AutocompleteVariantItem | None:
          """Enrich an rsID with its canonical autocomplete record (issue #20).

          The LitVar2 sensor endpoint returns only ``{pmids_count, rsid, link,
          logo}`` -- it carries neither the canonical ``variant_id`` (``_id``) nor
          ``gene``/``name``. resolve_rsid must surface those three so the result
          chains into get_variant_summary / get_variant_literature, so we read them
          from autocomplete. Best-effort: a transient autocomplete failure degrades
          to ``None`` (availability is already known from the sensor call).
          """
          try:
              search = await self.search_variants(rsid, limit=5)
          except Exception as exc:  # enrichment is best-effort; never break resolve
              if self.logger:
                  log_error_with_context(
                      self.logger, exc, "resolve_rsid_enrich", {"rsid": rsid})
              return None
          for item in search.variants:
              if getattr(item, "rsid", None) == rsid:
                  return item
          return search.variants[0] if search.variants else None

      @staticmethod
      def _unavailable_sensor(rsid: str, *, cached: bool) -> SensorResponse:
          """Build the 'rsID not in LitVar2' response (all metadata None)."""
          return SensorResponse(
              rsid=rsid, available=False, variant_id=None, litvar_url=None,
              pmids_count=None, gene=None, variant_name=None, cached=cached)
  ```
  Add `from litvar_link.models.endpoint_specific import AutocompleteVariantItem` to the typed imports (it is already imported locally in `search_variants`/`search_gene_variants`; promote to a module/TYPE_CHECKING import for the annotation). Replace the `lookup_rsid` `try` body (`:357`–`:393`):
  ```python
          rsid = validate_rsid(rsid)
          cached = False
          try:
              initial_hits = hits_before(self._cached_sensor_lookup)
              sensor_data = await self._cached_sensor_lookup(rsid)
              cached = was_cache_hit(self._cached_sensor_lookup, before=initial_hits)
              if sensor_data is None:
                  return self._unavailable_sensor(rsid, cached=cached)
              record = await self._resolve_rsid_record(rsid)
              return SensorResponse(
                  rsid=rsid,
                  available=True,
                  # variant_id/gene/variant_name come from autocomplete; the sensor
                  # payload exposes only pmids_count + link (issue #20).
                  variant_id=record.id if record else None,
                  litvar_url=sensor_data.get("link"),
                  pmids_count=sensor_data.get("pmids_count"),
                  gene=record.gene if record else None,
                  variant_name=(record.name or None) if record else None,
                  cached=cached,
              )
          except ValidationError:
              raise
          except LitVarAPIError as e:
              if _is_variant_not_found(e):
                  return self._unavailable_sensor(rsid, cached=False)
              if self.logger:
                  log_error_with_context(self.logger, e, "lookup_rsid", {"rsid": rsid})
              raise
          except Exception as e:
              if self.logger:
                  log_error_with_context(self.logger, e, "lookup_rsid", {"rsid": rsid})
              raise
  ```
  Fix the two sensor fixtures to the real shape so they stop encoding the fictional `litvar_url` key — `tests/conftest.py:160`:
  ```python
      return {
          "pmids_count": 834,
          "rsid": "rs1061170",
          "link": "https://www.ncbi.nlm.nih.gov/research/litvar2/docsum?variant=litvar%40rs1061170%23%23&query=rs1061170",
          "logo": "https://www.ncbi.nlm.nih.gov/research/litvar2/assets/litvar-logo-small.png",
      }
  ```
  and `tests/fixtures/api_responses.py:92` identically. (The existing `test_lookup_rsid_success` keeps passing: it asserts only `available`/`rsid`/`pmids_count`/`litvar_url is not None`, and `litvar_url` now comes from `link`; enrichment degrades to `None` for its un-stubbed `search_variants`.)
- [ ] (4) Run, expect PASS, then the whole service suite:
  `uv run pytest tests/unit/test_services/test_variant_service.py -q`
  Expected: all `passed` (including the pre-existing `test_lookup_rsid_success`).
- [ ] (5) Commit: `git commit -am "fix(rsid): populate variant_id/gene/variant_name from autocomplete; map sensor not-found (#20)"`

---

### Task 5: resolve→fetch chain integration test

Acceptance evidence that a resolved `variant_id` feeds `get_variant_literature`. One network-isolated service test (runs in `ci-local`) plus one opt-in live test.

**Files**
- Test `tests/unit/test_services/test_variant_service.py` (chain test, mocked).
- Test `tests/integration/test_live_litvar2.py` (live, `@pytest.mark.integration`, opt-in only).

**Interfaces**
- Consumes `VariantService.lookup_rsid` → `VariantService.get_variant_literature`.

- [ ] (1) Write the mocked chain test in `class TestVariantService` (PMIDs are 8 digits so they pass `Publication.pmid` validation):
  ```python
      @pytest.mark.asyncio
      async def test_resolve_rsid_then_get_literature_chain(
          self, service: VariantService, mock_client: AsyncMock,
      ) -> None:
          """The canonical variant_id from resolve_rsid feeds get_variant_literature
          -- the chain issue #20 broke (a null variant_id could not be forwarded).
          """
          mock_client.sensor_lookup.return_value = {
              "pmids_count": 2, "rsid": "rs113993960",
              "link": "https://www.ncbi.nlm.nih.gov/research/litvar2/docsum?variant=litvar%40rs113993960%23%23",
              "logo": "x"}
          mock_client.search_variants.return_value = [
              {"_id": "litvar@rs113993960##", "rsid": "rs113993960",
               "gene": ["CFTR"], "name": "p.F508del", "hgvs": "p.F508del", "pmids_count": 2}]
          mock_client.get_variant_publications.return_value = ["37388288", "18022401"]
          resolved = await service.lookup_rsid("rs113993960")
          assert resolved.variant_id == "litvar@rs113993960##"
          lit = await service.get_variant_literature(resolved.variant_id)
          mock_client.get_variant_publications.assert_awaited_once_with("litvar@rs113993960##")
          assert lit.total_count == 2
          assert all(isinstance(p.pmid, str) for p in lit.publications)
  ```
  And the opt-in live test in `tests/integration/test_live_litvar2.py` (module already carries `pytestmark = pytest.mark.integration`):
  ```python
  @pytest.mark.asyncio
  async def test_resolve_rsid_then_literature_chain_live() -> None:
      """End-to-end real API: sensor -> autocomplete enrichment -> publications,
      for CFH rs1061170 (issue #20). Excluded from ci-local; run via
      `make test-integration`.
      """
      from litvar_link.config import get_cache_config
      from litvar_link.services.variant_service import VariantService

      async with LitVar2Client(get_api_config()) as client:
          service = VariantService(client=client, cache_config=get_cache_config())
          resolved = await service.lookup_rsid("rs1061170")
          assert resolved.available is True
          assert resolved.variant_id == "litvar@rs1061170##"
          assert resolved.gene == ["CFH"]
          assert resolved.variant_name
          lit = await service.get_variant_literature(resolved.variant_id)
          assert lit.total_count > 0
          assert all(p.pmid.isdigit() for p in lit.publications)
  ```
- [ ] (2) Run the mocked chain test, expect PASS (it depends on Tasks 1–4):
  `uv run pytest tests/unit/test_services/test_variant_service.py::TestVariantService::test_resolve_rsid_then_get_literature_chain -q`
  Expected: `1 passed`. Confirm it would have FAILED before Task 4 by asserting `resolved.variant_id` is non-null (was `None`).
- [ ] (3) No implementation needed — Tasks 1–4 already provide the behaviour; this task is the executable acceptance contract.
- [ ] (4) Run the live test once to confirm the real contract, then the full non-integration suite:
  `uv run pytest tests/integration/test_live_litvar2.py -q -m integration` then `make test-fast`
  Expected: live test `passed` (network permitting); `make test-fast` all green.
- [ ] (5) Commit: `git commit -am "test(litvar): cover resolve_rsid->get_variant_literature chain (mocked + live)"`

---

### Task 6: Loosen the flaky ratelimiter timing assertion (PR #33) and run full CI

PR #33 (`fix/flaky-ratelimiter-timing`, MERGEABLE) loosens an over-tight token tolerance. Its branch is behind `main`, so re-land the 8-line change here rather than merging the stale branch, then verify the whole gate.

**Files**
- Modify `tests/unit/test_api/test_client.py:44` and `:138` (two `assert abs(limiter.tokens) < 0.001` lines).

**Interfaces**
- None (test-only).

- [ ] (1) No new test — this *is* the test fix. Confirm the flake first (may be intermittent):
  `uv run pytest tests/unit/test_api/test_client.py -q -k "tokens_refill or burst" --count=5` (if `pytest-repeat` is unavailable, run the two tests a few times).
- [ ] (2) Run, expect occasional FAIL with `assert abs(limiter.tokens) < 0.001` (real wall-clock refill at `rate=4/s` exceeds the tolerance).
- [ ] (3) Change both assertions to the PR #33 text:
  ```python
          # Tolerance covers the tiny refill that accrues during the real time the
          # four awaits above take (rate=4/s); 0.001 was too tight and flaked in CI.
          assert abs(limiter.tokens) < 0.1
  ```
- [ ] (4) Run the full local gate, expect PASS:
  `make ci-local && make lint-loc`
  Expected: `format-check`, `lint-ci`, `lint-loc` (variant_service.py and client.py both < 600 LOC), `typecheck-fast`, `test-fast` all green. Also confirm `make test-cov` stays ≥ the repo's `fail_under = 90`.
- [ ] (5) Commit: `git commit -am "test(ratelimiter): loosen flaky token-refill tolerance (closes #33)"`

---

## Acceptance criteria

- `resolve_rsid("rs1061170")` returns `available=True`, `variant_id == "litvar@rs1061170##"`, `gene == ["CFH"]`, `variant_name == "p.Y402H"`, non-null `litvar_url`, `pmids_count > 0` (Task 4 unit test + Task 5 live test).
- `get_variant_literature("litvar@rs113993960##")` (id containing `#`) succeeds — the request URL contains `litvar%40rs113993960%23%23` and no raw `#` (Task 1 unit test).
- The resolve→fetch chain passes an integration test: `test_resolve_rsid_then_get_literature_chain` (mocked, in `ci-local`) and `test_resolve_rsid_then_literature_chain_live` (live, `make test-integration`).
- Publication PMIDs are `str` (Task 2 + chain test `isinstance(p.pmid, str)`).
- Verify the whole gate green: `make ci-local` exits 0; `uv run pytest tests -q -m "not integration"` all pass; `make lint-loc` reports both edited modules under 600 LOC.
- GitHub #20 is closeable (cite the new `resolve_rsid` field-population behaviour) and the flaky ratelimiter assertion (PR #33 content) is merged.

## Risk & rollback

- **EXECUTION-GATED.** Closing the loop requires remote GitHub operations not performed in this plan: pushing the new branch, opening/merging its PR, **merging or closing PR #33**, and closing issue #20. Do these only after `make ci-local` is green and a reviewer approves. No redeploy is triggered by merge (router picks up backends via the fleet deploy pipeline separately); flag a redeploy of the live `litvar-link` container as a follow-up so the fix reaches the federation.
- **Rollback:** each task is one atomic commit; `git revert <sha>` backs out any single change. The risky behavioural change is Task 4's best-effort autocomplete enrichment — if upstream autocomplete shape drifts, `_resolve_rsid_record` degrades to `None` (fields null, `available` still correct) rather than throwing, so the blast radius is "fields not enriched", never a hard failure of `resolve_rsid`.
- **LOC watch:** `variant_service.py` lands at ~550 LOC after Tasks 3–4 (budget 600). If a later edit pushes it over, extract the rsID/literature resolution helpers into a small `services/resolution.py` rather than inlining further.
- **Network in CI:** the live tests are `@pytest.mark.integration` and excluded from `ci-local`/`test-fast`/`test-cov`; they must never gate CI (NCBI rate-limits and can be down). Keep them opt-in via `make test-integration`.

## Effort

~0.5–1 day. Tasks 1–3 are re-lands of proven, already-tested code (`fix/litvar-entrypoint-reliability`); Task 4 is the only genuinely new logic (~30 LOC + 2 fixture fixes); Tasks 5–6 are tests + the 8-line PR #33 re-land.
