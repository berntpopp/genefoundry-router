"""Fail-closed tests for fleet GitHub/GHCR control evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genefoundry_router.release import controls
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


def _main_rule() -> dict[str, object]:
    return {
        "active": True,
        "targets_main": True,
        "requires_pull_request": True,
        "required_approving_review_count": 1,
        "blocks_force_pushes": True,
        "blocks_deletions": True,
        "bypass_actors": [],
        "evidence": _evidence(),
    }


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
    rows = {repository: _row(repository) for repository in sorted(repositories)}
    router = "berntpopp/genefoundry-router"
    for row in rows.values():
        row["role"] = "backend"
    if router in rows:
        rows[router]["role"] = "trusted-builder"
        rows[router]["main_branch_ruleset"] = _main_rule()
    return {
        "schema_version": 1,
        "reviewed_at": "2026-07-13T12:00:00Z",
        "repositories": rows,
    }


def test_only_the_trusted_builder_requires_the_main_branch_rule() -> None:
    router = "berntpopp/genefoundry-router"
    backend = "berntpopp/example-link"
    payload = _ledger({router, backend})
    payload["repositories"][router]["role"] = "trusted-builder"  # type: ignore[index]
    payload["repositories"][router]["main_branch_ruleset"] = _main_rule()  # type: ignore[index]
    payload["repositories"][backend]["role"] = "backend"  # type: ignore[index]

    require_compliant_controls(load_control_ledger(payload), {router, backend})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("main_branch_ruleset", "active", False), "main branch"),
        (("main_branch_ruleset", "targets_main", False), "main branch"),
        (("main_branch_ruleset", "requires_pull_request", False), "main branch"),
        (("main_branch_ruleset", "bypass_actors", ["RepositoryRole:5"]), "main branch"),
        (("main_branch_ruleset", "required_approving_review_count", 2), "invalid control ledger"),
        (("main_branch_ruleset", "blocks_force_pushes", False), "main branch"),
        (("main_branch_ruleset", "blocks_deletions", False), "main branch"),
    ],
)
def test_trusted_builder_main_branch_control_fails_closed(
    change: tuple[str, str, object], message: str
) -> None:
    router = "berntpopp/genefoundry-router"
    payload = _ledger({router})
    row = payload["repositories"][router]  # type: ignore[index]
    row["role"] = "trusted-builder"
    row["main_branch_ruleset"] = _main_rule()
    row[change[0]][change[1]] = change[2]

    with pytest.raises(ControlLedgerError, match=message):
        require_compliant_controls(load_control_ledger(payload), {router})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active", "true"),
        ("targets_main", 1),
        ("required_approving_review_count", True),
        ("required_approving_review_count", 1.0),
    ],
)
def test_main_branch_control_rejects_coercible_policy_values(field: str, value: object) -> None:
    router = "berntpopp/genefoundry-router"
    payload = _ledger({router})
    rule = payload["repositories"][router]["main_branch_ruleset"]  # type: ignore[index]
    rule[field] = value

    with pytest.raises(ControlLedgerError, match="invalid control ledger"):
        load_control_ledger(payload)


def test_trusted_builder_main_branch_control_requires_available_evidence() -> None:
    router = "berntpopp/genefoundry-router"
    payload = _ledger({router})
    rule = payload["repositories"][router]["main_branch_ruleset"]  # type: ignore[index]
    rule["evidence"] = {
        **_evidence(),
        "status": "unavailable",
        "reason": "ruleset API unavailable",
    }

    with pytest.raises(ControlLedgerError, match="main branch"):
        require_compliant_controls(load_control_ledger(payload), {router})


def test_backend_forbids_a_main_branch_rule() -> None:
    router = "berntpopp/genefoundry-router"
    backend = "berntpopp/example-link"
    payload = _ledger({router, backend})
    payload["repositories"][backend]["main_branch_ruleset"] = _main_rule()  # type: ignore[index]

    with pytest.raises(ControlLedgerError, match="invalid control ledger"):
        load_control_ledger(payload)


def test_compliance_requires_the_router_to_be_the_only_trusted_builder() -> None:
    router = "berntpopp/genefoundry-router"
    backend = "berntpopp/example-link"
    payload = _ledger({router, backend})
    payload["repositories"][router]["role"] = "backend"  # type: ignore[index]
    payload["repositories"][router].pop("main_branch_ruleset")  # type: ignore[index]
    payload["repositories"][backend]["role"] = "trusted-builder"  # type: ignore[index]
    payload["repositories"][backend]["main_branch_ruleset"] = _main_rule()  # type: ignore[index]

    with pytest.raises(ControlLedgerError, match="trusted builder"):
        require_compliant_controls(load_control_ledger(payload), {router, backend})


def test_router_repository_is_derived_from_project_metadata(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project.urls]\nRepository = "https://github.com/example/router"\n',
        encoding="utf-8",
    )

    assert controls.router_repository(pyproject) == "example/router"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/router",
        "https://gitlab.com/example/router",
        "https://github.com/example/router/",
        "https://github.com/example/router/issues",
    ],
)
def test_router_repository_rejects_noncanonical_urls(tmp_path: Path, url: str) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project.urls]\nRepository = "{url}"\n', encoding="utf-8")

    with pytest.raises(ControlLedgerError, match="Repository URL"):
        controls.router_repository(pyproject)


def test_fleet_and_compliance_anchor_router_identity_outside_caller_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    router_root = Path(__file__).resolve().parents[2]
    caller = tmp_path / "caller-backend"
    caller.mkdir()
    (caller / "pyproject.toml").write_text(
        '[project.urls]\nRepository = "https://github.com/example/caller-backend"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(caller)

    repositories = expected_fleet_repositories(router_root / "servers.yaml")

    assert "berntpopp/genefoundry-router" in repositories
    assert "example/caller-backend" not in repositories
    require_compliant_controls(load_control_ledger(_ledger(repositories)), repositories)


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
    rows = payload["repositories"]
    assert isinstance(rows, dict)
    router = controls.router_repository()
    for row in rows.values():
        row["role"] = "backend"
    rows[router]["role"] = "trusted-builder"
    rows[router]["main_branch_ruleset"] = _main_rule()
    ledger = load_control_ledger(payload)

    assert set(ledger.repositories) == repositories
    require_compliant_controls(ledger, repositories)


def test_release_candidate_make_target_requires_release_manifests() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert "RELEASE_MANIFESTS" in makefile
    assert "--release-manifests" in makefile
    assert "--revisions" not in makefile
