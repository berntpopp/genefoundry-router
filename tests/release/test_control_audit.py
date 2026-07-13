"""The control audit must fail closed: an unproven control never becomes a pass."""

from __future__ import annotations

from typing import Any

import pytest

from genefoundry_router.release.controls import load_control_ledger, require_compliant_controls
from scripts import audit_container_controls as audit

REPO = "berntpopp/genefoundry-router"

RULESET_LIST = [{"id": 1, "name": audit.RULESET_NAME, "target": "tag"}]
RULESET_DETAIL = {
    "enforcement": "active",
    "rules": [{"type": rule} for rule in ("creation", "update", "deletion", "non_fast_forward")],
    "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5}],
}
ENVIRONMENT = {
    "protection_rules": [
        {"type": "required_reviewers", "reviewers": [{"reviewer": {"login": "berntpopp"}}]},
    ]
}
TAG_POLICIES = {"branch_policies": [{"type": "tag", "name": "v*.*.*"}]}
IMMUTABLE = {"enabled": True}


def _install_api(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any] | None = None) -> None:
    responses: dict[str, Any] = {
        f"repos/{REPO}/rulesets": RULESET_LIST,
        f"repos/{REPO}/rulesets/1": RULESET_DETAIL,
        f"repos/{REPO}/environments/release": ENVIRONMENT,
        f"repos/{REPO}/environments/release/deployment-branch-policies": TAG_POLICIES,
        f"repos/{REPO}/immutable-releases": IMMUTABLE,
    }
    responses.update(overrides or {})
    monkeypatch.setattr(audit, "_gh_api", lambda path: responses.get(path))


def _install_anonymous_pull(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(audit, "_anonymous_manifest_status", lambda repo: status)


def test_row_is_verified_when_every_control_is_proven(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_api(monkeypatch)
    _install_anonymous_pull(monkeypatch, 200)

    row = audit.build_row(REPO)

    assert row["status"] == "verified"
    assert row["package"]["anonymous_pull"] is True
    assert row["package"]["standing_package_pat"] is False
    ledger = load_control_ledger(
        {"schema_version": 1, "reviewed_at": audit._now(), "repositories": {REPO: row}}
    )
    require_compliant_controls(ledger, {REPO})


@pytest.mark.parametrize(
    ("control", "overrides", "pull_status"),
    [
        ("package", {}, 404),
        ("tag_ruleset", {f"repos/{REPO}/rulesets": []}, 200),
        (
            "tag_ruleset",
            {f"repos/{REPO}/rulesets/1": {**RULESET_DETAIL, "bypass_actors": []}},
            200,
        ),
        (
            "tag_ruleset",
            {f"repos/{REPO}/rulesets/1": {**RULESET_DETAIL, "enforcement": "evaluate"}},
            200,
        ),
        (
            "release_environment",
            {f"repos/{REPO}/environments/release": {"protection_rules": []}},
            200,
        ),
        (
            "release_environment",
            {
                f"repos/{REPO}/environments/release/deployment-branch-policies": {
                    "branch_policies": [{"type": "branch", "name": "v*.*.*"}]
                }
            },
            200,
        ),
        ("immutable_releases", {f"repos/{REPO}/immutable-releases": {"enabled": False}}, 200),
    ],
)
def test_unproven_control_blocks_the_release(
    monkeypatch: pytest.MonkeyPatch,
    control: str,
    overrides: dict[str, Any],
    pull_status: int,
) -> None:
    _install_api(monkeypatch, overrides)
    _install_anonymous_pull(monkeypatch, pull_status)

    row = audit.build_row(REPO)

    assert row["status"] == "unavailable"
    assert control in row["reason"]
    assert row["evidence"]["reviewer"] == audit.REVIEWER


def test_private_package_is_never_auto_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A package that exists but refuses an anonymous pull must not be recorded public."""
    _install_api(monkeypatch)
    _install_anonymous_pull(monkeypatch, 401)

    row = audit.build_row(REPO)

    assert row["status"] == "unavailable"
    assert "package" in row["reason"]


def test_unreachable_api_blocks_rather_than_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "_gh_api", lambda path: None)
    _install_anonymous_pull(monkeypatch, 0)

    row = audit.build_row(REPO)

    assert row["status"] == "unavailable"
    for control in ("tag_ruleset", "release_environment", "immutable_releases", "package"):
        assert control in row["reason"]
