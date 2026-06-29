"""MCP Transport Standard v1 conformance gate (vendored).

Skips unless CONFORMANCE_MCP_URL points at a running server. The conformance.yml
workflow sets it after `make docker-up`; local `make ci-local` skips it.
"""

from __future__ import annotations

import os

import pytest

from .conformance import run_probe

MCP_URL = os.environ.get("CONFORMANCE_MCP_URL")
EXPECTED_NAME = os.environ.get("CONFORMANCE_NAME", "REPLACE-ME-link")
TIER = os.environ.get("CONFORMANCE_TIER", "stateless")


@pytest.mark.skipif(not MCP_URL, reason="set CONFORMANCE_MCP_URL to run the live probe")
def test_mcp_transport_standard_v1() -> None:
    report = run_probe(MCP_URL, expected_name=EXPECTED_NAME, tier=TIER)
    assert report.conformant, "non-conformant:\n  " + "\n  ".join(report.failed)
