"""Strict models for container release configuration and immutable evidence."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    WithJsonSchema,
    model_validator,
)
from pydantic.json_schema import JsonDict


def _require_exact_schema_version(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be the integer 1")
    return value


def _require_exact_false(value: object) -> object:
    if type(value) is not bool or value is not False:
        raise ValueError("reproducible_rollback must be the boolean false")
    return value


def _require_exact_true(value: object) -> object:
    if type(value) is not bool or value is not True:
        raise ValueError("cache deletability must be the boolean true")
    return value


def _require_rfc3339_string(value: object) -> object:
    if not isinstance(value, str) or not re.fullmatch(RFC3339_PATTERN, value):
        raise ValueError("timestamp must be an RFC3339 string with an explicit timezone")
    return value


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _require_normalized_relative_path(value: str) -> str:
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or _has_control_character(value)
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).as_posix() != value
    ):
        raise ValueError("path must be a normalized nonempty relative POSIX path")
    return value


def _require_normalized_absolute_path(value: str) -> str:
    parts = value.split("/")[1:]
    if (
        value == "/"
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or _has_control_character(value)
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).as_posix() != value
    ):
        raise ValueError("path must be a normalized absolute non-root POSIX path")
    return value


def _require_local_http_path(value: str) -> str:
    _require_normalized_absolute_path(value)
    if "?" in value or "#" in value:
        raise ValueError("local HTTP path must not contain a query or fragment")
    return value


def _require_dns_endpoint(value: str) -> str:
    if (
        not value
        or _has_control_character(value)
        or any(character.isspace() for character in value)
    ):
        raise ValueError("egress entry must be a DNS hostname with an optional numeric port")
    if value.count(":") > 1:
        raise ValueError("egress entry must not use an IPv6 literal")
    hostname, separator, port_text = value.partition(":")
    if separator and (
        not port_text.isascii()
        or not port_text.isdigit()
        or not 1 <= int(port_text) <= 65535
        or port_text != str(int(port_text))
    ):
        raise ValueError("egress port must be an integer from 1 through 65535")
    labels = hostname.split(".")
    if len(hostname) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("egress entry must contain only explicit DNS hostname labels")
    return value


def _require_immutable_data_release_tag(value: str) -> str:
    if value.lower() in {"latest", "main", "master", "head", "stable", "current"}:
        raise ValueError("data release tag must be immutable")
    return value


RELATIVE_PATH_PATTERN = (
    r"^(?!/)(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)"
    r"(?!.*[\u0000-\u001f\u007f])[^/]+(?:/[^/]+)*$"
)
ABSOLUTE_PATH_PATTERN = (
    r"^/(?!/)(?!$)(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)"
    r"(?!.*[\u0000-\u001f\u007f])[^/]+(?:/[^/]+)*$"
)
LOCAL_HTTP_PATH_PATTERN = (
    r"^/(?!/)(?!$)(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)"
    r"(?!.*[?#\u0000-\u001f\u007f])[^/?#]+(?:/[^/?#]+)*$"
)
DNS_LABEL_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
DNS_PORT_PATTERN = (
    r"(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
    r"65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])"
)
DNS_ENDPOINT_PATTERN = (
    rf"^(?:{DNS_LABEL_PATTERN})(?:\.(?:{DNS_LABEL_PATTERN}))*(?::{DNS_PORT_PATTERN})?$"
)
RFC3339_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
SchemaVersion = Annotated[Literal[1], BeforeValidator(_require_exact_schema_version)]
FalseOnly = Annotated[Literal[False], BeforeValidator(_require_exact_false)]
TrueOnly = Annotated[Literal[True], BeforeValidator(_require_exact_true)]
RepositoryRelativePath = Annotated[
    str,
    AfterValidator(_require_normalized_relative_path),
    WithJsonSchema({"type": "string", "pattern": RELATIVE_PATH_PATTERN}),
]
ImageLayerPath = Annotated[
    str,
    AfterValidator(_require_normalized_relative_path),
    WithJsonSchema({"type": "string", "pattern": RELATIVE_PATH_PATTERN}),
]
AbsoluteRuntimePath = Annotated[
    str,
    AfterValidator(_require_normalized_absolute_path),
    WithJsonSchema({"type": "string", "pattern": ABSOLUTE_PATH_PATTERN}),
]
LocalHttpPath = Annotated[
    str,
    AfterValidator(_require_local_http_path),
    WithJsonSchema({"type": "string", "pattern": LOCAL_HTTP_PATH_PATTERN}),
]
DnsEndpoint = Annotated[
    str,
    AfterValidator(_require_dns_endpoint),
    WithJsonSchema({"type": "string", "pattern": DNS_ENDPOINT_PATTERN}),
]
Rfc3339Timestamp = Annotated[
    AwareDatetime,
    BeforeValidator(_require_rfc3339_string),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": RFC3339_PATTERN}),
]
SafeIdentifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
SEMANTIC_SCHEMA_COMMENT = (
    "This checked-in schema enforces structural constraints. Acceptance additionally requires "
    "semantic validation through ReleaseConfig or ApplicationReleaseManifest; cross-field "
    "equality is enforced by Pydantic validators."
)
TOP_LEVEL_SCHEMA_METADATA: JsonDict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$comment": SEMANTIC_SCHEMA_COMMENT,
}

StableVersion = Annotated[
    str,
    Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"),
]
StableReleaseTag = Annotated[
    str,
    Field(pattern=r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"),
]
DATA_RELEASE_TAG_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
MUTABLE_DATA_RELEASE_TAGS = ("latest", "main", "master", "head", "stable", "current")
DataReleaseTag = Annotated[
    str,
    Field(pattern=DATA_RELEASE_TAG_PATTERN),
    AfterValidator(_require_immutable_data_release_tag),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": DATA_RELEASE_TAG_PATTERN,
            "not": {"enum": list(MUTABLE_DATA_RELEASE_TAGS)},
        }
    ),
]
GitRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
RepositoryName = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/[A-Za-z0-9_.-]{1,100}$"),
]
GhcrImageName = Annotated[
    str,
    Field(
        pattern=(
            r"^ghcr\.io/[a-z0-9](?:[a-z0-9._-]{0,99})/"
            r"[a-z0-9](?:[a-z0-9._-]{0,99})$"
        )
    ),
]
WorkflowIdentityPath = Annotated[
    str,
    Field(
        pattern=(
            r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38})/"
            r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
            r"\.github/workflows/[A-Za-z0-9_-](?:[A-Za-z0-9_.-]{0,99})\.ya?ml$"
        )
    ),
]
DefinitionContract = Literal["data-independent", "data-bound"]


class StrictModel(BaseModel):
    """Base for immutable, fail-closed release contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AuxiliaryServiceConfig(StrictModel):
    """One approved sidecar and the role policy the central gate validates it against.

    The role, not the name, is what is authorized: every field the role permits is
    checked, so declaring a service can never authorize an unhardened container.
    """

    name: SafeIdentifier
    role: Literal["init", "database"]
    egress: Literal["denied", "internal", "approved-networks"] = "denied"
    writable_targets: Annotated[tuple[AbsoluteRuntimePath, ...], Field(max_length=8)] = ()
    read_only_targets: Annotated[tuple[AbsoluteRuntimePath, ...], Field(max_length=8)] = ()
    healthcheck_test: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=1024)], ...],
        Field(max_length=64),
    ] = ()
    # A root entrypoint that gosu-drops needs CAP_SETUID, impossible under cap_drop: [ALL].
    # Declaring the image's own uid:gid skips it: non-root, all capabilities still dropped.
    user: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,6}:[1-9][0-9]{0,6}$")] | None = None

    @model_validator(mode="after")
    def _role_requirements_are_complete(self) -> AuxiliaryServiceConfig:
        if self.role == "database":
            if self.egress != "approved-networks":
                raise ValueError("a database sidecar must reach an approved project network")
            if not self.healthcheck_test:
                raise ValueError("a database sidecar must declare its readiness probe")
        if not self.writable_targets:
            raise ValueError("an auxiliary sidecar must declare its writable volume targets")
        if set(self.writable_targets) & set(self.read_only_targets):
            raise ValueError("a mount target must be either writable or read-only, not both")
        return self


