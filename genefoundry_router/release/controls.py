"""Fail-closed GitHub/GHCR control ledger for fleet container releases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from genefoundry_router.config import load_registry
from genefoundry_router.release.models import GhcrImageName, RepositoryName, Rfc3339Timestamp


class ControlLedgerError(ValueError):
    """The control ledger is invalid, incomplete, or not release-ready."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiEvidence(_StrictModel):
    status: Literal["verified", "unavailable"]
    source: Literal["api"]
    url: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    verified_at: Rfc3339Timestamp
    reason: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def _unavailable_has_reason(self) -> ApiEvidence:
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("unavailable control evidence requires a reason")
        return self


class ManualEvidence(_StrictModel):
    status: Literal["verified", "unavailable"]
    source: Literal["manual"]
    url: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    verified_at: Rfc3339Timestamp
    reviewer: Annotated[str, Field(min_length=1, max_length=100)]
    reason: Annotated[str, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def _unavailable_has_reason(self) -> ManualEvidence:
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("unavailable control evidence requires a reason")
        return self


ControlEvidence = Annotated[ApiEvidence | ManualEvidence, Field(discriminator="source")]


class TagRulesetControl(_StrictModel):
    active: bool
    restricts_creation: bool
    restricts_update: bool
    restricts_deletion: bool
    restricts_non_fast_forward: bool
    bypass_actors: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...]
    evidence: ControlEvidence


class ReleaseEnvironmentControl(_StrictModel):
    protected: bool
    exact_tag_only: bool
    required_reviewers: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...]
    evidence: ControlEvidence


class ImmutableReleaseControl(_StrictModel):
    enabled: bool
    evidence: ControlEvidence


class PackageControl(_StrictModel):
    name: GhcrImageName
    visibility: Literal["public", "private", "internal"]
    linked_repository: RepositoryName
    anonymous_pull: bool
    standing_package_pat: bool
    evidence: ControlEvidence


class RetentionControl(_StrictModel):
    released_digests: bool
    deployed_digests: bool
    rollback_digests: bool
    automated_deletion: bool
    evidence: ControlEvidence


class VerifiedRepositoryControls(_StrictModel):
    status: Literal["verified"]
    repository: RepositoryName
    tag_ruleset: TagRulesetControl
    release_environment: ReleaseEnvironmentControl
    immutable_releases: ImmutableReleaseControl
    package: PackageControl
    retention: RetentionControl


class UnavailableRepositoryControls(_StrictModel):
    status: Literal["unavailable"]
    repository: RepositoryName
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    evidence: ControlEvidence


RepositoryControls = Annotated[
    VerifiedRepositoryControls | UnavailableRepositoryControls,
    Field(discriminator="status"),
]


class ContainerControlLedger(_StrictModel):
    schema_version: Literal[1]
    reviewed_at: Rfc3339Timestamp
    repositories: dict[RepositoryName, RepositoryControls]

    @model_validator(mode="after")
    def _keys_match_rows(self) -> ContainerControlLedger:
        if any(key != row.repository for key, row in self.repositories.items()):
            raise ValueError("control-ledger repository keys must match row identities")
        return self


def expected_fleet_repositories(servers_file: Path) -> set[str]:
    """Return the router plus every registered backend source repository."""
    backends = load_registry(servers_file, {})
    repositories = {backend.repo for backend in backends if backend.repo is not None}
    repositories.add("berntpopp/genefoundry-router")
    return repositories


def load_control_ledger(source: Path | dict[str, object]) -> ContainerControlLedger:
    """Parse control evidence without treating an unavailable row as compliant."""
    try:
        payload = (
            json.loads(source.read_text(encoding="utf-8")) if isinstance(source, Path) else source
        )
        return ContainerControlLedger.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ControlLedgerError("invalid control ledger") from exc


def _verified_row_errors(row: VerifiedRepositoryControls) -> list[str]:
    errors: list[str] = []
    rules = row.tag_ruleset
    if not (
        rules.active
        and rules.restricts_creation
        and rules.restricts_update
        and rules.restricts_deletion
        and rules.restricts_non_fast_forward
        and rules.bypass_actors
    ):
        errors.append("tag ruleset semantics or bypass actors are incomplete")
    environment = row.release_environment
    if not (
        environment.protected and environment.exact_tag_only and environment.required_reviewers
    ):
        errors.append("release environment is not protected for exact tags")
    if not row.immutable_releases.enabled:
        errors.append("immutable releases are not enabled")
    package = row.package
    if package.visibility != "public" or package.linked_repository != row.repository:
        errors.append("public package is not linked to its source repository")
    if not package.anonymous_pull:
        errors.append("anonymous pull is not verified")
    if package.standing_package_pat:
        errors.append("standing package PAT is present")
    retention = row.retention
    if not (
        retention.released_digests
        and retention.deployed_digests
        and retention.rollback_digests
        and not retention.automated_deletion
    ):
        errors.append("retention does not preserve released, deployed, and rollback digests")
    evidence = (
        rules.evidence,
        environment.evidence,
        row.immutable_releases.evidence,
        package.evidence,
        retention.evidence,
    )
    if any(item.status == "unavailable" for item in evidence):
        errors.append("a hard prerequisite has unavailable evidence")
    return errors


def require_compliant_controls(
    ledger: ContainerControlLedger, expected_repositories: set[str]
) -> None:
    """Fail unless every expected repository has all hard controls verified."""
    actual = set(ledger.repositories)
    if actual != expected_repositories:
        raise ControlLedgerError(
            "control ledger must exactly cover the router and registered backend repositories"
        )
    errors: list[str] = []
    for repository, row in sorted(ledger.repositories.items()):
        if row.status == "unavailable":
            errors.append(f"{repository}: hard controls unavailable: {row.reason}")
        else:
            errors.extend(f"{repository}: {error}" for error in _verified_row_errors(row))
    if errors:
        raise ControlLedgerError("; ".join(errors))


__all__ = [
    "ContainerControlLedger",
    "ControlLedgerError",
    "expected_fleet_repositories",
    "load_control_ledger",
    "require_compliant_controls",
]
