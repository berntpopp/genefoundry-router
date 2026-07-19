# StringDB Link #33 Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Release StringDB-Link v4.1.0's completed MCP repair, add deterministic audit regressions, and prove the public endpoint is the exact reviewed image.

**Architecture:** Preserve the current generated REST-to-MCP surface. The JSON image route owns base64 serialization; annotations stays a registered route; enrichment is category-filtered, FDR-sorted, then limited in the service; in-band STRING errors are classified before Pydantic parsing. Deployment is bound from main SHA to release tag to GHCR digest to health revision.

**Tech Stack:** Python 3.12, FastAPI, FastMCP, Pydantic, pytest/AsyncMock, uv, Ruff, mypy, Docker Compose, GitHub CLI, GHCR.

---

## File Map

- Read/characterize: stringdb_link/api/routes/images.py, annotations.py, enrichment.py; stringdb_link/services/stringdb_service.py; stringdb_link/models/requests.py; stringdb_link/app.py.
- Modify: tests/api/test_enrichment_network_regression.py and tests/unit/test_stringdb_service.py.
- Modify only if a characterization test disproves current behavior: stringdb_link/api/routes/images.py, annotations.py, stringdb_link/services/stringdb_service.py, stringdb_link/models/requests.py, or stringdb_link/app.py.
- Release metadata only if a source fix is needed: pyproject.toml, uv.lock, CHANGELOG.md.
- Read-only contracts: .github/workflows/ci.yml, conformance.yml, container-release.yml; container-release.json; docker/docker-compose*.yml.

## Invariants

- get_network_image produces nonempty decodable base64 plus image_format, content_type, and image_size_bytes. Empty upstream bytes are an error, never an empty success.
- get_functional_annotations remains registered at /api/annotations/functional.
- get_network_link advertises only formats that return a usable LinkInfo.
- Enrichment total_count is pre-limit count after category filter; terms are FDR ascending; truncated equals total_count greater than emitted count.
- HTTP-200 background_error becomes non-retryable invalid_input naming background_string_identifiers; server never repeats upstream prose.
- Version/tag alone is not release proof.

### Task 1: Establish Candidate and Capture the Stale Endpoint

**Files:** read-only.

- [ ] **Step 1: Create a clean branch.**

~~~bash
cd /home/bernt-popp/development/stringdb-link
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c fix/release-mcp-audit-33
git status --short
~~~

Expected: clean status and pyproject.toml declares 4.1.0.

- [ ] **Step 2: Confirm current source already has every repair.**

~~~bash
rg -n 'images/network/json|base64\.b64encode|_EMPTY_IMAGE_DETAIL' stringdb_link/api/routes/images.py
rg -n 'operation_id="get_functional_annotations"' stringdb_link/api/routes/annotations.py
rg -n 'category.*limit|sorted\(terms, key=lambda term: term.fdr\)|_raise_if_string_error' \
  stringdb_link/{models/requests.py,services/stringdb_service.py}
rg -n 'RouteMap\(pattern=r"\^/api/images/network"' stringdb_link/app.py
~~~

Expected: each command finds current source. A missing match blocks release and must be corrected by the red test in Task 2.

- [ ] **Step 3: Record red deployed evidence.**

~~~bash
BASE=https://stringdb-link.genefoundry.org
curl -fsS "$BASE/api/health" | tee /tmp/string-33-before-health.json | jq .
curl -sS "$BASE/mcp" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":33,"method":"tools/call","params":{"name":"get_network_image","arguments":{"identifiers":["TP53","MDM2"],"species":9606}}}' \
  | tee /tmp/string-33-before-image.json
~~~

Expected before rollout: an old version/revision or the audit's unusable image result.

### Task 2: Write Direct Regression Tests

**Files:** tests/api/test_enrichment_network_regression.py; tests/unit/test_stringdb_service.py.

- [ ] **Step 1: Add the JSON-image tests before implementation.**

Use current TestClient and AsyncMock patterns:

~~~python
def test_network_image_json_route_returns_decodable_base64(test_client: TestClient) -> None:
    with patch("stringdb_link.api.client.StringDBClient.get_network_image", new_callable=AsyncMock) as upstream:
        upstream.return_value = b"\x89PNG\r\n\x1a\nbytes"
        response = test_client.post(
            "/api/images/network/json", json={"identifiers": ["TP53"], "species": 9606}
        )
    body = response.json()
    assert response.status_code == 200
    assert base64.b64decode(body["image_base64"]) == b"\x89PNG\r\n\x1a\nbytes"
    assert body["content_type"] == "image/png"
    assert body["image_size_bytes"] == 13
~~~

Add a sibling b"" upstream response test asserting status 502. It must reject empty structured success.

- [ ] **Step 2: Add registration, limit, category, order, and input-error tests.**

Define three fully valid enrichment response rows: KEGG FDR 0.20 and 0.05 plus Process FDR 0.01. Add:

~~~python
def test_functional_annotation_operation_is_registered() -> None:
    assert any(route.name == "get_functional_annotations" for route in app.routes)


