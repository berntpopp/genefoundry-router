"""Ruleset bypass actors the control-audit App cannot see (``bypass_actors: null``).

GitHub omits ``bypass_actors`` from the rulesets API unless the caller has admin
access; the audit App is deliberately read-only. ``None`` therefore means "not
visible to this token" -- never "nobody can bypass" -- and is accepted only on a
``partial`` row that names the gap as manual evidence.
"""

from __future__ import annotations

import pytest

from genefoundry_router.release.controls import (
    ControlLedgerError,
    load_control_ledger,
    require_compliant_controls,
)
from tests.release.test_controls import _ledger, _to_partial

ROUTER = "berntpopp/genefoundry-router"
BYPASS_GAP = "ruleset bypass actors"


def _partial_router(*, tag_bypass: object, main_bypass: object, note: bool) -> dict[str, object]:
    payload = _ledger({ROUTER})
    row = _to_partial(payload["repositories"][ROUTER])  # type: ignore[index]
    row["tag_ruleset"]["bypass_actors"] = tag_bypass  # type: ignore[index]
    row["main_branch_ruleset"]["bypass_actors"] = main_bypass  # type: ignore[index]
    if note:
        row["manual_evidence_required"].append(BYPASS_GAP)  # type: ignore[union-attr]
    payload["repositories"][ROUTER] = row  # type: ignore[index]
    return payload


def test_partial_row_accepts_invisible_bypass_actors_named_as_manual_evidence() -> None:
    payload = _partial_router(tag_bypass=None, main_bypass=None, note=True)

    warnings = require_compliant_controls(load_control_ledger(payload), {ROUTER})

    assert any(BYPASS_GAP in warning for warning in warnings)


@pytest.mark.parametrize(("tag", "main"), [(None, []), (["RepositoryRole:5"], None)])
def test_invisible_bypass_actors_without_the_manual_note_fail_closed(
    tag: object, main: object
) -> None:
    payload = _partial_router(tag_bypass=tag, main_bypass=main, note=False)

    with pytest.raises(ControlLedgerError, match=r"bypass|main branch ruleset"):
        require_compliant_controls(load_control_ledger(payload), {ROUTER})


def test_verified_row_never_accepts_invisible_bypass_actors() -> None:
    for field in ("tag_ruleset", "main_branch_ruleset"):
        payload = _ledger({ROUTER})
        payload["repositories"][ROUTER][field]["bypass_actors"] = None  # type: ignore[index]

        with pytest.raises(ControlLedgerError, match=r"bypass|main branch ruleset"):
            require_compliant_controls(load_control_ledger(payload), {ROUTER})


def test_visible_bypass_on_main_still_fails_even_with_the_manual_note() -> None:
    payload = _partial_router(
        tag_bypass=["RepositoryRole:5"], main_bypass=["RepositoryRole:5"], note=True
    )

    with pytest.raises(ControlLedgerError, match="main branch ruleset"):
        require_compliant_controls(load_control_ledger(payload), {ROUTER})


def test_app_token_rulesets_without_bypass_actors_yield_a_compliant_partial_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the response shape the read-only App actually gets (no bypass_actors)."""
    from scripts import audit_container_controls as audit
    from tests.release import test_control_audit as fixtures

    hidden = {k: v for k, v in fixtures.RULESET_DETAIL.items() if k != "bypass_actors"}
    hidden_main = {k: v for k, v in fixtures.MAIN_RULESET_DETAIL.items() if k != "bypass_actors"}
    fixtures._install_api(
        monkeypatch,
        {f"repos/{ROUTER}/rulesets/1": hidden, f"repos/{ROUTER}/rulesets/2": hidden_main},
    )
    fixtures._install_anonymous_pull(monkeypatch, 200)

    row = audit.build_row(ROUTER, "trusted-builder")

    assert row["status"] == "partial"
    assert row["tag_ruleset"]["bypass_actors"] is None
    assert row["main_branch_ruleset"]["bypass_actors"] is None
    assert BYPASS_GAP in row["manual_evidence_required"]
    ledger = {"schema_version": 1, "reviewed_at": row["tag_ruleset"]["evidence"]["verified_at"]}
    ledger["repositories"] = {ROUTER: row}
    warnings = require_compliant_controls(load_control_ledger(ledger), {ROUTER})
    assert any(BYPASS_GAP in warning for warning in warnings)


def test_visible_but_empty_tag_bypass_is_still_unproven(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import audit_container_controls as audit
    from tests.release import test_control_audit as fixtures

    empty = {**fixtures.RULESET_DETAIL, "bypass_actors": []}
    fixtures._install_api(monkeypatch, {f"repos/{ROUTER}/rulesets/1": empty})

    assert audit.probe_tag_ruleset(ROUTER) is None
