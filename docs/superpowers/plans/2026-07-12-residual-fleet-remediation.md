# Residual Fleet Remediation Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close R-01 through R-08 with fail-closed behavior and regression tests across the router and affected GeneFoundry backends.

**Architecture:** Execute five isolated repository tracks in parallel after the router publishes the HTTP-policy-v1 recipe. Each repository writes a failing behavior test before the smallest scoped change, passes its own `make ci-local`, and commits independently. The router holds the release-contract fixture and fleet adoption ledger; it never performs a live re-pin in CI.

**Tech Stack:** Python 3.12+, uv, pytest/respx/pytest-xdist, httpx, FastMCP 3.x, GitHub Actions YAML, Ruff, mypy, `make ci-local`.

---

## Repository and file map

| Track | Repositories | Main files |
| --- | --- | --- |
| Router | `genefoundry-router` | `scripts/snapshot_fleet.py`, `genefoundry_router/{config,cli}.py`, baseline/CLI tests, production Compose |
| HTTP-policy v1 | router + eight backends | router policy/ledger; each `*/api/url_guard.py`, configured client, redirect/cap tests |
| Backend behavior | GeneReviews, UniProt, MaveDB | archive download guard, SPARQL validator/client, MaveDB resolvers |
| Supply chain | GeneReviews, Orphanet | workflow/composite YAML, recursive pin-check tests, Dependabot |
| CI isolation | MGI, UniProt | builder lookup/unit fixture; logger-state fixture |

### Task 1: Router R-02 and R-08

**Files:**
- Modify: `scripts/snapshot_fleet.py`, `genefoundry_router/config.py`, `genefoundry_router/cli.py`, `docker/docker-compose.prod.yml`, `.env.example`, `README.md`
- Modify: `tests/unit/test_ci_fleet_baseline.py`, `tests/unit/test_cli.py`
- Create: `tests/integration/test_release_candidate_baseline.py`, `ci/release-candidate-fleet.json`

- [ ] **Step 1: Write failing release-contract and production-loopback tests.**

```python
def test_release_candidate_baseline_has_corrected_annotations() -> None:
    manifest = load_manifest(BASELINE)
    assert manifest.backends["litvar"].tools[0].annotations is not None
    assert all(tool.annotations is not None for tool in manifest.backends["vep"].tools)

def test_production_loopback_requires_rate_limit_and_metrics_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GF_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.setenv("GF_RATE_LIMIT_RPM", "0")
    result = runner.invoke(app, ["run", "--servers-file", str(_write_registry(tmp_path))])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run the tests and verify they fail because the baseline is stale and loopback bypasses the controls.**

Run: `uv run pytest tests/integration/test_release_candidate_baseline.py tests/unit/test_cli.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement the smallest release-candidate capture and deployment-mode behavior.**

```python
def merge_backend(prior: BackendSpec | None, fresh: BackendSpec | None) -> BackendSpec:
    if fresh is None:
        raise RuntimeError("required release-candidate backend was unreachable")
    return fresh

def requires_observability_controls(auth_mode: str, deployment_mode: str) -> bool:
    return auth_mode != "none" and deployment_mode == "production"
```

Record the reviewed candidate identity in snapshot metadata; never retain stale members during a candidate snapshot. Require a positive rate limit and metrics token whenever `requires_observability_controls` is true. Set production Compose explicitly to production mode and document the reverse-proxy model.

- [ ] **Step 4: Verify and commit.**

Run: `make ci-local`

Expected: PASS.

```bash
git add scripts/snapshot_fleet.py genefoundry_router tests ci docker .env.example README.md
git commit -m "fix: gate reviewed fleet baseline and proxy-aware controls"
```

### Task 2: Publish router HTTP-policy v1 and adoption gate

**Files:**
- Create: `docs/HTTP-POLICY-STANDARD-v1.md`, `ci/http-policy-v1.json`, `tests/unit/test_http_policy_adoption.py`
- Modify: `Makefile`, `.github/workflows/ci.yml`

- [ ] **Step 1: Write a failing exact-fleet adoption test.**

