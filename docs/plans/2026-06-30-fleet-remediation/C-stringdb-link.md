# stringdb-link #5: Enrichment & Network HTTP 500 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal** Make `compute_functional_enrichment` and `search_protein_interactions` return HTTP 200 with a non-empty payload on their documented schema-example inputs by normalizing STRING's variant JSON shapes and converting upstream-parse failures into a structured 502 instead of a bare 500.

**Architecture** stringdb-link is a thin FastAPI->FastMCP adapter: routes (`stringdb_link/api/routes/*`) delegate to `StringDBService`, which calls `StringDBClient` (httpx) against the STRING REST API and constructs strict Pydantic response models. The 500s originate where STRING's payload does not match those strict models and the service swallows the Pydantic `ValidationError` into a status-less `StringDBServiceError` that the route renders as 500. The fix hardens the response models against documented STRING shape variance (comma-separated gene lists; out-of-range derived scores) and maps genuine parse failures to a 502 with the original error surfaced.

**Tech Stack** Python 3.12+, FastAPI, Pydantic v2, httpx, structlog, FastMCP 3.x, pytest / pytest-asyncio, uv, ruff, mypy.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

### Evidence base (verified 2026-06-30 against current `main` @ 251a69c and the live STRING API)

The audit finding was reproduced and partially corrected — read this before implementing:

- **Enrichment.** The live public STRING API (`string-db.org`, v12) returns `inputGenes`/`preferredNames` as **JSON arrays**, and the current `EnrichmentTerm` model parses 115 terms fine. BUT the comma-separated-string variant (returned by other STRING versions/mirrors, and the form the audit observed in production) fails with exactly the reported error:
  `ValidationError ... inputGenes Input should be a valid list [type=list_type, input_value='trpGD,trpE', input_type=str]`.
  The STRING API docs' own Python example does `",".join(row["preferredNames"])`, confirming the array form is current and the comma-separated form is a real historical variant. A `BeforeValidator` that splits strings and passes lists through is the version-robust fix.
- **Network.** With current STRING data, `search_protein_interactions` actually parses (3 interactions; note `ncbiTaxonId` arrives as the string `"9606"` and Pydantic coerces it to int). The reported 500 could not be reproduced on the current public API, so the network remediation is two-pronged: (1) **relax the brittle `le=1.0` upper bounds** on STRING-derived scores so upstream drift cannot 500, and (2) the shared **parse-failure -> 502** mapping (Task 2), which is the actual guarantee that no `NetworkInteraction` shape can produce a bare 500 again.
- **`required_score` docstrings.** `stringdb_link/api/client.py` lines 397, 437, 549, 688 document the int param `required_score: int = 400` as "(0.0-1.0)". That is wrong: the service pre-scales (`round(request.required_score * 1000)` at `stringdb_service.py:161,241,462,678`), so the client method receives STRING's 0-1000 integer scale. The request-model floats (`requests.py`) and the GET-route `Query` floats (`networks.py`) ARE on 0.0-1.0 and must NOT be touched.

## File Structure

Created:
- `stringdb_link/models/coercions.py` — reusable Pydantic `BeforeValidator` + `GeneNameList` annotated type that normalizes STRING's comma-separated/array gene fields to `list[str]`.
- `tests/unit/test_responses_string_coercion.py` — unit tests for the comma-separated coercion and relaxed network score bounds.
- `tests/api/test_enrichment_network_regression.py` — route-level regression proving both tools return 200 + non-empty on schema-example inputs and 502 on a genuinely malformed upstream record.

Modified:
- `stringdb_link/models/responses.py` — `EnrichmentTerm`/`FunctionalAnnotation` gene fields use `GeneNameList`; `NetworkInteraction`/`InteractionPartner` score fields drop the `le=1.0` upper bound.
- `stringdb_link/exceptions.py` — `StringDBServiceError` gains an optional `status_code` param (default preserves 500).
- `stringdb_link/services/stringdb_service.py` — network + enrichment error wrappers tag Pydantic `ValidationError` as status 502 and attach the original error.
- `stringdb_link/api/client.py` — corrected `required_score` docstrings (0-1000 integer scale).
- `.loc-allowlist` — bump the grandfathered ceiling for `stringdb_service.py` to cover the few added lines.

---

### Task 1: Normalize STRING comma-separated gene fields (fixes `compute_functional_enrichment` 500)

