"""Deterministic hashing and immutable application-release evidence assembly."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from genefoundry_router.release.definitions import (
    DefinitionEvidence,
    DefinitionEvidenceError,
    canonical_json_bytes,
    validate_definition_evidence,
)
from genefoundry_router.release.models import ApplicationReleaseManifest

_STANDARD_ASSETS = frozenset(
    {
        "attestation-bundle.json",
        "image-manifest.json",
        "mcp-capture-context.json",
        "mcp-definitions.json",
        "sbom.spdx.json",
        "trusted-root.json",
        "trivy.json",
        "verification.json",
    }
)


class EvidenceAssemblyError(ValueError):
    """Release assets cannot form one complete immutable evidence record."""


@dataclass(frozen=True)
class ApplicationIdentity:
    """Source, image, and workflow identities fixed before evidence assembly."""

    repository: str
    version: str
    source_tag: str
    source_revision: str
    image_name: str
    image_digest: str
    workflow_caller: str
    workflow_standard: str
    workflow_revision: str


@dataclass(frozen=True)
class ScannerIdentity:
    """Non-file scanner facts recorded beside the hashed Trivy evidence."""

    version: str
    database_updated_at: str


@dataclass(frozen=True)
class ReleaseAsset:
    """One standard release asset and its exact local bytes."""

    name: str
    path: Path


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a regular file in bounded binary chunks."""
    if chunk_size <= 0:
        raise EvidenceAssemblyError("hash chunk size must be positive")
    if path.is_symlink() or not path.is_file():
        raise EvidenceAssemblyError(f"release asset is not a regular file: {path.name}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
    except OSError as exc:
        raise EvidenceAssemblyError(f"unable to hash release asset: {path.name}") from exc
    return digest.hexdigest()


def hash_release_assets(assets: Iterable[ReleaseAsset]) -> dict[str, str]:
    """Hash named assets deterministically and reject ambiguous duplicate names."""
    paths: dict[str, Path] = {}
    for asset in assets:
        if asset.name in paths:
            raise EvidenceAssemblyError(f"duplicate release asset: {asset.name}")
        paths[asset.name] = asset.path
    return {name: sha256_file(paths[name]) for name in sorted(paths)}


def _require_standard_assets(asset_hashes: Mapping[str, str]) -> None:
    names = set(asset_hashes)
    if names != _STANDARD_ASSETS:
        missing = sorted(_STANDARD_ASSETS - names)
        unexpected = sorted(names - _STANDARD_ASSETS)
        raise EvidenceAssemblyError(
            f"release asset set is incomplete or unknown; missing={missing}, unexpected={unexpected}"
        )


def _require_definition_assets(
    asset_hashes: Mapping[str, str], definitions: DefinitionEvidence
) -> None:
    try:
        validate_definition_evidence(definitions)
    except DefinitionEvidenceError as exc:
        raise EvidenceAssemblyError("definition contract evidence is invalid") from exc
    if (
        asset_hashes["mcp-definitions.json"] != definitions.definitions_sha256
        or asset_hashes["mcp-capture-context.json"] != definitions.capture_context_sha256
    ):
        raise EvidenceAssemblyError("definition evidence assets do not match verified capture")


def _require_data_binding(
    definitions: DefinitionEvidence, data_requirements: Mapping[str, object]
) -> None:
    if definitions.definition_contract != "data-bound":
        return
    if definitions.data_identity_contract not in {"unadopted", "runtime-v1"}:
        raise EvidenceAssemblyError(
            "data-bound definition evidence lacks explicit identity provenance"
        )
    identity = definitions.data_identity
    if identity is None or (
        data_requirements.get("release_tag") != identity["release_tag"]
        or data_requirements.get("digest") != identity["digest"]
    ):
        raise EvidenceAssemblyError(
            "data-bound definition evidence must match the exact manifest data identity"
        )


def assemble_application_release_manifest(
    *,
    identity: ApplicationIdentity,
    definitions: DefinitionEvidence,
    scanner: ScannerIdentity,
    data_requirements: Mapping[str, object],
    assets: Iterable[ReleaseAsset],
) -> ApplicationReleaseManifest:
    """Assemble and validate one complete manifest from exact evidence bytes."""
    asset_hashes = hash_release_assets(assets)
    _require_standard_assets(asset_hashes)
    _require_definition_assets(asset_hashes, definitions)
    _require_data_binding(definitions, data_requirements)
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": identity.repository,
        "version": identity.version,
        "source": {"tag": identity.source_tag, "revision": identity.source_revision},
        "image": {
            "name": identity.image_name,
            "digest": identity.image_digest,
            "platforms": [{"platform": "linux/amd64", "digest": identity.image_digest}],
        },
        "workflow": {
            "caller": identity.workflow_caller,
            "standard": identity.workflow_standard,
            "standard_revision": identity.workflow_revision,
        },
        "mcp": {
            "definitions_sha256": definitions.definitions_sha256,
            "capture_context_sha256": definitions.capture_context_sha256,
            "definition_contract": definitions.definition_contract,
        },
        "security_evidence": {
            "scanner": "trivy",
            "scanner_version": scanner.version,
            "database_updated_at": scanner.database_updated_at,
            "sbom_sha256": asset_hashes["sbom.spdx.json"],
            "scanner_evidence_sha256": asset_hashes["trivy.json"],
            "attestation_bundle_sha256": asset_hashes["attestation-bundle.json"],
            "trusted_root_sha256": asset_hashes["trusted-root.json"],
            "verification_sha256": asset_hashes["verification.json"],
        },
        "release_assets": {name: f"sha256:{asset_hashes[name]}" for name in sorted(asset_hashes)},
        "data_requirements": dict(data_requirements),
    }
    try:
        return ApplicationReleaseManifest.model_validate(payload)
    except ValidationError as exc:
        raise EvidenceAssemblyError("assembled application release manifest is invalid") from exc


def manifest_json_bytes(manifest: ApplicationReleaseManifest) -> bytes:
    """Serialize a validated manifest with stable keys and compact separators."""
    return canonical_json_bytes(manifest.model_dump(mode="json"))


def write_json_atomic(path: Path, value: object) -> None:
    """Atomically write deterministic JSON with an exact public evidence mode."""
    payload = canonical_json_bytes(value)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o644)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise EvidenceAssemblyError(f"unable to write evidence file: {path.name}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


__all__ = [
    "ApplicationIdentity",
    "EvidenceAssemblyError",
    "ReleaseAsset",
    "ScannerIdentity",
    "assemble_application_release_manifest",
    "canonical_json_bytes",
    "hash_release_assets",
    "manifest_json_bytes",
    "sha256_file",
    "write_json_atomic",
]
