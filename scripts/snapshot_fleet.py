"""Refresh tests/fixtures/fleet_manifest.json from live (or local) backends.

Online, on-demand only — never run in tests. Per-backend resilient: an unreachable
backend keeps its prior manifest entry instead of being clobbered.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fakes import (
    BackendSpec,
    Manifest,
    SnapshotMeta,
    ToolSpec,
    load_manifest,
)


def merge_backend(prior: BackendSpec | None, fresh: BackendSpec | None) -> BackendSpec | None:
    """Prefer a fresh snapshot; fall back to the prior entry when unreachable."""
    return fresh if fresh is not None else prior


async def _snapshot_backend(url: str) -> BackendSpec | None:
    from fastmcp import Client

    try:
        async with Client(url) as client:
            tools = await client.list_tools()
            version = None
            init = getattr(client, "initialize_result", None)
            if init is not None and getattr(init, "serverInfo", None) is not None:
                version = init.serverInfo.version  # MCP initialize handshake
        specs = [
            ToolSpec(
                name=t.name,
                description=t.description or "",
                inputSchema=t.inputSchema or {"type": "object", "properties": {}},
                outputSchema=t.outputSchema,
                annotations=(
                    t.annotations.model_dump(mode="json", exclude_none=False)
                    if t.annotations is not None
                    else None
                ),
                execution=(
                    t.execution.model_dump(mode="json", exclude_none=False)
                    if t.execution is not None
                    else None
                ),
                tags=list((t.meta or {}).get("fastmcp", {}).get("tags", [])),
            )
            for t in tools
        ]
        return BackendSpec(version=version, tools=specs)
    except Exception as exc:  # report + keep prior
        print(f"  WARN unreachable: {url} ({exc})")
        return None


async def _run(servers_file: str, out: Path, captured_at: str) -> None:
    prior = load_manifest(out) if out.exists() else None
    registry = [b for b in load_registry(servers_file, os.environ) if b.enabled and b.url]
    backends: dict[str, BackendSpec] = {}
    for b in registry:
        assert b.url is not None
        fresh = await _snapshot_backend(b.url)
        prior_spec = prior.backends.get(b.namespace) if prior else None
        merged = merge_backend(prior_spec, fresh)
        if merged is not None:
            backends[b.namespace] = merged
    manifest = Manifest(
        snapshot_meta=SnapshotMeta(
            captured_at=captured_at, source="live", router_servers_file=servers_file
        ),
        backends=backends,
    )
    out.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(backends)} backends)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh the fake-fleet manifest.")
    parser.add_argument("--servers-file", default="servers.yaml")
    parser.add_argument("--out", default="genefoundry_router/data/fleet-baseline.json")
    parser.add_argument("--captured-at", required=True, help="ISO timestamp (date -u +%%FT%%TZ)")
    args = parser.parse_args()
    asyncio.run(_run(args.servers_file, Path(args.out), args.captured_at))


if __name__ == "__main__":
    main()