class ServiceConfig(StrictModel):
    """Runtime endpoint and Compose facts needed by standard smoke gates."""

    compose_files: Annotated[tuple[RepositoryRelativePath, ...], Field(min_length=1)] = (
        "docker/docker-compose.yml",
    )
    name: SafeIdentifier
    container_port: Annotated[StrictInt, Field(ge=1, le=65535)] = 8000
    health_path: LocalHttpPath = "/health"
    mcp_path: LocalHttpPath = "/mcp"
    startup_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 90
    networks: Annotated[tuple[SafeIdentifier, ...], Field(min_length=1, max_length=8)] = (
        "default",
    )
    internal_networks: Annotated[tuple[SafeIdentifier, ...], Field(max_length=8)] = ()
    auxiliary: Annotated[tuple[AuxiliaryServiceConfig, ...], Field(max_length=4)] = ()

    @model_validator(mode="after")
    def _declared_services_and_networks_are_consistent(self) -> ServiceConfig:
        names = [auxiliary.name for auxiliary in self.auxiliary]
        if len(set(names)) != len(names):
            raise ValueError("each auxiliary service may be declared only once")
        if self.name in names:
            raise ValueError("an auxiliary service must not name the application service")
        if not set(self.internal_networks).issubset(self.networks):
            raise ValueError("an internal network must also be an approved project network")
        if any(auxiliary.egress == "internal" for auxiliary in self.auxiliary) and (
            not self.internal_networks
        ):
            raise ValueError("an internal-egress sidecar requires a declared internal network")
        return self


