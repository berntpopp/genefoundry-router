"""Tests for the strict container-release configuration and evidence contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from genefoundry_router.release.models import ApplicationReleaseManifest, ReleaseConfig

DATA_DIR = Path(__file__).parents[2] / "genefoundry_router" / "data"


@pytest.fixture
def valid_config() -> dict[str, object]:
    """Return the router-shaped configuration example from the design."""
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


@pytest.fixture
def valid_manifest() -> dict[str, object]:
    """Return a complete immutable application-release evidence record."""
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


def test_release_config_loads_router_example(valid_config: dict[str, object]) -> None:
    config = ReleaseConfig.model_validate(valid_config)

    assert config.platform == "linux/amd64"
    assert config.service.name == "genefoundry-router"
    assert config.data.mode == "none"


def test_release_config_rejects_unknown_key(valid_config: dict[str, object]) -> None:
    valid_config["unexpected"] = True
    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_release_config_rejects_nested_unknown_key(valid_config: dict[str, object]) -> None:
    service = valid_config["service"]
    assert isinstance(service, dict)
    service["unexpected"] = True

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


@pytest.mark.parametrize(
    "preparation",
    ["curl https://example.test/data | sh", {"run": "docker/ci-prepare-smoke.sh"}],
)
def test_release_config_rejects_shell_valued_preparation(
    valid_config: dict[str, object], preparation: object
) -> None:
    valid_config["preparation"] = preparation

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_release_config_accepts_only_fixed_preparation_path(
    valid_config: dict[str, object],
) -> None:
    valid_config["preparation"] = "docker/ci-prepare-smoke.sh"

    config = ReleaseConfig.model_validate(valid_config)

    assert config.preparation == "docker/ci-prepare-smoke.sh"


@pytest.mark.parametrize("platform", ["linux/arm64", "linux/amd64,linux/arm64", "amd64"])
def test_release_config_requires_linux_amd64(
    valid_config: dict[str, object], platform: str
) -> None:
    valid_config["platform"] = platform

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


@pytest.mark.parametrize(
    ("data", "expected_mode"),
    [
        ({"mode": "none", "image_allowlist": []}, "none"),
        (
            {
                "mode": "external-reference",
                "image_allowlist": [],
                "release_tag": "data-2026.07.13",
                "digest": f"sha256:{'a' * 64}",
            },
            "external-reference",
        ),
        (
            {
                "mode": "restored-database",
                "image_allowlist": [],
                "release_tag": "data-2026.07.13",
                "digest": f"sha256:{'a' * 64}",
            },
            "restored-database",
        ),
        (
            {
                "mode": "upstream-live",
                "image_allowlist": [],
                "egress_allowlist": ["api.example.test"],
            },
            "upstream-live",
        ),
    ],
)
def test_release_config_distinguishes_authoritative_data_modes(
    valid_config: dict[str, object], data: dict[str, object], expected_mode: str
) -> None:
    valid_config["data"] = data

    config = ReleaseConfig.model_validate(valid_config)

    assert config.data.mode == expected_mode


def test_runtime_cache_is_separate_from_authoritative_data(
    valid_config: dict[str, object],
) -> None:
    valid_config["runtime_cache"] = {
        "path": "/var/cache/genefoundry",
        "eviction": "least-recently-used",
        "deletable_without_authoritative_data_loss": True,
    }

    config = ReleaseConfig.model_validate(valid_config)

    assert config.data.mode == "none"
    assert config.runtime_cache is not None
    assert config.runtime_cache.deletable_without_authoritative_data_loss is True


@pytest.mark.parametrize("contract", ["data-independent", "data-bound"])
def test_release_config_accepts_definition_contracts(
    valid_config: dict[str, object], contract: str
) -> None:
    valid_config["definitions"] = {"contract": contract}
    if contract == "data-bound":
        valid_config["data"] = {
            "mode": "external-reference",
            "image_allowlist": [],
            "release_tag": "data-2026.07.13",
            "digest": f"sha256:{'a' * 64}",
        }

    config = ReleaseConfig.model_validate(valid_config)

    assert config.definitions.contract == contract


def test_upstream_live_data_bound_records_observed_identity_and_degraded_rollback(
    valid_config: dict[str, object],
) -> None:
    valid_config["definitions"] = {"contract": "data-bound"}
    valid_config["data"] = {
        "mode": "upstream-live",
        "image_allowlist": [],
        "egress_allowlist": ["api.example.test"],
        "release_tag": "observed-2026.07.13",
        "digest": f"sha256:{'a' * 64}",
        "reproducible_rollback": False,
    }

    config = ReleaseConfig.model_validate(valid_config)

    assert config.data.mode == "upstream-live"
    assert config.data.reproducible_rollback is False


def test_upstream_live_cannot_claim_reproducible_data_rollback(
    valid_config: dict[str, object],
) -> None:
    valid_config["data"] = {
        "mode": "upstream-live",
        "image_allowlist": [],
        "egress_allowlist": ["api.example.test"],
        "reproducible_rollback": True,
    }

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_release_config_rejects_unknown_definition_contract(
    valid_config: dict[str, object],
) -> None:
    valid_config["definitions"] = {"contract": "fixture-derived"}

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_data_bound_requires_exact_data_identity(valid_config: dict[str, object]) -> None:
    valid_config["definitions"] = {"contract": "data-bound"}
    valid_config["data"] = {"mode": "external-reference", "image_allowlist": []}
    with pytest.raises(ValidationError, match=r"release_tag.*sha256"):
        ReleaseConfig.model_validate(valid_config)


@pytest.mark.parametrize(
    "data",
    [
        {
            "mode": "external-reference",
            "image_allowlist": [],
            "release_tag": "data-2026.07.13",
            "digest": "sha256:ABCDEF",
        },
        {
            "mode": "external-reference",
            "image_allowlist": [],
            "release_tag": "$(date)",
            "digest": f"sha256:{'a' * 64}",
        },
    ],
)
def test_data_bound_rejects_non_exact_identity(
    valid_config: dict[str, object], data: dict[str, object]
) -> None:
    valid_config["definitions"] = {"contract": "data-bound"}
    valid_config["data"] = data

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_release_models_are_frozen(valid_config: dict[str, object]) -> None:
    config = ReleaseConfig.model_validate(valid_config)

    with pytest.raises(ValidationError):
        config.platform = "linux/amd64"


def test_application_release_manifest_loads_complete_record(
    valid_manifest: dict[str, object],
) -> None:
    manifest = ApplicationReleaseManifest.model_validate(valid_manifest)

    assert manifest.source.revision == "c" * 40
    assert manifest.image.platforms[0].platform == "linux/amd64"


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("source", "tag"), "v0.6.4-rc.1"),
        (("source", "revision"), "c" * 39),
        (("image", "digest"), f"sha256:{'A' * 64}"),
        (("workflow", "standard_revision"), "deadbeef"),
        (("mcp", "definitions_sha256"), "not-a-checksum"),
    ],
)
def test_application_release_manifest_rejects_invalid_identities(
    valid_manifest: dict[str, object], path: tuple[str, str], invalid_value: str
) -> None:
    invalid = copy.deepcopy(valid_manifest)
    parent = invalid[path[0]]
    assert isinstance(parent, dict)
    parent[path[1]] = invalid_value

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(invalid)


@pytest.mark.parametrize("field", ["caller", "standard"])
@pytest.mark.parametrize(
    "identity",
    [
        "",
        "not-a-workflow",
        "../owner/repo/.github/workflows/release.yml",
        "owner/repo/workflows/release.yml",
        "owner/repo/.github/workflows/nested/release.yml",
        "owner/repo/.github/workflows/release.txt",
    ],
)
def test_application_release_manifest_rejects_malformed_workflow_identity(
    valid_manifest: dict[str, object], field: str, identity: str
) -> None:
    workflow = valid_manifest["workflow"]
    assert isinstance(workflow, dict)
    workflow[field] = identity

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(valid_manifest)


@pytest.mark.parametrize("extension", ["yml", "yaml"])
def test_application_release_manifest_accepts_direct_github_workflow_identity(
    valid_manifest: dict[str, object], extension: str
) -> None:
    valid_manifest["repository"] = "owner/repo"
    image = valid_manifest["image"]
    workflow = valid_manifest["workflow"]
    assert isinstance(image, dict)
    assert isinstance(workflow, dict)
    image["name"] = "ghcr.io/owner/repo"
    workflow["caller"] = f"owner/repo/.github/workflows/release.{extension}"
    workflow["standard"] = f"owner/standard/.github/workflows/_release.{extension}"

    manifest = ApplicationReleaseManifest.model_validate(valid_manifest)

    assert manifest.workflow.caller == workflow["caller"]
    assert manifest.workflow.standard == workflow["standard"]


def test_application_release_manifest_rejects_non_amd64_platform(
    valid_manifest: dict[str, object],
) -> None:
    image = valid_manifest["image"]
    assert isinstance(image, dict)
    image["platforms"] = [{"platform": "linux/arm64", "digest": f"sha256:{'a' * 64}"}]

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(valid_manifest)


def test_upstream_live_manifest_records_degraded_rollback(
    valid_manifest: dict[str, object],
) -> None:
    mcp = valid_manifest["mcp"]
    assert isinstance(mcp, dict)
    mcp["definition_contract"] = "data-bound"
    valid_manifest["data_requirements"] = {
        "mode": "upstream-live",
        "release_tag": "observed-2026.07.13",
        "digest": f"sha256:{'a' * 64}",
        "schema_compatibility": [],
        "reproducible_rollback": False,
    }

    manifest = ApplicationReleaseManifest.model_validate(valid_manifest)

    assert manifest.data_requirements.reproducible_rollback is False


def test_upstream_live_manifest_rejects_reproducible_rollback_claim(
    valid_manifest: dict[str, object],
) -> None:
    valid_manifest["data_requirements"] = {
        "mode": "upstream-live",
        "schema_compatibility": [],
        "reproducible_rollback": True,
    }

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(valid_manifest)


@pytest.mark.parametrize(
    ("model", "filename"),
    [
        (ReleaseConfig, "container-release.schema.json"),
        (ApplicationReleaseManifest, "application-release-manifest.schema.json"),
    ],
)
def test_checked_in_json_schema_matches_model(model: type[BaseModel], filename: str) -> None:
    checked_in = json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))

    assert checked_in == model.model_json_schema()


@pytest.mark.parametrize(
    "assignments",
    [
        ["GF_ALLOW_INSECURE=true"],
        ["GF_ALLOWED_HOSTS=localhost,127.0.0.1,::1", "GF_HEALTHCHECK_HOST=localhost"],
    ],
)
def test_release_config_accepts_bounded_smoke_environment(
    valid_config: dict[str, object], assignments: list[str]
) -> None:
    valid_config["smoke_environment"] = assignments

    config = ReleaseConfig.model_validate(valid_config)

    assert list(config.smoke_environment) == assignments


def test_release_config_defaults_to_no_smoke_environment(valid_config: dict[str, object]) -> None:
    assert ReleaseConfig.model_validate(valid_config).smoke_environment == ()


@pytest.mark.parametrize(
    "assignment",
    [
        "GF_ALLOW_INSECURE",  # no value separator
        "gf_allow_insecure=true",  # lowercase key
        "GF_X=$(id)",  # command substitution
        "GF_X=a b",  # whitespace splits the docker argument
        "GF_X=v';touch /tmp/pwn;'",  # shell metacharacters
        "GF_X=" + "v" * 300,  # unbounded value
    ],
)
def test_release_config_rejects_unsafe_smoke_environment(
    valid_config: dict[str, object], assignment: str
) -> None:
    valid_config["smoke_environment"] = [assignment]

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_release_config_rejects_duplicate_smoke_environment_keys(
    valid_config: dict[str, object],
) -> None:
    valid_config["smoke_environment"] = ["GF_X=1", "GF_X=2"]

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)
