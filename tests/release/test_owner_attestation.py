"""An owner attestation may close the named evidence gap, never mask an observed failure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from genefoundry_router.release.attestation import (
    OwnerAttestation,
    load_attestation,
    promote_partial_row,
)
from genefoundry_router.release.controls import (
    MAX_CONTROL_EVIDENCE_AGE,
    ControlLedgerError,
    load_control_ledger,
    require_compliant_controls,
)
from scripts import audit_container_controls as audit
from tests.release.test_control_audit import (
    MAIN_RULESET_DETAIL,
    REPO,
    RULESET_DETAIL,
    _install_anonymous_pull,
    _install_api,
)

NOW = datetime.now(UTC).replace(microsecond=0)
GAP = (
    "package linkage",
    "GITHUB_TOKEN publication",
    "standing package PAT absence",
    "retention",
    "ruleset bypass actors",
)


def _attestation(**overrides: Any) -> OwnerAttestation:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "confirmed": True,
        "attested_by": "berntpopp",
        "attested_at": (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "url": "https://github.com/berntpopp/genefoundry-router/pull/1",
        "controls": dict.fromkeys(GAP, "owner statement"),
        "ruleset_bypass_actors": {"tag": ["RepositoryRole:5"], "main": []},
    }
    payload.update(overrides)
    return OwnerAttestation.model_validate(payload)


def _hidden(detail: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in detail.items() if key != "bypass_actors"}


def _partial_row(monkeypatch: pytest.MonkeyPatch, *, bypass_visible: bool) -> dict[str, Any]:
    overrides = (
        {}
        if bypass_visible
        else {
            f"repos/{REPO}/rulesets/1": _hidden(RULESET_DETAIL),
            f"repos/{REPO}/rulesets/2": _hidden(MAIN_RULESET_DETAIL),
        }
    )
    _install_api(monkeypatch, overrides)
    _install_anonymous_pull(monkeypatch, 200)
    row = audit.build_row(REPO, role="trusted-builder")
    assert row["status"] == "partial"
    return row


def _gate(row: dict[str, Any]) -> list[str]:
    ledger = {"schema_version": 1, "reviewed_at": row["tag_ruleset"]["evidence"]["verified_at"]}
    ledger["repositories"] = {REPO: row}
    return require_compliant_controls(load_control_ledger(ledger), {REPO})


def test_confirmed_attestation_promotes_partial_row_to_a_verified_row_the_gate_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = promote_partial_row(_partial_row(monkeypatch, bypass_visible=False), _attestation())

    assert row["status"] == "verified"
    assert row["package"]["evidence"]["source"] == "manual"
    assert row["package"]["evidence"]["reviewer"] == "berntpopp"
    assert row["tag_ruleset"]["bypass_actors"] == ["RepositoryRole:5"]
    assert row["main_branch_ruleset"]["bypass_actors"] == []
    assert _gate(row) == []  # verified: no partial-row warnings left


def test_attestation_never_overwrites_observed_bypass_actors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = _partial_row(monkeypatch, bypass_visible=True)
    gap = [item for item in GAP if item != "ruleset bypass actors"]
    attestation = _attestation(
        controls=dict.fromkeys(gap, "owner statement"),
        ruleset_bypass_actors={"tag": ["Team:9"], "main": []},
    )

    row = promote_partial_row(partial, attestation)

    assert row["tag_ruleset"] == partial["tag_ruleset"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirmed": False},
        {"attested_at": (NOW - MAX_CONTROL_EVIDENCE_AGE - timedelta(days=1)).isoformat()},
        {"attested_at": (NOW + timedelta(days=1)).isoformat()},
        {"controls": dict.fromkeys(GAP[:-1], "owner statement")},
    ],
    ids=["unconfirmed", "stale", "post-dated", "incomplete"],
)
def test_unusable_attestation_leaves_the_row_partial(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]
) -> None:
    partial = _partial_row(monkeypatch, bypass_visible=False)

    assert promote_partial_row(partial, _attestation(**overrides), NOW) is partial


def test_attestation_never_promotes_an_observed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_api(monkeypatch)
    _install_anonymous_pull(monkeypatch, 404)
    row = audit.build_row(REPO, role="trusted-builder")

    assert row["status"] == "unavailable"
    assert promote_partial_row(row, _attestation()) is row


def test_attestation_requires_an_empty_main_bypass_list() -> None:
    with pytest.raises(ValueError, match="main"):
        _attestation(ruleset_bypass_actors={"tag": ["RepositoryRole:5"], "main": ["x"]})


def test_load_attestation_is_optional_but_fails_closed_when_malformed(tmp_path: Path) -> None:
    assert load_attestation(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(ControlLedgerError):
        load_attestation(bad)