class NoAuthoritativeData(StrictModel):
    """A service with no persistent authoritative local dataset."""

    mode: Literal["none"]
    image_allowlist: tuple[ImageLayerPath, ...] = ()


class OptionalReleaseDataIdentity(StrictModel):
    """A release-time data identity that must be wholly present or absent."""

    model_config = ConfigDict(
        json_schema_extra={
            "dependentRequired": {"release_tag": ["digest"], "digest": ["release_tag"]}
        }
    )

    image_allowlist: tuple[ImageLayerPath, ...] = ()
    release_tag: DataReleaseTag | None = None
    digest: Sha256Digest | None = None

    @model_validator(mode="after")
    def _identity_is_complete_or_absent(self) -> OptionalReleaseDataIdentity:
        if (self.release_tag is None) != (self.digest is None):
            raise ValueError("data identity requires both release_tag and digest")
        return self


class ExternalReferenceData(OptionalReleaseDataIdentity):
    """An immutable reference artifact mounted outside the application image."""

    mode: Literal["external-reference"]


class RestoredDatabaseData(OptionalReleaseDataIdentity):
    """An immutable data-only artifact restored into an external database."""

    mode: Literal["restored-database"]


class UpstreamLiveData(OptionalReleaseDataIdentity):
    """Transitional authoritative data materialized directly from an upstream."""

    mode: Literal["upstream-live"]
    egress_allowlist: Annotated[tuple[DnsEndpoint, ...], Field(min_length=1)]
    reproducible_rollback: FalseOnly = False


ReleaseDataConfig = Annotated[
    NoAuthoritativeData | ExternalReferenceData | RestoredDatabaseData | UpstreamLiveData,
    Field(discriminator="mode"),
]


class RuntimeCacheConfig(StrictModel):
    """Derived writable state that is not an authoritative-data mode."""

    path: AbsoluteRuntimePath
    eviction: Annotated[str, Field(min_length=1, max_length=200)]
    deletable_without_authoritative_data_loss: TrueOnly


class DefinitionsConfig(StrictModel):
    """How MCP definition identity relates to authoritative data."""

    contract: DefinitionContract


class SmokeConfig(StrictModel):
    """A centrally implemented smoke-test profile."""

    profile: Literal[
        "compose",
        "compose-two-context",
        "immutable-bundle",
        "postgres-bundle",
        "prepared-live-fixture",
    ] = "compose"


