"""Immutable reference-data release verification and materialization."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from genefoundry_router.release.models import (
    TOP_LEVEL_SCHEMA_METADATA,
    DataReleaseTag,
    GitRevision,
    RepositoryName,
    Rfc3339Timestamp,
    SchemaVersion,
    Sha256Digest,
    Sha256Hex,
    StrictModel,
)


class DataVerificationError(ValueError):
    pass


def _https_url(value: AnyHttpUrl) -> AnyHttpUrl:
    if value.scheme != "https":
        raise ValueError("data source URL must use HTTPS")
    return value


HttpsUrl = Annotated[AnyHttpUrl, AfterValidator(_https_url)]
SemanticVersion = Annotated[
    str,
    Field(pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"),
]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


class UpstreamSource(StrictModel):
    identifier: Annotated[str, Field(min_length=1, max_length=256)]
    url: HttpsUrl
    retrieved_at: Rfc3339Timestamp
    sha256: Sha256Hex
    etag: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    last_modified: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class DatasetIdentity(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    release: DataReleaseTag
    source: UpstreamSource


class TransformationIdentity(StrictModel):
    repository: RepositoryName
    revision: GitRevision


class CompatibilityRange(StrictModel):
    minimum: SemanticVersion
    maximum: SemanticVersion

    @model_validator(mode="after")
    def _ordered(self) -> CompatibilityRange:
        if _version_tuple(self.minimum) > _version_tuple(self.maximum):
            raise ValueError("compatibility minimum exceeds maximum")
        return self

    def contains(self, version: str) -> bool:
        candidate = _version_tuple(version)
        return _version_tuple(self.minimum) <= candidate <= _version_tuple(self.maximum)


class SchemaIdentity(CompatibilityRange):
    actual: SemanticVersion

    @model_validator(mode="after")
    def _actual_is_compatible(self) -> SchemaIdentity:
        if not self.contains(self.actual):
            raise ValueError("actual schema is outside the compatible schema range")
        return self


class ArtifactIdentity(StrictModel):
    filename: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")]
    sha256: Sha256Hex
    compressed_size: PositiveInt
    max_compressed_size: PositiveInt
    expanded_tree_sha256: Sha256Hex
    expanded_size: PositiveInt
    max_expanded_size: PositiveInt
    member_count: PositiveInt
    max_members: PositiveInt

    @model_validator(mode="after")
    def _actual_values_fit_ceilings(self) -> ArtifactIdentity:
        if self.compressed_size > self.max_compressed_size:
            raise ValueError("compressed size exceeds compressed size ceiling")
        if self.expanded_size > self.max_expanded_size:
            raise ValueError("expanded size exceeds expanded size ceiling")
        if self.member_count > self.max_members:
            raise ValueError("member count exceeds member ceiling")
        return self


class LicenseEvidence(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    url: HttpsUrl
    redistribution_allowed: StrictBool
    reviewed_at: Rfc3339Timestamp
    reviewer: Annotated[str, Field(min_length=1, max_length=256)]


class DataReleaseManifest(StrictModel):
    """Immutable and independently rollbackable reference-data release."""

    model_config = StrictModel.model_config | {"json_schema_extra": TOP_LEVEL_SCHEMA_METADATA}
    schema_version: SchemaVersion = 1
    dataset: DatasetIdentity
    transformation: TransformationIdentity
    schema_identity: SchemaIdentity = Field(alias="schema", serialization_alias="schema")
    record_counts: Annotated[dict[str, NonNegativeInt], Field(min_length=1)]
    artifact: ArtifactIdentity
    license: LicenseEvidence
    previous_known_good_digest: Sha256Digest
    application_compatibility: CompatibilityRange
    disclaimer: Annotated[str, Field(min_length=20, max_length=1000)]

    def validate_publication(self) -> None:
        """Fail when the reviewed license decision forbids redistribution."""

        if not self.license.redistribution_allowed:
            raise DataVerificationError("redistribution is not allowed for public publication")

    def requirement(self) -> DataRequirement:
        """Project the publication manifest into a runtime artifact requirement."""

        return DataRequirement(
            mode="external-reference",
            release_tag=self.dataset.release,
            sha256=self.artifact.sha256,
            compressed_size=self.artifact.compressed_size,
            max_compressed_size=self.artifact.max_compressed_size,
            expanded_tree_sha256=self.artifact.expanded_tree_sha256,
            expanded_size=self.artifact.expanded_size,
            max_expanded_size=self.artifact.max_expanded_size,
            member_count=self.artifact.member_count,
            max_members=self.artifact.max_members,
            schema_version=self.schema_identity.actual,
            schema_minimum=self.schema_identity.minimum,
            schema_maximum=self.schema_identity.maximum,
            previous_known_good_digest=self.previous_known_good_digest,
            reproducible_rollback=True,
        )


class DataRequirement(StrictModel):
    mode: Literal["external-reference", "restored-database", "upstream-live"]
    release_tag: DataReleaseTag | None = None
    sha256: Sha256Hex | None = None
    compressed_size: PositiveInt | None = None
    max_compressed_size: PositiveInt | None = None
    expanded_tree_sha256: Sha256Hex | None = None
    expanded_size: PositiveInt | None = None
    max_expanded_size: PositiveInt | None = None
    member_count: PositiveInt | None = None
    max_members: PositiveInt | None = None
    schema_version: SemanticVersion | None = None
    schema_minimum: SemanticVersion | None = None
    schema_maximum: SemanticVersion | None = None
    previous_known_good_digest: Sha256Digest | None = None
    reproducible_rollback: StrictBool

    @model_validator(mode="after")
    def _mode_contract(self) -> DataRequirement:
        if self.mode == "upstream-live":
            if self.reproducible_rollback:
                raise ValueError("upstream-live cannot claim reproducible rollback")
            if any(
                value is not None
                for value in (self.release_tag, self.sha256, self.expanded_tree_sha256)
            ):
                raise ValueError("upstream-live cannot claim an immutable artifact identity")
            return self
        required = (
            self.release_tag,
            self.sha256,
            self.compressed_size,
            self.max_compressed_size,
            self.expanded_tree_sha256,
            self.expanded_size,
            self.max_expanded_size,
            self.member_count,
            self.max_members,
            self.schema_version,
            self.schema_minimum,
            self.schema_maximum,
            self.previous_known_good_digest,
        )
        if any(value is None for value in required):
            raise ValueError("immutable data mode requires complete artifact identity")
        assert self.compressed_size is not None
        assert self.max_compressed_size is not None
        assert self.expanded_size is not None
        assert self.max_expanded_size is not None
        assert self.member_count is not None
        assert self.max_members is not None
        if self.compressed_size > self.max_compressed_size:
            raise ValueError("compressed size exceeds compressed size ceiling")
        if self.expanded_size > self.max_expanded_size:
            raise ValueError("expanded size exceeds expanded size ceiling")
        if self.member_count > self.max_members:
            raise ValueError("member count exceeds member ceiling")
        assert self.schema_minimum is not None
        assert self.schema_maximum is not None
        assert self.schema_version is not None
        if not CompatibilityRange(
            minimum=self.schema_minimum, maximum=self.schema_maximum
        ).contains(self.schema_version):
            raise ValueError("schema version is incompatible")
        if not self.reproducible_rollback:
            raise ValueError("immutable data mode must support reproducible rollback")
        return self


class DownloadPolicy(StrictModel):
    max_redirects: Annotated[StrictInt, Field(ge=0, le=5)] = 3
    stall_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 30
    minimum_bytes_per_second: Annotated[StrictInt, Field(ge=1, le=1024 * 1024 * 1024)] = 1
    connect_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 30


from genefoundry_router.release.data_materialization import (  # noqa: E402
    download_artifact,
    expanded_tree_identity,
    materialize_data,
    probe_schema_file,
    rollback_data,
    verify_compressed_artifact,
)

__all__ = [
    "ArtifactIdentity",
    "CompatibilityRange",
    "DataReleaseManifest",
    "DataRequirement",
    "DataVerificationError",
    "DatasetIdentity",
    "DownloadPolicy",
    "LicenseEvidence",
    "SchemaIdentity",
    "TransformationIdentity",
    "UpstreamSource",
    "download_artifact",
    "expanded_tree_identity",
    "materialize_data",
    "probe_schema_file",
    "rollback_data",
    "verify_compressed_artifact",
]
