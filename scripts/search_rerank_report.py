#!/usr/bin/env python3
"""Measure the configured search reranker end-to-end on the held-out set (online).

Runs the router's served search pipeline — expanded BM25 → ``GF_SEARCH_RERANK`` stage →
``GF_SEARCH_MAX_RESULTS`` cut — over the catalog snapshot, once without and once with the
reranker, and reports:

* hit@1 / hit@K on in-scope tasks (pins excluded: this measures search quality alone),
* abstention: share of out-of-scope tasks (``expected: []``) that return NO hits, and the
  share of in-scope tasks wrongly emptied (only non-zero with GF_SEARCH_RERANK_MIN_SCORE),
* fallbacks (reranker skipped/failed -> BM25 order) and latency.

Use it to validate a deployment's endpoint (hosted Jev, or a self-hosted OneJev on a VPS)
and to pick GF_SEARCH_RERANK_MIN_SCORE. Every held-out query (synthetic, no personal data)
is sent to GF_SEARCH_RERANK_URL.

    GF_SEARCH_RERANK=systemone GF_SEARCH_RERANK_URL=... uv run python scripts/search_rerank_report.py
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from genefoundry_router.config import RouterSettings  # noqa: E402
from genefoundry_router.devtools.discoverability import (  # noqa: E402
    HELDOUT_TASKS,
    Task,
    load_catalog,
    load_tasks,
)
from genefoundry_router.search_expansion import expansion_texts  # noqa: E402
from genefoundry_router.search_rerank import build_reranker  # noqa: E402
from genefoundry_router.tool_search import CompactBM25SearchTransform  # noqa: E402


async def _run(
    transform: CompactBM25SearchTransform, tasks: list[Task], concurrency: int
) -> list[tuple[Task, list[str], bool, float]]:
    catalog = load_catalog()
    gate = asyncio.Semaphore(concurrency)

    async def one(task: Task) -> tuple[Task, list[str], bool, float]:
        async with gate:
            start = time.perf_counter()
            hits, scores = await transform._ranked(catalog, task.query)
            return task, [t.name for t in hits], scores is not None, time.perf_counter() - start

    return list(await asyncio.gather(*(one(t) for t in tasks)))


def _report(label: str, rows: list[tuple[Task, list[str], bool, float]], k: int) -> None:
    pos = [r for r in rows if r[0].expected]
    neg = [r for r in rows if not r[0].expected]

    def hit(r: tuple[Task, list[str], bool, float], n: int) -> bool:
        return any(name in r[0].expected for name in r[1][:n])

    lat = sorted(r[3] * 1000 for r in rows)
    reranked = sum(r[2] for r in rows)
    print(
        f"{label:<10} hit@1 {sum(hit(r, 1) for r in pos) / len(pos):.3f}  "
        f"hit@{k} {sum(hit(r, k) for r in pos) / len(pos):.3f}  "
        f"abstain(neg) {sum(not r[1] for r in neg) / max(len(neg), 1):.3f}  "
        f"false-abstain(pos) {sum(not r[1] for r in pos) / len(pos):.3f}  "
        f"reranked {reranked}/{len(rows)}  "
        f"p50 {statistics.median(lat):.0f} ms  p95 {lat[int(0.95 * (len(lat) - 1))]:.0f} ms"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tasks", type=Path, default=HELDOUT_TASKS)
    ap.add_argument("--limit", type=int, default=0, help="only the first N tasks (0 = all)")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    settings = RouterSettings()
    reranker = build_reranker(settings)
    if reranker is None:
        print("GF_SEARCH_RERANK is off — set GF_SEARCH_RERANK=systemone and _URL", file=sys.stderr)
        return 2
    tasks = load_tasks(args.tasks, include_negatives=True)
    tasks = tasks[: args.limit] if args.limit else tasks
    expansions = expansion_texts(settings.GF_SEARCH_EXPANSIONS)
    k = settings.GF_SEARCH_MAX_RESULTS
    base = CompactBM25SearchTransform(max_results=k, expansions=expansions)
    staged = CompactBM25SearchTransform(
        max_results=k,
        expansions=expansions,
        reranker=reranker,
        rerank_pool=settings.GF_SEARCH_RERANK_POOL,
    )
    cfg = reranker.config
    print(
        f"{len(tasks)} tasks ({sum(not t.expected for t in tasks)} out-of-scope) | "
        f"model={cfg.model} question={cfg.question} pool={settings.GF_SEARCH_RERANK_POOL} "
        f"min_score={cfg.min_score} expansions={len(expansions)} tools"
    )
    _report("bm25", asyncio.run(_run(base, tasks, args.concurrency)), k)
    _report("reranked", asyncio.run(_run(staged, tasks, args.concurrency)), k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