# Each assignment becomes one `docker run --env KEY=VALUE` argument on the gate
# container. The value charset excludes whitespace, quotes, and shell metacharacters
# so an assignment can never split the argument or reach a shell. This is checked-in
# public configuration and must never carry a secret.
EnvAssignment = Annotated[
    str,
    Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}=[A-Za-z0-9_.,:/@+-]{1,255}$"),
]


class ReleaseConfig(StrictModel):
    """Per-repository facts consumed by the central container release workflow."""

    model_config = ConfigDict(json_schema_extra=TOP_LEVEL_SCHEMA_METADATA)

    schema_version: SchemaVersion = 1
    dockerfile: RepositoryRelativePath = "docker/Dockerfile"
    target: SafeIdentifier = "production"
    platform: Literal["linux/amd64"] = "linux/amd64"
    service: ServiceConfig
    data: ReleaseDataConfig = Field(default_factory=lambda: NoAuthoritativeData(mode="none"))
    runtime_cache: RuntimeCacheConfig | None = None
    definitions: DefinitionsConfig
    smoke: SmokeConfig = Field(default_factory=SmokeConfig)
    smoke_environment: tuple[EnvAssignment, ...] = ()
    preparation: Literal["docker/ci-prepare-smoke.sh"] | None = None

    @model_validator(mode="after")
    def _smoke_environment_keys_are_unique(self) -> ReleaseConfig:
        keys = [assignment.split("=", 1)[0] for assignment in self.smoke_environment]
        if len(keys) != len(set(keys)):
            raise ValueError("smoke_environment must not assign the same key twice")
        return self

    @model_validator(mode="after")
    def _data_bound_has_exact_identity(self) -> ReleaseConfig:
        if self.definitions.contract != "data-bound":
            return self
        release_tag = getattr(self.data, "release_tag", None)
        digest = getattr(self.data, "digest", None)
        if release_tag is None or digest is None:
            raise ValueError(
                "data-bound definitions require an exact data release_tag and sha256 digest"
            )
        return self


class SourceIdentity(StrictModel):
    """Stable source tag and exact Git commit."""

    tag: StableReleaseTag
    revision: GitRevision


class PlatformImage(StrictModel):
    """The accepted manifest for one tested platform."""

    platform: Literal["linux/amd64"]
    digest: Sha256Digest


class ImageIdentity(StrictModel):
    """Published application image identity."""

    name: GhcrImageName
    digest: Sha256Digest
    platforms: Annotated[tuple[PlatformImage, ...], Field(min_length=1, max_length=1)]

    @model_validator(mode="after")
    def _one_amd64_manifest_matches_image(self) -> ImageIdentity:
        if len(self.platforms) != 1:
            raise ValueError("v1 releases require exactly one linux/amd64 platform manifest")
        if self.platforms[0].digest != self.digest:
            raise ValueError("single-platform digest must equal the image manifest digest")
        return self


class WorkflowIdentity(StrictModel):
    """Caller and exact router-owned reusable workflow identity."""

    caller: WorkflowIdentityPath
    standard: WorkflowIdentityPath
    standard_revision: GitRevision


class McpEvidence(StrictModel):
    """Canonical MCP definition evidence and its data contract."""

    definitions_sha256: Sha256Hex
    capture_context_sha256: Sha256Hex
    definition_contract: DefinitionContract


class SecurityEvidence(StrictModel):
    """Digests for the scanner, SBOM, provenance, and verification evidence."""

    scanner: Literal["trivy"]
    scanner_version: Annotated[str, Field(min_length=1, max_length=100)]
    database_updated_at: Rfc3339Timestamp
    sbom_sha256: Sha256Hex
    scanner_evidence_sha256: Sha256Hex
    attestation_bundle_sha256: Sha256Hex
    trusted_root_sha256: Sha256Hex
    verification_sha256: Sha256Hex


