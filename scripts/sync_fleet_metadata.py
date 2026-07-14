#!/usr/bin/env python
"""Sync ``fleet-metadata.yaml`` to the fleet's GitHub "About" boxes.

ONLINE. Needs an authenticated ``gh``. Deliberately NOT in ``make ci-local`` — a check
that needs the network belongs on a schedule, not in the local loop. That is the same
CI-vs-prod lesson the fleet already learned with ``fleet-probe``.

    python scripts/sync_fleet_metadata.py --check    # report drift; exit 1 if any
    python scripts/sync_fleet_metadata.py --apply    # push the file to GitHub

``--check`` is the drift gate: GitHub's UI lets anyone hand-edit a description, and a
hand-edit is how the fleet's metadata rotted in the first place. The file wins.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_fleet_metadata import enabled_namespaces, load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def gh(*args: str) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout


def repo_for(namespace: str) -> str:
    import yaml

    reg = yaml.safe_load((ROOT / "servers.yaml").read_text(encoding="utf-8"))
    for s in reg["servers"]:
        if s["namespace"] == namespace:
            return str(s["repo"])
    raise SystemExit(f"namespace {namespace!r} not found in servers.yaml")


def desired() -> list[dict[str, object]]:
    """Resolve the file into the exact metadata each repo should carry."""
    data = load()
    uni = data["universal"]
    uni_topics: list[str] = uni["topics"]
    homepage: str = uni["homepage"]
    n = len(enabled_namespaces())

    out: list[dict[str, object]] = [
        {
            "repo": data["router"]["repo"],
            "description": " ".join(data["router"]["description"].split()).format(n=n),
            # Topics are a SET on GitHub, but we sort for a stable diff.
            "topics": sorted(set(uni_topics) | set(data["router"]["topics"])),
            "homepage": homepage,
        }
    ]
    for b in data["backends"]:
        out.append(
            {
                "repo": repo_for(b["namespace"]),
                "description": " ".join(b["description"].split()),
                "topics": sorted(set(uni_topics) | set(b["topics"])),
                "homepage": homepage,
            }
        )
    return out


def live(repo: str) -> dict[str, object]:
    meta = json.loads(gh("api", f"repos/{repo}"))
    topics = json.loads(gh("api", f"repos/{repo}/topics")).get("names", [])
    return {
        "description": meta.get("description") or "",
        "homepage": meta.get("homepage") or "",
        "topics": sorted(topics),
    }


def apply_one(spec: dict[str, object]) -> None:
    repo = str(spec["repo"])
    gh(
        "api",
        "-X",
        "PATCH",
        f"repos/{repo}",
        "-f",
        f"description={spec['description']}",
        "-f",
        f"homepage={spec['homepage']}",
        "--silent",
    )
    args = ["api", "-X", "PUT", f"repos/{repo}/topics"]
    for t in spec["topics"]:  # type: ignore[union-attr]
        args += ["-f", f"names[]={t}"]
    gh(*args, "--silent")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="report drift, change nothing")
    g.add_argument("--apply", action="store_true", help="push fleet-metadata.yaml to GitHub")
    args = ap.parse_args()

    drifted = 0
    for spec in desired():
        repo = str(spec["repo"])
        cur = live(repo)
        diffs = [
            f"{field}:\n      live: {cur[field]!r}\n      file: {spec[field]!r}"
            for field in ("description", "homepage", "topics")
            if cur[field] != spec[field]
        ]
        if not diffs:
            print(f"  ok    {repo}")
            continue
        drifted += 1
        if args.check:
            print(f"  DRIFT {repo}")
            for d in diffs:
                print(f"    - {d}")
        else:
            apply_one(spec)
            print(f"  SET   {repo}  ({len(diffs)} field(s))")

    if args.check:
        if drifted:
            print(
                f"\n{drifted} repo(s) drifted from fleet-metadata.yaml. "
                f"Run `make metadata-apply` (the file wins).",
                file=sys.stderr,
            )
            return 1
        print("\nno drift — live GitHub matches fleet-metadata.yaml")
    else:
        print(f"\napplied: {drifted} repo(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
