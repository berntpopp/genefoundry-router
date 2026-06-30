# uniprot-link: Finish the QLever EXISTS Remediation + Restore a Live-Integration Safety Net Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal** Remove the last `FILTER EXISTS` from the SPARQL query builders (the QLever endpoint rejects EXISTS in expression position with HTTP 400) so the `search_example_queries` text-search path works live, and restore a scheduled CI safety net that runs the live integration tests off the PR critical path.

**Architecture** `uniprot-link` is a thin FastMCP backend over the public UniProt QLever SPARQL endpoint; query strings are built by pure functions in `uniprot_link/services/queries/` and verified by unit tests (mocked client) plus integration tests (live endpoint). The one remaining unsupported construct lives in the example-catalog keyword-filter builder; the fix swaps `FILTER EXISTS { … }` for a post-grouping `HAVING` over `GROUP_CONCAT` (the proven QLever-safe idiom already used elsewhere), and the safety net is a `schedule` + `workflow_dispatch` GitHub Actions job that runs `make test-integration`.

**Tech Stack** Python 3.12, `uv`, `pytest` + `pytest-asyncio`, `ruff`, `mypy`, FastMCP 3.x, SPARQL (QLever engine at `https://sparql.uniprot.org/sparql`), GitHub Actions.

## Global Constraints