class ReleaseAssets(StrictModel):
    """Closed v1 release-asset set, addressed by each asset's SHA-256."""

    model_config = ConfigDict(serialize_by_alias=True)

    image_manifest_json: Sha256Digest = Field(alias="image-manifest.json")
    sbom_spdx_json: Sha256Digest = Field(alias="sbom.spdx.json")
    mcp_definitions_json: Sha256Digest = Field(alias="mcp-definitions.json")
    mcp_capture_context_json: Sha256Digest = Field(alias="mcp-capture-context.json")
    trivy_json: Sha256Digest = Field(alias="trivy.json")
    attestation_bundle_json: Sha256Digest = Field(alias="attestation-bundle.json")
    trusted_root_json: Sha256Digest = Field(alias="trusted-root.json")
    verification_json: Sha256Digest = Field(alias="verification.json")


class NoDataRequirements(StrictModel):
    """A released application with no authoritative data identity."""

    mode: Literal["none"]
    schema_compatibility: tuple[str, ...] = ()


class ExactDataRequirements(StrictModel):
    """An exact immutable or observed authoritative data identity."""

    release_tag: DataReleaseTag
    digest: Sha256Digest
    schema_compatibility: tuple[str, ...] = ()


class ExternalReferenceRequirements(ExactDataRequirements):
    """Exact external reference artifact required by the application."""

    mode: Literal["external-reference"]


class RestoredDatabaseRequirements(ExactDataRequirements):
    """Exact restored database artifact required by the application."""

    mode: Literal["restored-database"]


class UpstreamLiveRequirements(ExactDataRequirements):
    """Observed upstream identity with explicitly degraded rollback."""

    mode: Literal["upstream-live"]
    reproducible_rollback: FalseOnly


DataRequirements = Annotated[
    NoDataRequirements
    | ExternalReferenceRequirements
    | RestoredDatabaseRequirements
    | UpstreamLiveRequirements,
    Field(discriminator="mode"),
]


class ApplicationReleaseManifest(StrictModel):
    """Complete immutable evidence record for one accepted application image."""

    model_config = ConfigDict(json_schema_extra=TOP_LEVEL_SCHEMA_METADATA)

    schema_version: SchemaVersion = 1
    repository: RepositoryName
    version: StableVersion
    source: SourceIdentity
    image: ImageIdentity
    workflow: WorkflowIdentity
    mcp: McpEvidence
    security_evidence: SecurityEvidence
    release_assets: ReleaseAssets
    data_requirements: DataRequirements

    @model_validator(mode="after")
    def _version_and_data_identity_are_consistent(self) -> ApplicationReleaseManifest:
        if self.source.tag != f"v{self.version}":
            raise ValueError("source tag must equal the manifest version")
        if self.image.name != f"ghcr.io/{self.repository.lower()}":
            raise ValueError("image name must match the source repository")
        caller_repository = self.workflow.caller.split("/.github/workflows/", maxsplit=1)[0]
        if caller_repository.lower() != self.repository.lower():
            raise ValueError("workflow caller must belong to the source repository")
        if self.release_assets.image_manifest_json != self.image.digest:
            raise ValueError("image manifest asset digest must equal the published image digest")
        asset_evidence = (
            (self.release_assets.sbom_spdx_json, self.security_evidence.sbom_sha256),
            (self.release_assets.mcp_definitions_json, self.mcp.definitions_sha256),
            (self.release_assets.mcp_capture_context_json, self.mcp.capture_context_sha256),
            (self.release_assets.trivy_json, self.security_evidence.scanner_evidence_sha256),
            (
                self.release_assets.attestation_bundle_json,
                self.security_evidence.attestation_bundle_sha256,
            ),
            (self.release_assets.trusted_root_json, self.security_evidence.trusted_root_sha256),
            (self.release_assets.verification_json, self.security_evidence.verification_sha256),
        )
        if any(asset != f"sha256:{evidence}" for asset, evidence in asset_evidence):
            raise ValueError("release asset digest must match its corresponding evidence digest")
        if self.mcp.definition_contract == "data-bound" and self.data_requirements.mode == "none":
            raise ValueError("data-bound definitions require an authoritative data mode")
        return self


__all__ = ["ApplicationReleaseManifest", "ReleaseConfig"]
