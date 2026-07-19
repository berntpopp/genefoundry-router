"""The control audit must fail closed: an unproven control never becomes a pass."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from genefoundry_router.release.controls import load_control_ledger, require_compliant_controls
from scripts import audit_container_controls as audit

REPO = "berntpopp/genefoundry-router"

RULESET_LIST = [
    {"id": 1, "name": audit.RULESET_NAME, "target": "tag"},
    {"id": 2, "name": "Protect trusted-builder main", "target": "branch"},
]
RULESET_DETAIL = {
    "enforcement": "active",
    "rules": [{"type": rule} for rule in ("creation", "update", "deletion", "non_fast_forward")],
    "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5}],
}
# Current GitHub REST ``GET /repos/{owner}/{repo}/rulesets/{id}`` response shape. The API
# serializes neutral merge/reviewer/Copilot settings instead of omitting those fields.
MAIN_RULESET_DETAIL = {
    "enforcement": "active",
    "conditions": {
        "ref_name": {"include": ["refs/heads/main"], "exclude": []},
    },
    "bypass_actors": [],
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "pull_request",
            "parameters": {
                "allowed_merge_methods": ["squash", "merge", "rebase"],
                "automatic_copilot_code_review_enabled": False,
                "dismiss_stale_reviews_on_push": False,
                "require_code_owner_review": False,
                "require_last_push_approval": False,
                "required_approving_review_count": 1,
                "required_review_thread_resolution": False,
                "required_reviewers": [],
            },
        },
    ],
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
        f"repos/{REPO}/rulesets/2": MAIN_RULESET_DETAIL,
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

    row = audit.build_row(REPO, role="trusted-builder")

    assert row["status"] == "verified"
    assert row["role"] == "trusted-builder"
    assert row["main_branch_ruleset"] == {
        "active": True,
        "targets_main": True,
        "requires_pull_request": True,
        "required_approving_review_count": 1,
        "blocks_force_pushes": True,
        "blocks_deletions": True,
        "bypass_actors": [],
        "evidence": row["main_branch_ruleset"]["evidence"],
    }
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

    row = audit.build_row(REPO, role="backend")

    assert row["status"] == "unavailable"
    assert control in row["reason"]
    assert row["evidence"]["reviewer"] == audit.REVIEWER


def test_private_package_is_never_auto_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A package that exists but refuses an anonymous pull must not be recorded public."""
    _install_api(monkeypatch)
    _install_anonymous_pull(monkeypatch, 401)

    row = audit.build_row(REPO, role="backend")

    assert row["status"] == "unavailable"
    assert "package" in row["reason"]


def test_unreachable_api_blocks_rather_than_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "_gh_api", lambda path: None)
    _install_anonymous_pull(monkeypatch, 0)

    row = audit.build_row(REPO, role="backend")

    assert row["status"] == "unavailable"
    for control in ("tag_ruleset", "release_environment", "immutable_releases", "package"):
        assert control in row["reason"]


def test_main_branch_ruleset_probe_returns_exact_verified_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_api(monkeypatch)

    control = audit.probe_main_branch_ruleset(REPO)

    assert control is not None
    assert control["bypass_actors"] == []
    assert control["required_approving_review_count"] == 1
    for field in (
        "active",
        "targets_main",
        "requires_pull_request",
        "blocks_force_pushes",
        "blocks_deletions",
    ):
        assert control[field] is True


@pytest.mark.parametrize(
    "detail",
    [
        {**MAIN_RULESET_DETAIL, "bypass_actors": [{"actor_type": "RepositoryRole"}]},
        {**MAIN_RULESET_DETAIL, "enforcement": "evaluate"},
        {
            **MAIN_RULESET_DETAIL,
            "conditions": {
                "ref_name": {"include": ["refs/heads/dev"], "exclude": []},
            },
        },
        {
            **MAIN_RULESET_DETAIL,
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "pull_request",
                    "parameters": {"required_approving_review_count": 2},
                },
            ],
        },
        None,
        {},
        {**MAIN_RULESET_DETAIL, "conditions": {"ref_name": "unknown"}},
        {**MAIN_RULESET_DETAIL, "rules": "unknown"},
        {
            **MAIN_RULESET_DETAIL,
            "rules": [
                *MAIN_RULESET_DETAIL["rules"],
                {
                    "type": "pull_request",
                    "parameters": {"required_approving_review_count": 1},
                },
            ],
        },
    ],
)
def test_main_branch_ruleset_probe_rejects_untrusted_or_malformed_policy(
    monkeypatch: pytest.MonkeyPatch, detail: object
) -> None:
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


