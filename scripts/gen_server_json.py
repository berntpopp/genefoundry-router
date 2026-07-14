#!/usr/bin/env python
"""Generate the router's ``server.json`` — the official MCP Registry manifest.

The registry (registry.modelcontextprotocol.io) is the hub MCP directories poll; its own
docs tell aggregators to scrape ``GET /v0.1/servers`` hourly. Glama, Smithery and
awesome-mcp-servers are otherwise submission-driven and do not crawl GitHub by topic.
Publishing here is how the fleet gets *listed*, as opposed to merely *searchable*.

**Only the router is published, and that is a security boundary.** A ``remotes`` entry
must be publicly reachable; the 21 backends are unauthenticated by design and reachable
only behind the router (AGENTS.md). Publishing one as a remote would expose an
unauthenticated server to the internet.

Derived from fleet-metadata.yaml (name, endpoint, 100-char description) + pyproject
(version). Never typed.

    python scripts/gen_server_json.py            # rewrite server.json
    python scripts/gen_server_json.py --check    # fail if server.json is stale
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_fleet_metadata import enabled_namespaces, load  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "server.json"

# From the live schema (definitions/ServerDetail), verified 2026-07-14.
DESCRIPTION_MAX = 100
NAME_RE = re.compile(r"^[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+$")


def render() -> dict[str, Any]:
    data = load()
    reg = data["registry"]
    n = len(enabled_namespaces())

    description = " ".join(reg["description"].split()).format(n=n)
    if len(description) > DESCRIPTION_MAX:
        raise SystemExit(
            f"error: registry description is {len(description)} chars; the MCP Registry "
            f"schema caps it at {DESCRIPTION_MAX}. It is a separate, shorter copy than the "
            f"GitHub About-box description — shorten it in fleet-metadata.yaml."
        )
    if not NAME_RE.match(reg["name"]):
        raise SystemExit(f"error: registry name {reg['name']!r} violates the schema pattern")

    pp = tomllib.loads((ROOT / "pyproject.toml").read_bytes().decode())
    version = pp["project"]["version"]
    repo = data["router"]["repo"]

    return {
        "$schema": reg["schema"],
        "name": reg["name"],
        "description": description,
        "version": version,
        "websiteUrl": data["universal"]["homepage"],
        "repository": {
            "url": f"https://github.com/{repo}",
            "source": "github",
        },
        # streamable-http only. The fleet does not offer SSE (AGENTS.md).
        "remotes": [
            {
                "type": "streamable-http",
                "url": reg["endpoint"],
            }
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if server.json is stale")
    args = ap.parse_args()

    want = json.dumps(render(), indent=2) + "\n"

    if args.check:
        if not TARGET.exists():
            print("error: server.json is missing. Run `make server-json`.", file=sys.stderr)
            return 1
        if TARGET.read_text(encoding="utf-8") != want:
            print(
                "error: server.json is stale (it is generated from fleet-metadata.yaml + "
                "pyproject.toml). Run `make server-json`.",
                file=sys.stderr,
            )
            return 1
        print("server.json: current")
        return 0

    TARGET.write_text(want, encoding="utf-8")
    print(f"wrote {TARGET.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
