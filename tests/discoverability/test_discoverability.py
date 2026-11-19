"""Discoverability benchmark — the regression gate for issue #3.

Measures, against a snapshot of the real 218-tool catalog, whether each realistic
task's canonical tool is reachable through the EXACT surface the router serves (pinned
entry points + the instructions map + the field-weighted/stemmed BM25 ``search_tools``).
Fails CI if discoverability regresses below the bar reached on 2026-06-18 (9.79/10,
100% reachable). Keeping it a test (not a one-off script) means tuning search, pins, or
descriptions is now evidence-driven and protected against silent regressions.
"""

from __future__ import annotations

from pathlib import Path

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.discoverability import (
    DEFAULT_MAX_RESULTS,
    HELDOUT_TASKS,
    evaluate,
    format_report,
    load_catalog,
    load_tasks,
)
from genefoundry_router.search_expansion import expansion_texts
from genefoundry_router.tool_search import resolve_entrypoints

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_snapshot_is_the_real_fleet() -> None:
    names = {t.name for t in load_catalog()}
    assert len(names) >= 200  # 218 at capture; tolerate small fleet drift
    # canonical tools the benchmark leans on must exist in the snapshot
    for n in ("mondo_resolve_disease", "spliceai_predict_splicing", "vep_annotate_variant"):
        assert n in names


def test_catalog_keeps_enum_values_for_the_reranker() -> None:
    # the reranker's parameter summary must match production, enum values included
    from types import SimpleNamespace

    from genefoundry_router.search_rerank import candidate_text
    from scripts.snapshot_catalog import _entry

    tools = {t.name: t for t in load_catalog()}
    text = candidate_text(tools["autopvs1_get_cnv_pvs1_data"], 200)
    assert "genome_build (hg19|hg38)" in text
    schema = {"properties": {"b": {"description": "x", "anyOf": [{"enum": ["a"]}, {}]}}}
    entry = _entry(SimpleNamespace(name="t", description="", meta=None, input_schema=schema))
    assert entry["enums"] == {"b": ["a"]}


def test_tasks_target_tools_that_exist() -> None:
    catalog = {t.name for t in load_catalog()}
    tasks = load_tasks()
    assert len(tasks) >= 40
    for task in tasks:
        assert task.expected, f"task {task.id} has no expected tools"
        for tool in task.expected:
            assert tool in catalog, f"task {task.id} targets unknown tool {tool!r}"
    # broad coverage: every backend namespace is exercised by at least one task
    namespaces = {t.expected[0].split("_", 1)[0] for t in tasks}
    assert len(namespaces) >= 15


def test_every_enabled_backend_has_a_discoverability_task() -> None:
    """Guard against the silent gap that let hpo/mavedb/metadome/orphanet sit unmonitored:
    every enabled backend MUST be exercised by >=1 golden task, so a newly-federated backend
    can't be added without also being measured for findability."""
    registry = load_registry(ROOT / "servers.yaml", {})
    enabled = {b.namespace for b in registry if b.enabled}
    covered = {tool.split("_", 1)[0] for task in load_tasks() for tool in task.expected}
    missing = sorted(enabled - covered)
    assert not missing, f"enabled backends with no discoverability task: {missing}"


def _served_pins() -> list[str]:
    return resolve_entrypoints(load_registry(ROOT / "servers.yaml", {}))


async def test_discoverability_meets_bar() -> None:
    # The served surface: pins + BM25 over tool text AND the packaged expansions.
    report = await evaluate(
        load_catalog(), load_tasks(), _served_pins(), expansions=expansion_texts("builtin")
    )
    detail = "\n" + format_report(report)
    assert report.score_out_of_10 >= 9.0, detail
    assert report.discoverable_rate >= 0.95, detail  # every use case reachable in top-K
    assert report.hit_at(5) >= 0.95, detail
    assert min(report.by_category().values()) >= 7.0, detail  # no dead domain


async def test_pins_alone_do_not_carry_the_score() -> None:
    # Honesty check: with NO pins, the field-weighted + stemmed search must still reach
    # most tools, so the headline score reflects real ranking quality, not just pinning.
    report = await evaluate(load_catalog(), load_tasks(), surfaced=[])
    assert report.discoverable_rate >= 0.90
    assert report.score_out_of_10 >= 7.0


