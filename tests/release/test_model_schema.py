"""Schema and evidence-boundary tests for strict release models."""

from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for
from pydantic import ValidationError

from genefoundry_router.release.models import ApplicationReleaseManifest, ReleaseConfig
from tests.release.test_model_hardening import valid_config, valid_manifest

JSON_SCHEMA_2020_12 = "https://json-schema.org/draft/2020-12/schema"


def test_manifest_requires_retained_image_manifest_asset() -> None:
    manifest = valid_manifest()
    assets = manifest["release_assets"]
    assert isinstance(assets, dict)
    del assets["image-manifest.json"]

    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(manifest)


@pytest.mark.parametrize("model", [ReleaseConfig, ApplicationReleaseManifest])
def test_generated_schema_declares_and_selects_draft_2020_12(
    model: type[ReleaseConfig] | type[ApplicationReleaseManifest],
) -> None:
    schema = model.model_json_schema()

    assert schema["$schema"] == JSON_SCHEMA_2020_12
    assert validator_for(schema) is Draft202012Validator


def test_manifest_rejects_retained_image_manifest_digest_mismatch() -> None:
    manifest = valid_manifest()
    assets = manifest["release_assets"]
    assert isinstance(assets, dict)
    assets["image-manifest.json"] = f"sha256:{'9' * 64}"

    with pytest.raises(ValidationError, match="image manifest"):
        ApplicationReleaseManifest.model_validate(manifest)


def test_manifest_accepts_exact_retained_image_manifest_digest() -> None:
    parsed = ApplicationReleaseManifest.model_validate(valid_manifest())

    assert parsed.release_assets.image_manifest_json == parsed.image.digest


def test_image_manifest_asset_serializes_with_standard_name() -> None:
    dumped = ApplicationReleaseManifest.model_validate(valid_manifest()).model_dump(mode="json")
    assets = dumped["release_assets"]

    assert "image-manifest.json" in assets
    assert "image_manifest_json" not in assets


@pytest.mark.parametrize(
    "case",
    ["repository-path", "image-layer-path", "wildcard-egress", "partial-data-identity"],
)
def test_release_config_json_schema_rejects_invalid_boundaries(case: str) -> None:
    config = copy.deepcopy(valid_config())
    if case == "repository-path":
        config["dockerfile"] = "../Dockerfile"
    elif case == "image-layer-path":
        config["data"] = {"mode": "none", "image_allowlist": ["/../secrets"]}
    elif case == "wildcard-egress":
        config["data"] = {
            "mode": "upstream-live",
            "image_allowlist": [],
            "egress_allowlist": ["*.example.test"],
        }
    else:
        config["data"] = {
            "mode": "external-reference",
            "image_allowlist": [],
            "release_tag": "data-2026.07.13",
        }

    errors = list(Draft202012Validator(ReleaseConfig.model_json_schema()).iter_errors(config))

    assert errors, f"JSON Schema accepted invalid {case} configuration"


@pytest.mark.parametrize("platform_count", [0, 2])
def test_manifest_json_schema_requires_exactly_one_platform(platform_count: int) -> None:
    manifest = copy.deepcopy(valid_manifest())
    image = manifest["image"]
    assert isinstance(image, dict)
    platform = {"platform": "linux/amd64", "digest": image["digest"]}
    image["platforms"] = [platform] * platform_count

    errors = list(
        Draft202012Validator(ApplicationReleaseManifest.model_json_schema()).iter_errors(manifest)
    )

    assert errors


def test_generated_json_schemas_accept_valid_documents() -> None:
    pairs = [
        (ReleaseConfig, valid_config()),
        (ApplicationReleaseManifest, valid_manifest()),
    ]

    for model, document in pairs:
        schema = model.model_json_schema()
        Draft202012Validator.check_schema(schema)
        assert not list(Draft202012Validator(schema).iter_errors(document))


def test_models_round_trip_through_canonical_json_names() -> None:
    config = ReleaseConfig.model_validate(valid_config())
    manifest = ApplicationReleaseManifest.model_validate(valid_manifest())

    assert ReleaseConfig.model_validate_json(config.model_dump_json()) == config
    assert ApplicationReleaseManifest.model_validate_json(manifest.model_dump_json()) == manifest


