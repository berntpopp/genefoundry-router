# PubTator-Link Issue #127 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the six verified #127 MCP defects while keeping v7.1.0's unauthenticated `readonly` profile strictly read-only and every advertised readonly workflow reachable.

**Architecture:** Keep source evidence partitioned by match confidence: an exact/equivalent ClinVar record is authoritative and broad ClinVar search results are explicitly labelled candidates. Make relation and PMCID adapters normalize upstream shape before creating public models; make sessions cursor-paginated summaries with detail behind the existing status tool. Profile-aware workflow construction must be the single source of follow-up tool names, so `readonly` ends at direct public retrieval while authenticated `lean`/`full` retain indexing.

**Tech Stack:** Python 3.12, FastMCP 3.x, Pydantic 2, FastAPI, asyncpg, pytest, Ruff, mypy, Docker Compose.

---

**Target checkout:** `/home/bernt-popp/development/pubtator-link` on a new branch from the current `main` SHA. Do not change router code for this issue.

## Fixed constraints

- `v7.1.0` has already shipped the profile/auth boundary: public `readonly` must continue to omit `index_review_evidence`, `stage_research_session`, `review_quickstart`, and every other write tool. This plan does **not** re-implement or loosen that boundary.
- A classification is authoritative only if the returned ClinVar record matches a normalized requested HGVS/protein expression. Broad gene-search records may be returned only in `candidate_variants`, never as peer `source_classifications`.
- `get_pmc_annotations` accepts canonical `PMC<digits>` input only; each requested canonical ID produces either meaningful BioC evidence or an actionable per-ID result. It may not report `count=len(request)` for an empty/mismatched upstream response.
- Public output remains research-use-only and is not a clinical interpretation.

## File map

- Modify: `pubtator_link/models/variants.py` — declare exact-match and candidate record shape.
- Modify: `pubtator_link/services/variant_evidence.py` — normalize query/record expressions and partition ClinVar results.
- Modify: `pubtator_link/mcp/relations.py` — select the endpoint opposite the queried entity and reject nonincident edges.
- Create: `pubtator_link/mcp/pmc_annotations.py` — canonical PMCID normalization and meaningful-document/error classification.
- Modify: `pubtator_link/mcp/service_adapters.py` — use the PMC normalizer and add opaque session paging arguments.
- Modify: `pubtator_link/mcp/tools/review/research.py` — expose `limit` and `cursor` for session lists.
- Modify: `pubtator_link/models/review_rerag.py` — page metadata and compact session-list row model.
- Modify: `pubtator_link/services/research_session.py` — stable ordering, signed opaque cursor scope, compact summaries.
- Modify: `pubtator_link/mcp/session_orientation.py` — return the compact paginated list, not manifests containing candidates.
- Modify: `pubtator_link/services/workflow_help.py`, `pubtator_link/mcp/resources.py`, and `pubtator_link/mcp/service_adapters.py` — derive profile-safe next tools/workflows from registered tools.
- Modify: `pubtator_link/services/source_preflight.py` — retain resolver evidence and classify the audit PMIDs truthfully when a resolver succeeds; do not assert a source defect until the live probe proves one.
- Modify: `docs/MCP_CONNECTION_GUIDE.md`, `docs/configuration.md`, `README.md`, and `CHANGELOG.md` — document readonly retrieval workflow, exact/candidate evidence, PMCID semantics, and pagination.
- Test: `tests/unit/test_variant_evidence_service.py`, `tests/unit/test_variant_evidence_models.py`, `tests/unit/mcp/test_mcp_service_adapters.py`, `tests/unit/mcp/test_mcp_facade.py`, `tests/unit/mcp/test_mcp_profiles.py`, `tests/unit/test_source_preflight.py`, `tests/unit/test_research_session_service.py`, `tests/unit/test_workflow_help.py`, `tests/unit/test_ncbi_discovery_service.py`, `tests/unit/test_corpus_suggestion_service.py`, `tests/unit/test_search_shaping.py`, and `tests/integration/test_mcp_live_surface_contract.py`.

### Task 1: Make variant classifications exact/equivalent-only

**Files:**

- Modify: `pubtator_link/models/variants.py`
- Modify: `pubtator_link/services/variant_evidence.py`
- Test: `tests/unit/test_variant_evidence_models.py`
- Test: `tests/unit/test_variant_evidence_service.py`