```python
def test_http_policy_v1_adoption_manifest_covers_exact_issue_repositories() -> None:
    manifest = json.loads(Path("ci/http-policy-v1.json").read_text())
    assert set(manifest["repositories"]) == {
        "gtex-link", "litvar-link", "metadome-link", "panelapp-link",
        "spliceailookup-link", "stringdb-link", "uniprot-link", "vep-link",
    }
    assert all(entry["version"] == "v1" for entry in manifest["repositories"].values())
```

- [ ] **Step 2: Run it and verify the missing-policy failure.**

Run: `uv run pytest tests/unit/test_http_policy_adoption.py -q`

Expected: FAIL.

- [ ] **Step 3: Add the normative v1 recipe and adoption manifest.**

Define normalized exact origins, request-hook validation on every redirect hop, `max_redirects <= 5`, decoded-byte streaming caps, fixed host-free errors, and non-retryability. The manifest records each repo's conformance-file SHA-256.

- [ ] **Step 4: Verify and commit.**

Run: `uv run pytest tests/unit/test_http_policy_adoption.py -q`

Expected: PASS.

```bash
git add docs/HTTP-POLICY-STANDARD-v1.md ci/http-policy-v1.json tests/unit/test_http_policy_adoption.py Makefile .github/workflows/ci.yml
git commit -m "feat: publish fleet HTTP policy v1 adoption gate"
```

### Task 3: Adopt HTTP-policy v1 in eight repositories (R-05)

**Files per repository:**
- Modify: `<package>/api/url_guard.py`, configured API client, existing redirect/cap test
- Create: `tests/conformance/test_http_policy_v1.py`

- [ ] **Step 1: Vendor the same failing conformance case into every affected backend.**

```python
@pytest.mark.parametrize("url", [
    "http://allowed.example/path",
    "https://user:pass@allowed.example/path",
    "https://allowed.example:8443/path",
])
def test_policy_rejects_disallowed_origin(url: str) -> None:
    with pytest.raises(OutboundPolicyError, match="outbound request rejected by policy"):
        validate_request_url(httpx.URL(url), {AllowedOrigin("allowed.example", 443)})
```

Also cover omitted/explicit `:443`, cross-host redirect, redirect loop, decoded limit+1, compressed decoded overflow, and error/envelope redaction.

- [ ] **Step 2: Run the conformance test in each repository and verify the current host-only behavior fails.**

Run: `uv run pytest tests/conformance/test_http_policy_v1.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement the v1 guard with each backend's configured origin/cap semantics.**

```python
@dataclass(frozen=True)
class AllowedOrigin:
    hostname: str
    port: int = 443

def validate_request_url(url: httpx.URL, allowed: frozenset[AllowedOrigin]) -> None:
    if url.scheme != "https" or url.user or url.host is None:
        raise OutboundPolicyError("outbound request rejected by policy")
    if AllowedOrigin(url.host.lower(), url.port or 443) not in allowed:
        raise OutboundPolicyError("outbound request rejected by policy")
```

Keep `follow_redirects=True`, streaming decoded-byte accounting, non-retryable policy errors, and each service's documented POST/pagination/retry behavior.

- [ ] **Step 4: Verify and commit each repository.**

Run: `uv run pytest tests/conformance/test_http_policy_v1.py <existing-redirect-cap-test> -q && make ci-local`

Expected: PASS.

```bash
git add <package>/api/url_guard.py <client-and-tests> tests/conformance/test_http_policy_v1.py
git commit -m "fix: adopt HTTP policy v1"
```

### Task 4: GeneReviews, UniProt, and MaveDB behavioral hardening

**Files:**
- GeneReviews: `genereview_link/download_guard.py`, `genereview_link/corpus/parallel.py`, callers, `tests/unit/test_corpus_ingest_ceilings.py`
- UniProt: `uniprot_link/services/queries/validation.py`, `services/sparql_service.py`, `api/client.py`, `tests/unit/test_queries.py`, `tests/unit/test_client.py`
- MaveDB: `mavedb_link/services/{resolvers,variant_lookup}.py`, resolver and lookup tests

- [ ] **Step 1: Write the failing adversarial behavior tests.**

```python
def test_ignored_regular_member_consumes_cumulative_archive_budget(): ...
def test_slow_drip_expires_monotonic_total_deadline(): ...
def test_graph_and_service_query_forms_are_rejected_before_http(): ...
def test_valid_hgvs_never_appears_in_not_found_response_or_logs(caplog): ...
```

- [ ] **Step 2: Verify each test fails for the reported residual behavior.**

Run: `uv run pytest tests/unit/test_corpus_ingest_ceilings.py -q`; `uv run pytest tests/unit/test_queries.py tests/unit/test_client.py -q`; `uv run pytest tests/unit/test_resolvers_hgvs.py tests/unit/test_variant_lookup.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement only the failing behavior.**

