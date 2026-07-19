"""Strict verification of runtime-observed data identity readiness evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import ValidationError

from genefoundry_router.release.models import (
    DataReleaseTag,
    SchemaVersion,
    Sha256Digest,
    StrictModel,
)

DataIdentityAdoption = Literal["unadopted", "runtime-v1"]


class RuntimeIdentityError(ValueError):
    """Runtime readiness cannot prove its declared data identity."""


class RuntimeIdentityPair(StrictModel):
    """One exact immutable data release identity."""

    release_tag: DataReleaseTag
    digest: Sha256Digest


class RuntimeDataIdentity(StrictModel):
    """Configured expectation and independently observed materialization identity."""

    expected: RuntimeIdentityPair
    actual: RuntimeIdentityPair


class ReleaseIdentity(StrictModel):
    """Versioned readiness fragment for runtime data identity."""

    schema_version: SchemaVersion
    data_identity: RuntimeDataIdentity


def verify_readiness_data_identity(
    readiness: Mapping[str, object],
    *,
    release_tag: str | None,
    digest: str | None,
    adoption: DataIdentityAdoption,
) -> dict[str, str] | None:
    """Return only a verified observed pair for an adopted data-bound service."""
    if adoption == "unadopted":
        return None
    if adoption != "runtime-v1":
        raise RuntimeIdentityError(f"unknown data identity adoption state: {adoption}")
    if release_tag is None or digest is None:
        raise RuntimeIdentityError("runtime-v1 adoption requires data-bound release requirements")
    try:
        declared = RuntimeIdentityPair.model_validate(
            {"release_tag": release_tag, "digest": digest}
        )
    except ValidationError as exc:
        raise RuntimeIdentityError("declared data identity is invalid") from exc
    if "release_identity" not in readiness:
        raise RuntimeIdentityError("runtime-v1 readiness is missing release_identity")
    try:
        fragment = ReleaseIdentity.model_validate(readiness["release_identity"])
    except ValidationError as exc:
        raise RuntimeIdentityError("invalid runtime-v1 release_identity fragment") from exc
    if fragment.data_identity.expected != declared:
        raise RuntimeIdentityError("runtime expected identity does not match declared identity")
    if fragment.data_identity.actual != declared:
        raise RuntimeIdentityError("runtime actual identity does not match declared identity")
    return fragment.data_identity.actual.model_dump(mode="json")


__all__ = [
    "DataIdentityAdoption",
    "ReleaseIdentity",
    "RuntimeDataIdentity",
    "RuntimeIdentityError",
    "RuntimeIdentityPair",
    "verify_readiness_data_identity",
]
