"""Regenerate the discoverability benchmark catalog (tests/discoverability/catalog.json).

The benchmark scores realistic tasks against a frozen snapshot of the real federated
catalog. That snapshot is a committed fixture; until now it was hand-frozen (2026-06-18)
with no regenerator, so it silently went stale when the fleet grew — hpo/mavedb/metadome/
orphanet were added afterward and had 0 tools in it. This script makes the snapshot
reproducible: it builds the router exactly as production does (namespaced, search DISABLED
so the full catalog is visible) and captures, per tool, the fields FastMCP's BM25 index
reads — name, description, and per-parameter descriptions. Online; needs GF_*_URL in env:

    uv run --env-file ci/fleet-urls.env python scripts/snapshot_catalog.py

Faithfulness note: tags are captured as the router surfaces them here (historically empty,
because tag injection is a normalization-pass concern); keeping the same basis means the
benchmark score stays comparable to the prior bar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastmcp import Client

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.server import build_server

DEFAULT_OUT = Path("tests/discoverability/catalog.json")


def _entry(tool: Any) -> dict[str, Any]:
    schema = tool.inputSchema or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    params = {name: (spec.get("description") or "") for name, spec in props.items()}
    tags = sorted((tool.meta or {}).get("fastmcp", {}).get("tags", []))
    return {
        "name": tool.name,
        "description": tool.description or "",
        "tags": tags,
        "params": params,
    }


async def _run(servers_file: str, out: Path) -> None:
    settings = RouterSettings(_env_file=None, GF_SERVERS_FILE=servers_file)
    registry = load_registry(servers_file, os.environ)
    # enable_search=False → the BM25 transform is not applied, so list_tools() returns the
    # FULL federated catalog (post-namespace), which is exactly what the index is built over.
    server = build_server(settings, registry, enable_search=False)
    async with Client(server) as client:
        tools = await client.list_tools()
    entries = sorted((_entry(t) for t in tools), key=lambda e: e["name"])
    out.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    namespaces = sorted({e["name"].split("_", 1)[0] for e in entries if "_" in e["name"]})
    print(f"wrote {out} ({len(entries)} tools across {len(namespaces)} namespaces)")
    print("namespaces:", ", ".join(namespaces))


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate the discoverability catalog snapshot.")
    parser.add_argument("--servers-file", default="servers.yaml")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    asyncio.run(_run(args.servers_file, Path(args.out)))


if __name__ == "__main__":
    main()
