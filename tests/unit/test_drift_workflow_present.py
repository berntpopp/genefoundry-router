"""The drift workflow exists, is opt-in, least-privilege, SHA-pinned, fail-safe."""

import re
from pathlib import Path

WF = Path(".github/workflows/drift.yml")


def test_drift_workflow_present_and_gated():
    text = WF.read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "DRIFT_ENABLED" in text  # opt-in gate
    assert "DRIFT_HEARTBEAT_URL" in text  # heartbeat
    assert "tool-drift" in text  # dedup label


def test_permissions_are_least_privilege():
    text = WF.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "issues: write" in text
    # No broad grants.
    assert "write-all" not in text
    assert "contents: write" not in text


def test_all_external_actions_are_sha_pinned():
    refs = re.findall(r"uses:\s*(\S+)", WF.read_text(encoding="utf-8"))
    assert refs, "expected at least one external action"
    for ref in refs:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"


def test_heartbeat_is_fail_safe():
    # The dead-man's-switch must fire even when the drift step fails.
    assert re.search(r"always\(\)\s*&&\s*env\.DRIFT_HEARTBEAT_URL", WF.read_text(encoding="utf-8"))


def test_fleet_urls_loaded_via_filter_not_raw_cat():
    text = WF.read_text(encoding="utf-8")
    # Comments in ci/fleet-urls.env must not reach $GITHUB_ENV — load via a grep filter.
    assert "grep -E" in text and "ci/fleet-urls.env" in text
    assert "cat ci/fleet-urls.env" not in text