**Files**
- Create: `stringdb_link/models/coercions.py`
- Modify: `stringdb_link/models/responses.py:283` (`EnrichmentTerm.input_genes`), `:289` (`EnrichmentTerm.preferred_names`), `:348` (`FunctionalAnnotation.input_genes`), `:354` (`FunctionalAnnotation.preferred_names`), plus one new import line
- Test: `tests/unit/test_responses_string_coercion.py`

**Interfaces**
- Produces: `split_comma_separated(value: Any) -> Any` and `GeneNameList = Annotated[list[str], BeforeValidator(split_comma_separated)]`
- Consumes: STRING `/api/json/enrichment` records where `inputGenes`/`preferredNames` may be a `list[str]` OR a single comma-separated `str`.

Steps:
- [ ] (1) Write the failing test in `tests/unit/test_responses_string_coercion.py`:
```python
"""Coercion + bound-relaxation regressions for STRING response models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stringdb_link.models.responses import EnrichmentTerm, FunctionalAnnotation


def _enrichment_record(input_genes, preferred_names):
    return {
        "category": "Process",
        "term": "GO:0000162",
        "number_of_genes": 5,
        "number_of_genes_in_background": 9,
        "ncbiTaxonId": 511145,
        "inputGenes": input_genes,
        "preferredNames": preferred_names,
        "p_value": 1.97e-13,
        "fdr": 6.18e-10,
        "description": "Tryptophan biosynthetic process",
    }


def test_enrichment_term_splits_comma_separated_strings():
    # STRING (non-v12 mirrors / older API) returns these as comma-separated strings.
    term = EnrichmentTerm(**_enrichment_record("trpA,trpB,trpC", "trpA,trpB,trpC"))
    assert term.input_genes == ["trpA", "trpB", "trpC"]
    assert term.preferred_names == ["trpA", "trpB", "trpC"]


def test_enrichment_term_passes_arrays_through():
    # Current public v12 API returns arrays; these must still parse unchanged.
    term = EnrichmentTerm(**_enrichment_record(["trpGD", "trpE"], ["trpD", "trpE"]))
    assert term.input_genes == ["trpGD", "trpE"]
    assert term.preferred_names == ["trpD", "trpE"]


def test_functional_annotation_splits_comma_separated_strings():
    annotation = FunctionalAnnotation(
        category="Process",
        term="GO:0006915",
        number_of_genes=1,
        ratio_in_set=0.5,
        ncbiTaxonId=9606,
        inputGenes="TP53,MDM2",
        preferredNames="TP53,MDM2",
        description="apoptotic process",
    )
    assert annotation.input_genes == ["TP53", "MDM2"]
    assert annotation.preferred_names == ["TP53", "MDM2"]


def test_enrichment_term_rejects_non_string_non_list():
    with pytest.raises(ValidationError):
        EnrichmentTerm(**_enrichment_record(123, ["x"]))
```
- [ ] (2) Run it and confirm FAIL: `cd /home/bernt-popp/development/stringdb-link && uv run pytest tests/unit/test_responses_string_coercion.py -q` — expected FAIL with `ValidationError ... inputGenes Input should be a valid list [type=list_type, input_value='trpA,trpB,trpC', input_type=str]`.
- [ ] (3) Minimal implementation. Create `stringdb_link/models/coercions.py`:
```python
"""Coercions that normalize STRING REST payloads onto strict response models.

STRING's JSON endpoints are not internally consistent across API versions and
stable mirrors: a logical field can arrive as a JSON array on one deployment and
as a single comma-separated string on another. These before-validators normalize
the known-variant shapes so the gateway never returns a bare HTTP 500 on a
documented, valid query.

Research use only; not clinical decision support. Mirror STRING's disclaimers.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator


def split_comma_separated(value: Any) -> Any:
    """Normalize a STRING gene-name field to ``list[str]``.

    STRING ``/api/json/enrichment`` returns ``inputGenes`` / ``preferredNames``
    as a JSON array on the current public v12 API but as a single
    comma-separated string on other versions/mirrors. Split the string form and
    pass any non-string value (already a list) through untouched so the
    downstream ``list[str]`` validation succeeds in both cases.
    """
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


GeneNameList = Annotated[list[str], BeforeValidator(split_comma_separated)]
"""``list[str]`` that also accepts STRING's comma-separated string variant."""
```
  Then in `stringdb_link/models/responses.py`, add the import directly under the existing pydantic import (line 11 `from pydantic import BaseModel, ConfigDict, Field`):
