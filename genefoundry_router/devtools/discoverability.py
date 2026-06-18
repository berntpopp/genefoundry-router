"""Offline discoverability benchmark for the router's tool-search surface.

Issue #3 surfaced that a model cannot find a capability if the discovery surface
does not put it within reach. This module measures, against a snapshot of the real
federated catalog, how reliably each realistic task's canonical tool is discoverable
through the EXACT surface the router serves: pinned entry points + the instructions
map + FastMCP's BM25 ``search_tools``. It scores per task and aggregates to a /10 so
improvements (pins, weighted search text, descriptions) can be measured, not asserted.

Faithfulness: the snapshot (``catalog.json``) stores exactly what FastMCP's BM25 index
reads — tool name, description, parameter names/descriptions, tags — and the search is
run through the real ``CompactBM25SearchTransform``, so a benchmark hit corresponds to a
real hit. Tasks live in ``tasks.yaml`` (intent -> acceptable canonical target tools).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastmcp.tools.base import Tool

from genefoundry_router.tool_search import CompactBM25SearchTransform

_HERE = Path(__file__).resolve()
# tests/discoverability/ holds the snapshot + task set (committed fixtures).
DATA_DIR = _HERE.parents[2] / "tests" / "discoverability"
DEFAULT_CATALOG = DATA_DIR / "catalog.json"
DEFAULT_TASKS = DATA_DIR / "tasks.yaml"
DEFAULT_MAX_RESULTS = 5  # mirrors GF_SEARCH_MAX_RESULTS — the model only sees the top-K
PROBE_DEPTH = 10  # search depth used to compute rank metrics beyond the served cutoff


@dataclass(frozen=True)
class Task:
    """One discoverability probe: a natural-language intent and the tools that satisfy it."""

    id: str
    category: str
    query: str
    expected: tuple[str, ...]  # any one of these counts as discovered


@dataclass
class TaskResult:
    task: Task
    pinned: bool  # an expected tool is pinned / named in instructions (zero-search reach)
    search_rank: int | None  # 1-based rank of the first expected tool in BM25 search, else None
    score: float  # graded 0..1
    matched: str | None


@dataclass
class Report:
    results: list[TaskResult] = field(default_factory=list)

    @property
    def score_out_of_10(self) -> float:
        return round(10 * self._mean(r.score for r in self.results), 2)

    @property
    def discoverable_rate(self) -> float:
        """Fraction of tasks whose target is reachable at all (pinned or within top-K)."""
        return self._mean(1.0 if r.score > 0 else 0.0 for r in self.results)

    def hit_at(self, k: int) -> float:
        return self._mean(
            1.0 if r.pinned or (r.search_rank is not None and r.search_rank <= k) else 0.0
            for r in self.results
        )

    @property
    def mrr(self) -> float:
        return self._mean(self._reciprocal_rank(r) for r in self.results)

    @property
    def misses(self) -> list[TaskResult]:
        """Tasks not reachable through the served surface (the iteration work-list)."""
        return [r for r in self.results if r.score == 0]

    def by_category(self) -> dict[str, float]:
        cats: dict[str, list[float]] = {}
        for r in self.results:
            cats.setdefault(r.task.category, []).append(r.score)
        return {c: round(10 * sum(v) / len(v), 2) for c, v in sorted(cats.items())}

    @staticmethod
    def _reciprocal_rank(r: TaskResult) -> float:
        if r.pinned:
            return 1.0
        return 1.0 / r.search_rank if r.search_rank else 0.0

    @staticmethod
    def _mean(values: Any) -> float:
        vals = list(values)
        return sum(vals) / len(vals) if vals else 0.0


def load_catalog(path: Path = DEFAULT_CATALOG) -> list[Tool]:
    """Rebuild Tool objects from the lean snapshot, reconstructing exactly the fields
    FastMCP's BM25 index reads (``parameters.properties[name].description``)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    tools: list[Tool] = []
    for entry in raw:
        props = {
            pname: ({"description": pdesc} if pdesc else {})
            for pname, pdesc in entry["params"].items()
        }
        tools.append(
            Tool(
                name=entry["name"],
                description=entry.get("description") or "",
                parameters={"type": "object", "properties": props},
                tags=set(entry.get("tags") or []),
            )
        )
    return tools


def load_tasks(path: Path = DEFAULT_TASKS) -> list[Task]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Task(
            id=t["id"],
            category=t["category"],
            query=t["query"],
            expected=tuple(t["expected"]),
        )
        for t in raw["tasks"]
    ]


def _grade(pinned: bool, search_rank: int | None) -> float:
    """Pinned/named -> 1.0 (zero-search reach); otherwise reciprocal rank within the
    served top-K. Rewards both deterministic pinning and good search ranking."""
    if pinned:
        return 1.0
    if search_rank is not None and search_rank <= DEFAULT_MAX_RESULTS:
        return 1.0 / search_rank
    return 0.0


async def evaluate(
    catalog: list[Tool],
    tasks: list[Task],
    surfaced: list[str],
    max_results: int = DEFAULT_MAX_RESULTS,
) -> Report:
    """Score every task against the surface. ``surfaced`` = tools reachable without a
    search (pins + instruction entry points); everything else must rank in BM25 search.
    """
    surfaced_set = set(surfaced)
    # Pinned tools are excluded from the searchable set, exactly as the live router does.
    hidden = [t for t in catalog if t.name not in surfaced_set]
    transform = CompactBM25SearchTransform(max_results=PROBE_DEPTH)

    report = Report()
    for task in tasks:
        pinned_match = next((e for e in task.expected if e in surfaced_set), None)
        ranked = [t.name for t in await transform._search(hidden, task.query)]
        rank: int | None = None
        search_match: str | None = None
        for e in task.expected:
            if e in ranked:
                r = ranked.index(e) + 1
                if rank is None or r < rank:
                    rank, search_match = r, e
        # Honour the served cutoff for the "pinned vs search" grade.
        served_rank = rank if (rank is not None and rank <= max_results) else None
        score = _grade(pinned_match is not None, rank)
        report.results.append(
            TaskResult(
                task=task,
                pinned=pinned_match is not None,
                search_rank=rank,
                score=score,
                matched=pinned_match or (search_match if served_rank else None),
            )
        )
    return report


def format_report(report: Report) -> str:
    """Human-readable summary: headline score, metrics, per-category, and the misses."""
    lines = [
        f"Discoverability: {report.score_out_of_10}/10  "
        f"(reachable {report.discoverable_rate:.0%} | "
        f"hit@1 {report.hit_at(1):.0%} | hit@3 {report.hit_at(3):.0%} | "
        f"hit@5 {report.hit_at(5):.0%} | MRR {report.mrr:.2f}) "
        f"over {len(report.results)} tasks",
        "",
        "By category:",
    ]
    lines += [f"  {cat:<18} {score}/10" for cat, score in report.by_category().items()]
    weak = [r for r in report.results if r.score < 1.0]
    if weak:
        lines += ["", "Weak / missed (rank shown; ✗ = not in top-10):"]
        for r in sorted(weak, key=lambda x: x.score):
            where = "PIN" if r.pinned else (f"#{r.search_rank}" if r.search_rank else "✗")
            lines.append(f"  [{where:>3}] {r.task.id:<28} {r.task.query[:54]!r}")
    return "\n".join(lines)
