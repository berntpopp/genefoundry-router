#!/usr/bin/env python
"""Generate each fleet repo's CITATION.cff.

GitHub renders a **"Cite this repository"** button (APA + BibTeX) when a valid
``CITATION.cff`` sits in the repo root of the default branch. The fleet shipped 0/22 —
the largest gap relative to an audience of geneticists, whose output is papers.

The file is derived, never typed:

- title / abstract / keywords  <- fleet-metadata.yaml (the same copy as the About box)
- version                      <- the repo's own pyproject.toml
- date-released                <- that repo's latest GitHub release (``--write`` only)
- authors / license / urls     <- fleet-metadata.yaml + servers.yaml

Author identity is taken from ``citation.authors`` in fleet-metadata.yaml and NOT from
the 21 pyproject.toml files, which disagree and are partly fabricated (pubtator-link's
author is "AI Assistant"; gtex/litvar/stringdb use non-existent e-mail domains).

    python scripts/gen_citation_cff.py --check   # fail if any repo's CITATION.cff is stale
    python scripts/gen_citation_cff.py --write   # (re)write all 22

Operates on sibling checkouts under the parent directory, so it needs the fleet cloned
locally — like `make metadata-apply`, it is a fleet-level tool, not a repo-level one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_fleet_metadata import load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OWNER = "berntpopp"
LICENSE = "MIT"

HEADER = (
    "# CITATION.cff — GENERATED. Do not edit.\n"
    "# Source: genefoundry-router/fleet-metadata.yaml + this repo's pyproject.toml.\n"
    "# Regenerate: `make citation-write` in genefoundry-router.\n"
    "# See genefoundry-router/docs/REPO-METADATA-STANDARD-v1.md.\n"
)


def fleet_dir(root: Path = ROOT) -> Path:
    """Find the sibling-checkout directory from a main checkout or a worktree."""
    for candidate in root.parents:
        if (candidate / "genefoundry-router" / "pyproject.toml").is_file():
            return candidate
    return root.parent


FLEET_DIR = fleet_dir()


def repo_slugs() -> list[tuple[str, dict[str, Any]]]:
    """(slug, entry) for the router and every enabled backend."""
    data = load()
    reg = yaml.safe_load((ROOT / "servers.yaml").read_text(encoding="utf-8"))
    by_ns = {s["namespace"]: s for s in reg["servers"]}

    out: list[tuple[str, dict[str, Any]]] = [(data["router"]["repo"].split("/")[1], data["router"])]
    for b in data["backends"]:
        out.append((by_ns[b["namespace"]]["repo"].split("/")[1], b))
    return out


def pyproject_version(slug: str) -> str:
    pp = tomllib.loads((FLEET_DIR / slug / "pyproject.toml").read_bytes().decode())
    return str(pp["project"]["version"])


def released_at(slug: str) -> str | None:
    proc = subprocess.run(
        ["gh", "api", f"repos/{OWNER}/{slug}/releases/latest", "--jq", ".published_at"],
        capture_output=True,
        text=True,
    )
    date = proc.stdout.strip()[:10]
    return date if proc.returncode == 0 and len(date) == 10 else None


def render(slug: str, entry: dict[str, Any], data: dict[str, Any], date: str | None) -> str:
    uni = data["universal"]
    n = len([b for b in data["backends"]])  # only used by the router's templated description
    abstract = " ".join(entry["description"].split()).format(n=n)

    cff: dict[str, Any] = {
        "cff-version": "1.2.0",
        "message": data["citation"]["message"],
        "title": slug,
        "abstract": abstract,
        "type": "software",
        "authors": data["citation"]["authors"],
        "version": pyproject_version(slug),
        "license": LICENSE,
        "repository-code": f"https://github.com/{OWNER}/{slug}",
        "url": uni["homepage"],
        "keywords": sorted(set(uni["topics"]) | set(entry["topics"])),
    }
    if date:
        cff["date-released"] = date

    body = yaml.safe_dump(cff, sort_keys=False, allow_unicode=True, width=100)
    return HEADER + body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="fail if any CITATION.cff is stale")
    g.add_argument("--write", action="store_true", help="(re)write every CITATION.cff")
    args = ap.parse_args()

    data = load()
    stale: list[str] = []
    missing_checkout: list[str] = []

    for slug, entry in repo_slugs():
        repo_dir = FLEET_DIR / slug
        if not (repo_dir / "pyproject.toml").exists():
            missing_checkout.append(slug)
            continue
        target = repo_dir / "CITATION.cff"

        if args.write:
            text = render(slug, entry, data, released_at(slug))
            target.write_text(text, encoding="utf-8")
            print(f"  wrote {slug}/CITATION.cff")
            continue

        # --check: compare everything except date-released, which is release-driven and
        # would otherwise make the gate depend on the network.
        if not target.exists():
            stale.append(f"{slug}: CITATION.cff is missing")
            continue
        current = yaml.safe_load(target.read_text(encoding="utf-8"))
        want = yaml.safe_load(render(slug, entry, data, current.get("date-released")))
        if current != want:
            diffs = [k for k in want if current.get(k) != want[k]] + [
                k for k in current if k not in want
            ]
            stale.append(f"{slug}: CITATION.cff is stale ({', '.join(sorted(set(diffs)))})")

    if missing_checkout:
        print(
            f"note: {len(missing_checkout)} repo(s) not cloned locally, skipped: "
            f"{', '.join(missing_checkout)}",
            file=sys.stderr,
        )

    if args.check:
        if stale:
            print(f"{len(stale)} CITATION.cff file(s) out of date:\n", file=sys.stderr)
            for s in stale:
                print(f"  - {s}", file=sys.stderr)
            print("\nRun `make citation-write`.", file=sys.stderr)
            return 1
        print("CITATION.cff: all repos current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
