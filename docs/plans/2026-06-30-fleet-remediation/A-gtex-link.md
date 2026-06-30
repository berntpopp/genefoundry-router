# GTEx-Link gtex_v10/GENCODE Fix Reconciliation & Version Drift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax.

**Goal:** Get the already-written-but-stranded dataset-aware GENCODE fix (local commit `8c48b7c`) onto `origin/main` with green CI and reconcile the `__init__.__version__` drift so `gtex_v10` median expression returns non-empty rows once redeployed.

**Architecture:** GTEx-Link is a FastMCP 3.x backend whose expression tools call `resolve_gene_ids` to turn symbols/IDs into the dataset's GENCODE-versioned IDs before querying the GTEx Portal v2 REST API; the bug was that resolution was hard-wired to gtex_v8/GENCODE v26, so gtex_v10 (GENCODE v39) queries used the wrong versioned ID and returned zero rows. The corrective code (dataset→GENCODE map + dataset-aware re-resolution + corrected `GencodeVersion` enum) already lives in unpushed local commit `8c48b7c` with passing tests; this plan adds the one residual change it missed (the stale `__version__`), hardens the regression assertion, and ships the whole thing via a PR. It is the gateway's job to namespace, not to patch backends — so all fixes land in the `gtex-link` source repo, never in router-side `transform` blocks.

**Tech Stack:** Python 3.12+, uv, FastMCP 3.x, FastAPI, Pydantic v2, httpx, respx (test HTTP mocking), pytest + pytest-asyncio + pytest-xdist, ruff, mypy, GitHub Actions.

## Global Constraints