async def test_pubtator_search_is_reachable_without_pins() -> None:
    """Literature search should rank within the actual five-result search surface."""
    task = next(task for task in load_tasks() if task.id == "pubtator-search")
    report = await evaluate(load_catalog(), [task], surfaced=[])

    assert report.results[0].search_rank is not None
    assert report.results[0].search_rank <= DEFAULT_MAX_RESULTS


async def test_bm25_without_expansion_still_meets_the_golden_bar() -> None:
    # GF_SEARCH_EXPANSIONS=off must remain a safe fallback, not a cliff.
    report = await evaluate(load_catalog(), load_tasks(), _served_pins())
    # 8.9 (was 9.0): owner decision 2026-11-19: clinpgx un-pinned (pinned budget); restore when clinpgx-link#56 lands
    # 8.7 (was 8.9): 2026-11-19: 4 new search-only backend tasks added (no-expansion ranks 1/1/2/4);
    # old 54 tasks unchanged at 8.90; with-expansion bar 9.0 still enforced
    assert report.score_out_of_10 >= 8.7, "\n" + format_report(report)
    assert report.hit_at(5) >= 0.95


def test_heldout_set_is_broad_and_targets_real_tools() -> None:
    catalog = {t.name for t in load_catalog()}
    positives = load_tasks(HELDOUT_TASKS)
    everything = load_tasks(HELDOUT_TASKS, include_negatives=True)
    assert len(positives) >= 150
    assert len(everything) - len(positives) >= 20  # abstention probes stay in the file
    for task in everything:
        for tool in task.expected:
            assert tool in catalog, f"held-out task {task.id} targets unknown tool {tool!r}"
    styles = {t.style for t in positives}
    assert {"lay", "jargon", "paraphrase", "crosslingual", "concrete"} <= styles


async def test_heldout_discoverability_meets_bar() -> None:
    # Measured 2026-09-28: BM25 alone reached 0.877; with expansions 0.990 (score 9.32).
    # Bars leave headroom for a regenerated (temperature > 0) expansion file.
    report = await evaluate(
        load_catalog(),
        load_tasks(HELDOUT_TASKS),
        _served_pins(),
        expansions=expansion_texts("builtin"),
    )
    detail = "\n" + format_report(report) + f"\nby style: {report.by_style()}"
    assert report.score_out_of_10 >= 9.0, detail
    assert report.hit_at(5) >= 0.95, detail
    assert min(report.by_style().values()) >= 0.80, detail  # no phrasing style left behind
    categories = report.by_category()
    # pharmacogenomics 6.0 (was 7.0): owner decision 2026-11-19: clinpgx un-pinned (pinned budget); restore when clinpgx-link#56 lands
    assert categories.pop("pharmacogenomics") >= 6.0, detail
    assert min(categories.values()) >= 7.0, detail


async def test_expansion_lift_on_pure_search_is_real() -> None:
    # Guards the mechanism itself: without pins, expansion must keep a large lift over
    # plain BM25 on the held-out phrasing (0.682 -> 0.944 hit@5 when introduced).
    tasks = load_tasks(HELDOUT_TASKS)
    plain = await evaluate(load_catalog(), tasks, surfaced=[])
    expanded = await evaluate(
        load_catalog(), tasks, surfaced=[], expansions=expansion_texts("builtin")
    )
    assert expanded.hit_at(5) >= 0.90
    assert expanded.hit_at(5) - plain.hit_at(5) >= 0.20
    assert expanded.by_style()["lay"] >= 0.80  # plain BM25: 0.06


async def test_bench_regressions_stay_in_the_served_top_five() -> None:
    """#292: every query a genefoundry-bench run missed must stay in the search top 5.

    Pins don't count: the bench reranks the BM25 shortlist, which a pin never enters.
    """
    tasks = load_tasks(Path(__file__).parent / "bench_regressions.yaml")
    report = await evaluate(load_catalog(), tasks, [], expansions=expansion_texts("builtin"))
    missed = [
        (r.task.id, r.search_rank)
        for r in report.results
        if r.search_rank is None or r.search_rank > 5
    ]
    assert not missed, "\n" + format_report(report)


def test_heldout_set_has_enough_out_of_scope_negatives() -> None:
    # a rerank-floor change needs more than a couple dozen "nothing fits" requests to rest on
    negatives = [t for t in load_tasks(HELDOUT_TASKS, include_negatives=True) if not t.expected]
    assert len(negatives) >= 60
    assert len({t.query for t in negatives}) == len(negatives)