```python
from stringdb_link.models.coercions import GeneNameList
```
  And change the four field annotations from `list[str]` to `GeneNameList` (keep the `Field(...)` calls verbatim):
  - line 283 `    input_genes: list[str] = Field(` -> `    input_genes: GeneNameList = Field(`
  - line 289 `    preferred_names: list[str] = Field(` -> `    preferred_names: GeneNameList = Field(`
  - line 348 `    input_genes: list[str] = Field(` -> `    input_genes: GeneNameList = Field(`
  - line 354 `    preferred_names: list[str] = Field(` -> `    preferred_names: GeneNameList = Field(`
  (These are in-place edits; the only net new line in `responses.py` is the import. `responses.py` is grandfathered at ceiling 640 with one line of slack — Task 3 removes lines, so no `.loc-allowlist` change is needed here. Verify with `make lint-loc` in step 4.)
- [ ] (4) Run and confirm PASS: `uv run pytest tests/unit/test_responses_string_coercion.py -q && make lint-loc` — expected: 4 passed; lint-loc reports no file over its ceiling.
- [ ] (5) Commit: `fix(models): split STRING comma-separated inputGenes/preferredNames (#5)`

---

### Task 2: Map upstream parse failures to a structured 502 (the network 500 safety net)

**Files**
- Modify: `stringdb_link/exceptions.py:332-357` (`StringDBServiceError.__init__`)
- Modify: `stringdb_link/services/stringdb_service.py` — add Pydantic import near line 10; network wrapper `:178-185`; enrichment wrapper `:331-338`
- Modify: `.loc-allowlist` (`stringdb_service.py` ceiling)
- Test: `tests/unit/test_stringdb_service.py` (append, reuse existing `service`/`mock_client` fixtures)

**Interfaces**
- Produces: `StringDBServiceError(message, operation=None, original_error=None, status_code=None)` where `status_code` defaults to 500; the service raises it with `status_code=502, original_error=<pydantic ValidationError>` when STRING's payload fails schema validation.
- Consumes: `pydantic.ValidationError` raised inside `_cached_get_network_interactions` (`responses.py` `NetworkInteraction(**...)` at `stringdb_service.py:211`) and `_cached_get_functional_enrichment` (`:360`).
- Route contract unchanged: `networks.py:92` (`e.status_code or 502`) and `enrichment.py:56` (`e.status_code or 500`) already pass the exception's status through.

Steps:
- [ ] (1) Write the failing test (append to `tests/unit/test_stringdb_service.py`):
```python
class TestUpstreamParseFailures:
    """Upstream STRING payloads that violate the schema must surface as 502."""

    async def test_network_parse_failure_maps_to_502(self, service, mock_client):
        # Missing the required preferredName_B -> NetworkInteraction ValidationError.
        mock_client.get_network_interactions.return_value = [
            {
                "stringId_A": "9606.ENSP00000269305",
                "stringId_B": "9606.ENSP00000344843",
                "preferredName_A": "TP53",
                "ncbiTaxonId": 9606,
                "score": 0.9,
                "nscore": 0.0,
                "fscore": 0.0,
                "pscore": 0.0,
                "ascore": 0.2,
                "escore": 0.9,
                "dscore": 0.9,
                "tscore": 0.9,
            }
        ]
        request = NetworkRequest(identifiers=["TP53", "EGFR", "CDK2"], species=9606)
        with pytest.raises(StringDBServiceError) as exc_info:
            await service.get_network_interactions(request)
        assert exc_info.value.status_code == 502
        assert exc_info.value.original_error is not None

    async def test_enrichment_parse_failure_maps_to_502(self, service, mock_client):
        # p_value out of [0, 1] -> EnrichmentTerm ValidationError.
        mock_client.get_functional_enrichment.return_value = [
            {
                "category": "Process",
                "term": "GO:0000162",
                "number_of_genes": 5,
                "number_of_genes_in_background": 9,
                "ncbiTaxonId": 511145,
                "inputGenes": ["trpA"],
                "preferredNames": ["trpA"],
                "p_value": 7.5,
                "fdr": 0.01,
                "description": "bad p-value",
            }
        ]
        request = EnrichmentRequest(identifiers=["trpA", "trpB"], species=511145)
        with pytest.raises(StringDBServiceError) as exc_info:
            await service.get_functional_enrichment(request)
        assert exc_info.value.status_code == 502

    async def test_non_parse_error_stays_500(self, service, mock_client):
        mock_client.get_network_interactions.side_effect = RuntimeError("boom")
        request = NetworkRequest(identifiers=["TP53", "EGFR"], species=9606)
        with pytest.raises(StringDBServiceError) as exc_info:
            await service.get_network_interactions(request)
        assert exc_info.value.status_code == 500
```
- [ ] (2) Run and confirm FAIL: `uv run pytest tests/unit/test_stringdb_service.py::TestUpstreamParseFailures -q` — expected FAIL: `assert 500 == 502` (current `StringDBServiceError` hardcodes 500) and `AttributeError`/`None` for `original_error`.
- [ ] (3) Minimal implementation.
  In `stringdb_link/exceptions.py`, extend `StringDBServiceError.__init__` (currently `message, operation=None, original_error=None`) to accept `status_code` and use it:
```python
    def __init__(
        self,
        message: str,
        operation: str | None = None,
        original_error: Exception | None = None,
        status_code: int | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if operation:
            details["operation"] = operation
        if original_error:
            details["original_error"] = str(original_error)
            details["error_type"] = type(original_error).__name__

        super().__init__(message, status_code or 500, details)
        self.operation = operation
        self.original_error = original_error
```
  In `stringdb_link/services/stringdb_service.py`, add the import (after the `from typing import ...` line ~10):
```python
from pydantic import ValidationError as PydanticValidationError
```
  Replace the network wrapper (`:178-185`) tail so the `raise` carries status + original error:
```python
        except Exception as e:
            self.logger.exception(
                "Error getting network interactions",
                error=str(e),
                identifiers=request.identifiers,
            )
            msg = f"Failed to get network interactions: {e}"
            status = 502 if isinstance(e, PydanticValidationError) else None
            raise StringDBServiceError(msg, original_error=e, status_code=status) from e
```
  Replace the enrichment wrapper (`:331-338`) tail the same way:
```python
        except Exception as e:
            self.logger.exception(
                "Error getting functional enrichment",
                error=str(e),
                identifiers=request.identifiers,
            )
            msg = f"Failed to get functional enrichment: {e}"
            status = 502 if isinstance(e, PydanticValidationError) else None
            raise StringDBServiceError(msg, original_error=e, status_code=status) from e
```
  These add ~3 lines to `stringdb_service.py` (1 import + 1 `status` line per site). It is grandfathered at ceiling 771 with zero slack, so bump `.loc-allowlist`:
```
stringdb_link/services/stringdb_service.py:776
```
  (Decomposition of this module remains tracked in the `.loc-allowlist` backlog; this is a minimal ceiling bump, not new debt.)
- [ ] (4) Run and confirm PASS: `uv run pytest tests/unit/test_stringdb_service.py -q && make lint-loc && make typecheck` — expected: all green; `stringdb_service.py` at <=776.
- [ ] (5) Commit: `fix(service): map STRING parse failures to structured 502, surface original error (#5)`

---

### Task 3: Relax brittle NetworkInteraction/InteractionPartner score bounds

**Files**
- Modify: `stringdb_link/models/responses.py` — `NetworkInteraction` score fields (`score:103`, `nscore:110`, `fscore:117`, `pscore:124`, `ascore:131`, `escore:138`, `dscore:145`, `tscore:152`) and `InteractionPartner` score fields (`score:194`, `nscore:201`, `fscore:208`, `pscore:215`, `ascore:222`, `escore:229`, `dscore:236`, `tscore:243`)
- Test: `tests/unit/test_responses_string_coercion.py` (append)

**Interfaces**
- Produces: `NetworkInteraction`/`InteractionPartner` accept STRING-reported scores >= 0.0 with no enforced upper bound (STRING combined/sub-scores are gateway-passthrough, not user input).
- Consumes: STRING `/api/json/network` and `/api/json/interaction_partners` records.

