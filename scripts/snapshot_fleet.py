"""Refresh tests/fixtures/fleet_manifest.json from live (or local) backends.

Online, on-demand only — never run in tests. Ordinary fixture refreshes may retain a
prior entry, but a reviewed release-candidate capture fails closed if any required
backend cannot be harvested.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fakes import (
    BackendSpec,
    Manifest,
    SnapshotMeta,
    ToolSpec,
    load_manifest,
)
from genefoundry_router.drift import canonical_json_schema
from genefoundry_router.release.models import ApplicationReleaseManifest


class ReleaseCandidateCaptureError(RuntimeError):
    """A required backend could not be included in a reviewed release capture."""


def backend_definitions_digest(spec: BackendSpec) -> str:
    """Return the canonical attestation digest for one normalized backend definition set."""
    canonical = json.dumps(
        spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validated_release_manifest(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseCandidateCaptureError(f"{label} requires an application release manifest")
    try:
        manifest = ApplicationReleaseManifest.model_validate(value)
    except ValidationError as exc:
        raise ReleaseCandidateCaptureError(
            f"invalid application release manifest for {label}"
        ) from exc
    return manifest.model_dump(mode="json")


def validate_release_candidate_inventory(inventory: object) -> dict[str, Any]:
    """Validate one atomic router/backend inventory bound to release manifests."""
    if not isinstance(inventory, dict) or not isinstance(inventory.get("identity"), str):
        raise ReleaseCandidateCaptureError("release-candidate inventory requires an identity")
    identity = inventory["identity"]
    if not identity.strip():
        raise ReleaseCandidateCaptureError("release-candidate inventory requires an identity")
    router = _validated_release_manifest(
        inventory.get("router"), label="router application release"
    )
    if router["repository"] != "berntpopp/genefoundry-router":
        raise ReleaseCandidateCaptureError("router application release has a repository mismatch")
    backends = inventory.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise ReleaseCandidateCaptureError("release-candidate inventory requires backends")
    normalized: dict[str, dict[str, Any]] = {}
    for namespace, entry in backends.items():
        if not isinstance(namespace, str) or not isinstance(entry, dict):
            raise ReleaseCandidateCaptureError(
                "release-candidate inventory has invalid backend entry"
            )
        if set(entry) != {"endpoint", "application_release"}:
            raise ReleaseCandidateCaptureError(
                f"release-candidate backend {namespace} requires endpoint and application release"
            )
        endpoint = entry["endpoint"]
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise ReleaseCandidateCaptureError("release-candidate endpoint must be an HTTPS URL")
        normalized[namespace] = {
            "endpoint": endpoint,
            "application_release": _validated_release_manifest(
                entry["application_release"], label=namespace
            ),
        }
    return {"identity": identity, "router": router, "backends": normalized}


def load_release_candidate_inventory(path: Path) -> dict[str, Any]:
    """Load immutable image/source/data/definition provenance for a candidate fleet."""
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateCaptureError(f"invalid release-candidate inventory: {path}") from exc
    return validate_release_candidate_inventory(inventory)


def merge_backend(
    prior: BackendSpec | None, fresh: BackendSpec | None, *, release_candidate: bool = False
) -> BackendSpec | None:
    """Prefer fresh data; a release candidate never silently retains stale data."""
    if release_candidate and fresh is None:
        raise ReleaseCandidateCaptureError("required release-candidate backend was unreachable")
    return fresh if fresh is not None else prior


async def _snapshot_backend(url: str, service_token: str | None = None) -> BackendSpec | None:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    # A backend that requires the router's service credential (pubtator) answers /mcp with
    # 401 to an anonymous probe, which a release-candidate capture reads as "unreachable"
    # and fails closed on. Present the same credential the router uses at runtime.
    target: Any = url
    if service_token:
        target = StreamableHttpTransport(url, headers={"Authorization": f"Bearer {service_token}"})

    try:
        async with Client(target) as client:
            tools = await client.list_tools()
            version = None
            init = getattr(client, "initialize_result", None)
            if init is not None and getattr(init, "serverInfo", None) is not None:
                version = init.serverInfo.version  # MCP initialize handshake
        specs = [
            ToolSpec(
                name=t.name,
                description=t.description or "",
                inputSchema=canonical_json_schema(
                    t.inputSchema or {"type": "object", "properties": {}}
                ),
                outputSchema=canonical_json_schema(t.outputSchema),
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
    servers_file: str,
    out: Path,
    captured_at: str,
    release_candidate_inventory: dict[str, Any] | None = None,
    normalized: bool = False,
) -> None:
    if release_candidate_inventory is not None:
        release_candidate_inventory = validate_release_candidate_inventory(
            release_candidate_inventory
        )
    prior = load_manifest(out) if out.exists() else None
    registry = [b for b in load_registry(servers_file, os.environ) if b.enabled]
    if release_candidate_inventory is not None:
        candidate_backends = release_candidate_inventory["backends"]
        enabled = {backend.namespace for backend in registry}
        if set(candidate_backends) != enabled:
            raise ReleaseCandidateCaptureError(
                "release-candidate inventory must cover exactly the enabled registry backends"
            )
        for backend in registry:
            expected_repository = backend.repo
            actual_repository = candidate_backends[backend.namespace]["application_release"][
                "repository"
            ]
            if (
                expected_repository is None
                or actual_repository.lower() != expected_repository.lower()
            ):
                raise ReleaseCandidateCaptureError(
                    f"application release repository mismatch for {backend.namespace}"
                )
    backends: dict[str, BackendSpec] = {}
    for b in registry:
        endpoint = (
            release_candidate_inventory["backends"][b.namespace]["endpoint"]
            if release_candidate_inventory is not None
            else b.url
        )
        fresh = await _snapshot_backend(endpoint, b.service_token) if endpoint else None
        if release_candidate_inventory is not None and fresh is not None:
            release = release_candidate_inventory["backends"][b.namespace]["application_release"]
            expected_digest = release["mcp"]["definitions_sha256"]
            if backend_definitions_digest(fresh) != expected_digest:
                raise ReleaseCandidateCaptureError(
                    f"definition attestation mismatch for required release-candidate backend {b.namespace}"
                )
            if fresh.version != release["version"]:
                raise ReleaseCandidateCaptureError(
                    f"version mismatch for required release-candidate backend {b.namespace}"
                )
        prior_spec = prior.backends.get(b.namespace) if prior else None
        merged = merge_backend(
            prior_spec, fresh, release_candidate=release_candidate_inventory is not None
        )
        if merged is not None:
            backends[b.namespace] = merged

    if normalized:
        # Swap in the router's normalized definitions (the ones the runtime guard hashes),
        # keeping each backend's version from the probe above. The raw probe is still what
        # the release-candidate digest attests, so backend provenance is unaffected.
        from genefoundry_router.catalog import capture_normalized_catalog

        catalog, _ = await capture_normalized_catalog(registry)
        for namespace, spec in list(backends.items()):
            tools = catalog.get(namespace)
            if tools is None:
                raise ReleaseCandidateCaptureError(
                    f"backend {namespace} contributed no tools to the normalized catalog"
                )
            backends[namespace] = spec.model_copy(update={"tools": tools})

    manifest = Manifest(
        snapshot_meta=SnapshotMeta(
            captured_at=captured_at,
            source="release-candidate" if release_candidate_inventory else "live",
            router_servers_file=servers_file,
            release_candidate=release_candidate_inventory,
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
        "--candidate-inventory",
        type=Path,
        help="Source-controlled immutable endpoint/revision inventory for a reviewed candidate.",
    )
    parser.add_argument(
        "--normalized",
        action="store_true",
        help=(
            "Pin the router's normalized catalog (what the runtime drift guard hashes) "
            "instead of the raw backend definitions. Required for the runtime baseline."
        ),
    )
    args = parser.parse_args()
    candidate = (
        load_release_candidate_inventory(args.candidate_inventory)
        if args.candidate_inventory is not None
        else None
    )
    asyncio.run(
        _run(args.servers_file, Path(args.out), args.captured_at, candidate, args.normalized)
    )


if __name__ == "__main__":
    main()
