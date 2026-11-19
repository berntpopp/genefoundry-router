"""Tool-surface CI gate — the offline enforcement of two fleet standards.

    python scripts/check_tool_surface.py            # every backend in the pinned baseline
    python scripts/check_tool_surface.py --json      # machine-readable
    python scripts/check_tool_surface.py --rule D1   # description-lead report (warn only)
    python scripts/check_tool_surface.py --rule D1 --strict-lead   # ... and fail on it

Enforces, against `genefoundry_router/data/fleet-baseline.json` (the digest-attested snapshot
of every backend's real tool definitions — no network, fully deterministic):

  docs/TOOL-SURFACE-BUDGET-STANDARD-v1.md
    B1  a tool definition MUST NOT exceed 1,200 tokens
    B2  a server's whole tool surface MUST NOT exceed 10,000 tokens

  docs/TOOL-SCHEMA-DOCUMENTATION-STANDARD-v1.md
    S1  every input property MUST carry a non-empty `description`
    S2  every REQUIRED property MUST carry at least one `examples` value
    S3  every ARRAY-typed property MUST carry at least one `examples` value
    D1  (v1.1, advisory) a tool description's first sentence MUST say what the tool returns;
        reported as a warning, a failure only under --strict-lead

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
    lead_states_return,
    properties,
    surface_metrics,
    tokens,
)


ADVISORY = {"D1"}  # warn-only until a repo opts in with --strict-lead


class Violation(NamedTuple):
    server: str
    rule: str
    detail: str


def lint_tool_schema(tool: dict[str, Any], server: str = "server") -> list[Violation]:
    name = tool.get("name", "?")
    bad: list[Violation] = []
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

    if not lead_states_return(tool.get("description")):
        bad.append(Violation(server, "D1", f"{name}: first sentence does not say what it returns"))

    if (
        name == "get_diagnostics"
        or name.endswith("_get_diagnostics")
        or name.endswith("_diagnostics")
    ):
        props = properties(tool)
        if "response_mode" in props:
            bad.append(
                Violation(
                    server,
                    "S5",
                    f"{name} must not advertise response_mode (diagnostics tools must be zero-argument)",
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
        bad.extend(lint_tool_schema(tool, server=server))
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the fleet's tool-surface standards")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--rule", help="report only this rule (e.g. D1), listing every finding")
    parser.add_argument("--strict-lead", action="store_true", help="fail on D1 instead of warning")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    backends = json.loads((root / args.baseline).read_text())["backends"]

    violations: list[Violation] = []
    rows: list[dict[str, Any]] = []
    for namespace, entry in sorted(backends.items()):
        found = [v for v in check_backend(namespace, entry) if v.rule == (args.rule or v.rule)]
        violations.extend(found)
        metrics = surface_metrics(entry.get("tools", []))
        lead = sum(v.rule == "D1" for v in found)
        rows.append(
            {"server": f"{namespace}-link", "violations": len(found), "lead": lead, **metrics}
        )
    blocking = [v for v in violations if args.strict_lead or v.rule not in ADVISORY]

    if args.json:
        print(
            json.dumps(
                {
                    "conformant": not blocking,
                    "servers": rows,
                    "violations": [v._asdict() for v in violations],
                },
                indent=2,
            )
        )
        return 1 if blocking else 0

    header = (
        f"{'server':<22}{'tools':>6}{'surface':>9}{'out%':>6}"
        f"{'doc%':>6}{'enum':>6}{'ex':>5}{'D1':>5}{'fails':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda r: -r["surface"]):
        flag = "" if not r["violations"] else f"{r['violations']:>7}"
        print(
            f"{r['server']:<22}{r['tools']:>6}{r['surface']:>8,}t{r['out_pct']:>5}%"
            f"{r['doc_pct']:>5}%{r['enums']:>6}{r['examples']:>5}{r['lead']:>5}{flag or '      -':>7}"
        )
    print("-" * len(header))
    print(f"{'TOTAL':<22}{sum(r['tools'] for r in rows):>6}{sum(r['surface'] for r in rows):>8,}t")

    if not violations:
        print(
            f"\nCONFORMANT — every tool <= {MAX_TOOL_TOKENS:,}t, every server <= "
            f"{MAX_SERVER_TOKENS:,}t, {MIN_DOCUMENTED_PCT}% of params documented."
        )
        return 0

    advisory = len(violations) - len(blocking)
    print(f"\n{len(blocking)} VIOLATIONS, {advisory} WARNINGS (D1 is advisory)\n")
    by_rule: dict[str, list[Violation]] = {}
    for v in violations:
        by_rule.setdefault(v.rule, []).append(v)
    titles = {
        "B1": f"tool definition over {MAX_TOOL_TOKENS:,} tokens",
        "B2": f"server surface over {MAX_SERVER_TOKENS:,} tokens",
        "S1": "input property with no description",
        "S2": "required property with no examples",
        "S3": "array property with no examples",
        "S5": "diagnostics tool advertises response_mode",
        "D1": "description lead does not say what the tool returns",
    }
    for rule in sorted(by_rule):
        found = by_rule[rule]
        print(f"{rule}  {titles[rule]}  [{len(found)}]")
        shown = found if args.rule else found[:6]
        for v in shown:
            print(f"      {v.server:<18} {v.detail}")
        if len(found) > len(shown):
            print(f"      ... and {len(found) - len(shown)} more")
        print()
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
