from unittest.mock import AsyncMock

import pytest
from fastmcp.server.transforms import Transform
from fastmcp.tools import Tool

from genefoundry_router.exceptions import StartupError
from genefoundry_router.runtime_drift import RuntimeDriftGuard


def test_runtime_drift_guard_subclasses_transform() -> None:
    guard = RuntimeDriftGuard(pinned={}, mode="off")
    assert isinstance(guard, Transform)


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


@pytest.mark.asyncio
async def test_list_tools_filters_quarantined() -> None:
    guard = RuntimeDriftGuard(pinned={"t1": "a", "t2": "b"}, mode="warn")
    guard.quarantined.add("t1")

    tools = [
        Tool(name="t1", description="", parameters={}),
        Tool(name="t2", description="", parameters={}),
    ]

    visible = await guard.list_tools(tools)
    assert len(visible) == 1
    assert visible[0].name == "t2"


@pytest.mark.asyncio
async def test_get_tool_quarantined_returns_none_without_calling_next() -> None:
    guard = RuntimeDriftGuard(pinned={"t1": "a"}, mode="warn")
    guard.quarantined.add("t1")

    call_next = AsyncMock()

    result = await guard.get_tool("t1", call_next)
    assert result is None
    call_next.assert_not_called()

    # Non-quarantined calls downstream
    expected_tool = Tool(name="t2", description="", parameters={})
    call_next.return_value = expected_tool

    result2 = await guard.get_tool("t2", call_next)
    assert result2 == expected_tool
    call_next.assert_called_once_with("t2", version=None)


def test_recovery_on_matching_pinned_digest() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "pinned_hash"}, mode="warn")
    # Simulate drift during poll
    guard.evaluate({"gnomad_get_gene": "drifted_hash"}, phase="poll", unreachable=set())
    assert "gnomad_get_gene" in guard.quarantined

    # Simulate recovery: definition matches pinned
    guard.evaluate({"gnomad_get_gene": "pinned_hash"}, phase="poll", unreachable=set())
    assert "gnomad_get_gene" not in guard.quarantined


def test_outage_retains_quarantine() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "pinned_hash"}, mode="warn")
    # Drift
    guard.evaluate({"gnomad_get_gene": "drifted_hash"}, phase="poll", unreachable=set())
    assert "gnomad_get_gene" in guard.quarantined

    # Outage: gnomad namespace unreachable
    guard.evaluate({}, phase="poll", unreachable={"gnomad"})
    # Must NOT clear quarantine on outage!
    assert "gnomad_get_gene" in guard.quarantined


def test_removed_unpinned_tool_clears_quarantine_and_restores_health() -> None:
    guard = RuntimeDriftGuard(pinned={"gnomad_get_gene": "pinned_hash"}, mode="warn")
    # 1. Unreviewed tool added during poll -> quarantined and degraded
    report1 = guard.evaluate(
        {"gnomad_get_gene": "pinned_hash", "gnomad_extra": "extra_hash"},
        phase="poll",
        unreachable=set(),
    )
    assert report1.added == ["gnomad_extra"]
    assert "gnomad_extra" in guard.quarantined
    assert guard.degraded is True

    # 2. Outage while unreviewed tool is quarantined -> quarantine preserved
    guard.evaluate({}, phase="poll", unreachable={"gnomad"})
    assert "gnomad_extra" in guard.quarantined

    # 3. Unreviewed tool rolled back / removed on reachable refresh -> quarantine cleared and healthy
    report2 = guard.evaluate(
        {"gnomad_get_gene": "pinned_hash"},
        phase="poll",
        unreachable=set(),
    )
    assert report2.has_drift is False
    assert "gnomad_extra" not in guard.quarantined
    assert guard.degraded is False
