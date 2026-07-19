# P0 Contract Truth and Surface Budget Implementation Plan

> Historical record — this plan records the approved 2026-07-18 implementation sequence. Current
> behavior is defined by implemented controls, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centrally enforce truthful client-facing tool-call documentation and eliminate the three remaining fleet tool-surface budget failures before making `lint-surface` part of local CI.

**Architecture:** A pure, byte-pinned `docs/conformance/contract_truth.py` receives a repository root plus the backend's live FastMCP tool catalog. It discovers eligible Markdown by policy rather than a hand-maintained file list and reports deterministic file/line findings. Backends vendor the helper and own their FastMCP-specific catalog construction. The router dogfoods it, then the fleet updates the three failing tool surfaces and refreshes the reviewed baseline before the existing offline surface gate joins `ci-local`.

**Tech Stack:** Python 3.12 standard library, FastMCP 3.x live tool registry, pytest, `hashlib`, `pathlib`, `re`, `uv`, Ruff, mypy, Make.

---

## File structure

### Router repository

- Create: `docs/conformance/contract_truth.py` — canonical pure parser/linter and CLI.
- Create: `docs/conformance/contract_truth.sha256` — SHA-256 pin for the exact canonical bytes.
- Create: `tests/conformance/test_contract_truth.py` — parser, discovery, and finding tests.
- Create: `tests/conformance/test_contract_truth_router.py` — router dogfood test against its live registry and documentation root.
- Modify: `tests/unit/test_makefile_targets.py` — require `lint-surface` in `ci-local` only after the baseline is green.
- Modify: `Makefile` — append `lint-surface` to `ci-local` and remove the now-obsolete exclusion comment.
- Modify: `docs/specs/**/*.md`, `docs/plans/**/*.md`, and `docs/superpowers/**/*.md` only where a dated historical file lacks the required marker; do not rewrite historical content.
- Modify: `genefoundry_router/data/fleet-baseline.json`, `ci/fleet-application-releases.json`, and `ci/release-candidate-inventory.json` through the existing reviewed capture process after backend changes.

### Backend adoption files

- Create in every `*-link` repository: `tests/conformance/contract_truth.py` — byte-identical copy of the router source.
- Create in every `*-link` repository: `tests/conformance/contract_truth.sha256` — byte-identical pin.
- Create in every `*-link` repository: `tests/conformance/test_contract_truth_v1.py` — local live-registry adapter plus hash and lint assertion.
- Modify each backend's CI invocation only when its existing test command does not already collect `tests/conformance/`.

### Budget-remediation files

- Modify: `/home/bernt-popp/development/pubtator-link/pubtator_link/mcp/` tool registration modules and their focused `tests/unit/mcp/` surface tests.
- Modify: `/home/bernt-popp/development/gnomad-link/gnomad_link/mcp/tools/` registrations and `tests/unit/mcp/` surface tests.
- Modify: `/home/bernt-popp/development/genereviews-link/genereview_link/server_manager.py` and `tests/test_mcp_search_passages_params.py` / `tests/test_tool_schema_descriptions.py`.

## Exact contract policy

1. **Active documents:** root `README.md`, root `CHANGELOG.md`, and every `docs/**/*.md` except files below `docs/specs/`, `docs/plans/`, `docs/superpowers/`, and `docs/reviews/`.
2. **Documented call arguments:** only expressions in the form `tool_name(keyword=value[, next_keyword=value])` whose callee exactly matches a live tool name are checked. A non-tool function call is ignored. The check covers keyword names only; it intentionally does not validate values, types, requiredness, positional arguments, multiline syntax, or JSON-form examples.
3. **Universal response claims:** reject an unqualified same-clause claim that an MCP response/envelope is universal, such as `every response includes` or `all tools return`. Ignore a negated claim (`not every response`) and an explicitly qualified exception (`all tools except the explicitly named tool`). Permit only the exact canonical research-use disclaimer in a fixture-tested allowlist.
4. **Historical records:** a dated `YYYY-MM-DD-*.md` file below `docs/specs/`, `docs/plans/`, or `docs/superpowers/` must have, as its first non-title/non-metadata prose block, a blockquote whose first trimmed line is `> Historical record` followed only by end-of-line, whitespace, or an em dash explanation.
5. **Pin:** each backend's `contract_truth.sha256` must equal `sha256sum docs/conformance/contract_truth.py` from the router. A source change updates the helper and pin in the same router commit; an adopter copies both exactly.