Python 3.12+ with uv (`uv sync --group dev`, `uv run`); modern typing (`X|None`, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (`scripts/check_file_size.py` via `make lint-loc`); `make ci-local` must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-`Authorization` passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

## File Structure

Created:
- `tests/unit/test_version.py` — single-source-of-truth guard: `gtex_link.__version__` must equal the installed package metadata (pyproject).

Modified:
- `gtex_link/__init__.py` (line 3) — bump `__version__` from `"2.0.0"` to `"2.0.1"` so `/health`, the FastAPI app title, the CLI, and structured logs stop reporting a stale version.
- `tests/test_mcp/test_tool_bodies.py` (`test_median_gtex_v10_resolves_gene_to_v39_id`, ends at line 935) — add an explicit non-empty-rows assertion that locks the acceptance criterion ("gtex_v10 median returns rows for a known gene").

Already-present (from stranded commit `8c48b7c`, NOT to be re-implemented — verify only):
- `gtex_link/models/gtex.py` — `GencodeVersion` StrEnum `V19/V26/V39`, `DATASET_GENCODE_VERSION` map (`gtex_v8→v26`, `gtex_v10→v39`), `gencode_version_for_dataset()`.
- `gtex_link/models/responses.py:44` — `GencodeVersion = Literal["v19", "v26", "v39"]`.
- `gtex_link/mcp/search_match.py` — dataset-aware `resolve_gene_ids(..., gencode_version=...)`.
- `gtex_link/mcp/tools/expression.py` — wires `gencode_version_for_dataset(dataset_id)` into both expression tools.
- `gtex_link/mcp/metadata.py` — advertises `dataset_gencode_versions`.
- `tests/test_mcp/test_search_match.py`, `tests/test_mcp/test_tool_bodies.py` — the v39 re-resolution regression tests.

---

### Task 1: Reconcile package `__version__` drift (2.0.0 → 2.0.1)

**Files**
- Create test: `tests/unit/test_version.py`
- Modify: `gtex_link/__init__.py:3`

**Interfaces**
- Consumes: `importlib.metadata.version("gtex-link")` (reflects `pyproject.toml` `version = "2.0.1"` post-`uv sync`; verified `metadata: 2.0.1` at plan time) and `gtex_link.__version__` (currently the stale string literal `"2.0.0"`).
- Produces: no API change — only a value change. `app.py:37,77,93`, `api/routes/health.py:55,66`, `cli.py:217`, and `logging_config.py:28` all read `__version__` and become consistent with `mcp/metadata.py:8` (which already used `importlib.metadata` = 2.0.1).

Steps:

- [ ] (1) Write the failing test. Create `tests/unit/test_version.py`:

```python
"""Single-source-of-truth guard: __version__ must match the installed package metadata."""

from __future__ import annotations

from importlib.metadata import version

import gtex_link


def test_dunder_version_matches_package_metadata() -> None:
    # gtex_link/__init__.py.__version__ is hand-maintained and drifted to "2.0.0"
    # while pyproject.toml (the source importlib.metadata reflects after `uv sync`)
    # is "2.0.1", so /health, the FastAPI app title, the CLI, and structured logs
    # all reported a stale version while mcp/metadata.py (importlib.metadata)
    # reported the real one. Pin both consumers to one source.
    assert gtex_link.__version__ == version("gtex-link")
```

- [ ] (2) Run it and watch it FAIL. Command:

```bash
uv run pytest tests/unit/test_version.py -q
```

Expected FAIL: `AssertionError: assert '2.0.0' == '2.0.1'` (1 failed).

- [ ] (3) Minimal implementation. Edit `gtex_link/__init__.py` line 3, changing only the literal:

```diff
-__version__ = "2.0.0"
+__version__ = "2.0.1"
```

Do NOT touch `tests/unit/test_gtex_service.py:68` or `tests/fixtures/gtex_api_responses.py:214` — those `"2.0.0"` strings are the *upstream GTEx Portal API's* `SERVICE_INFO_RESPONSE` version, not the package version, and must stay. Every other test compares against the `__version__` *symbol* (`test_health_routes.py`, `test_app.py`, `test_cli.py`, `test_api/test_health.py`, `conformance/test_transport_mode.py`), so they track the new value automatically.

- [ ] (4) Run it and watch it PASS. Commands:

```bash
uv run pytest tests/unit/test_version.py -q
uv run pytest tests/unit -q   # confirm no symbol-based test regressed
```

Expected PASS: `2 passed` for the first; the second run green (the `__version__`-symbol tests now assert "2.0.1" on both sides).

- [ ] (5) Commit:

```bash
git add gtex_link/__init__.py tests/unit/test_version.py
git commit -m "fix(version): pin __version__ to 2.0.1 to match pyproject/metadata

__init__.__version__ was 2.0.0 while pyproject and importlib.metadata were
2.0.1, so /health, the app title, the CLI, and logs reported a stale version
while the capabilities surface reported the real one. Add a single-source-of-
truth regression test.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Lock the gtex_v10 non-empty-rows acceptance into the v39 regression test

**Files**
- Modify test: `tests/test_mcp/test_tool_bodies.py` (`test_median_gtex_v10_resolves_gene_to_v39_id`, current body lines 892–935)
- Verify (read-only, do not edit): `gtex_link/mcp/search_match.py:123` (`resolve_gene_ids`), `gtex_link/mcp/tools/expression.py:91` and `:210` (the `gencode_version_for_dataset(dataset_id)` wiring), `gtex_link/models/gtex.py:64` (`DATASET_GENCODE_VERSION`).

**Interfaces**
- Consumes: the existing fix from commit `8c48b7c`. `resolve_gene_ids(service, ["ENSG00000008710.19"], gencode_version="v39")` must strip the v26 suffix, re-query `get_genes(gencodeVersion="v39")`, and return `["ENSG00000008710.20"]`; the median tool then queries that ID and yields a non-empty grouped payload (`payload["genes"][0]["tissues"]`).
- Produces: a strengthened assertion. No source change in this task — the behaviour is already implemented and the red→green for it was paid in `8c48b7c`. This task is the **verification half**: it confirms the stranded fix is present and green on the local tree, and adds the literal acceptance assertion that is RED against `origin/main` (the deployed code — proven below) and GREEN against the fixed tree.

> Deployed-bug evidence (captured at plan time via the live router, read-only):
> `gtex_get_median_expression_levels(gencode_id=["PKD1"], dataset_id="gtex_v10")` returns
> `{"success": false, "error_code": "not_found", "message": "...Gene IDs resolve against gtex_v8 (GENCODE v26)...Retry with dataset_id='gtex_v8'."}` —
> the *pre-fix* error string from the old `expression.py`, confirming `origin/main`/prod lack `8c48b7c`.

Steps:

- [ ] (1) Strengthen the regression test. In `tests/test_mcp/test_tool_bodies.py`, the test `test_median_gtex_v10_resolves_gene_to_v39_id` currently ends (line 935) with:

```python
    assert payload["success"] is True
    assert captured["genes_req"].gencode_version == "v39"
    assert captured["median_req"].gencode_id == ["ENSG00000008710.20"]
```

Append the literal acceptance assertion immediately after that last line:

```python
    # Acceptance: gtex_v10 median must return non-empty rows for a known gene.
    assert payload["genes"], "gtex_v10 median returned no gene groups"
    tissues = payload["genes"][0]["tissues"]
    assert tissues, "gtex_v10 median returned empty tissue rows"
    assert tissues[0]["median"] == 510.7
```

(The `fake_median` in the test already returns one `MedianGeneExpression` row with `median=510.7` for `Brain_Cerebellum`; this asserts the tool surfaces it as a non-empty grouped row rather than collapsing to `not_found`.)

- [ ] (2) Run it and confirm GREEN on the fixed local tree (the verification half of TDD). Command:

```bash
uv run pytest tests/test_mcp/test_tool_bodies.py -k v10 -q
```

Expected PASS: `1 passed`. (Documented RED state: the same assertions fail against the deployed `origin/main` because `resolve_gene_ids` there passes `.19` through unchanged, so `captured["median_req"].gencode_id` is `["ENSG00000008710.19"]` and `captured["genes_req"]` is never populated. Do NOT check out `origin/main` to reproduce — that is out of scope for this plan; the live-router evidence above already proves the RED.)

- [ ] (3) Verify the rest of the stranded fix's tests are present and green (no implementation change):

```bash
uv run pytest tests/test_mcp/test_search_match.py tests/test_mcp/test_tool_bodies.py -q
```

Expected PASS: every test green, including `test_resolve_gene_ids_resolves_against_dataset_version`, `test_gencode_version_for_dataset_maps_known_datasets`, `test_median_empty_on_nondefault_dataset_is_not_found`, and `test_median_gtex_v10_resolves_gene_to_v39_id`.

- [ ] (4) Re-run the targeted file once more to confirm the new assertion sticks:

```bash
uv run pytest tests/test_mcp/test_tool_bodies.py -k "v10 or median" -q
```

Expected PASS.

- [ ] (5) Commit:

```bash
git add tests/test_mcp/test_tool_bodies.py
git commit -m "test(gtex): assert gtex_v10 median returns non-empty rows

Lock the acceptance criterion for the dataset-aware GENCODE fix: a v26 id on
gtex_v10 must re-resolve to the v39 id (.20) and yield a non-empty grouped
payload, not a not_found. Hardens the regression added in 8c48b7c.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Ship the stranded fix + reconciliation to origin/main via PR with green CI  [EXECUTION-GATED]

**Files**
- No source edits. This task moves local commit `8c48b7c` (the stranded fix) plus the Task 1 and Task 2 commits onto `origin/main` through a PR so CI (`.github/workflows/ci.yml`) gates them, then merges. CI runs `make ci-local` on every PR and the coverage gate on push to `main`.

**Interfaces**
- Consumes: local `main` at HEAD = `8c48b7c` + Task 1 commit + Task 2 commit (three commits ahead of `origin/main`).
- Produces: `origin/main` containing all three commits, CI green. After redeploy, the live `gtex_v10` median call returns rows instead of `not_found`.

Steps:

- [ ] (1) Create a PR branch carrying all three commits (local `main` is already 3 ahead of `origin/main`; branch from it so the branch contains `8c48b7c` + Task 1 + Task 2):

```bash
git -C /home/bernt-popp/development/gtex-link switch -c fix/gtex-v10-gencode-and-version
git -C /home/bernt-popp/development/gtex-link log origin/main..HEAD --oneline   # expect 3 commits, oldest = 8c48b7c
```

- [ ] (2) Rebase on the latest remote to avoid shipping against stale `origin/main`, then run the full local CI gate:

```bash
git -C /home/bernt-popp/development/gtex-link fetch origin
git -C /home/bernt-popp/development/gtex-link rebase origin/main   # resolve conflicts if origin advanced
cd /home/bernt-popp/development/gtex-link && make ci-local
```

Expected PASS: `make ci-local` runs `format-check lint-ci lint-loc typecheck-fast test-fast` and ends green (no ruff/mypy errors, LOC budget OK, all tests passing under xdist).

- [ ] (3) Push the branch and open the PR (CI `ci.yml` runs `make ci-local` on the PR):

```bash
git -C /home/bernt-popp/development/gtex-link push -u origin fix/gtex-v10-gencode-and-version
gh pr create --repo berntpopp/gtex-link --base main \
  --title "fix(gtex): dataset-aware GENCODE resolution (v10/v39) + version reconcile" \
  --body "Ships the stranded fix (8c48b7c) so gtex_v10 (GENCODE v39) median/individual expression returns rows instead of not_found, and pins __version__ to 2.0.1. Deployed prod still returns not_found for PKD1 on gtex_v10. Research use only; not clinical decision support."
```

- [ ] (4) Wait for CI to go green, then merge preserving history (so `8c48b7c` and its Co-Authored-By trailers survive — do not squash):

```bash
gh pr checks --repo berntpopp/gtex-link --watch
gh pr merge --repo berntpopp/gtex-link --merge --delete-branch
```

- [ ] (5) Verify origin/main now contains the fix and the push-only coverage gate passed:

```bash
git -C /home/bernt-popp/development/gtex-link fetch origin
git -C /home/bernt-popp/development/gtex-link log origin/main --oneline | head -5   # 8c48b7c + version + test commits present
git -C /home/bernt-popp/development/gtex-link log origin/main..main --oneline       # expect empty
```

- [ ] (6) Out-of-band post-deploy acceptance (manual, after the gtex backend is redeployed from the new `origin/main` — NOT part of CI, since the test suite blocks live network via respx): re-run the live router call and confirm it now returns rows:

```text
gtex_get_median_expression_levels(gencode_id=["PKD1"], dataset_id="gtex_v10", tissue_site_detail_id="Kidney_Cortex")
# expect success:true with a non-empty genes[0].tissues, gencode resolved to ENSG00000008710.20
```

---

**Acceptance criteria**
- `uv run python -c "import gtex_link, importlib.metadata as m; assert gtex_link.__version__ == m.version('gtex-link') == '2.0.1'"` exits 0.
- `uv run pytest tests/test_mcp/test_tool_bodies.py -k v10 -q` passes, including the new `payload["genes"][0]["tissues"]` non-empty assertion and `captured["median_req"].gencode_id == ["ENSG00000008710.20"]`.
- `make ci-local` is green on the PR branch.
- `git -C /home/bernt-popp/development/gtex-link log origin/main --oneline | grep 8c48b7c` finds the stranded fix commit; `git log origin/main..main` is empty (nothing left unpushed).
- PR CI green (the `quality` job in `ci.yml`) and the push-to-main coverage gate green.
- Post-redeploy: the live `gtex_get_median_expression_levels(["PKD1"], dataset_id="gtex_v10")` returns `success:true` with non-empty rows (no `not_found`).

**Risk & rollback** — **EXECUTION-GATED** (execution ends in `git push` + PR merge to `origin/main`, and the acceptance is only fully met after a backend redeploy).
- *Risk:* `origin/main` advanced since `8c48b7c` and the rebase conflicts. *Mitigation:* step (2) rebases first; resolve conflicts before `make ci-local`.
- *Risk:* the version bump breaks a test that hard-codes `"2.0.0"`. *Mitigation:* verified — the only `"2.0.0"` literals are the upstream GTEx Portal API version in `tests/fixtures/gtex_api_responses.py:214` / `tests/unit/test_gtex_service.py:68` (unrelated to the package version); all package-version tests use the `__version__` symbol.
- *Risk:* merge ships a latent issue. *Mitigation:* CI gate on the PR; merge only after green.
- *Rollback:* revert the merge commit (`git revert -m 1 <merge_sha>`) or `gh pr revert`; the version change is a one-line revert; the test change is additive. No schema/data migration, no destructive remote operation beyond the push/merge itself.

**Effort:** ~1–2 hours. The substantive fix is already written and tested in `8c48b7c`; net-new work is one line (`__version__`), one new test file, one strengthened assertion, plus the PR/CI/merge cycle (and a redeploy that is owned by the deployment runbook, not this plan).