Steps:
- [ ] (1) Write the failing test (append to `tests/unit/test_responses_string_coercion.py`):
```python
from stringdb_link.models.responses import InteractionPartner, NetworkInteraction


def _network_record(score):
    return {
        "stringId_A": "9606.ENSP00000269305",
        "stringId_B": "9606.ENSP00000275493",
        "preferredName_A": "TP53",
        "preferredName_B": "EGFR",
        "ncbiTaxonId": "9606",  # STRING returns this as a string; must coerce
        "score": score,
        "nscore": 0.0,
        "fscore": 0.0,
        "pscore": 0.0,
        "ascore": 0.0,
        "escore": 0.329,
        "dscore": 0.0,
        "tscore": 0.919,
    }


def test_network_interaction_accepts_score_above_one():
    interaction = NetworkInteraction(**_network_record(1.02))
    assert interaction.score == pytest.approx(1.02)
    assert interaction.ncbi_taxon_id == 9606


def test_interaction_partner_accepts_score_above_one():
    record = _network_record(1.05)
    partner = InteractionPartner(**record)
    assert partner.score == pytest.approx(1.05)
```
- [ ] (2) Run and confirm FAIL: `uv run pytest tests/unit/test_responses_string_coercion.py -k accepts_score -q` — expected FAIL: `ValidationError ... score Input should be less than or equal to 1 [type=less_than_equal, input_value=1.02]`.
- [ ] (3) Minimal implementation: in `stringdb_link/models/responses.py`, delete the `        le=1.0,` line from each of the 8 score fields in `NetworkInteraction` and the 8 in `InteractionPartner` (16 line deletions total). Keep `ge=0.0,` and the descriptions. Example for `NetworkInteraction.score`:
```python
    score: float = Field(
        ...,
        ge=0.0,
        description="Combined confidence score (STRING-reported, normally 0.0-1.0)",
        json_schema_extra={"example": 0.999},
    )
```
  (Do NOT touch `EnrichmentTerm.p_value`/`fdr` or `PPIEnrichmentResult.*` bounds — those are genuine probabilities and remain `le=1.0`.)
- [ ] (4) Run and confirm PASS: `uv run pytest tests/unit/test_responses_string_coercion.py -q && make lint-loc` — expected: all passed; `responses.py` now well under its 640 ceiling.
- [ ] (5) Commit: `fix(models): relax STRING-derived network score upper bounds (#5)`

---

### Task 4: Correct misleading `required_score` docstrings (0-1000 integer scale)

**Files**
- Modify: `stringdb_link/api/client.py:397, 437, 549, 688` (identical docstring line)
- Test: `tests/unit/test_responses_string_coercion.py` (append a docstring-contract guard)

**Interfaces**
- Produces: client method docstrings that correctly describe `required_score: int = 400` as STRING's 0-1000 integer scale.
- Consumes: nothing at runtime; this guards against the misleading doc regressing.

Steps:
- [ ] (1) Write the failing test (append to `tests/unit/test_responses_string_coercion.py`):
```python
import inspect

from stringdb_link.api.client import StringDBClient


@pytest.mark.parametrize(
    "method_name",
    [
        "get_network_interactions",
        "get_interaction_partners",
        "get_network_image",
        "get_ppi_enrichment",
    ],
)
def test_required_score_docstring_uses_0_1000_scale(method_name):
    doc = inspect.getdoc(getattr(StringDBClient, method_name)) or ""
    assert "0-1000" in doc, f"{method_name} docstring should state the 0-1000 scale"
    assert "(0.0-1.0)" not in doc, f"{method_name} docstring still claims 0.0-1.0"
```
- [ ] (2) Run and confirm FAIL: `uv run pytest tests/unit/test_responses_string_coercion.py -k required_score_docstring -q` — expected FAIL: `AssertionError: ... docstring should state the 0-1000 scale`.
- [ ] (3) Minimal implementation: all four offending lines are byte-identical — `            required_score: Minimum confidence score (0.0-1.0)`. Replace every occurrence **in `client.py` only** (use Edit with `replace_all: true` on that exact string; do NOT edit `requests.py` or `networks.py`, whose `0.0-1.0` floats are correct):
```
            required_score: Minimum confidence score on STRING's 0-1000 integer scale (e.g. 400 = 0.4)
```
  (In-place edits; no change to `client.py` line count, so it stays within its 796 ceiling.)
- [ ] (4) Run and confirm PASS: `uv run pytest tests/unit/test_responses_string_coercion.py -k required_score_docstring -q` — expected: 4 passed.
- [ ] (5) Commit: `docs(client): correct required_score docstrings to 0-1000 integer scale (#5)`