### Task 1: Implement the canonical parser with fixture-driven tests

**Files:**

- Create: `tests/conformance/test_contract_truth.py`
- Create: `docs/conformance/contract_truth.py`
- Create: `docs/conformance/contract_truth.sha256`

- [ ] **Step 1: Write failing pure-function tests**

Create `tests/conformance/test_contract_truth.py` with a temporary repository tree and a tiny live
catalog:

```python
from __future__ import annotations

from pathlib import Path

from docs.conformance.contract_truth import Finding, lint_repository


CATALOG = {
    "search_genes": {"inputSchema": {"properties": {"query": {}, "limit": {}}}},
    "get_gene": {"inputSchema": {"properties": {"symbol": {}}}},
}


def test_lint_reports_an_unknown_keyword_with_file_line_and_tool(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("Use `search_genes(query=\"BRCA1\", page=1)`.\n")

    findings = lint_repository(tmp_path, CATALOG)

    assert findings == [
        Finding(
            path=Path("README.md"),
            line=1,
            rule="unknown-argument",
            message="search_genes.page is absent from the live inputSchema.properties",
        )
    ]


def test_non_tool_call_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("requests.get(timeout=5)\n")

    assert lint_repository(tmp_path, CATALOG) == []
```

Add tests for a known keyword, nested active documentation discovery, a docs/specs dated file with
and without the marker, the router's accepted `> Historical record — historical context` form, universal
response claims, negated claims, `except` claims, the exact allowlisted disclaimer, and an
unallowlisted `all tools return` claim. Add a test that an unknown call name is ignored rather than
misclassified as an invalid MCP argument.

- [ ] **Step 2: Run the parser tests to verify they fail**

Run:

```bash
uv run pytest tests/conformance/test_contract_truth.py -q
```

Expected: FAIL because the canonical helper does not exist.

- [ ] **Step 3: Implement the pure helper and stable CLI**

Create `docs/conformance/contract_truth.py` with no project imports. Define the `Finding` model:

```python
@dataclass(frozen=True, order=True)
class Finding:
    path: Path
    line: int
    rule: Literal["unknown-argument", "universal-response-claim", "historical-record-marker"]
    message: str
```

Export `active_markdown_files(root: Path) -> list[Path]`,
`historical_markdown_files(root: Path) -> list[Path]`,
`lint_repository(root: Path, catalog: Mapping[str, Mapping[str, object]]) -> list[Finding]`, and
`main(argv: Sequence[str] | None = None) -> int`. Implement `active_markdown_files()` with sorted
glob discovery and the exact root exclusions above. Implement a line-preserving call-expression
scan that only validates a callee found in `catalog`; obtain allowed names only from
`catalog[tool]["inputSchema"]["properties"]`. Implement universal
claim matching at sentence/clause scope, with the two qualification exclusions and an equality
allowlist of canonical disclaimer text. Implement historical checking before active linting; parse
the first prose block after optional YAML front matter, H1, and `**Date:**`/`**Status:**` metadata.
Return findings sorted by `(path.as_posix(), line, rule, message)`. The CLI accepts `--root` and
`--catalog`, prints one `path:line: rule: message` line per finding, and returns 1 iff findings
exist.

Calculate the pin from the exact helper bytes and write it as one lowercase 64-hex line plus a
newline in `docs/conformance/contract_truth.sha256`:

```bash
sha256sum docs/conformance/contract_truth.py | cut -d ' ' -f1
```

- [ ] **Step 4: Run tests and source quality checks**

Run:

```bash
uv run pytest tests/conformance/test_contract_truth.py -q
uv run ruff check docs/conformance tests/conformance
```

Expected: PASS. The test output must make a documented argument typo actionable without falsely
failing normal Python/HTTP examples.

- [ ] **Step 5: Commit the canonical contract helper**

```bash
git add docs/conformance/contract_truth.py docs/conformance/contract_truth.sha256 \
  tests/conformance/test_contract_truth.py
