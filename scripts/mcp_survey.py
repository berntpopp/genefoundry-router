"""Fleet MCP survey — the live tool surface, measured.

Objective, comparable metrics for every server's *tool surface*: the part of an MCP server a
client pays for on every request, before any work happens. Tool definitions sit in the
system-prompt prefix, so this is a per-request tax, not a one-off cost at connect time.

    python scripts/mcp_survey.py                                  # whole fleet, public HTTPS
    python scripts/mcp_survey.py --url http://127.0.0.1:8000 --name gtex-link

Columns:

    ver / pin       live serverInfo.version vs the version in the pinned fleet baseline
    tools           tools advertised
    surface         approx tokens to list them all (chars/4 — comparative, not a tokenizer)
    out%            share of that tax that is outputSchema
    doc%            share of input properties carrying a `description`
    enum / ex       properties declaring an `enum` / carrying `examples`

This is the observability tool, run against production; it enforces nothing. The CI gate is
`scripts/check_tool_surface.py`, which applies the thresholds offline against the pinned
baseline. See docs/TOOL-SURFACE-BUDGET-STANDARD-v1.md and
docs/TOOL-SCHEMA-DOCUMENTATION-STANDARD-v1.md.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from typing import Any

from mcp_probe import MCPError, MCPSession, _root, load_servers
from surface import BASELINE, surface_metrics


def _pinned_versions() -> dict[str, str]:
    """Pinned version per service slug, from the router's own fleet baseline."""
    data = json.loads((_root() / BASELINE).read_text())
    return {
        f"{namespace}-link": str(entry.get("version", "?"))
        for namespace, entry in data.get("backends", {}).items()
    }


def survey(name: str, url: str, pinned: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"server": name}
    try:
        session = MCPSession(name, url, timeout=90)
        session.initialize()
        tools = session.list_tools()
    except MCPError as exc:
        return {**row, "error": str(exc)[:70]}

    live = session.server_info.get("version", "?")
    row["version"] = live
    # The router is not a backend and carries no row in the backend baseline.
    row["pin"] = "-" if pinned is None else ("ok" if live == pinned else "DRIFT")
    row.update(surface_metrics(tools))
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Survey the fleet's live tool surface")
    parser.add_argument("--url", help="probe one server (e.g. a local container)")
    parser.add_argument("--name", help="its service slug, e.g. gtex-link")
    args = parser.parse_args()

    pins = _pinned_versions()
    if args.url:
        if not args.name:
            raise SystemExit("--url requires --name")
        rows = [survey(args.name, args.url.rstrip("/") + "/mcp", pins.get(args.name))]
    else:
        # Modest concurrency: these are live production endpoints, and the router mints a
        # single OAuth token that a wide fan-out would race.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(survey, name, url, pins.get(name))
                for name, url in load_servers().items()
            ]
            rows = [f.result() for f in futures]

    ok = sorted([r for r in rows if "error" not in r], key=lambda r: -r["surface"])
    bad = [r for r in rows if "error" in r]

    header = (
        f"{'server':<22}{'ver':>8}{'pin':>6}{'tools':>6}{'surface':>9}"
        f"{'out%':>6}{'doc%':>6}{'enum':>6}{'ex':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in ok:
        print(
            f"{r['server']:<22}{r['version']:>8}{r['pin']:>6}{r['tools']:>6}"
            f"{r['surface']:>8,}t{r['out_pct']:>5}%{r['doc_pct']:>5}%"
            f"{r['enums']:>6}{r['examples']:>5}"
        )
    print("-" * len(header))
    print(
        f"{'TOTAL':<22}{'':>8}{'':>6}{sum(r['tools'] for r in ok):>6}"
        f"{sum(r['surface'] for r in ok):>8,}t"
    )
    for r in bad:
        print(f"{r['server']:<22} ERROR: {r['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
