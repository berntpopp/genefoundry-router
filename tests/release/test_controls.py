"""Fail-closed tests for fleet GitHub/GHCR control evidence."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from genefoundry_router.release import controls
from genefoundry_router.release.controls import (
    MAX_CONTROL_EVIDENCE_AGE,
    ControlLedgerError,
    expected_fleet_repositories,
    load_control_ledger,
    oldest_evidence_age,
    require_compliant_controls,
)

ROOT = Path(__file__).resolve().parents[2]
VALIDATE_CONTROLS = ROOT / "scripts/validate_container_controls.py"
# Synthetic fixtures must be dated relative to now. `require_compliant_controls` bounds
# evidence age against the wall clock, so a hard-coded stamp would turn every ledger test into
# a time bomb that goes red on a calendar date with no code change. Staleness has its own
# tests below, and those pass an explicit `now` instead of relying on the clock.
FIXTURE_MOMENT = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
FIXTURE_VERIFIED_AT = FIXTURE_MOMENT.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_validator(ledger: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, str(VALIDATE_CONTROLS), str(ledger)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _evidence(source: str = "api") -> dict[str, object]:
    value: dict[str, object] = {
        "status": "verified",
        "source": source,
        "url": "https://github.com/berntpopp/example/settings",
        "verified_at": FIXTURE_VERIFIED_AT,
    }
    if source == "manual":
        value["reviewer"] = "bernt-popp"
    return value


def _main_rule() -> dict[str, object]:
    return {
        "active": True,
        "targets_main": True,
        "requires_pull_request": True,
        "required_approving_review_count": 0,
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
        "reviewed_at": FIXTURE_VERIFIED_AT,
        "repositories": rows,
    }


def test_main_branch_control_accepts_zero_required_approvals_for_one_maintainer() -> None:
    router = "berntpopp/genefoundry-router"
    payload = _ledger({router})
    row = payload["repositories"][router]  # type: ignore[index]
    row["main_branch_ruleset"]["required_approving_review_count"] = 0  # type: ignore[index]

    require_compliant_controls(load_control_ledger(payload), {router})


def test_main_branch_control_rejects_one_required_approval_for_one_maintainer() -> None:
    router = "berntpopp/genefoundry-router"
    payload = _ledger({router})
    row = payload["repositories"][router]  # type: ignore[index]
    row["main_branch_ruleset"]["required_approving_review_count"] = 1  # type: ignore[index]

    with pytest.raises(ControlLedgerError, match="invalid control ledger"):
        load_control_ledger(payload)


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
        "verified_at": FIXTURE_VERIFIED_AT,
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
    """The committed ledger must pass the release gate *exactly as committed*.

    This test used to read the real ledger and then inject `role` into every row and a
    synthetic `main_branch_ruleset` into the router's row before validating — asserting the
    ledger was release-ready after making it release-ready. It passed for ten days against a
    ledger `_container-release.yml` rejected outright, so `make ci-local` reported green while
    every container release failed closed. Load the committed bytes and nothing else: this is
    the only check standing between a stale ledger and a silently unreleasable fleet.

    This now also asserts freshness against the real clock, deliberately: it must use the same
    bound as the release gate, or `make ci-local` goes green while `_container-release.yml`
    fails closed -- exactly the divergence described above. The committed ledger is dated
    2026-07-30, so this starts failing around 2026-10-28 unless the control audit runs and the
    refreshed ledger is committed.
    """
    repositories = expected_fleet_repositories(Path("servers.yaml"))
    ledger = load_control_ledger(Path("ci/container-controls.json"))

    assert set(ledger.repositories) == repositories
    require_compliant_controls(ledger, repositories)


def test_release_gate_accepts_evidence_exactly_at_the_age_limit() -> None:
    repositories = {"berntpopp/genefoundry-router"}
    ledger = load_control_ledger(_ledger(repositories))

    require_compliant_controls(ledger, repositories, now=FIXTURE_MOMENT + MAX_CONTROL_EVIDENCE_AGE)


def test_release_gate_rejects_evidence_past_the_age_limit() -> None:
    repositories = {"berntpopp/genefoundry-router"}
    ledger = load_control_ledger(_ledger(repositories))
    expired = FIXTURE_MOMENT + MAX_CONTROL_EVIDENCE_AGE + timedelta(seconds=1)

    with pytest.raises(ControlLedgerError, match="stale"):
        require_compliant_controls(ledger, repositories, now=expired)


def test_evidence_age_is_measured_from_the_oldest_claim_not_the_review_stamp() -> None:
    """A fresh `reviewed_at` must not launder a control whose own evidence is ancient."""
    repositories = {"berntpopp/genefoundry-router"}
    payload = _ledger(repositories)
    row = payload["repositories"]["berntpopp/genefoundry-router"]  # type: ignore[index]
    stale = FIXTURE_MOMENT - MAX_CONTROL_EVIDENCE_AGE - timedelta(days=1)
    row["retention"]["evidence"]["verified_at"] = stale.strftime("%Y-%m-%dT%H:%M:%SZ")  # type: ignore[index]

    with pytest.raises(ControlLedgerError, match="stale"):
        require_compliant_controls(load_control_ledger(payload), repositories, now=FIXTURE_MOMENT)


def test_release_gate_rejects_post_dated_evidence() -> None:
    """Evidence dated in the future would otherwise defeat the age bound outright."""
    repositories = {"berntpopp/genefoundry-router"}
    ledger = load_control_ledger(_ledger(repositories))

    with pytest.raises(ControlLedgerError, match="in the future"):
        require_compliant_controls(ledger, repositories, now=FIXTURE_MOMENT - timedelta(days=1))


def test_release_gate_tolerates_small_clock_skew_between_runners() -> None:
    repositories = {"berntpopp/genefoundry-router"}
    ledger = load_control_ledger(_ledger(repositories))

    require_compliant_controls(ledger, repositories, now=FIXTURE_MOMENT - timedelta(minutes=1))


@pytest.mark.parametrize(
    "malformed",
    [
        "not-a-timestamp",
        "2026-07-30",  # date only: not RFC 3339, no time component
        "2026-07-30T15:05:02",  # missing UTC offset: AwareDatetime requires tz-aware
        "2026-13-40T99:99:99Z",  # syntactically timestamp-shaped, calendrically impossible
        "",
    ],
)
def test_load_control_ledger_rejects_malformed_verified_at(malformed: str) -> None:
    """A hand-edited or corrupted `verified_at` must fail closed at parse time.

    This is parse-time rejection (`load_control_ledger`), distinct from the age checks
    above (`require_compliant_controls`): a malformed timestamp can never be compared to
    `now`, so it must never reach that comparison in the first place.
    """
    repositories = {"berntpopp/genefoundry-router"}
    payload = _ledger(repositories)
    row = payload["repositories"]["berntpopp/genefoundry-router"]  # type: ignore[index]
    row["retention"]["evidence"]["verified_at"] = malformed  # type: ignore[index]

    with pytest.raises(ControlLedgerError, match="invalid control ledger"):
        load_control_ledger(payload)


def test_oldest_evidence_age_reports_the_minimum_claim_age() -> None:
    """A passing gate can still report freshness -- it is not only an internal fail bound."""
    repositories = {"berntpopp/genefoundry-router"}
    payload = _ledger(repositories)
    row = payload["repositories"]["berntpopp/genefoundry-router"]  # type: ignore[index]
    older = (FIXTURE_MOMENT - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row["retention"]["evidence"]["verified_at"] = older  # type: ignore[index]
    ledger = load_control_ledger(payload)

    age = oldest_evidence_age(ledger, now=FIXTURE_MOMENT)

    assert age >= timedelta(days=5)
    assert age < timedelta(days=6)


def test_validate_controls_cli_accepts_an_exact_compliant_fleet(tmp_path: Path) -> None:
    repositories = expected_fleet_repositories(ROOT / "servers.yaml")
    ledger = tmp_path / "container-controls.json"
    ledger.write_text(json.dumps(_ledger(repositories)), encoding="utf-8")

    completed = _run_validator(ledger)

    assert completed.returncode == 0, completed.stderr
    assert "validated 22 compliant repository controls" in completed.stdout
    assert "day(s) old" in completed.stdout  # evidence age is reported on success, not silent


def test_validate_controls_cli_rejects_malformed_json(tmp_path: Path) -> None:
    ledger = tmp_path / "container-controls.json"
    ledger.write_text("not json\n", encoding="utf-8")

    completed = _run_validator(ledger)

    assert completed.returncode == 1
    assert "control ledger is not compliant" in completed.stderr


def test_validate_controls_cli_rejects_inexact_fleet(tmp_path: Path) -> None:
    repositories = expected_fleet_repositories(ROOT / "servers.yaml")
    payload = _ledger(repositories)
    rows = payload["repositories"]
    assert isinstance(rows, dict)
    rows.pop("berntpopp/gnomad-link")
    ledger = tmp_path / "container-controls.json"
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run_validator(ledger)

    assert completed.returncode == 1
    assert "exactly cover" in completed.stderr


def test_validate_controls_cli_rejects_exact_fleet_with_noncompliant_hard_control(
    tmp_path: Path,
) -> None:
    repositories = expected_fleet_repositories(ROOT / "servers.yaml")
    payload = _ledger(repositories)
    row = payload["repositories"]["berntpopp/gnomad-link"]  # type: ignore[index]
    row["package"]["standing_package_pat"] = True  # type: ignore[index]
    ledger = tmp_path / "container-controls.json"
    ledger.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run_validator(ledger)

    assert completed.returncode == 1
    assert "standing package PAT" in completed.stderr


def test_release_candidate_make_target_requires_release_manifests() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")

    assert "RELEASE_MANIFESTS" in makefile
    assert "--release-manifests" in makefile
    assert "--revisions" not in makefile