async def test_enrichment_filters_sorts_and_reports_full_total(service, mock_client) -> None:
    mock_client.get_functional_enrichment.return_value = TERMS
    result = await service.get_functional_enrichment(
        EnrichmentRequest(identifiers=["TP53"], species=9606, category="KEGG", limit=1)
    )
    assert result.total_count == 2
    assert result.truncated is True
    assert [term.fdr for term in result.terms] == [0.05]


async def test_enrichment_background_error_names_parameter(service, mock_client) -> None:
    mock_client.get_functional_enrichment.return_value = [{"error": "background_error", "message": "ignored"}]
    with pytest.raises(ValidationError) as excinfo:
        await service.get_functional_enrichment(EnrichmentRequest(identifiers=["BBS1", "BBS2"]))
    assert excinfo.value.field == "background_string_identifiers"
~~~

Also assert truncated equals total_count > len(terms) for each limit value 1, 2, and 3.

- [ ] **Step 3: Add a generated-link schema test.**

List the actual generated MCP tools, find get_network_link, and assert its output_format allowed values are exactly formats for which mocked StringDB response becomes a LinkInfo with nonempty url. Do not hand-copy the former nine-format upstream enum.

- [ ] **Step 4: Run focused regression set.**

~~~bash
uv run pytest tests/api/test_enrichment_network_regression.py \
  tests/unit/test_stringdb_service.py tests/test_api_routes_error_handling.py -q
~~~

Expected: PASS on v4.1.0. These are characterization tests because source already has the correction. If one fails, retain it red, repair only the named current source path, rerun it green, and do not weaken assertion.

- [ ] **Step 5: Commit tests and any narrowly required repair.**

~~~bash
git add stringdb_link tests
git commit -m "test: lock StringDB MCP audit regressions"
~~~

### Task 3: Full Local and Runtime Verification

**Files:** no edit unless a check fails.

- [ ] **Step 1: Run mandatory local gate.**

Run: make ci-local

Expected: exit 0.

- [ ] **Step 2: Use the same actual MCP shape as conformance CI.**

~~~bash
make docker-down || true
make docker-build
make docker-up
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/api/health && break || sleep 2; done
CONFORMANCE_NAME=stringdb-link CONFORMANCE_MCP_URL=http://127.0.0.1:8000 \
  uv run pytest tests/conformance/test_transport_v1.py tests/conformance/test_behaviour_v1.py -v
make docker-down
~~~

Expected: PASS. A live STRING call is release evidence only, never a default unit-test dependency.

### Task 4: Exact-SHA Release and Digest Deployment

**Files:** Git/GitHub/deployment state.

- [ ] **Step 1: Keep release version truthful.**

If the verified candidate stays 4.1.0, add only an Unreleased CHANGELOG note documenting deployment of the already-merged repair. Do not make a pretend version bump. If Task 2 found source debt, choose next SemVer version, update pyproject.toml, run uv lock, and amend CHANGELOG before CI.

- [ ] **Step 2: Open PR and require checks for exact reviewed head.**

~~~bash
git push -u origin fix/release-mcp-audit-33
gh pr create --base main --head fix/release-mcp-audit-33 \
  --title "fix: release StringDB MCP audit remediation" --fill
HEAD_SHA=$(gh pr view --json headRefOid -q .headRefOid)
gh api "repos/berntpopp/stringdb-link/commits/$HEAD_SHA/check-runs" --jq '.check_runs[] | {name,status,conclusion}'
~~~

Expected: CI, conformance, container, and security all succeed for HEAD_SHA exactly.

- [ ] **Step 3: Merge and tag only that verified main commit.**

~~~bash
gh pr merge --squash --delete-branch
git fetch origin main --tags
MAIN_SHA=$(git rev-parse origin/main)
git tag -a v4.1.0 "$MAIN_SHA" -m "stringdb-link 4.1.0"
git push origin v4.1.0
test "$(git rev-parse v4.1.0^{commit})" = "$MAIN_SHA"
~~~

If v4.1.0 already points to another commit, stop and cut the next approved release; never force-move it.

- [ ] **Step 4: Deploy immutable image.**

After tag-triggered Container release passes, resolve its GHCR manifest digest and deploy only STRINGDB_LINK_IMAGE=ghcr.io/berntpopp/stringdb-link@sha256:<resolved-digest>. Record main SHA, tag, digest, rollout time, and release workflow URL on issue #33.

### Task 5: Public Acceptance and Closure

**Files:** issue #33 evidence only.

- [ ] **Step 1: Verify deployed provenance.**

Public /api/health must report version 4.1.0+ and main-SHA revision/build. If health lacks a revision field, combine get_server_capabilities with digest-addressed deployment evidence; version alone fails this check.

- [ ] **Step 2: Re-run all five audit probes through /mcp.**

Call get_network_image(TP53, MDM2); get_functional_annotations(CFTR); compute_functional_enrichment(BBS1, BBS2, limit 3); get_network_link(SCN1A, SCN2A); and enrichment with BBS query genes but invalid one-id background.

Expected: decodable image/media metadata; nonempty CFTR annotations; at most three FDR-sorted terms with honest total/truncated; a usable link; non-retryable invalid_input naming background_string_identifiers.

- [ ] **Step 3: Close with evidence.**

Comment exact SHA, release tag, image digest, check-run URLs, health proof, and five compact probe outcomes. Close #33 only when every condition passed.
