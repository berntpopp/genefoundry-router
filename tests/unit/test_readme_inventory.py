"""The README's federated-backend inventory must match the registry and the pin.

The hand-maintained table drifted: it advertised 280 tools and pubtator=43 while the
reviewed baseline said 272 and 35. README Standard v1 therefore forbids hand-typed
aggregates and puts a test behind the generated block.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
BASELINE = ROOT / "genefoundry_router/data/fleet-baseline.json"

ROW_RE = re.compile(r"^\| `(?P<ns>[a-z0-9_]+)` \|.*\| (?P<tools>\d+) \|", re.M)


def _generated_block() -> str:
    text = README.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- BEGIN GENERATED: fleet-inventory -->(.*?)<!-- END GENERATED: fleet-inventory -->",
        text,
        re.S,
    )
    assert match, "README.md is missing the fleet-inventory GENERATED block"
    return match.group(1)


def test_readme_inventory_is_not_stale() -> None:
    """`--check` fails if the committed README differs from what the generator renders."""
    result = subprocess.run(
        [sys.executable, "scripts/gen_readme_inventory.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"README fleet inventory is stale — run `make readme-inventory`.\n{result.stderr}"
    )


def test_readme_inventory_covers_every_enabled_backend() -> None:
    block = _generated_block()
    listed = {m.group("ns") for m in ROW_RE.finditer(block)}
    enabled = {b.namespace for b in load_registry(ROOT / "servers.yaml", os.environ) if b.enabled}
    assert listed == enabled, (
        f"README inventory does not match servers.yaml.\n"
        f"  missing from README: {sorted(enabled - listed)}\n"
        f"  stale in README:     {sorted(listed - enabled)}"
    )


def test_readme_tool_counts_match_the_reviewed_baseline() -> None:
    block = _generated_block()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["backends"]

    mismatches = []
    for match in ROW_RE.finditer(block):
        ns, claimed = match.group("ns"), int(match.group("tools"))
        actual = len(baseline[ns]["tools"])
        if claimed != actual:
            mismatches.append(f"{ns}: README says {claimed}, baseline says {actual}")
    assert not mismatches, "README tool counts drifted from the pin:\n  " + "\n  ".join(mismatches)


def test_readme_total_matches_the_sum_of_rows() -> None:
    block = _generated_block()
    total_claim = re.search(r"\*\*(\d+) backends, (\d+) tools\*\*", block)
    assert total_claim, "generated block lost its '<N> backends, <M> tools' summary"

    rows = list(ROW_RE.finditer(block))
    assert int(total_claim.group(1)) == len(rows)
    assert int(total_claim.group(2)) == sum(int(m.group("tools")) for m in rows)