git commit -m "feat(conformance): add contract truth v1"
```

### Task 2: Dogfood the helper and fence historical router records

**Files:**

- Create: `tests/conformance/test_contract_truth_router.py`
- Modify: dated files under `docs/specs/`, `docs/plans/`, and `docs/superpowers/` that the new test reports

- [ ] **Step 1: Write the router live-catalog test**

Create `tests/conformance/test_contract_truth_router.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from docs.conformance.contract_truth import lint_repository
from genefoundry_router.server import create_server


@pytest.mark.asyncio
async def test_router_active_documentation_matches_its_live_tool_catalog() -> None:
    server = create_server()
    tools = await server.list_tools()
    catalog = {
        tool.name: {"inputSchema": tool.input_schema or {"properties": {}}}
        for tool in tools
    }

    assert lint_repository(Path("."), catalog) == []
```

If `create_server()` requires configuration injection, use the existing server fixture from
`tests/integration/test_server.py`; do not construct a hand-maintained tool catalog.

- [ ] **Step 2: Run the dogfood test to verify it fails**

Run:

```bash
uv run pytest tests/conformance/test_contract_truth_router.py -q
```

Expected: FAIL with the exact file/line findings for current missing historical markers or current
client-facing documentation mistakes.

- [ ] **Step 3: Make reported historical status explicit and correct active docs**

For each reported dated path below the three historical roots, add a first prose-block marker in
this exact form without changing the historical body:

```markdown
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.
```

For a reported active document, replace only the invalid keyword or universal response-shape claim
with a schema-true bounded sentence. Do not add a per-file suppression, a hardcoded document list,
or an allowlist entry for a product capability claim.

- [ ] **Step 4: Run dogfood and full conformance tests**

Run:

```bash
uv run pytest tests/conformance/test_contract_truth.py tests/conformance/test_contract_truth_router.py -q
```

Expected: PASS. The router is subject to the same helper and pin semantics it distributes.

- [ ] **Step 5: Commit router dogfooding**

```bash
git add docs tests/conformance/test_contract_truth_router.py
git commit -m "test(conformance): dogfood contract truth v1"
```

### Task 3: Vendor the exact helper and live-registry test into the fleet

**Files:**

- Create in every backend: `tests/conformance/contract_truth.py`
- Create in every backend: `tests/conformance/contract_truth.sha256`
- Create in every backend: `tests/conformance/test_contract_truth_v1.py`

- [ ] **Step 1: Write a failing adopter test in each backend**

Each test begins by proving byte identity, then obtains the live tools from the backend's own
FastMCP factory. The shared lint never imports a backend module. For PubTator, create
`/home/bernt-popp/development/pubtator-link/tests/conformance/test_contract_truth_v1.py` using:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pubtator_link.mcp.facade import create_pubtator_mcp
from tests.conformance.contract_truth import lint_repository


@pytest.mark.asyncio
async def test_contract_truth_v1_matches_live_pubtator_tools() -> None:
    helper = Path("tests/conformance/contract_truth.py")
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == Path(
        "tests/conformance/contract_truth.sha256"
    ).read_text().strip()
    tools = await create_pubtator_mcp(profile="full").list_tools()
    catalog = {tool.name: {"inputSchema": tool.input_schema or {"properties": {}}} for tool in tools}

    assert lint_repository(Path("."), catalog) == []
```

For gnomAD, use `create_gnomad_mcp(service_factory=fake_service_factory)` and the existing fake service factory from
`tests/unit/mcp/test_mcp_facade_surface.py`; for GeneReviews, use
`await UnifiedServerManager().create_mcp_server(app, ServerConfig())` following
`tests/unit/test_readme_tools.py`. Each adapter must call `await mcp.list_tools()` and construct the
catalog from returned tool objects, never a copied names list.

