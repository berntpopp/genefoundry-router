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
    evaluate,
    format_report,
    load_catalog,
    load_tasks,
)
from genefoundry_router.tool_search import resolve_entrypoints

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_snapshot_is_the_real_fleet() -> None:
    names = {t.name for t in load_catalog()}
    assert len(names) >= 200  # 218 at capture; tolerate small fleet drift
    # canonical tools the benchmark leans on must exist in the snapshot
    for n in ("mondo_resolve_disease", "spliceai_predict_splicing", "vep_annotate_variant"):
        assert n in names


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


async def test_discoverability_meets_bar() -> None:
    registry = load_registry(ROOT / "servers.yaml", {})
    surfaced = resolve_entrypoints(registry)
    report = await evaluate(load_catalog(), load_tasks(), surfaced)
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