Use `time.monotonic()` for GeneReviews total deadlines and account every regular tar member before suffix filtering. Reject UniProt graph forms and real `SERVICE` tokens outside comments/literals/IRIs, then wrap whole request/retry execution with `asyncio.timeout`. Replace MaveDB interpolated HGVS messages with fixed texts while preserving typed error code/retryability.

- [ ] **Step 4: Verify and commit independently.**

Run: focused tests above, then `make ci-local` in each of the three repositories.

Expected: PASS.

```bash
git add genereview_link tests && git commit -m "fix: bound corpus ingest work"
git add uniprot_link tests docs && git commit -m "fix: bound SPARQL execution policy"
git add mavedb_link tests && git commit -m "fix: redact resolution identifiers"
```

### Task 5: Action pinning and CI isolation

**Files:**
- GeneReviews/Orphanet: `.github/**/*.yml`, recursive pin-check tests; Orphanet `.github/dependabot.yml`
- MGI: `tests/conftest.py`, `tests/unit/test_cli.py`, integration bulk-download test
- UniProt: `tests/conftest.py`, `tests/unit/mcp/test_log_filters.py`

- [ ] **Step 1: Write the failing pin and isolation tests.**

```python
def test_all_external_uses_are_full_commit_shas():
    for uses in external_uses(Path(".github")):
        assert re.search(r"@[0-9a-f]{40}(?:\\s|$)", uses), uses

def test_unit_network_access_raises_network_disabled(): ...
def test_log_filter_fixture_restores_global_logger_state(): ...
```

- [ ] **Step 2: Run the tests and observe the mutable-tag, live-network, and logger-state failures.**

Run: targeted pytest files in GeneReviews, Orphanet, MGI, and UniProt.

Expected: FAIL.

- [ ] **Step 3: Implement the narrow repairs.**

Replace external Action refs with audited SHA pins plus version comments; add recursive scan and Orphanet GitHub-Actions Dependabot. Patch MGI at the `builder.rebuild` dependency lookup and install a unit-only network deny fixture. Snapshot/restore UniProt logger filters, handlers, level, and propagation; retain the strict fixed-message assertion.

- [ ] **Step 4: Verify and commit.**

Run: `PYTEST_XDIST_AUTO_NUM_WORKERS=2 uv run pytest -n auto tests/unit/mcp/test_log_filters.py -q` in UniProt; focused tests in the other repositories; then `make ci-local` in GeneReviews, Orphanet, MGI, and UniProt.

Expected: PASS.

```bash
git add .github tests && git commit -m "ci: pin third-party actions by commit"
git add tests mgi_link && git commit -m "test: isolate refresh from network"
git add tests uniprot_link && git commit -m "test: restore global log filter state"
```

### Task 6: Completion audit

**Files:**
- Create: `docs/superpowers/plans/2026-07-12-residual-fleet-remediation-ledger.md`

- [ ] **Step 1: Create an R-01 through R-08 acceptance matrix.**

For each issue acceptance bullet, record the repository, exact test, verification command, commit SHA, and fresh exit code. Do not mark an item complete from a source diff alone.

- [ ] **Step 2: Run all required CI and fleet-adoption checks.**

Run: `make ci-local` in router and every affected backend; then `uv run pytest tests/unit/test_http_policy_adoption.py -q` in router.

Expected: every command exits 0.

- [ ] **Step 3: Inspect final diffs, complete the ledger, and commit it.**

```bash
git add docs/superpowers/plans/2026-07-12-residual-fleet-remediation-ledger.md ci/http-policy-v1.json
git commit -m "docs: record residual fleet remediation verification"
```
