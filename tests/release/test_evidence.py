"""Tests for deterministic application-release evidence assembly."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from genefoundry_router.release.definitions import (
    DefinitionEvidence,
    capture_definitions,
    verify_definition_contract,
)
from genefoundry_router.release.evidence import (
    ApplicationIdentity,
    EvidenceAssemblyError,
    ReleaseAsset,
    ScannerIdentity,
    assemble_application_release_manifest,
    canonical_json_bytes,
    hash_release_assets,
    manifest_json_bytes,
    sha256_file,
    write_json_atomic,
)


def _definition_evidence() -> DefinitionEvidence:
    tools: list[dict[str, object]] = [
        {
            "name": "get_capabilities",
            "description": "Describe the server.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "outputSchema": None,
            "annotations": {"readOnlyHint": True},
            "execution": None,
        }
    ]
    first = capture_definitions(tools, context={"fixture": "empty", "rows": 0})
    second = capture_definitions(tools, context={"fixture": "populated", "rows": 1})
    return verify_definition_contract("data-independent", (first, second))


def _observed_definition_evidence() -> DefinitionEvidence:
    observed = {
        "release_tag": "data-clingen-2026-07-16",
        "digest": "sha256:" + "a" * 64,
    }
    tools: list[dict[str, object]] = [
        {
            "name": "get_capabilities",
            "description": "Describe the server.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "outputSchema": None,
            "annotations": {"readOnlyHint": True},
            "execution": None,
        }
    ]
    capture = capture_definitions(
        tools,
        context={"runtime": "published"},
        observed_identity=observed,
    )
    return verify_definition_contract("data-bound", (capture,), observed_identity=observed)


def _asset_files(tmp_path: Path) -> tuple[list[ReleaseAsset], DefinitionEvidence, str]:
    definitions = _definition_evidence()
    payloads: dict[str, object] = {
        "image-manifest.json": {"schemaVersion": 2, "config": {"digest": "sha256:config"}},
        "sbom.spdx.json": {"spdxVersion": "SPDX-2.3", "packages": []},
        "mcp-definitions.json": definitions.definitions_document,
        "mcp-capture-context.json": definitions.context_document,
        "trivy.json": {"SchemaVersion": 2, "Results": []},
        "attestation-bundle.json": {"version": 1, "attestations": []},
        "trusted-root.json": {
            "mediaType": "application/vnd.dev.sigstore.trustedroot+json;version=0.1"
        },
        "verification.json": {"verified": True, "signer": "router"},
    }
    assets: list[ReleaseAsset] = []
    for name, payload in payloads.items():
        path = tmp_path / name
        write_json_atomic(path, payload)
        assets.append(ReleaseAsset(name=name, path=path))
    image_digest = f"sha256:{sha256_file(tmp_path / 'image-manifest.json')}"
    return assets, definitions, image_digest


def _identity(image_digest: str) -> ApplicationIdentity:
    return ApplicationIdentity(
        repository="berntpopp/genefoundry-router",
        version="0.6.4",
        source_tag="v0.6.4",
        source_revision="c" * 40,
        image_name="ghcr.io/berntpopp/genefoundry-router",
        image_digest=image_digest,
        workflow_caller=("berntpopp/genefoundry-router/.github/workflows/container-release.yml"),
        workflow_standard=("berntpopp/genefoundry-router/.github/workflows/_container-release.yml"),
        workflow_revision="d" * 40,
    )


def test_canonical_json_hash_is_key_order_independent() -> None:
    first = {"outer": {"b": 2, "a": 1}, "items": [3, 2, 1]}
    second = {"items": [3, 2, 1], "outer": {"a": 1, "b": 2}}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(first) == b'{"items":[3,2,1],"outer":{"a":1,"b":2}}'


def test_sha256_file_hashes_binary_content_in_chunks(tmp_path: Path) -> None:
    content = bytes(range(256)) * 9000
    path = tmp_path / "large.bin"
    path.write_bytes(content)

    assert sha256_file(path, chunk_size=4096) == hashlib.sha256(content).hexdigest()


def test_release_asset_hashing_rejects_duplicate_names(tmp_path: Path) -> None:
    path = tmp_path / "one.json"
    path.write_bytes(b"{}")

    with pytest.raises(EvidenceAssemblyError, match="duplicate release asset"):
        hash_release_assets(
            (
                ReleaseAsset(name="trivy.json", path=path),
                ReleaseAsset(name="trivy.json", path=path),
            )
        )


def test_complete_release_evidence_is_assembled_from_asset_bytes(tmp_path: Path) -> None:
    assets, definitions, image_digest = _asset_files(tmp_path)

    manifest = assemble_application_release_manifest(
        identity=_identity(image_digest),
        definitions=definitions,
        scanner=ScannerIdentity(
            version="0.66.0",
            database_updated_at="2026-07-13T10:30:00Z",
        ),
        data_requirements={"mode": "none", "schema_compatibility": []},
        assets=reversed(assets),
    )

    assert manifest.source.revision == "c" * 40
    assert manifest.image.digest == image_digest
    assert manifest.workflow.standard_revision == "d" * 40
    assert manifest.security_evidence.sbom_sha256 == sha256_file(tmp_path / "sbom.spdx.json")
    assert manifest.security_evidence.scanner_evidence_sha256 == sha256_file(
        tmp_path / "trivy.json"
    )
    assert manifest.security_evidence.attestation_bundle_sha256 == sha256_file(
        tmp_path / "attestation-bundle.json"
    )
    assert manifest.release_assets.model_dump(mode="json") == {
        name: f"sha256:{sha256_file(tmp_path / name)}"
        for name in sorted(asset.name for asset in assets)
    }


def test_manifest_assembly_requires_complete_standard_asset_set(tmp_path: Path) -> None:
    assets, definitions, image_digest = _asset_files(tmp_path)

    with pytest.raises(EvidenceAssemblyError, match="release asset set"):
        assemble_application_release_manifest(
            identity=_identity(image_digest),
            definitions=definitions,
            scanner=ScannerIdentity(
                version="0.66.0",
                database_updated_at="2026-07-13T10:30:00Z",
            ),
            data_requirements={"mode": "none", "schema_compatibility": []},
            assets=assets[:-1],
        )


def test_manifest_assembly_rejects_definition_asset_tampering(tmp_path: Path) -> None:
    assets, definitions, image_digest = _asset_files(tmp_path)
    (tmp_path / "mcp-definitions.json").write_bytes(b"{}")

    with pytest.raises(EvidenceAssemblyError, match="definition evidence"):
        assemble_application_release_manifest(
            identity=_identity(image_digest),
            definitions=definitions,
            scanner=ScannerIdentity(
                version="0.66.0",
                database_updated_at="2026-07-13T10:30:00Z",
            ),
            data_requirements={"mode": "none", "schema_compatibility": []},
            assets=assets,
        )


def test_manifest_assembly_revalidates_definition_contract_document(tmp_path: Path) -> None:
    assets, definitions, image_digest = _asset_files(tmp_path)
    captures = definitions.context_document["captures"]
    assert isinstance(captures, list)
    second = captures[1]
    assert isinstance(second, dict)
    second["definitions_sha256"] = "f" * 64
    write_json_atomic(tmp_path / "mcp-capture-context.json", definitions.context_document)
    forged = replace(
        definitions,
        capture_context_sha256=sha256_file(tmp_path / "mcp-capture-context.json"),
    )

    with pytest.raises(EvidenceAssemblyError, match="definition contract"):
        assemble_application_release_manifest(
            identity=_identity(image_digest),
            definitions=forged,
            scanner=ScannerIdentity(
                version="0.66.0",
                database_updated_at="2026-07-13T10:30:00Z",
            ),
            data_requirements={"mode": "none", "schema_compatibility": []},
            assets=assets,
        )


def test_manifest_only_identity_change_cannot_rewrite_observed_capture(tmp_path: Path) -> None:
    assets, _, image_digest = _asset_files(tmp_path)
    definitions = _observed_definition_evidence()
    write_json_atomic(tmp_path / "mcp-definitions.json", definitions.definitions_document)
    write_json_atomic(tmp_path / "mcp-capture-context.json", definitions.context_document)

    with pytest.raises(EvidenceAssemblyError, match="manifest data identity"):
        assemble_application_release_manifest(
            identity=_identity(image_digest),
            definitions=definitions,
            scanner=ScannerIdentity(
                version="0.66.0",
                database_updated_at="2026-07-13T10:30:00Z",
            ),
            data_requirements={
                "mode": "external-reference",
                "release_tag": "data-clingen-2026-07-17",
                "digest": "sha256:" + "b" * 64,
                "reproducible_rollback": False,
                "schema_compatibility": [],
            },
            assets=assets,
        )


def test_manifest_bytes_and_atomic_write_are_deterministic(tmp_path: Path) -> None:
    assets, definitions, image_digest = _asset_files(tmp_path)
    manifest = assemble_application_release_manifest(
        identity=_identity(image_digest),
        definitions=definitions,
        scanner=ScannerIdentity(
            version="0.66.0",
            database_updated_at="2026-07-13T10:30:00Z",
        ),
        data_requirements={"mode": "none", "schema_compatibility": []},
        assets=assets,
    )

    first = manifest_json_bytes(manifest)
    second = manifest_json_bytes(manifest)
    path = tmp_path / "application-release-manifest.json"
    write_json_atomic(path, manifest.model_dump(mode="json"))

    assert first == second == path.read_bytes()
    assert json.loads(first) == manifest.model_dump(mode="json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert not list(tmp_path.glob(".application-release-manifest.json.*"))
