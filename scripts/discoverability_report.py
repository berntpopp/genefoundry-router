"""Print the router's tool-discoverability benchmark report.

Reproduces the EXACT discovery surface the router serves (pinned entry points + the
instructions map + the field-weighted/stemmed BM25 ``search_tools``) over a snapshot of
the real federated catalog, and scores how reliably each realistic task's canonical tool
is reachable. See ``genefoundry_router/devtools/discoverability.py`` for the engine and
``tests/discoverability/`` for the snapshot, task set, and CI gate.

Usage:
    uv run python scripts/discoverability_report.py [--min-score 9.0] [--search-only]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.discoverability import (
    evaluate,
    format_report,
    load_catalog,
    load_tasks,
)
from genefoundry_router.tool_search import resolve_entrypoints

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-score", type=float, default=0.0, help="fail below this /10 score")
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="ignore pins/instructions — measure pure BM25 ranking quality",
    )
    args = parser.parse_args()

    registry = load_registry(ROOT / "servers.yaml", {})
    surfaced = [] if args.search_only else resolve_entrypoints(registry)
    report = asyncio.run(evaluate(load_catalog(), load_tasks(), surfaced))
    print(("SEARCH-ONLY " if args.search_only else "") + format_report(report))

    if report.score_out_of_10 < args.min_score:
        print(f"\nFAIL: {report.score_out_of_10}/10 < required {args.min_score}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
