"""Author a reviewed release-candidate inventory from the live fleet.

This is the release-engineering step that produces ``ci/release-candidate-inventory.json``.
It records, per backend, the identity of what is actually deployed:

  endpoint            the exact HTTPS /mcp URL that was probed
  application_release the verified immutable image/source/workflow/security/data tuple

``make snapshot-baseline`` then re-probes the fleet and FAILS CLOSED if any live definition
digest disagrees with what this wrote, so the pin and the inventory cannot drift apart.

The input is ``{"backends": {...}}``: verified immutable backend application-release
manifests produced by protected release workflows. Router provenance comes from its protected
release manifest and Strato lock/runtime attestation, not recursive candidate data. Bare revision
maps are intentionally unsupported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from pydantic import ValidationError

from genefoundry_router.config import load_registry
from genefoundry_router.release.models import ApplicationReleaseManifest
from scripts.snapshot_fleet import (
    ReleaseCandidateCaptureError,
    _capture_backend,
    release_definitions_digest,
    validate_release_candidate_inventory,
)


def _parse_manifest(value: object, label: str) -> dict[str, object]:
    try:
        manifest = ApplicationReleaseManifest.model_validate(value)
    except ValidationError as exc:
        raise ReleaseCandidateCaptureError(
            f"invalid application release manifest for {label}"
        ) from exc
    return manifest.model_dump(mode="json")


def _load_application_releases(path: Path) -> dict[str, object]:
    """Load one complete backend application-release manifest set."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateCaptureError(f"invalid application release set: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != {"backends"}:
        raise ReleaseCandidateCaptureError("application release set requires backends only")
    backends = payload.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise ReleaseCandidateCaptureError("application release set requires backends")
    normalized: dict[str, dict[str, object]] = {}
    for namespace, manifest in backends.items():
        if not isinstance(namespace, str):
            raise ReleaseCandidateCaptureError("application release namespace must be a string")
        normalized[namespace] = _parse_manifest(manifest, namespace)
    return {"backends": normalized}


async def _run(
    servers_file: str,
    identity: str,
    releases: dict[str, object],
    out: Path,
) -> None:
    registry = [backend for backend in load_registry(servers_file, os.environ) if backend.enabled]
    release_backends = releases["backends"]
    assert isinstance(release_backends, dict)
    enabled = {backend.namespace for backend in registry}
    if set(release_backends) != enabled:
        raise ReleaseCandidateCaptureError(
            "application release set must cover exactly the enabled registry backends"
        )

    backends: dict[str, dict[str, object]] = {}
    problems: list[str] = []

    for backend in registry:
        namespace = backend.namespace
        release = release_backends[namespace]
        assert isinstance(release, dict)
        if backend.repo is None or release["repository"].lower() != backend.repo.lower():
            problems.append(f"{namespace}: application release repository mismatch")
            continue
        if not backend.url:
            problems.append(f"{namespace}: no URL configured ({backend.url_env})")
            continue

        captured = await _capture_backend(backend.url, backend.service_token)
        if captured is None:
            problems.append(f"{namespace}: unreachable at {backend.url}")
            continue
        spec, raw_tools = captured
        if spec.version != release["version"]:
            problems.append(f"{namespace}: live version disagrees with application release")
            continue
        # Compare like with like: the manifest's digest is the release canonicalization of the
        # payload the backend put on the wire, not the drift baseline's ToolSpec projection.
        definitions_sha256 = release_definitions_digest(raw_tools)
        release_mcp = release["mcp"]
        assert isinstance(release_mcp, dict)
        if definitions_sha256 != release_mcp["definitions_sha256"]:
            problems.append(f"{namespace}: live definitions disagree with application release")
            continue

        backends[namespace] = {
            "endpoint": backend.url,
            "application_release": release,
        }
        print(
            f"  {namespace:<14} v{spec.version or '?':<10} "
            f"{release['source']['revision'][:12]}  {len(spec.tools):>3} tools"
        )

    if problems:
        # A release candidate never records a partial fleet: a backend missing here would be
        # silently excluded from the drift diff and stop being watched at all.
        for problem in problems:
            print(f"  FAIL {problem}")
        raise ReleaseCandidateCaptureError(f"incomplete fleet capture: {len(problems)} backend(s)")

    candidate = validate_release_candidate_inventory({"identity": identity, "backends": backends})
    out.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\nwrote {out} ({len(backends)} backends) identity={identity}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servers-file", default="servers.yaml")
    parser.add_argument("--identity", required=True, help="Reviewed release identity.")
    parser.add_argument(
        "--release-manifests",
        type=Path,
        required=True,
        help="JSON document containing verified backend application release manifests.",
    )
    parser.add_argument("--out", type=Path, default=Path("ci/release-candidate-inventory.json"))
    args = parser.parse_args()

    releases = _load_application_releases(args.release_manifests)
    asyncio.run(_run(args.servers_file, args.identity, releases, args.out))


if __name__ == "__main__":
    main()