def _config_with_runtime_cache(deletable: object) -> dict[str, object]:
    config = valid_config()
    config["runtime_cache"] = {
        "path": "/var/cache/genefoundry",
        "eviction": "least-recently-used",
        "deletable_without_authoritative_data_loss": deletable,
    }
    return config


@pytest.mark.parametrize("value", [False, 0, 1, "true"])
def test_runtime_cache_requires_strict_true(value: object) -> None:
    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(_config_with_runtime_cache(value))


def test_runtime_cache_accepts_boolean_true() -> None:
    parsed = ReleaseConfig.model_validate(_config_with_runtime_cache(True))

    assert parsed.runtime_cache is not None
    assert parsed.runtime_cache.deletable_without_authoritative_data_loss is True


def test_runtime_cache_true_only_is_visible_in_json_schema() -> None:
    schema = ReleaseConfig.model_json_schema()
    validator = Draft202012Validator(schema)

    assert list(validator.iter_errors(_config_with_runtime_cache(False)))
    assert not list(validator.iter_errors(_config_with_runtime_cache(True)))


def _manifest_with_database_timestamp(value: object) -> dict[str, object]:
    manifest = valid_manifest()
    evidence = manifest["security_evidence"]
    assert isinstance(evidence, dict)
    evidence["database_updated_at"] = value
    return manifest


@pytest.mark.parametrize(
    "value",
    [
        1_700_000_000,
        1_700_000_000.5,
        "1700000000",
        "2026-07-13T10:30:00",
        "2026-02-30T10:30:00Z",
    ],
)
def test_database_timestamp_rejects_non_rfc3339_input(value: object) -> None:
    with pytest.raises(ValidationError):
        ApplicationReleaseManifest.model_validate(_manifest_with_database_timestamp(value))


@pytest.mark.parametrize(
    "value",
    ["2026-07-13T10:30:00Z", "2026-07-13T12:30:00+02:00", "2026-07-13T10:30:00.123Z"],
)
def test_database_timestamp_accepts_rfc3339_offsets(value: str) -> None:
    parsed = ApplicationReleaseManifest.model_validate(_manifest_with_database_timestamp(value))

    assert parsed.security_evidence.database_updated_at.utcoffset() is not None


def test_database_timestamp_schema_is_string_date_time() -> None:
    security_schema = ApplicationReleaseManifest.model_json_schema()["$defs"]["SecurityEvidence"]
    timestamp_schema = security_schema["properties"]["database_updated_at"]

    assert timestamp_schema["type"] == "string"
    assert timestamp_schema["format"] == "date-time"
    assert "pattern" in timestamp_schema


@pytest.mark.parametrize("field", ["health_path", "mcp_path"])
@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "//example.test/health",
        "/health//ready",
        "/health/./ready",
        "/health/../ready",
        "/health?full=true",
        "/health#details",
        "/health\\ready",
        "/health/\x01ready",
    ],
)
def test_service_rejects_unsafe_local_http_path(field: str, path: str) -> None:
    config = valid_config()
    service = config["service"]
    assert isinstance(service, dict)
    service[field] = path

    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(config)


@pytest.mark.parametrize("field", ["health_path", "mcp_path"])
@pytest.mark.parametrize("path", ["/health", "/ready/live"])
def test_service_accepts_normalized_local_http_path(field: str, path: str) -> None:
    config = valid_config()
    service = config["service"]
    assert isinstance(service, dict)
    service[field] = path

    parsed = ReleaseConfig.model_validate(config)

    assert getattr(parsed.service, field) == path


@pytest.mark.parametrize("field", ["health_path", "mcp_path"])
def test_local_http_path_constraints_are_visible_in_json_schema(field: str) -> None:
    config = valid_config()
    service = config["service"]
    assert isinstance(service, dict)
    service[field] = "//example.test/health"

    errors = list(Draft202012Validator(ReleaseConfig.model_json_schema()).iter_errors(config))

    assert errors
