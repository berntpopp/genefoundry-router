"""Refresh tests/fixtures/fleet_manifest.json from live (or local) backends.

Online, on-demand only — never run in tests. Ordinary fixture refreshes may retain a
prior entry, but a reviewed release-candidate capture fails closed if any required
backend cannot be harvested.
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


class ReleaseCandidateCaptureError(RuntimeError):
    """A required backend could not be included in a reviewed release capture."""


def merge_backend(
    prior: BackendSpec | None, fresh: BackendSpec | None, *, release_candidate: bool = False
) -> BackendSpec | None:
    """Prefer fresh data; a release candidate never silently retains stale data."""
    if release_candidate and fresh is None:
        raise ReleaseCandidateCaptureError("required release-candidate backend was unreachable")
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


async def _run(
    servers_file: str, out: Path, captured_at: str, release_candidate: str | None = None
) -> None:
    prior = load_manifest(out) if out.exists() else None
    registry = [b for b in load_registry(servers_file, os.environ) if b.enabled]
    backends: dict[str, BackendSpec] = {}
    for b in registry:
        fresh = await _snapshot_backend(b.url) if b.url else None
        prior_spec = prior.backends.get(b.namespace) if prior else None
        merged = merge_backend(prior_spec, fresh, release_candidate=release_candidate is not None)
        if merged is not None:
            backends[b.namespace] = merged
    manifest = Manifest(
        snapshot_meta=SnapshotMeta(
            captured_at=captured_at,
            source="release-candidate" if release_candidate else "live",
            router_servers_file=servers_file,
            release_candidate=release_candidate,
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
    parser.add_argument(
        "--release-candidate",
        help="Reviewed candidate identity; fail instead of retaining stale unreachable backends.",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.servers_file, Path(args.out), args.captured_at, args.release_candidate))


if __name__ == "__main__":
    main()