Python 3.12+ with uv (uv sync --group dev, uv run); modern typing (X|None, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (scripts/check_file_size.py via make lint-loc); 'make ci-local' must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

| Path | Change | Responsibility |
| --- | --- | --- |
| `uniprot_link/services/queries/examples.py` | Modify | Replace the `FILTER EXISTS` keyword-filter clause in `search_example_queries` with a `HAVING` over `GROUP_CONCAT` (QLever-safe). |
| `tests/unit/test_queries.py` | Modify | Add a red-first unit test asserting the text path emits no `EXISTS` and builds a `HAVING`; tighten the existing multiword test to the new shape. |
| `tests/integration/test_live.py` | Modify | Add a live regression that the text path round-trips on QLever (no HTTP 400) **and** actually filters (real token → hits, nonsense token → 0). |
| `.github/workflows/integration.yml` | Create | Scheduled (cron) + manual-dispatch job that runs `make test-integration` off the PR critical path. |

---

### Task 1: Replace `FILTER EXISTS` with `HAVING` over `GROUP_CONCAT` in `search_example_queries`

**Files**
- Modify: `uniprot_link/services/queries/examples.py:10-41` (the `search_example_queries` builder; the offending clause is `examples.py:18`).
- Test: `tests/unit/test_queries.py` (class `TestExampleQueries`, currently `tests/unit/test_queries.py:251-264`).

**Interfaces**
- Consumes: `search_example_queries(text: str | None = None, limit: int = 25) -> str` (unchanged signature).
- Produces: a SPARQL string whose optional text filter uses `HAVING(CONTAINS(LCASE(GROUP_CONCAT(?comment; separator=" ")), …) || CONTAINS(LCASE(GROUP_CONCAT(?kw; separator=" ")), …))` and contains **no** `EXISTS`. The outer SELECT columns (`?ex ?desc ?qtype ?keywords`) are unchanged, so `shape_example_list` (`uniprot_link/services/shaping.py:476`) needs no change.

Steps:

- [ ] (1) Write the failing test. Append to class `TestExampleQueries` in `tests/unit/test_queries.py`, and rewrite the existing `test_search_examples_multiword_builds_or_filter`:

```python
    def test_search_examples_text_path_avoids_filter_exists(self) -> None:
        """QLever rejects EXISTS in expression position (HTTP 400); the text path
        must filter via HAVING over GROUP_CONCAT, never FILTER EXISTS."""
        query = q.search_example_queries("disease cancer")
        assert "EXISTS" not in query
        assert "HAVING(" in query
        # Each token is matched against BOTH the comment- and keyword-concat.
        assert 'LCASE("disease")' in query
        assert 'LCASE("cancer")' in query

    def test_search_examples_multiword_builds_having_over_concat(self) -> None:
        """F: multiword text builds one CONTAINS per token per field inside a
        HAVING over GROUP_CONCAT (never a FILTER EXISTS -> QLever HTTP 400)."""
        query = q.search_example_queries("protein domain architecture")
        assert "EXISTS" not in query
        assert "HAVING(" in query
        # 3 tokens x 2 fields (comment-concat + keyword-concat) = 6 CONTAINS.
        assert query.count("CONTAINS(LCASE(GROUP_CONCAT(") == 6

    def test_search_examples_no_text_omits_having(self) -> None:
        """The no-text path is unchanged: no HAVING, no EXISTS, plain GROUP BY."""
        query = q.search_example_queries(None)
        assert "HAVING" not in query
        assert "EXISTS" not in query
        assert "GROUP BY ?ex" in query
```

  Then delete the old `test_search_examples_multiword_builds_or_filter` (`tests/unit/test_queries.py:261-264`) — it is replaced by `test_search_examples_multiword_builds_having_over_concat`.

- [ ] (2) Run it, expect FAIL. Command:

```bash
cd /home/bernt-popp/development/uniprot-link && uv run pytest tests/unit/test_queries.py::TestExampleQueries -q
```

  Expected: `test_search_examples_text_path_avoids_filter_exists` and `test_search_examples_multiword_builds_having_over_concat` FAIL — the current builder emits `EXISTS {{ ?ex schema:keywords ?k2 … }}` (`examples.py:18`) and no `HAVING`, so `assert "EXISTS" not in query` and `assert "HAVING(" in query` fail.

- [ ] (3) Minimal implementation. Replace `search_example_queries` (`uniprot_link/services/queries/examples.py:10-41`) entirely with:

```python
def search_example_queries(text: str | None = None, limit: int = 25) -> str:
    """Build a SELECT over the curated example catalog (optional text filter).

    The optional text filter matches each whitespace token against the example's
    comment text OR its keywords. Both are multi-valued per example, so the match
    is applied AFTER grouping, via a ``HAVING`` over the ``GROUP_CONCAT`` of
    comments and keywords. EXISTS is deliberately avoided: the QLever endpoint
    (``constants.py``) rejects EXISTS in expression position (BIND/FILTER) with
    HTTP 400 -- the same constraint proteins.py works around with a
    BOUND-over-OPTIONAL sub-SELECT (see proteins.py:198). Ref:
    https://github.com/ad-freiburg/qlever/wiki/Current-deviations-from-the-SPARQL-1.1-standard
    """
    having = ""
    if text:
        tokens = [escape_literal(t) for t in text.strip().split() if t][:6]
        if tokens:
            clauses = " || ".join(
                f'CONTAINS(LCASE(GROUP_CONCAT(?comment; separator=" ")), LCASE("{t}")) || '
                f'CONTAINS(LCASE(GROUP_CONCAT(?kw; separator=" ")), LCASE("{t}"))'
                for t in tokens
            )
            having = f"HAVING({clauses})\n"
    # GROUP BY ?ex only (Bug 12): an example can carry >1 rdfs:comment AND >1
    # matching rdf:type, which previously produced duplicate rows. ?comment and
    # ?type are collapsed with SAMPLE under distinct aliases (?desc/?qtype -- the
    # SPARQL alias must not reuse an in-scope variable). UniProt-native vs
    # federated ranking is decided in shaping from the example IRI host.
    return f"""{prefix_block()}
SELECT ?ex (SAMPLE(?comment) AS ?desc) (SAMPLE(?type) AS ?qtype)
       (GROUP_CONCAT(DISTINCT ?kw; separator=", ") AS ?keywords)
WHERE {{
  GRAPH <{SPARQL_EXAMPLES_GRAPH}> {{
    ?ex a sh:SPARQLExecutable ; rdfs:comment ?comment .
    OPTIONAL {{ ?ex schema:keywords ?kw }}
    OPTIONAL {{ ?ex a ?type .
               FILTER(?type IN (sh:SPARQLSelectExecutable, sh:SPARQLAskExecutable,
                                sh:SPARQLConstructExecutable)) }}
  }}
}}
GROUP BY ?ex
{having}ORDER BY ?ex
LIMIT {limit}"""
```

  Notes for the implementer: (a) the old per-row `text_filter` injected inside the `WHERE` (`{text_filter}  }}` at `examples.py:37`) is gone — the keyword test is now post-grouping, which is required because keywords are multi-valued; (b) `{having}` carries its own trailing `\n`, so the empty (no-text) case renders `GROUP BY ?ex\nORDER BY ?ex` byte-for-byte as today; (c) do **not** change the outer SELECT projection or `shape_example_list` — columns are identical.

- [ ] (4) Run it, expect PASS. Commands:

```bash
cd /home/bernt-popp/development/uniprot-link && uv run pytest tests/unit/test_queries.py::TestExampleQueries -q
cd /home/bernt-popp/development/uniprot-link && uv run ruff format uniprot_link tests && uv run ruff check uniprot_link tests && uv run mypy uniprot_link
```

  Expected: all `TestExampleQueries` tests PASS; ruff/mypy clean. Sanity-check no other test references the removed clause: `grep -rn "EXISTS" uniprot_link tests` should return only proteins.py comments and the new assertions (`assert "EXISTS" not in query`).

- [ ] (5) Commit:

```bash
git commit -am "fix(queries): replace FILTER EXISTS with HAVING/GROUP_CONCAT in example search (QLever-400)"
```

---

### Task 2: Live regression — the text path round-trips on QLever AND actually filters

**Files**
- Modify: `tests/integration/test_live.py` (add a test next to the existing `test_multiword_example_search_returns_hits` at `tests/integration/test_live.py:75-77`).

**Interfaces**
- Consumes: `SparqlService.search_examples(text: str | None = None, limit: int = 25) -> dict[str, Any]` (`uniprot_link/services/sparql_service.py:435`), which returns `{"count": int, "query_text": ..., "examples": [...]}`.
- Produces: a `pytest.mark.integration` async test asserting a real token returns hits and a nonsense token returns none. A surviving `FILTER EXISTS` would make the SPARQL request raise (HTTP 400 → `QuerySyntaxError`/`UpstreamError`), so this test fails loudly on the original bug; the `== 0` assertion guards against the filter being silently dropped.

Steps:

- [ ] (1) Write the test. Insert into `tests/integration/test_live.py` immediately after `test_multiword_example_search_returns_hits` (after line 77):

```python
async def test_example_search_text_filter_is_applied_live(service: SparqlService) -> None:
    """The text path must round-trip on QLever (no EXISTS HTTP 400) AND actually
    filter: a real token returns hits, a nonsense token returns none. A surviving
    FILTER EXISTS would raise here instead of returning a result set."""
    hits = await service.search_examples("disease", limit=25)
    assert hits["count"] > 0
    none = await service.search_examples("zzqqxxnotaword", limit=25)
    assert none["count"] == 0
```

- [ ] (2) Run it against the live endpoint, expect PASS (this confirms the Task 1 fix is QLever-valid). Command:

```bash
cd /home/bernt-popp/development/uniprot-link && uv run pytest "tests/integration/test_live.py::test_example_search_text_filter_is_applied_live" "tests/integration/test_live.py::test_multiword_example_search_returns_hits" "tests/integration/test_live.py::test_example_catalog" "tests/integration/test_live.py::test_example_search_has_no_duplicate_ids_live" -q
```

  Expected: PASS — `hits["count"] > 0` for `"disease"` and `none["count"] == 0` for the nonsense token. If this raises an upstream/4xx error, the Task 1 query shape is still QLever-invalid → switch to the sub-SELECT fallback in **Risk & rollback** before proceeding. (Requires network access to `https://sparql.uniprot.org/sparql`; if the runner is offline, defer this verification to the Task 3 scheduled job.)

- [ ] (3) No implementation code — this task is the live contract for Task 1. (If step 2 surfaced a HAVING-over-aggregate rejection, apply the fallback from Risk & rollback, re-run Task 1 unit tests, then re-run this step.)

- [ ] (4) Confirm the marker keeps it off the default unit path:

```bash
cd /home/bernt-popp/development/uniprot-link && uv run pytest tests -q -m "not integration" -k "example" && echo "DESELECTED-FROM-UNIT-PATH-OK"
```

  Expected: the live test does NOT run under `-m "not integration"` (it is collected only by `make test-integration`).

- [ ] (5) Commit:

```bash
git commit -am "test(integration): assert example text-search filters live on QLever (regression net)"
```

---

### Task 3: Scheduled + manual-dispatch workflow running `make test-integration`

**Files**
- Create: `.github/workflows/integration.yml` (new; mirrors the pinned-action style of `.github/workflows/ci.yml:23-38` and `.github/workflows/conformance.yml:21-22`).

**Interfaces**
- Consumes: `make test-integration` → `uv run pytest tests -q -m "integration"` (`Makefile:58-59`); the live tests hit the default endpoint `https://sparql.uniprot.org/sparql` (`uniprot_link/config.py:21`) directly — no Docker, no secrets.
- Produces: a GitHub Actions workflow that runs on a daily cron and on manual dispatch, with **no** `pull_request`/`push` trigger, so query-builder regressions are caught without blocking merges on public-endpoint network flakiness.

Steps:

- [ ] (1) Write the failing check first — assert the workflow exists and is off the PR critical path:

```bash
cd /home/bernt-popp/development/uniprot-link && \
  test -f .github/workflows/integration.yml && \
  ! grep -qE '^\s*(pull_request|push)\s*:' .github/workflows/integration.yml && \
  grep -q 'schedule:' .github/workflows/integration.yml && \
  grep -q 'workflow_dispatch:' .github/workflows/integration.yml && \
  grep -q 'make test-integration' .github/workflows/integration.yml && \
  echo PASS || echo FAIL
```

- [ ] (2) Run it, expect FAIL. The file does not exist yet, so the command prints `FAIL`.

- [ ] (3) Create `.github/workflows/integration.yml`:

```yaml
name: live-integration

# Live query-shape regression net for the SPARQL query builders. Runs OFF the
# PR critical path (scheduled + manual only) so a builder regression -- e.g. a
# QLever-rejected construct like FILTER EXISTS -- is caught without blocking
# merges on public-endpoint network flakiness. Hits https://sparql.uniprot.org.
on:
  schedule:
    - cron: "17 6 * * *" # daily 06:17 UTC
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: live-integration-${{ github.ref }}
  cancel-in-progress: true

jobs:
  integration:
    name: make test-integration (live UniProt SPARQL endpoint)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Checkout
        uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0

      - name: Set up Python
        uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6
        with:
          python-version: "3.12"

      - name: Set up uv
        uses: astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39 # v8.2.0
        with:
          enable-cache: true
          version: "0.8.7"

      - name: Install dependencies
        run: uv sync --group dev --frozen

      - name: Run live integration tests
        run: make test-integration
```

- [ ] (4) Run the check again, expect PASS, plus YAML lint:

```bash
cd /home/bernt-popp/development/uniprot-link && \
  test -f .github/workflows/integration.yml && \
  ! grep -qE '^\s*(pull_request|push)\s*:' .github/workflows/integration.yml && \
  grep -q 'schedule:' .github/workflows/integration.yml && \
  grep -q 'workflow_dispatch:' .github/workflows/integration.yml && \
  grep -q 'make test-integration' .github/workflows/integration.yml && \
  echo PASS || echo FAIL
cd /home/bernt-popp/development/uniprot-link && uv run python -c "import sys,yaml; yaml.safe_load(open('.github/workflows/integration.yml')); print('YAML-OK')"
```

  Expected: `PASS` then `YAML-OK`. (`yaml` ships with the dev env via the test deps; if absent, use `python -c "import yaml"` after `uv sync` or skip — GitHub validates on push.)

- [ ] (5) Commit:

```bash
git commit -am "ci: add scheduled + manual live-integration workflow (off PR critical path)"
```

---

**Acceptance criteria**

- `grep -rn "EXISTS" /home/bernt-popp/development/uniprot-link/uniprot_link/services/queries/examples.py` returns nothing (the only remaining `EXISTS` matches in the repo are the explanatory comments in `proteins.py`).
- `cd /home/bernt-popp/development/uniprot-link && uv run pytest tests/unit/test_queries.py::TestExampleQueries -q` passes, including `test_search_examples_text_path_avoids_filter_exists` and `test_search_examples_multiword_builds_having_over_concat`.
- `cd /home/bernt-popp/development/uniprot-link && make test-integration` passes (notably `test_example_search_text_filter_is_applied_live`, `test_multiword_example_search_returns_hits`, `test_example_catalog`, `test_example_search_has_no_duplicate_ids_live`) — proving the text path round-trips on live QLever.
- `cd /home/bernt-popp/development/uniprot-link && make ci-local` passes (format-check, lint, lint-loc, mypy, unit + fast tests).
- `.github/workflows/integration.yml` exists, has `schedule` + `workflow_dispatch` triggers, has **no** `pull_request`/`push` trigger, and runs `make test-integration`.

**Risk & rollback**

- **Not EXECUTION-GATED.** All steps end in local atomic commits; no `git push`, no redeploy, no destructive remote operation is instructed. The router/backends are unaffected (this is a query-builder + CI change only).
- **Primary risk — HAVING-over-aggregate rejected by the endpoint's QLever build.** The QLever wiki lists HAVING-with-aggregates and `CONTAINS` as supported (https://github.com/ad-freiburg/qlever/wiki/Current-deviations-from-the-SPARQL-1.1-standard), and the no-text query already runs `GROUP_CONCAT` against this endpoint, so risk is low — but the live UniProt deployment may lag. Task 2 step (2) is the gate that catches this. **Fallback (proven-constructs-only):** wrap the aggregation in a sub-SELECT and apply a plain outer `FILTER` over the projected `GROUP_CONCAT` strings (sub-SELECT + plain `FILTER` + `CONTAINS` are all already verified live in `proteins.py`):

  ```sparql
  SELECT ?ex ?desc ?qtype ?keywords
  WHERE {
    { SELECT ?ex (SAMPLE(?comment) AS ?desc) (SAMPLE(?type) AS ?qtype)
             (GROUP_CONCAT(DISTINCT ?kw; separator=", ") AS ?keywords)
             (GROUP_CONCAT(DISTINCT ?comment; separator=" ") AS ?haystack)
      WHERE { GRAPH <…> {
        ?ex a sh:SPARQLExecutable ; rdfs:comment ?comment .
        OPTIONAL { ?ex schema:keywords ?kw }
        OPTIONAL { ?ex a ?type . FILTER(?type IN (…)) } } }
      GROUP BY ?ex }
    FILTER( CONTAINS(LCASE(?haystack), LCASE("t1")) || CONTAINS(LCASE(?keywords), LCASE("t1")) || … )
  }
  ORDER BY ?ex
  LIMIT {limit}
  ```

  Re-run Task 1 unit tests (adjust the structural asserts to the sub-SELECT shape) and Task 2 live test after switching.
- **Scheduled-workflow activation.** GitHub only fires `schedule` triggers (and shows `workflow_dispatch`) once the workflow file is on the **default branch (`main`)**. The job will not run from a feature branch; this is expected and not a regression. A transient endpoint outage will redden a scheduled run (a notification) but cannot block any PR, satisfying the "without blocking merges on network flakiness" requirement.
- **Rollback:** `git revert` the three commits; the EXISTS-based builder and the absence of the scheduled job are restored with no data or deployment impact.

**Effort** Small — one ~20-line builder rewrite, ~3 unit-test edits, one live-test addition, one ~40-line workflow file. Roughly half a day including a live `make test-integration` verification pass; add ~1 hour if the HAVING fallback is needed.
