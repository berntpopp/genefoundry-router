"""Tool-surface CI gate — the offline enforcement of two fleet standards.

    python scripts/check_tool_surface.py            # every backend in the pinned baseline
    python scripts/check_tool_surface.py --json      # machine-readable

Enforces, against `genefoundry_router/data/fleet-baseline.json` (the digest-attested snapshot
of every backend's real tool definitions — no network, fully deterministic):

  docs/TOOL-SURFACE-BUDGET-STANDARD-v1.md
    B1  a tool definition MUST NOT exceed 1,200 tokens
    B2  a server's whole tool surface MUST NOT exceed 10,000 tokens

  docs/TOOL-SCHEMA-DOCUMENTATION-STANDARD-v1.md
    S1  every input property MUST carry a non-empty `description`
    S2  every REQUIRED property MUST carry at least one `examples` value
    S3  every ARRAY-typed property MUST carry at least one `examples` value

The baseline is derived, never hardcoded: every backend present in it is checked, so a new
backend is gated the day it is snapshotted. A hardcoded server list would be the same bug one
level up.

Not enforced here, deliberately: "every closed vocabulary MUST declare an `enum`" (S4) cannot
be decided by reading a schema — an undeclared enum looks exactly like a free string. It is
enforced empirically instead, by the behaviour gate (docs/conformance/behaviour.py), which
sends an unrecognised value and requires the server to reject it rather than silently match
nothing. Static and dynamic gates together close the loop; neither closes it alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

from surface import (
    BASELINE,
    MAX_SERVER_TOKENS,
    MAX_TOOL_TOKENS,
    MIN_DOCUMENTED_PCT,
    is_array,
    properties,
    surface_metrics,
    tokens,
)


class Violation(NamedTuple):
    server: str
    rule: str
    detail: str


def check_backend(namespace: str, entry: dict[str, Any]) -> list[Violation]:
    tools: list[dict[str, Any]] = entry.get("tools", [])
    server = f"{namespace}-link"
    bad: list[Violation] = []
    metrics = surface_metrics(tools)

    if metrics["surface"] > MAX_SERVER_TOKENS:
        bad.append(
            Violation(
                server,
                "B2",
                f"tool surface {metrics['surface']:,}t exceeds "
                f"{MAX_SERVER_TOKENS:,}t ({metrics['out_pct']}% of it is outputSchema)",
            )
        )

    for tool in tools:
        name = tool.get("name", "?")
        cost = tokens(tool)
        if cost > MAX_TOOL_TOKENS:
            out = tokens(tool.get("outputSchema") or {})
            bad.append(
                Violation(
                    server,
                    "B1",
                    f"{name} is {cost:,}t (limit {MAX_TOOL_TOKENS:,}t; "
                    f"{round(100 * out / cost)}% outputSchema)",
                )
            )

        required = set((tool.get("inputSchema") or {}).get("required") or [])
        for prop_name, prop in properties(tool).items():
            if not prop.get("description"):
                bad.append(Violation(server, "S1", f"{name}.{prop_name} has no description"))
            if prop_name in required and not prop.get("examples"):
                bad.append(
                    Violation(server, "S2", f"{name}.{prop_name} is required but has no examples")
                )
            if is_array(prop) and not prop.get("examples"):
                bad.append(
                    Violation(
                        server,
                        "S3",
                        f"{name}.{prop_name} takes an array but has no examples "
                        "(a model cannot tell a list from a scalar)",
                    )
                )
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the fleet's tool-surface standards")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--baseline", default=BASELINE)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    backends = json.loads((root / args.baseline).read_text())["backends"]

    violations: list[Violation] = []
    rows: list[dict[str, Any]] = []
    for namespace, entry in sorted(backends.items()):
        found = check_backend(namespace, entry)
        violations.extend(found)
        metrics = surface_metrics(entry.get("tools", []))
        rows.append({"server": f"{namespace}-link", "violations": len(found), **metrics})

    if args.json:
        print(
            json.dumps(
                {
                    "conformant": not violations,
                    "servers": rows,
                    "violations": [v._asdict() for v in violations],
                },
                indent=2,
            )
        )
        return 1 if violations else 0

    header = (
        f"{'server':<22}{'tools':>6}{'surface':>9}{'out%':>6}"
        f"{'doc%':>6}{'enum':>6}{'ex':>5}{'fails':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda r: -r["surface"]):
        flag = "" if not r["violations"] else f"{r['violations']:>7}"
        print(
            f"{r['server']:<22}{r['tools']:>6}{r['surface']:>8,}t{r['out_pct']:>5}%"
            f"{r['doc_pct']:>5}%{r['enums']:>6}{r['examples']:>5}{flag or '      -':>7}"
        )
    print("-" * len(header))
    print(f"{'TOTAL':<22}{sum(r['tools'] for r in rows):>6}{sum(r['surface'] for r in rows):>8,}t")

    if not violations:
        print(
            f"\nCONFORMANT — every tool <= {MAX_TOOL_TOKENS:,}t, every server <= "
            f"{MAX_SERVER_TOKENS:,}t, {MIN_DOCUMENTED_PCT}% of params documented."
        )
        return 0

    print(f"\n{len(violations)} VIOLATIONS\n")
    by_rule: dict[str, list[Violation]] = {}
    for v in violations:
        by_rule.setdefault(v.rule, []).append(v)
    titles = {
        "B1": f"tool definition over {MAX_TOOL_TOKENS:,} tokens",
        "B2": f"server surface over {MAX_SERVER_TOKENS:,} tokens",
        "S1": "input property with no description",
        "S2": "required property with no examples",
        "S3": "array property with no examples",
    }
    for rule in sorted(by_rule):
        found = by_rule[rule]
        print(f"{rule}  {titles[rule]}  [{len(found)}]")
        for v in found[:6]:
            print(f"      {v.server:<18} {v.detail}")
        if len(found) > 6:
            print(f"      ... and {len(found) - 6} more")
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