- [ ] **Step 1: Write failing partition and binding tests**

```python
@pytest.mark.asyncio
async def test_brca1_cys61gly_never_exposes_val191ile_as_authoritative() -> None:
    service = VariantEvidenceService(
        clinvar=StaticClinVar(
            [
                record("17661", "BRCA1 c.181T>G (p.Cys61Gly)", ["NM_007294.4:c.181T>G", "NP_009225.1:p.Cys61Gly"], "Pathogenic"),
                record("37684", "BRCA1 c.571G>A (p.Val191Ile)", ["NM_007294.4:c.571G>A", "NP_009225.1:p.Val191Ile"], "Benign"),
            ]
        ),
        pubtator_client=FakePubTatorClient(),
    )

    result = await service.lookup(
        VariantEvidenceRequest(gene="BRCA1", protein="p.Cys61Gly", sources=["clinvar"])
    )

    assert [item.variation_id for item in result.normalized_variants] == ["17661"]
    assert [item.variation_id for item in result.source_classifications] == ["17661"]
    assert [(item.variation_id, item.match_confidence) for item in result.candidate_variants] == [
        ("37684", "broad_candidate")
    ]


def test_variant_response_binds_every_authoritative_classification_to_one_exact_variant() -> None:
    response = VariantEvidenceResponse(
        query={"gene": "BRCA1", "protein": "p.Cys61Gly"},
        normalized_variants=[NormalizedVariant(source="clinvar", name="p.Cys61Gly", variation_id="17661")],
        source_classifications=[
            SourceClassification(source="clinvar", classification="Pathogenic", variation_id="17661")
        ],
    )
    assert {item.variation_id for item in response.source_classifications} <= {
        item.variation_id for item in response.normalized_variants
    }
```

- [ ] **Step 2: Run the focused tests and verify the red failure**

Run: `uv run pytest tests/unit/test_variant_evidence_service.py tests/unit/test_variant_evidence_models.py -q`

Expected: FAIL because `candidate_variants` and `match_confidence` do not exist and `p.Val191Ile` is currently emitted in authoritative parallel arrays.

- [ ] **Step 3: Add explicit candidate and exact-match models**

In `pubtator_link/models/variants.py`, add `match_confidence: Literal["exact", "equivalent"] = "exact"` to `NormalizedVariant`, add `match_confidence` with the same type to `SourceClassification`, and add:

```python
class CandidateVariant(NormalizedVariant):
    """A gene-level ClinVar search hit that did not match the requested expression."""

    match_confidence: Literal["broad_candidate"] = "broad_candidate"
    classification: str | None = None
    review_status: str | None = None
    condition: str | None = None


class VariantEvidenceResponse(BaseModel):
    success: bool = True
    query: dict[str, Any]
    normalized_variants: list[NormalizedVariant] = Field(default_factory=list)
    source_classifications: list[SourceClassification] = Field(default_factory=list)
    candidate_variants: list[CandidateVariant] = Field(default_factory=list)
    literature: list[VariantLiteratureEvidence] = Field(default_factory=list)
    conflicts: list[VariantConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Implement deterministic HGVS matching and partitioning**

Replace the parallel-list construction in `VariantEvidenceService.lookup` with the following helpers and use them before literature lookup:

```python
def _canonical_expression(value: str) -> str:
    value = value.casefold().replace(" ", "")
    value = value.removeprefix("np_").removeprefix("nm_")
    return value.split(":", maxsplit=1)[-1]


def _record_expressions(record: ClinVarRecord) -> set[str]:
    values = [*record.hgvs, record.preferred_name or ""]
    return {_canonical_expression(value) for value in values if value}


def _match_kind(record: ClinVarRecord, requested: list[str]) -> Literal["exact", "equivalent"] | None:
    expressions = _record_expressions(record)
    for term in requested:
        normalized = _canonical_expression(term)
        if normalized in expressions:
            return "exact"
        if normalized.startswith("p.") and normalized.removeprefix("p.") in expressions:
            return "equivalent"
    return None