@pytest.mark.parametrize(
    "rule_type",
    [
        "required_status_checks",
        "merge_queue",
        "required_signatures",
        "required_linear_history",
        "unknown_future_rule",
    ],
)
def test_main_branch_ruleset_probe_rejects_additional_rule_types(
    monkeypatch: pytest.MonkeyPatch, rule_type: str
) -> None:
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [*MAIN_RULESET_DETAIL["rules"], {"type": rule_type}],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("dismiss_stale_reviews_on_push", True),
        ("require_code_owner_review", True),
        ("require_last_push_approval", True),
        ("required_review_thread_resolution", True),
        (
            "required_reviewers",
            [
                {
                    "file_patterns": ["*"],
                    "minimum_approvals": 1,
                    "reviewer": {"id": 1, "type": "Team"},
                }
            ],
        ),
        (
            "dismissal_restriction",
            {
                "allowed_actors": [{"id": 1, "type": "Team"}],
                "enabled": True,
            },
        ),
        ("allowed_merge_methods", ["squash"]),
        ("automatic_copilot_code_review_enabled", True),
        ("unknown_future_parameter", False),
    ],
)
def test_main_branch_ruleset_probe_rejects_additional_review_requirements(
    monkeypatch: pytest.MonkeyPatch, parameter: str, value: object
) -> None:
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [
            *MAIN_RULESET_DETAIL["rules"][:-1],
            {
                "type": "pull_request",
                "parameters": {
                    **MAIN_RULESET_DETAIL["rules"][-1]["parameters"],
                    parameter: value,
                },
            },
        ],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


def test_main_branch_ruleset_probe_accepts_neutral_dismissal_restriction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [
            *MAIN_RULESET_DETAIL["rules"][:-1],
            {
                "type": "pull_request",
                "parameters": {
                    **MAIN_RULESET_DETAIL["rules"][-1]["parameters"],
                    "dismissal_restriction": {"allowed_actors": [], "enabled": False},
                },
            },
        ],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is not None


@pytest.mark.parametrize(
    "methods",
    [
        ["merge", "squash", "squash"],
        ["merge", "squash", "unknown"],
        ["merge", "squash", 1],
    ],
)
def test_main_branch_ruleset_probe_rejects_non_neutral_merge_methods(
    monkeypatch: pytest.MonkeyPatch, methods: list[object]
) -> None:
    parameters = {
        **MAIN_RULESET_DETAIL["rules"][-1]["parameters"],
        "allowed_merge_methods": methods,
    }
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [
            *MAIN_RULESET_DETAIL["rules"][:-1],
            {"type": "pull_request", "parameters": parameters},
        ],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


@pytest.mark.parametrize(
    "dismissal_restriction",
    [
        None,
        {"enabled": False},
        {"allowed_actors": [{"id": 1, "type": "Team"}], "enabled": False},
        {"allowed_actors": [], "enabled": True},
        {"allowed_actors": [], "enabled": False, "unknown_future_field": False},
    ],
)
def test_main_branch_ruleset_probe_rejects_non_neutral_dismissal_restriction(
    monkeypatch: pytest.MonkeyPatch, dismissal_restriction: object
) -> None:
    parameters = {
        **MAIN_RULESET_DETAIL["rules"][-1]["parameters"],
        "dismissal_restriction": dismissal_restriction,
    }
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [
            *MAIN_RULESET_DETAIL["rules"][:-1],
            {"type": "pull_request", "parameters": parameters},
        ],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("required_approving_review_count", True),
        ("require_code_owner_review", 0),
    ],
)
def test_main_branch_ruleset_probe_rejects_json_type_coercion(
    monkeypatch: pytest.MonkeyPatch, parameter: str, value: object
) -> None:
    parameters = {
        **MAIN_RULESET_DETAIL["rules"][-1]["parameters"],
        parameter: value,
    }
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [
            *MAIN_RULESET_DETAIL["rules"][:-1],
            {"type": "pull_request", "parameters": parameters},
        ],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


def test_main_branch_ruleset_probe_rejects_missing_required_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parameters = dict(MAIN_RULESET_DETAIL["rules"][-1]["parameters"])
    parameters.pop("require_code_owner_review")
    detail = {
        **MAIN_RULESET_DETAIL,
        "rules": [
            *MAIN_RULESET_DETAIL["rules"][:-1],
            {"type": "pull_request", "parameters": parameters},
        ],
    }
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None


def test_trusted_builder_row_fails_closed_when_main_ruleset_is_unproven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": None})
    _install_anonymous_pull(monkeypatch, 200)

    row = audit.build_row(REPO, role="trusted-builder")

    assert row["status"] == "unavailable"
    assert "main_branch_ruleset" in row["reason"]
    assert "role" not in row


def test_backend_row_never_probes_or_includes_main_ruleset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_api(monkeypatch)
    _install_anonymous_pull(monkeypatch, 200)

    def unexpected_probe(repo: str) -> dict[str, Any] | None:
        raise AssertionError(f"unexpected main ruleset probe for {repo}")

    monkeypatch.setattr(audit, "probe_main_branch_ruleset", unexpected_probe)

    row = audit.build_row(REPO, role="backend")

    assert row["status"] == "verified"
    assert row["role"] == "backend"
    assert "main_branch_ruleset" not in row


def test_build_ledger_assigns_router_as_only_trusted_builder_outside_repo_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = "berntpopp/example-link"
    calls: list[tuple[str, str]] = []

    def record_row(repo: str, role: str) -> dict[str, Any]:
        calls.append((repo, role))
        return {"status": "unavailable", "repository": repo}

    monkeypatch.setattr(audit, "build_row", record_row)
    monkeypatch.chdir(tmp_path)

    audit.build_ledger({REPO, backend})

    assert calls == [(backend, "backend"), (REPO, "trusted-builder")]
