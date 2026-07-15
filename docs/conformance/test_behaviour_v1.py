"""Behaviour Conformance v1 gate (vendored).

Skips unless CONFORMANCE_MCP_URL points at a running server. The conformance.yml workflow sets
it after `make docker-up`; a local `make ci-local` skips it.

This is the gate for the three recurring behavioural bugs (silent-empty filter, lying
total/truncated, error an LLM cannot act on). See docs/conformance/behaviour.py for what it
asserts and why it derives every probe from the server's own schema.
"""

from __future__ import annotations

import os

import pytest

from .behaviour import run_probe

MCP_URL = os.environ.get("CONFORMANCE_MCP_URL")
EXPECTED_NAME = os.environ.get("CONFORMANCE_NAME", "REPLACE-ME-link")


@pytest.mark.skipif(not MCP_URL, reason="set CONFORMANCE_MCP_URL to run the live probe")
def test_mcp_behaviour_standard_v1() -> None:
    report = run_probe(MCP_URL, expected_name=EXPECTED_NAME)
    # UNGATED counts against conformance (B7): a tool whose required parameters carry no
    # `examples` cannot be probed at all, and an unverifiable tool must never be certified.
    problems = report.failed + report.ungated
    assert report.conformant, "non-conformant:\n  " + "\n  ".join(problems)