- [ ] **Step 2: Run each new test to verify it fails before vendoring**

Run in each repository:

```bash
uv run pytest tests/conformance/test_contract_truth_v1.py -q
```

Expected: FAIL because the helper and hash pin are absent.

- [ ] **Step 3: Copy the source and pin byte-for-byte**

For every backend, copy exactly these router files without modification:

```bash
cp /home/bernt-popp/development/genefoundry-router/docs/conformance/contract_truth.py tests/conformance/contract_truth.py
cp /home/bernt-popp/development/genefoundry-router/docs/conformance/contract_truth.sha256 tests/conformance/contract_truth.sha256
```

Add the adapter test described above. If a repository's existing CI target omits
`tests/conformance`, add this exact command to its unit/conformance job:

```bash
uv run pytest tests/conformance/test_contract_truth_v1.py -q
```

- [ ] **Step 4: Verify every adoption with its native test suite**

For each backend, run the new test plus its existing local CI target. For the three budget owners:

```bash
cd /home/bernt-popp/development/pubtator-link && uv run pytest tests/conformance/test_contract_truth_v1.py -q
cd /home/bernt-popp/development/gnomad-link && uv run pytest tests/conformance/test_contract_truth_v1.py -q
cd /home/bernt-popp/development/genereviews-link && uv run pytest tests/conformance/test_contract_truth_v1.py -q
```

Expected: PASS with a live schema-derived catalog. A hash mismatch must fail before lint findings
are evaluated.

- [ ] **Step 5: Commit independently in each backend**

Use one atomic commit per repository:

```bash
git add tests/conformance/contract_truth.py tests/conformance/contract_truth.sha256 \
  tests/conformance/test_contract_truth_v1.py .github/workflows
git commit -m "test(conformance): enforce contract truth v1"
```

Do not combine backend adoption with surface-budget changes; each remains independently reviewable.

### Task 4: Eliminate the three budget failures without weakening documentation guarantees

**Files:**

- Modify: `/home/bernt-popp/development/pubtator-link/pubtator_link/mcp/` registration modules and `tests/unit/mcp/test_tool_surface_budget.py`
- Modify: `/home/bernt-popp/development/gnomad-link/gnomad_link/mcp/tools/` registrations and `tests/unit/mcp/test_mcp_facade_surface.py`
- Modify: `/home/bernt-popp/development/genereviews-link/genereview_link/server_manager.py`, `tests/test_mcp_search_passages_params.py`, and `tests/test_tool_schema_descriptions.py`

- [ ] **Step 1: Write exact budget assertions before changing tool prose**

In each owner repository, extend the existing live-FastMCP surface test to assert the measured
definition sizes. The limits are strict: each tool is at most 1,200 tokens and each server is at
most 10,000. In PubTator, assert the full profile remains schema-valid and that the review tools
`get_review_context_batch`, `build_topic_literature_map`, and `search_literature` retain their
required parameters/examples while their descriptions are concise. In gnomAD, assert the whole
live facade is at most 10,000 tokens and that `compute_gene_carrier_frequency` remains callable.
In GeneReviews, assert `search_passages.output_schema is None`, `q`/`query` compatibility remains,
and the complete definition is at most 1,200 tokens.

- [ ] **Step 2: Run the targeted failures and record the baseline numbers**

Run:

```bash
cd /home/bernt-popp/development/pubtator-link && uv run pytest tests/unit/mcp/test_tool_surface_budget.py -q
cd /home/bernt-popp/development/gnomad-link && uv run pytest tests/unit/mcp/test_mcp_facade_surface.py -q
cd /home/bernt-popp/development/genereviews-link && uv run pytest tests/test_mcp_search_passages_params.py tests/test_tool_schema_descriptions.py -q
```

