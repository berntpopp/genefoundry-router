import pytest

from genefoundry_router.exceptions import StartupError
from genefoundry_router.runtime_drift import RuntimeDriftGuard


def test_changed_fails_startup_in_enforce_mode() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "old"}, mode="enforce")
    with pytest.raises(StartupError, match="changed tool definition"):
        guard.evaluate(
            {"gnomad_get_gene": "new"},
            phase="startup",
            unreachable=set(),
        )


def test_added_and_removed_degrade_without_startup_failure() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_old": "a"}, mode="enforce")
    report = guard.evaluate(
        {"gnomad_new": "b"},
        phase="startup",
        unreachable=set(),
    )
    assert report.added == ["gnomad_new"]
    assert report.removed == ["gnomad_old"]
    assert guard.degraded is True
    assert guard.quarantined == frozenset({"gnomad_new"})


def test_poll_changed_definition_is_quarantined_without_raising() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "old"}, mode="enforce")
    report = guard.evaluate(
        {"gnomad_get_gene": "new"},
        phase="poll",
        unreachable=set(),
    )
    assert report.changed == ["gnomad_get_gene"]
    assert guard.quarantined == frozenset({"gnomad_get_gene"})


def test_unreachable_namespace_is_excluded_from_both_sides() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "a"}, mode="enforce")
    report = guard.evaluate({}, phase="startup", unreachable={"gnomad"})
    assert report.has_drift is False


def test_off_mode_never_reports_or_quarantines() -> None:
    guard = RuntimeDriftGuard(pinned={}, mode="off")
    report = guard.evaluate(
        {"gnomad_get_gene": "new"},
        phase="startup",
        unreachable=set(),
    )
    assert report.has_drift is False
    assert guard.degraded is False
    assert guard.quarantined == frozenset()
