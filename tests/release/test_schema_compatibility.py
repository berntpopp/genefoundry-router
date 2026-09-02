"""The data schema versions an application build declares it can serve.

A repository declares them under ``data.schema_compatibility`` in
``container-release.json``; the release workflow projects them into the published
manifest's ``data_requirements.schema_compatibility``. The fleet controller's data
attestation binds a data release to one member of that list and refuses an empty one,
so a data-bearing repository that omits the field can never activate a data release.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from genefoundry_router.release.definitions import (
    DefinitionEvidence,
    capture_definitions,
    verify_definition_contract,
)
from genefoundry_router.release.evidence import (
    ApplicationIdentity,
    ReleaseAsset,
    ScannerIdentity,
    application_release_document,
    assemble_application_release_manifest,
    sha256_file,
    write_json_atomic,
)
from genefoundry_router.release.models import ApplicationReleaseManifest, ReleaseConfig

ROOT = Path(__file__).resolve().parents[2]
REUSABLE = ROOT / ".github/workflows/_container-release.yml"
FLEET_IDENTIFIER_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"

EXACT_DATA: dict[str, object] = {
    "release_tag": "data-clingen-2026-07-16",
    "digest": "sha256:" + "a" * 64,
}
DATA_MODES: tuple[dict[str, object], ...] = (
    {"mode": "none"},
    {"mode": "external-reference", **EXACT_DATA},
    {"mode": "restored-database", **EXACT_DATA},
    {"mode": "upstream-live", **EXACT_DATA, "egress_allowlist": ["clingen.example.org"]},
)


@pytest.fixture
def config() -> dict[str, object]:
    """Return a minimal, data-independent release configuration."""
    return {
        "schema_version": 1,
        "service": {
            "compose_files": ["docker/docker-compose.yml"],
            "name": "example-link",
            "container_port": 8000,
            "health_path": "/health",
            "mcp_path": "/mcp",
        },
        "data": {"mode": "none"},
        "definitions": {"contract": "data-independent"},
    }


def _with_data(config: dict[str, object], data: dict[str, object]) -> ReleaseConfig:
    config["data"] = data
    return ReleaseConfig.model_validate(config)


@pytest.mark.parametrize("data", DATA_MODES)
def test_absent_declaration_stays_empty(config: dict[str, object], data: dict[str, object]) -> None:
    parsed = _with_data(config, dict(data))

    assert parsed.data.schema_compatibility == ()


@pytest.mark.parametrize("data", DATA_MODES)
def test_declared_versions_are_accepted(config: dict[str, object], data: dict[str, object]) -> None:
    parsed = _with_data(config, {**data, "schema_compatibility": ["4", "5.1", "schema-6"]})

    assert parsed.data.schema_compatibility == ("4", "5.1", "schema-6")


@pytest.mark.parametrize(
    "declared",
    [
        [""],  # an empty version names nothing
        [">=1,<2"],  # a range expression is not a version the controller can compare
        ["4 "],  # trailing whitespace would silently never compare equal
        [".4"],  # must start alphanumerically, like every fleet identifier
        ["schema/4"],  # path separators are outside the fleet identifier charset
        ["4" * 129],  # an unbounded version
        [4],  # not a string
        ["4"] * 33,  # an unbounded list
        "4",  # not a list
    ],
)
def test_invalid_versions_are_refused(config: dict[str, object], declared: object) -> None:
    with pytest.raises(ValidationError):
        _with_data(config, {"mode": "none", "schema_compatibility": declared})


def _definitions() -> DefinitionEvidence:
    tools = [
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


def _assemble(tmp_path: Path, data_requirements: dict[str, object]) -> dict[str, object]:
    definitions = _definitions()
    payloads: dict[str, object] = {
        "image-manifest.json": {"schemaVersion": 2},
        "sbom.spdx.json": {"spdxVersion": "SPDX-2.3", "packages": []},
        "mcp-definitions.json": definitions.definitions_document,
        "mcp-capture-context.json": definitions.context_document,
        "trivy.json": {"SchemaVersion": 2, "Results": []},
        "attestation-bundle.json": {"version": 1, "attestations": []},
        "trusted-root.json": {"mediaType": "application/vnd.dev.sigstore.trustedroot+json"},
        "verification.json": {"verified": True},
    }
    assets = []
    for name, payload in payloads.items():
        path = tmp_path / name
        write_json_atomic(path, payload)
        assets.append(ReleaseAsset(name=name, path=path))
    digest = f"sha256:{sha256_file(tmp_path / 'image-manifest.json')}"
    identity = ApplicationIdentity(
        repository="berntpopp/genefoundry-router",
        version="0.8.6",
        source_tag="v0.8.6",
        source_revision="c" * 40,
        image_name="ghcr.io/berntpopp/genefoundry-router",
        image_digest=digest,
        workflow_caller="berntpopp/genefoundry-router/.github/workflows/container-release.yml",
        workflow_standard=("berntpopp/genefoundry-router/.github/workflows/_container-release.yml"),
        workflow_revision="d" * 40,
    )
    manifest = assemble_application_release_manifest(
        identity=identity,
        definitions=definitions,
        scanner=ScannerIdentity(version="0.66.0", database_updated_at="2026-09-02T10:30:00Z"),
        data_requirements=data_requirements,
        assets=assets,
    )
    return application_release_document(manifest)


def _requirements(document: dict[str, object]) -> dict[str, object]:
    requirements = document["data_requirements"]
    assert isinstance(requirements, dict)
    return requirements


def test_assembled_manifest_publishes_the_declared_versions(tmp_path: Path) -> None:
    document = _assemble(tmp_path, {"mode": "none", "schema_compatibility": ["4"]})

    assert _requirements(document)["schema_compatibility"] == ["4"]


def test_assembled_manifest_defaults_to_no_declared_versions(tmp_path: Path) -> None:
    document = _assemble(tmp_path, {"mode": "none", "schema_compatibility": []})

    assert _requirements(document)["schema_compatibility"] == []


@pytest.mark.parametrize(
    "requirements",
    [
        {"mode": "none", "schema_compatibility": ["4"]},
        {"mode": "external-reference", **EXACT_DATA, "schema_compatibility": ["4", "5"]},
    ],
)
def test_manifest_carries_declared_versions(
    tmp_path: Path, requirements: dict[str, object]
) -> None:
    document = _assemble(tmp_path, {"mode": "none", "schema_compatibility": []})
    document["data_requirements"] = requirements

    parsed = ApplicationReleaseManifest.model_validate(document)

    assert (
        list(parsed.data_requirements.schema_compatibility) == requirements["schema_compatibility"]
    )


def test_manifest_refuses_an_unmatchable_version(tmp_path: Path) -> None:
    document = _assemble(tmp_path, {"mode": "none", "schema_compatibility": []})
    document["data_requirements"] = {"mode": "none", "schema_compatibility": [">=1,<2"]}

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(document)


def test_release_workflow_projects_the_declaration_into_sealed_evidence() -> None:
    """The projection is the only path from configuration to manifest."""
    workflow: Any = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))
    capture = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["capture"]["steps"])

    assert "{schema_compatibility: (.schema_compatibility // [])}" in capture


def test_checked_in_schema_documents_the_declaration() -> None:
    schema = json.loads(
        (ROOT / "genefoundry_router/data/container-release.schema.json").read_text(encoding="utf-8")
    )
    declarations = [
        definition["properties"]["schema_compatibility"]
        for definition in schema["$defs"].values()
        if "schema_compatibility" in definition.get("properties", {})
    ]

    assert len(declarations) == len(DATA_MODES)
    assert all(
        declaration["items"]["pattern"] == FLEET_IDENTIFIER_PATTERN for declaration in declarations
    )
