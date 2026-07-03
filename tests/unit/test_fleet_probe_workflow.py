"""The live fleet-probe has a Makefile target and a scheduled, gated CI workflow.

This is the prod-conformance gate that closes the CI-green != prod-green gap that hid
the genereviews 307 regression: CI conformance proves the code conformant; this proves
the deployed fleet conformant on a schedule.
"""

import re
from pathlib import Path

WF = Path(".github/workflows/fleet-probe.yml")


def test_makefile_has_fleet_probe_target():
    text = Path("Makefile").read_text(encoding="utf-8")
    assert "fleet-probe:" in text
    assert "fleet-probe" in text and "ci/fleet-urls.env" in text  # runs against the live fleet URLs


def test_fleet_probe_workflow_present_and_gated():
    text = WF.read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "FLEET_PROBE_ENABLED" in text  # opt-in gate, mirrors DRIFT_ENABLED
    assert "fleet-probe" in text  # invokes the probe command/target


def test_fleet_probe_workflow_least_privilege_and_pinned():
    text = WF.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "write-all" not in text
    refs = re.findall(r"uses:\s*(\S+)", text)
    assert refs, "expected at least one external action"
    for ref in refs:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"


def test_fleet_probe_workflow_loads_urls_via_filter_not_raw_cat():
    text = WF.read_text(encoding="utf-8")
    assert "grep -E" in text and "ci/fleet-urls.env" in text
    assert "cat ci/fleet-urls.env" not in text