Expected: FAIL on the pre-existing B2/B2/B1 measurements, not on output-schema presence. The
router audit already established outputSchema is not the remaining source of bloat.

- [ ] **Step 3: Reduce only redundant advertised prose and keep schema documentation**

For PubTator's three named high-cost review tools, replace duplicated workflow/tutorial paragraphs
in the tool descriptions with one concise `Use this when this tool is the next intended action.` sentence; retain every input property,
its non-empty description, required flag, and examples. Move long workflow narration to the existing
usage resource or README, which remains checked by contract truth. For gnomAD, shorten duplicated
dataset/population narration in the highest-cost registered tool descriptions, starting with
`compute_gene_carrier_frequency` and `get_gene_variants`, while keeping enum values and examples in
the input schema. For GeneReviews `search_passages`, keep its already-suppressed output schema and
its q/query behavior; shorten repeated mode/rerank/section vocabulary prose to concise descriptions
and retain the existing examples and enum validation. Do not delete tools, remove required parameter
documentation, or reintroduce `output_schema`.

- [ ] **Step 4: Run native tests and inspect the live tool measurements**

Run the focused tests above, then start each service's existing test fixture and use its actual
`await mcp.list_tools()` objects to print the measured tool tokens. Finally, refresh the router
candidate only from reviewed, published backend revisions:

```bash
cd /home/bernt-popp/development/genefoundry-router
make release-candidate
make snapshot-fleet
make lint-surface
```

Expected: `make lint-surface` prints `CONFORMANT` with zero B1/B2/S1/S2/S3 violations.

- [ ] **Step 5: Commit backend fixes, then reviewed router baseline**

In each backend:

```bash
git add pubtator_link tests
git commit -m "fix(mcp): fit PubTator review tools within surface budget"
```

In gnomAD:

```bash
cd /home/bernt-popp/development/gnomad-link
git add gnomad_link tests
git commit -m "fix(mcp): fit gnomAD tools within surface budget"
```

In GeneReviews:

```bash
cd /home/bernt-popp/development/genereviews-link
git add genereview_link tests
git commit -m "fix(mcp): fit GeneReviews search within surface budget"
```

After their reviewed releases are published, in the router repository:

```bash
git add ci/release-candidate-inventory.json ci/fleet-application-releases.json \
  genefoundry_router/data/fleet-baseline.json
git commit -m "chore(fleet): re-pin conformant tool surface baseline"
```

### Task 5: Make the now-green surface gate a required local check

**Files:**

- Modify: `tests/unit/test_makefile_targets.py`
- Modify: `Makefile:1-109`

- [ ] **Step 1: Write the failing Makefile regression test**

Add to `tests/unit/test_makefile_targets.py`:

```python
def test_ci_local_requires_the_green_surface_gate() -> None:
    text = Path("Makefile").read_text(encoding="utf-8")
    ci_local_target = text.split("ci-local:", 1)[1].split("##", 1)[0]

    assert "lint-surface" in ci_local_target
```

- [ ] **Step 2: Run the Makefile test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_makefile_targets.py::test_ci_local_requires_the_green_surface_gate -q
```

Expected: FAIL because `lint-surface` is deliberately absent today.

- [ ] **Step 3: Add the gate only after Task 4 is green**

Change the target to:

```make
ci-local: format-check lint-ci lint-loc lint-readme lint-metadata lint-server-json lint-actions typecheck http-policy-adoption lint-surface test-fast test-integration test-release ## Fast local CI-equivalent checks
```

Delete the adjacent comment that explains why the failing gate is omitted; it would be false once
the budget baseline has zero violations.

- [ ] **Step 4: Run the full mandatory handoff verification**

Run:

```bash
make lint-surface
make ci-local
```

Expected: both pass. `ci-local` now fails immediately if a later fleet snapshot exceeds the tool
budget or drops required schema documentation.

- [ ] **Step 5: Commit the CI gate**

```bash
git add Makefile tests/unit/test_makefile_targets.py
git commit -m "ci: require fleet tool surface conformance"
```