```

For each returned record, append its normalized variant and classification only when `_match_kind` is not `None`, setting that confidence on both. For nonmatches append a `CandidateVariant` containing the identity and source-attributed label. Append the fixed warning `"ClinVar records in candidate_variants are broad gene-search candidates, not evidence for the requested variant."` only when candidates exist. Do not infer a protein/DNA equivalence that is absent from the upstream record.

- [ ] **Step 5: Run focused tests and retain current good behavior**

Run: `uv run pytest tests/unit/test_variant_evidence_service.py tests/unit/test_variant_evidence_models.py -q`

Expected: PASS; the existing MEFV exact match remains authoritative and the new BRCA1 test proves the benign Val191Ile record is candidate-only.

- [ ] **Step 6: Refactor query spelling tests, format, and commit**

Add a parameterized test for `"p.Cys61Gly"`, `"NP_009225.1:p.Cys61Gly"`, and whitespace/case variants, then run:

Run: `uv run ruff format --check pubtator_link/models/variants.py pubtator_link/services/variant_evidence.py tests/unit/test_variant_evidence_service.py && uv run ruff check pubtator_link/models/variants.py pubtator_link/services/variant_evidence.py tests/unit/test_variant_evidence_service.py && uv run mypy pubtator_link/models/variants.py pubtator_link/services/variant_evidence.py`

Expected: all commands exit 0.

```bash
git add pubtator_link/models/variants.py pubtator_link/services/variant_evidence.py \
  tests/unit/test_variant_evidence_models.py tests/unit/test_variant_evidence_service.py
git commit -m "fix: separate exact ClinVar evidence from broad candidates"
```

### Task 2: Correct relation endpoints and classify PMC annotation outcomes

**Files:**

- Modify: `pubtator_link/mcp/relations.py`
- Create: `pubtator_link/mcp/pmc_annotations.py`
- Modify: `pubtator_link/mcp/service_adapters.py`
- Test: `tests/unit/mcp/test_mcp_service_adapters.py`
- Test: `tests/unit/mcp/test_mcp_facade.py`

- [ ] **Step 1: Write failing shape tests**

```python
def test_relation_uses_endpoint_opposite_the_query_and_rejects_nonincident_edge() -> None:
    payload = shape_entity_relations(
        entity_id="@GENE_SCN1A",
        api_results=[
            {"source": "@DISEASE_Epilepsies_Myoclonic", "target": "@GENE_SCN1A", "type": "associate", "publications": 716},
            {"source": "@GENE_BRCA1", "target": "@GENE_BRCA2", "type": "associate"},
        ],
        relation_type=None,
        target_entity_type=None,
        limit=20,
        response_mode="standard",
        max_response_chars=12_000,
    )
    assert [row["entity_id"] for row in payload["related_entities"]] == ["@DISEASE_Epilepsies_Myoclonic"]
    assert payload["total_relations"] == 1


@pytest.mark.asyncio
async def test_pmc_adapter_normalizes_bare_id_and_classifies_empty_document() -> None:
    response = await fetch_pmc_annotations_impl(
        service=EmptyPmcService(), pmcids=["PMC11223834"], format="biocjson"
    )
    assert response["success"] is False
    assert response["error_code"] == "not_found"
    assert response["message"] == "No PubTator full text is available for PMCID PMC11223834."
```

- [ ] **Step 2: Verify the red tests**

Run: `uv run pytest tests/unit/mcp/test_mcp_service_adapters.py tests/unit/mcp/test_mcp_facade.py -q -k 'relation or pmc'`

Expected: FAIL; `_related_entity` blindly selects `target`, and `fetch_pmc_annotations_impl` reports a success with `count=len(pmcids)` even when the upstream document is empty or uses a bare identifier.

- [ ] **Step 3: Implement relation edge normalization**

Replace `_related_entity` in `pubtator_link/mcp/relations.py` with a function returning `RelatedEntity | None`:

```python
def _related_entity(
    item: dict[str, Any], *, query_entity_id: str, response_mode: RelationResponseMode
) -> RelatedEntity | None:
    source = str(item.get("source") or "")
    target = str(item.get("target") or "")
    if source == query_entity_id and target and target != query_entity_id:
        related = target
    elif target == query_entity_id and source and source != query_entity_id:
        related = source
    else:
        return None
    return RelatedEntity(
        entity_id=related,
        entity_name=item.get("entity_name") or item.get("name"),
        entity_type=item.get("entity_type") or item.get("type_name"),
        relation_type=str(item.get("type") or ""),
        confidence=item.get("confidence"),
        pmids=[] if response_mode == "compact" else list(item.get("pmids") or []),
        source=source,
        target=target,
        publications=item.get("publications"),
    )