---

### Task 5: Route-level regression — both tools 200 + non-empty on schema examples

**Files**
- Create: `tests/api/test_enrichment_network_regression.py`
- Test target endpoints: `POST /api/enrichment/functional` (`compute_functional_enrichment`), `POST /api/networks/interactions` (`search_protein_interactions`)

**Interfaces**
- Consumes: the `test_client` fixture from `tests/conftest.py`; patches `stringdb_link.api.client.StringDBClient.get_functional_enrichment` / `.get_network_interactions` (same pattern as `tests/api/test_homology.py`).
- Produces: end-to-end proof that the upstream variant shapes now yield 200 + non-empty, and a genuinely malformed record yields 502 (not 500).

Steps:
- [ ] (1) Write the failing test in `tests/api/test_enrichment_network_regression.py`:
```python
"""Route-level regression for stringdb-link #5 (enrichment/network HTTP 500)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from stringdb_link.models.responses import (
    EnrichmentTermListResponse,
    NetworkInteractionListResponse,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_string_caches():
    """Network/enrichment service methods are cached on a process-global manager;
    clear it so cache keys from other tests cannot mask the route behavior."""
    import asyncio

    from stringdb_link.utils.caching import cache_manager

    asyncio.run(cache_manager.clear_all_caches())
    yield


# STRING comma-separated variant (the production form that triggered the 500).
ENRICHMENT_RESPONSE = [
    {
        "category": "Process",
        "term": "GO:0000162",
        "number_of_genes": 5,
        "number_of_genes_in_background": 9,
        "ncbiTaxonId": 511145,
        "inputGenes": "trpA,trpB,trpC,trpGD,trpE",
        "preferredNames": "trpA,trpB,trpC,trpD,trpE",
        "p_value": 1.97e-13,
        "fdr": 6.18e-10,
        "description": "Tryptophan biosynthetic process",
    }
]

# Network record with ncbiTaxonId as a string and a score marginally above 1.0.
NETWORK_RESPONSE = [
    {
        "stringId_A": "9606.ENSP00000269305",
        "stringId_B": "9606.ENSP00000275493",
        "preferredName_A": "TP53",
        "preferredName_B": "EGFR",
        "ncbiTaxonId": "9606",
        "score": 1.02,
        "nscore": 0.0,
        "fscore": 0.0,
        "pscore": 0.0,
        "ascore": 0.0,
        "escore": 0.329,
        "dscore": 0.0,
        "tscore": 0.919,
    }
]


def test_functional_enrichment_returns_200_on_schema_example(test_client: TestClient):
    request_data = {"identifiers": ["trpA", "trpB", "trpC", "trpE", "trpGD"], "species": 511145}
    with patch(
        "stringdb_link.api.client.StringDBClient.get_functional_enrichment",
        new_callable=AsyncMock,
    ) as mock_enrichment:
        mock_enrichment.return_value = ENRICHMENT_RESPONSE
        response = test_client.post("/api/enrichment/functional", json=request_data)

    assert response.status_code == 200
    model = EnrichmentTermListResponse(**response.json())
    assert model.total_count == 1
    assert model.terms[0].input_genes == ["trpA", "trpB", "trpC", "trpGD", "trpE"]


def test_search_protein_interactions_returns_200_on_schema_example(test_client: TestClient):
    request_data = {"identifiers": ["TP53", "EGFR", "CDK2"], "species": 9606}
    with patch(
        "stringdb_link.api.client.StringDBClient.get_network_interactions",
        new_callable=AsyncMock,
    ) as mock_network:
        mock_network.return_value = NETWORK_RESPONSE
        response = test_client.post("/api/networks/interactions", json=request_data)

    assert response.status_code == 200
    model = NetworkInteractionListResponse(**response.json())
    assert model.total_count == 1
    assert model.interactions[0].score == pytest.approx(1.02)


def test_malformed_enrichment_record_returns_502_not_500(test_client: TestClient):
    bad = [dict(ENRICHMENT_RESPONSE[0])]
    del bad[0]["description"]  # required field missing -> upstream parse failure
    request_data = {"identifiers": ["trpA", "trpB"], "species": 511145}
    with patch(
        "stringdb_link.api.client.StringDBClient.get_functional_enrichment",
        new_callable=AsyncMock,
    ) as mock_enrichment:
        mock_enrichment.return_value = bad
        response = test_client.post("/api/enrichment/functional", json=request_data)

    assert response.status_code == 502
```
- [ ] (2) Run and confirm FAIL on a clean checkout of Tasks 1-3 NOT yet applied (or run now to confirm it passes once 1-3 are in): `uv run pytest tests/api/test_enrichment_network_regression.py -q`. Against current `main` (no fixes) the first two would FAIL with 500; after Tasks 1-3 they PASS. Expected pre-fix failure: `assert 500 == 200`.
- [ ] (3) No new implementation — this task is the integration gate over Tasks 1-3. If a test fails here, fix the responsible task, not the test.
- [ ] (4) Run and confirm PASS: `uv run pytest tests/api/test_enrichment_network_regression.py -q` then the full gate `make ci-local` — expected: 3 passed; ci-local green (format-check, lint, lint-loc, mypy, unit + integration).
- [ ] (5) Commit: `test(api): route regression for enrichment/network 200 + 502 (#5)`

