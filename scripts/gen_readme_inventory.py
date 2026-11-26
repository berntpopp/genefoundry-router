#!/usr/bin/env python
"""Generate the README's federated-backend inventory table.

The table is derived, never typed: domain/source copy comes from ``servers.yaml``,
tool counts from the reviewed ``fleet-baseline.json`` pin. The hand-maintained
version drifted (it advertised 280 tools and pubtator=43 when the baseline said
272 and 35), which is why README Standard v1 forbids hand-typed aggregates.

    python scripts/gen_readme_inventory.py            # rewrite the block in README.md
    python scripts/gen_readme_inventory.py --check    # fail if README.md is stale

The block is delimited so ``check_readme.py`` knows a machine owns it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from genefoundry_router.config import load_community_registry, load_registry

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SERVERS = ROOT / "servers.yaml"
BASELINE = ROOT / "genefoundry_router/data/fleet-baseline.json"
COMMUNITY = ROOT / "registry/community.yaml"
TOMBSTONES = ROOT / "registry/tombstones.yaml"

MARKER = "fleet-inventory"
BEGIN = f"<!-- BEGIN GENERATED: {MARKER} -->"
END = f"<!-- END GENERATED: {MARKER} -->"
BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
COMMUNITY_MARKER = "community-inventory"
COMMUNITY_BEGIN = f"<!-- BEGIN GENERATED: {COMMUNITY_MARKER} -->"
COMMUNITY_END = f"<!-- END GENERATED: {COMMUNITY_MARKER} -->"
COMMUNITY_RE = re.compile(re.escape(COMMUNITY_BEGIN) + r".*?" + re.escape(COMMUNITY_END), re.S)


def render() -> str:
    backends = [b for b in load_registry(SERVERS, os.environ) if b.enabled]
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["backends"]

    rows = []
    for b in backends:
        entry = baseline.get(b.namespace)
        if entry is None:
            raise SystemExit(
                f"error: backend {b.namespace!r} is enabled in servers.yaml but absent "
                f"from the reviewed baseline ({BASELINE.name}). Re-pin before regenerating."
            )
        rows.append((b, len(entry["tools"])))

    rows.sort(key=lambda r: (-r[1], r[0].namespace))
    total = sum(n for _, n in rows)

    lines = [
        BEGIN,
        f"**{len(rows)} backends, {total} tools**, each surfaced namespaced — e.g. `gnomad_search_genes`.",
        "",
        "| Namespace | Domain | Data source | Tools | Repo |",
        "|-----------|--------|-------------|------:|------|",
    ]
    for b, n in rows:
        repo_slug = (b.repo or "").split("/")[-1]
        lines.append(
            f"| `{b.namespace}` | {b.description} | "
            f"[{b.source_name}]({b.source_url}) | {n} | "
            f"[{repo_slug}](https://github.com/{b.repo}) |"
        )
    lines.append(END)
    return "\n".join(lines)


def render_community(path: Path = COMMUNITY) -> str:
    """Community backends (Federation Standard v1), kept apart from the core table."""
    core = load_registry(SERVERS, os.environ)
    backends = [b for b in load_community_registry(path, core, TOMBSTONES) if b.enabled]
    lines = [COMMUNITY_BEGIN]
    if not backends:
        lines.append("No community backends are admitted.")
    else:
        lines += [
            "Operated outside GeneFoundry; served only by the community endpoint.",
            "",
            "| Namespace | Domain | Data source | Operator | Data retention |",
            "|-----------|--------|-------------|----------|----------------|",
        ]
        for b in sorted(backends, key=lambda b: b.namespace):
            source = f"[{b.source_name}]({b.source_url})" if b.source_url else b.source_name
            retention = b.data_egress.retention if b.data_egress else ""
            lines.append(
                f"| `{b.namespace}` | {b.description or ''} | {source} | {b.operator} | "
                f"{retention} |"
            )
    lines.append(COMMUNITY_END)
    return "\n".join(lines)


def main() -> int:
    check = "--check" in sys.argv
    text = README.read_text(encoding="utf-8")

    for marker, pattern, begin, end in (
        (MARKER, BLOCK_RE, BEGIN, END),
        (COMMUNITY_MARKER, COMMUNITY_RE, COMMUNITY_BEGIN, COMMUNITY_END),
    ):
        if not pattern.search(text):
            print(
                f"error: README.md has no '{marker}' GENERATED block (expected {begin} ... {end})",
                file=sys.stderr,
            )
            return 1

    updated = BLOCK_RE.sub(lambda _: render(), text)
    updated = COMMUNITY_RE.sub(lambda _: render_community(), updated)

    if check:
        if updated != text:
            print(
                "error: README.md fleet inventory is stale.\n       Run: make readme-inventory",
                file=sys.stderr,
            )
            return 1
        print("README fleet inventory: up to date")
        return 0

    if updated != text:
        README.write_text(updated, encoding="utf-8")
        print("README.md fleet inventory regenerated")
    else:
        print("README.md fleet inventory already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