```

Build `related_entities` by iterating all upstream records, skipping `None`, then applying the caller limit; set `total_relations=len(incident_entities)`, not the raw upstream length.

- [ ] **Step 4: Add the PMCID normalizer and meaningful-document classifier**

Create `pubtator_link/mcp/pmc_annotations.py`:

```python
from __future__ import annotations

import re
from typing import Any

_PMCID = re.compile(r"^PMC(?P<number>[1-9][0-9]*)$")


def canonical_pmcid(value: str) -> str:
    candidate = value.strip().upper()
    match = _PMCID.fullmatch(candidate)
    if match is None:
        raise ValueError("pmcids must contain canonical PMC IDs such as PMC11223843")
    return f"PMC{match.group('number')}"


def canonical_document_id(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if text.isdigit():
        return f"PMC{text}"
    return text if _PMCID.fullmatch(text) else None


def has_meaningful_pmc_content(document: dict[str, Any]) -> bool:
    return any(bool(document.get(key)) for key in ("passages", "annotations", "relations")) or bool(
        str(document.get("text") or "").strip()
    )


def classify_pmc_documents(
    requested: list[str], documents: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id = {canonical_document_id(document.get("id")): document for document in documents}
    found = [by_id[pmcid] for pmcid in requested if pmcid in by_id and has_meaningful_pmc_content(by_id[pmcid])]
    unavailable = [pmcid for pmcid in requested if pmcid not in {canonical_document_id(row.get("id")) for row in found}]
    return found, unavailable
```

In `fetch_pmc_annotations_impl`, canonicalize all arguments before calling the service. Return the existing successful `PublicationExportResponse` only when every requested ID has meaningful content; otherwise return one flat envelope with `success=False`, `isError=True`, `error_code="not_found"`, `message="No PubTator full text is available for PMCID <id>."` for one ID, or the fixed plural form with `unavailable_pmcids` for several. Catch an upstream `PubTatorAPIError` whose status is 400/404 and return the same `not_found` envelope; let 5xx/timeout continue to the standard `upstream_unavailable` boundary. Do not expose raw upstream prose.

- [ ] **Step 5: Pass the focused unit and facade tests**

Run: `uv run pytest tests/unit/mcp/test_mcp_service_adapters.py tests/unit/mcp/test_mcp_facade.py -q -k 'relation or pmc'`

Expected: PASS; a bare upstream `11223843` maps back to `PMC11223843`, a nonexistent document is `not_found`, and a nonincident edge is omitted.

- [ ] **Step 6: Commit the independently releasable adapter repair**

```bash
git add pubtator_link/mcp/relations.py pubtator_link/mcp/pmc_annotations.py \
  pubtator_link/mcp/service_adapters.py tests/unit/mcp/test_mcp_service_adapters.py \
  tests/unit/mcp/test_mcp_facade.py
git commit -m "fix: normalize PubTator relation and PMCID evidence"
```

### Task 3: Bound research-session listings with opaque cursors

**Files:**

- Modify: `pubtator_link/models/review_rerag.py`
- Modify: `pubtator_link/services/research_session.py`
- Modify: `pubtator_link/mcp/session_orientation.py`
- Modify: `pubtator_link/mcp/service_adapters.py`
- Modify: `pubtator_link/mcp/tools/review/research.py`
- Test: `tests/unit/test_research_session_service.py`
- Test: `tests/unit/mcp/test_mcp_facade.py`

- [ ] **Step 1: Add the failing API-contract test**

```python
@pytest.mark.asyncio
async def test_list_sessions_is_compact_and_cursor_pages_without_gaps() -> None:
    service = ResearchSessionService(repository=TwentyFiveSessionsRepository(), search_provider=FakeSearch(), preflight_service=FakePreflight(), queue=FakeQueue())
    first = await service.list_sessions_page(review_id=None, limit=2, cursor=None)
    second = await service.list_sessions_page(review_id=None, limit=2, cursor=first.next_cursor)

    assert [row.session_id for row in first.sessions] == ["session-25", "session-24"]
    assert [row.session_id for row in second.sessions] == ["session-23", "session-22"]
    assert first.sessions[0].model_dump().keys() == {
        "session_id", "review_id", "query", "status", "updated_at", "candidate_count", "preparation_status"
    }
    assert first.next_cursor is not None
```

- [ ] **Step 2: Run it red**

Run: `uv run pytest tests/unit/test_research_session_service.py tests/unit/mcp/test_mcp_facade.py -q -k 'list_research_sessions or sessions_page'`

Expected: FAIL because only an unbounded `review_id` argument exists and `ListResearchSessionsResponse` serializes every candidate plus coverage hint.

- [ ] **Step 3: Add the compact page contract and opaque scope cursor**

In `pubtator_link/models/review_rerag.py`, add:

```python
class ResearchSessionListItem(BaseModel):
    session_id: str
    review_id: str
    query: str | None = None
    status: ResearchSessionStatus
    updated_at: str | None = None
    candidate_count: int = Field(ge=0)
    preparation_status: PreparationStatus | None = None


class ListResearchSessionsResponse(BaseModel):
    success: bool = True
    sessions: list[ResearchSessionListItem] = Field(default_factory=list)
    limit: int = Field(ge=1, le=20)
    next_cursor: str | None = None
    total_returned: int = Field(ge=0)
```

In `services/research_session.py`, encode JSON `{ "v": 1, "scope": sha256((review_id or "global").encode()).hexdigest()[:16], "updated_at": row.updated_at, "session_id": row.session_id }` using URL-safe base64 without padding. Decode strictly; reject a mismatched scope or malformed token with `ValueError("cursor is invalid for this research-session listing")`. Ask the repository for `limit + 1` rows ordered `updated_at DESC NULLS LAST, session_id DESC`, trim to `limit`, and set a cursor only when the extra row exists. Project each manifest to `ResearchSessionListItem`; never load/reconcile `candidates` for list output. Keep `get_research_session_status` as the sole detail endpoint.

- [ ] **Step 4: Wire the MCP schema and adapter**

Change the tool signature in `pubtator_link/mcp/tools/review/research.py` to:

```python
async def list_research_sessions(
    review_id: Annotated[str | None, Field(min_length=1, description="Optional review index; omit for recent global sessions.")] = None,
    limit: Annotated[int, Field(ge=1, le=20, description="Maximum compact session summaries to return.")] = 10,
    cursor: Annotated[str | None, Field(description="Opaque cursor returned by the previous list_research_sessions page.")] = None,
) -> dict[str, Any]:
```

Pass both fields through `list_research_sessions_impl` and `research_sessions_payload`. The invalid-cursor error must arrive through `run_mcp_tool` as `invalid_input` naming `cursor`.

- [ ] **Step 5: Verify schemas and behavior**

Run: `uv run pytest tests/unit/test_research_session_service.py tests/unit/mcp/test_mcp_facade.py -q -k 'list_research_sessions or sessions_page'`

Expected: PASS; schema exposes `limit` and `cursor`, first/second pages have no duplicates or gaps, and serialized rows have no `candidates` or `coverage_hint`.

- [ ] **Step 6: Commit pagination independently**

```bash
git add pubtator_link/models/review_rerag.py pubtator_link/services/research_session.py \
  pubtator_link/mcp/session_orientation.py pubtator_link/mcp/service_adapters.py \
  pubtator_link/mcp/tools/review/research.py tests/unit/test_research_session_service.py \
  tests/unit/mcp/test_mcp_facade.py
git commit -m "fix: paginate compact research session summaries"
```

### Task 4: Make readonly workflow advice contiguous and profile-safe

**Files:**

- Modify: `pubtator_link/services/workflow_help.py`
- Modify: `pubtator_link/mcp/resources.py`
- Modify: `pubtator_link/mcp/service_adapters.py`
- Modify: `pubtator_link/services/corpus_suggestion.py`
- Modify: `pubtator_link/services/citation_graph.py`
- Modify: `pubtator_link/mcp/prompts.py`
- Test: `tests/unit/test_workflow_help.py`
- Test: `tests/unit/test_ncbi_discovery_service.py`
- Test: `tests/unit/test_corpus_suggestion_service.py`
- Test: `tests/unit/mcp/test_mcp_profiles.py`

- [ ] **Step 1: Capture every stale next-tool family with failing tests**

```python
def assert_registered(profile: str, names: list[str]) -> None:
    registered = _tool_names(profile)
    assert set(names) <= registered


def test_readonly_clinical_workflow_is_contiguous_and_retrieval_only() -> None:
    response = WorkflowHelpService(profile="readonly").get_help("clinical_genetics_review")
    assert [step.order for step in response.steps] == list(range(1, len(response.steps) + 1))
    assert response.tool_sequence[-1] == "get_publication_passages"
    assert_registered("readonly", response.tool_sequence)
    assert "index_review_evidence" not in response.tool_sequence


def test_readonly_search_metadata_never_suggests_a_missing_write_tool() -> None:
    assert readonly_next_tools(["preflight_review_sources", "index_review_evidence"]) == [
        "preflight_review_sources", "get_publication_passages"
    ]
```

- [ ] **Step 2: Run the tests red**

Run: `uv run pytest tests/unit/test_workflow_help.py tests/unit/test_ncbi_discovery_service.py tests/unit/test_corpus_suggestion_service.py tests/unit/mcp/test_mcp_profiles.py -q`

Expected: FAIL because `WorkflowHelpService._profile_response` filters tools after assigning order and static service strings still advertise `index_review_evidence`/`stage_research_session` to the readonly public profile.

- [ ] **Step 3: Introduce a single profile-aware follow-up selector**

Create a small pure helper in `pubtator_link/mcp/profiles.py`:

```python
def reachable_tools(profile: MCPToolProfile, preferred: tuple[str, ...]) -> list[str]:
    allowed = tool_names_for_profile(profile)
    return [name for name in preferred if name in allowed]


def readonly_retrieval_followup(profile: MCPToolProfile) -> list[str]:
    return reachable_tools(
        profile,
        ("preflight_review_sources", "index_review_evidence", "inspect_review_index", "get_review_context_batch", "get_publication_passages"),
    )
```

Construct `WorkflowStep` lists after filtering, then recreate `order` with `enumerate(steps, start=1)`. For `readonly`, make the canonical chain `search_biomedical_entities -> find_entity_relations -> get_variant_evidence -> search_literature -> get_publication_metadata -> preflight_review_sources -> get_publication_passages`; do not include demo-only review indexes in a public workflow. For `lean` and `full`, preserve write workflows and add an authentication/profile message rather than exposing any write tool in `readonly`.

Pass the selected profile from tool registration into response-producing service adapters; replace every static `next_tools`/`next_commands` in the named files with `reachable_tools(profile, ...)`. Filter text-only workflow bundle strings too, not merely mappings. Add a test that walks every nested `tool`, `next_tools`, and `next_commands` in `get_server_capabilities(profile="readonly")` and asserts all names are registered.

- [ ] **Step 4: Verify profile behavior and preserve v7.1.0 security**

Run: `uv run pytest tests/unit/test_workflow_help.py tests/unit/test_ncbi_discovery_service.py tests/unit/test_corpus_suggestion_service.py tests/unit/mcp/test_mcp_profiles.py -q`

Expected: PASS; `readonly` remains `full - WRITE_TOOLS`, every advertised callback is registered, and `lean`/`full` still advertise their authorized indexing flow.

- [ ] **Step 5: Add source-preflight audit coverage without inventing live facts**

Add a unit test with the actual audit PMIDs using injected resolver fixtures:

```python
@pytest.mark.asyncio
async def test_audit_pmids_are_full_text_when_the_resolver_proves_it() -> None:
    service = SourcePreflightService(
        id_converter=lambda pmid: {"pmcid": {"36644199": "PMC10000001", "38034271": "PMC10000002"}[pmid]},
        pmc_bioc_available=lambda _pmcid: True,
    )
    hints = await service.preflight_pmids(["36644199", "38034271"])
    assert [(hint.expected_coverage, hint.coverage_reason) for hint in hints] == [
        ("full_text", "pmc_oa_bioc"), ("full_text", "pmc_oa_bioc")
    ]
```

Run the actual live-safe contract command only against public records and record its result before changing source semantics:

Run: `uv run python scripts/mcp_live_contract_probe.py --tool preflight_review_sources --arguments '{"pmids":["36644199","38034271"]}'`

Expected: either two resolver-backed `full_text` hints or a classified upstream result with resolver attempts. If the live output contradicts the unit resolver path, add the minimal resolver/client fix with a recorded upstream payload fixture; do not hard-code these PMIDs or claim full text from a stale demo index.

- [ ] **Step 6: Commit workflow and preflight repair**

```bash
git add pubtator_link/mcp/profiles.py pubtator_link/services/workflow_help.py \
  pubtator_link/mcp/resources.py pubtator_link/mcp/service_adapters.py \
  pubtator_link/services/corpus_suggestion.py pubtator_link/services/citation_graph.py \
  pubtator_link/mcp/prompts.py pubtator_link/services/source_preflight.py \
  tests/unit/test_workflow_help.py tests/unit/test_ncbi_discovery_service.py \
  tests/unit/test_corpus_suggestion_service.py tests/unit/mcp/test_mcp_profiles.py \
  tests/unit/test_source_preflight.py
git commit -m "fix: keep readonly PubTator workflows reachable"
```

### Task 5: Document, verify, release, deploy, and close #127

**Files:**

- Modify: `README.md`
- Modify: `docs/MCP_CONNECTION_GUIDE.md`
- Modify: `docs/configuration.md`
- Modify: `CHANGELOG.md`
- Test: `tests/unit/test_readme_tools.py`
- Test: `tests/integration/test_mcp_live_surface_contract.py`

- [ ] **Step 1: Add exact public-contract documentation and tests**

Document these literal rules: `readonly` ends in `get_publication_passages`; `index_review_evidence` is available only to configured authenticated non-readonly profiles; `candidate_variants` are not classifications for the query; and session lists are compact/cursor-paginated with status used for details. Add README tool-table assertions if descriptions change.

- [ ] **Step 2: Run all issue-specific tests**

Run: `uv run pytest tests/unit/test_variant_evidence_service.py tests/unit/test_variant_evidence_models.py tests/unit/mcp/test_mcp_service_adapters.py tests/unit/mcp/test_mcp_facade.py tests/unit/test_research_session_service.py tests/unit/test_workflow_help.py tests/unit/test_source_preflight.py tests/unit/mcp/test_mcp_profiles.py tests/unit/test_readme_tools.py tests/integration/test_mcp_live_surface_contract.py -q`

Expected: PASS.

- [ ] **Step 3: Execute repository gates and commit docs**

Run: `make ci-local`

Expected: exit 0, including README, tool-surface, format, lint, LOC, type, unit, and integration gates.

```bash
git add README.md docs/MCP_CONNECTION_GUIDE.md docs/configuration.md CHANGELOG.md \
  tests/unit/test_readme_tools.py tests/integration/test_mcp_live_surface_contract.py
git commit -m "docs: describe safe PubTator evidence workflows"
git push -u origin fix/issue-127-mcp-correctness
gh pr create --repo berntpopp/pubtator-link --base main --head fix/issue-127-mcp-correctness \
  --title "fix: correct PubTator evidence and readonly workflows" --body-file .github/PULL_REQUEST_TEMPLATE.md
```

- [ ] **Step 4: Merge only the checked commit, tag, and deploy**

After required checks and review are green, record `MERGE_SHA=$(gh pr view --json mergeCommit --jq .mergeCommit.oid)`, verify `gh api "repos/berntpopp/pubtator-link/commits/$MERGE_SHA/check-runs" --jq '.check_runs[] | select(.conclusion != "success" and .conclusion != "skipped")'` emits no rows, then create the next patch release from that exact SHA. Build the production image, record its digest, and deploy only that digest with `PUBTATOR_LINK_MCP_PROFILE=readonly` and no public backend port.

- [ ] **Step 5: Run post-deploy MCP acceptance probes and close with evidence**

Run each against `https://pubtator-link.genefoundry.org/mcp` using a plain JSON-RPC `tools/call` client:

```text
get_variant_evidence({"gene":"BRCA1","protein":"p.Cys61Gly","sources":["clinvar"],"max_literature_pmids":0})
find_entity_relations({"entity_id":"@GENE_SCN1A","limit":3,"response_mode":"full"})
get_pmc_annotations({"pmcids":["PMC11223843"]})
get_pmc_annotations({"pmcids":["PMC11223834"]})
list_research_sessions({"limit":3})
workflow_help({"task":"clinical_genetics_review"})
```

Expected: no benign Val191Ile classification in authoritative BRCA1 evidence; every returned relation points away from SCN1A; valid PMCID is evidence or classified availability result and nonexistent PMCID is `not_found`; sessions are bounded with `next_cursor`; every readonly next tool appears in `tools/list`; no write tool is listed. Post the merge SHA, release tag, image digest, probe command/output summaries, and the preflight audit result to issue #127, then close it.