---

## Acceptance criteria

- `uv run pytest tests/api/test_enrichment_network_regression.py -q` -> 3 passed: `POST /api/enrichment/functional` and `POST /api/networks/interactions` return **200 with non-empty payloads** on their schema-example inputs; a malformed upstream enrichment record returns **502**.
- `uv run pytest tests/unit/test_responses_string_coercion.py tests/unit/test_stringdb_service.py -q` -> all passed, including: comma-separated `inputGenes`/`preferredNames` split to `list[str]`; arrays pass through; `NetworkInteraction`/`InteractionPartner` accept `score > 1.0`; `StringDBServiceError` carries `status_code == 502` with `original_error` set on Pydantic `ValidationError`, and `== 500` otherwise.
- `inspect.getdoc(StringDBClient.get_network_interactions)` contains `"0-1000"` and not `"(0.0-1.0)"` (same for `get_interaction_partners`, `get_network_image`, `get_ppi_enrichment`).
- `make ci-local` passes (format-check, lint, `lint-loc` with `stringdb_service.py` <= 776 and `responses.py` <= 640, mypy, unit + integration, coverage >= 70).
- (Manual smoke, optional, requires network egress to STRING) `uv run python -c "import asyncio, json, urllib.request as u; print(len(json.load(u.urlopen('https://string-db.org/api/json/enrichment?identifiers=trpA%0dtrpB%0dtrpC%0dtrpE%0dtrpGD&species=511145&caller_identity=ci'))))"` returns a positive count and `compute_functional_enrichment` against a live backend returns 200.
- GitHub issue berntpopp/stringdb-link#5 is closeable (link the merged PR; reference Tasks 1-5).

## Risk & rollback

- **Scope is local code + tests only.** Per-task execution ends at a local conventional commit plus `make ci-local`; the plan does **not** push, open a PR, or redeploy. Therefore this plan is **NOT EXECUTION-GATED**.
- **Deploying the fix to the live VPS backend is a separate, out-of-scope step that IS execution-gated** (the fleet runs digest-pinned, hardened containers behind the router/proxy; a redeploy is a destructive remote op and must be gated/approved separately). Do not redeploy as part of executing this plan.
- **Rollback:** each task is one atomic commit; `git revert <sha>` per task. The new module (`coercions.py`) and the three test files are additive; reverting Task 1/3 restores the original strict models. The `.loc-allowlist` bump (Task 2) is a one-line revert.
- **Compatibility risks & mitigations:** (a) changing `StringDBServiceError.__init__` is backward-compatible (new param is optional, default preserves 500) — verified no positional callers pass a 4th arg; (b) `BeforeValidator` resolves correctly under `from __future__ import annotations` because `GeneNameList` is imported into the model module namespace; (c) FastAPI serializes the response model `by_alias=True`, so the regression tests reconstruct the model (`EnrichmentTermListResponse(**response.json())`) rather than asserting raw keys; (d) the comma-split is defensive — current STRING returns arrays, so the change is a no-op on the live API and only activates on the variant shape.

## Effort

~0.5 day (5 small, mostly mechanical tasks; one new ~20-line module, three focused test files, four in-place model/exception edits, one docstring `replace_all`, one allowlist bump). Lowest-risk, smallest-correct-change set that satisfies every acceptance criterion.
