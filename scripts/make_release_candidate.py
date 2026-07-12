"""Author a reviewed release-candidate inventory from the live fleet.

This is the release-engineering step that produces ``ci/release-candidate-inventory.json``.
It records, per backend, the identity of what is actually deployed:

  endpoint            the exact HTTPS /mcp URL that was probed
  revision            the 40-hex commit the running image was built from
  version             the package version the backend reports in its MCP handshake
  definitions_sha256  an attestation of that backend's raw tool definitions

``make snapshot-baseline`` then re-probes the fleet and FAILS CLOSED if any live definition
digest disagrees with what this wrote, so the pin and the inventory cannot drift apart.

Revisions are supplied by the deploy side, which is the only thing that knows them: the
images are built from a source checkout, not pulled from a registry, so there is no digest
to read back. On the GeneFoundry VPS, `make attest` in the strato repo verifies that each
running image's `org.opencontainers.image.revision` label matches its checkout, and emits
the map this script consumes:

    python scripts/manage.py attest --json > revisions.json     # strato repo
    make release-candidate REVISIONS=revisions.json IDENTITY=fleet-2026-07-12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, "scripts")

from genefoundry_router.config import load_registry  # noqa: E402
from snapshot_fleet import (  # noqa: E402
    ReleaseCandidateCaptureError,
    _snapshot_backend,
    backend_definitions_digest,
)

_COMMIT_LEN = 40


async def _run(servers_file: str, identity: str, revisions: dict[str, str], out: Path) -> None:
    registry = [backend for backend in load_registry(servers_file, os.environ) if backend.enabled]

    backends: dict[str, dict[str, str]] = {}
    problems: list[str] = []

    for backend in registry:
        namespace = backend.namespace
        revision = revisions.get(namespace, "")
        if len(revision) != _COMMIT_LEN:
            problems.append(f"{namespace}: no 40-hex revision supplied")
            continue
        if not backend.url:
            problems.append(f"{namespace}: no URL configured ({backend.url_env})")
            continue

        spec = await _snapshot_backend(backend.url, backend.service_token)
        if spec is None:
            problems.append(f"{namespace}: unreachable at {backend.url}")
            continue

        backends[namespace] = {
            "endpoint": backend.url,
            "revision": revision,
            "version": spec.version or "unknown",
            "definitions_sha256": backend_definitions_digest(spec),
        }
        print(f"  {namespace:<14} v{spec.version or '?':<10} {revision[:12]}  {len(spec.tools):>3} tools")

    if problems:
        # A release candidate never records a partial fleet: a backend missing here would be
        # silently excluded from the drift diff and stop being watched at all.
        for problem in problems:
            print(f"  FAIL {problem}")
        raise ReleaseCandidateCaptureError(f"incomplete fleet capture: {len(problems)} backend(s)")

    out.write_text(
        json.dumps({"identity": identity, "backends": backends}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out} ({len(backends)} backends) identity={identity}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servers-file", default="servers.yaml")
    parser.add_argument("--identity", required=True, help="Reviewed release identity.")
    parser.add_argument(
        "--revisions",
        type=Path,
        required=True,
        help="JSON map of namespace -> 40-hex deployed commit (from the deploy side).",
    )
    parser.add_argument("--out", type=Path, default=Path("ci/release-candidate-inventory.json"))
    args = parser.parse_args()

    revisions = json.loads(args.revisions.read_text(encoding="utf-8"))
    asyncio.run(_run(args.servers_file, args.identity, revisions, args.out))


if __name__ == "__main__":
    main()
