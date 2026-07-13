"""Fail-closed tests for fleet GitHub/GHCR control evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genefoundry_router.release.controls import (
    ControlLedgerError,
    expected_fleet_repositories,
    load_control_ledger,
    require_compliant_controls,
)


def _evidence(source: str = "api") -> dict[str, object]:
    value: dict[str, object] = {
        "status": "verified",
        "source": source,
        "url": "https://github.com/berntpopp/example/settings",
        "verified_at": "2026-07-13T12:00:00Z",
    }
    if source == "manual":
        value["reviewer"] = "bernt-popp"
    return value


def _row(repository: str) -> dict[str, object]:
    package = repository.split("/", maxsplit=1)[1]
    return {
        "status": "verified",
        "repository": repository,
        "tag_ruleset": {
            "active": True,
            "restricts_creation": True,
            "restricts_update": True,
            "restricts_deletion": True,
            "restricts_non_fast_forward": True,
            "bypass_actors": ["release-maintainers"],
            "evidence": _evidence(),
        },
        "release_environment": {
            "protected": True,
            "exact_tag_only": True,
            "required_reviewers": ["bernt-popp"],
            "evidence": _evidence("manual"),
        },
        "immutable_releases": {"enabled": True, "evidence": _evidence()},
        "package": {
            "name": f"ghcr.io/berntpopp/{package}",
            "visibility": "public",
            "linked_repository": repository,
            "anonymous_pull": True,
            "standing_package_pat": False,
            "evidence": _evidence(),
        },
        "retention": {
            "released_digests": True,
            "deployed_digests": True,
            "rollback_digests": True,
            "automated_deletion": False,
            "evidence": _evidence("manual"),
        },
    }


def _ledger(repositories: set[str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "reviewed_at": "2026-07-13T12:00:00Z",
        "repositories": {repository: _row(repository) for repository in sorted(repositories)},
    }


def test_complete_fleet_control_ledger_passes() -> None:
    repositories = expected_fleet_repositories(Path("servers.yaml"))
    ledger = load_control_ledger(_ledger(repositories))

    require_compliant_controls(ledger, repositories)

    assert len(ledger.repositories) == 22
    assert "berntpopp/genefoundry-router" in ledger.repositories


def test_control_ledger_rejects_missing_repository_atomically() -> None:
    repositories = expected_fleet_repositories(Path("servers.yaml"))
    payload = _ledger(repositories)
    rows = payload["repositories"]
    assert isinstance(rows, dict)
    rows.pop("berntpopp/gnomad-link")

    with pytest.raises(ControlLedgerError, match="exactly cover"):
        require_compliant_controls(load_control_ledger(payload), repositories)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("tag_ruleset", "restricts_deletion"), False, "tag ruleset"),
        (("release_environment", "protected"), False, "release environment"),
        (("immutable_releases", "enabled"), False, "immutable releases"),
        (("package", "visibility"), "private", "public package"),
        (("package", "anonymous_pull"), False, "anonymous pull"),
        (("package", "standing_package_pat"), True, "standing package PAT"),
        (("retention", "rollback_digests"), False, "retention"),
    ],
)
def test_hard_prerequisite_failure_blocks_release(
    path: tuple[str, str], value: object, message: str
) -> None:
    repositories = {"berntpopp/genefoundry-router"}
    payload = _ledger(repositories)
    row = payload["repositories"]["berntpopp/genefoundry-router"]  # type: ignore[index]
    row[path[0]][path[1]] = value

    with pytest.raises(ControlLedgerError, match=message):
        require_compliant_controls(load_control_ledger(payload), repositories)


def test_unavailable_evidence_never_auto_passes() -> None:
    repositories = {"berntpopp/genefoundry-router"}
    payload = _ledger(repositories)
    row = payload["repositories"]["berntpopp/genefoundry-router"]  # type: ignore[index]
    row["immutable_releases"]["evidence"] = {
        "status": "unavailable",
        "source": "manual",
        "url": "https://github.com/berntpopp/genefoundry-router/settings",
        "verified_at": "2026-07-13T12:00:00Z",
        "reviewer": "bernt-popp",
        "reason": "setting is not available through the current API",
    }

    with pytest.raises(ControlLedgerError, match="unavailable"):
        require_compliant_controls(load_control_ledger(payload), repositories)


def test_manual_evidence_requires_named_reviewer() -> None:
    repositories = {"berntpopp/genefoundry-router"}
    payload = copy.deepcopy(_ledger(repositories))
    evidence = payload["repositories"]["berntpopp/genefoundry-router"][  # type: ignore[index]
        "release_environment"
    ]["evidence"]
    evidence.pop("reviewer")

    with pytest.raises(ControlLedgerError, match="invalid control ledger"):
        load_control_ledger(payload)


def test_checked_in_ledger_covers_every_repository_and_is_release_ready() -> None:
    repositories = expected_fleet_repositories(Path("servers.yaml"))
    payload = json.loads(Path("ci/container-controls.json").read_text(encoding="utf-8"))
    ledger = load_control_ledger(payload)

    assert set(ledger.repositories) == repositories
    require_compliant_controls(ledger, repositories)


def test_release_candidate_make_target_requires_release_manifests() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert "RELEASE_MANIFESTS" in makefile
    assert "--release-manifests" in makefile
    assert "--revisions" not in makefile
