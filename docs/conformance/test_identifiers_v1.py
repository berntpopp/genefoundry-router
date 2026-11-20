"""Foreign-identifier conformance gate, Response-Envelope Standard v1 §2b (vendored).

Skips unless CONFORMANCE_MCP_URL points at a running server, like the other gates here. See
identifiers.py for what it asserts. This backend's parameter declarations and known failures are
in identifiers.local.json when present, otherwise the seed in identifiers.json under
`backends.<CONFORMANCE_NAME>`.
"""

from __future__ import annotations

import os

import pytest

from .identifiers import failures, render, run_probe

MCP_URL = os.environ.get("CONFORMANCE_MCP_URL")
EXPECTED_NAME = os.environ.get("CONFORMANCE_NAME", "REPLACE-ME-link")


@pytest.mark.skipif(not MCP_URL, reason="set CONFORMANCE_MCP_URL to run the live probe")
def test_foreign_identifiers_v1() -> None:
    table = run_probe(MCP_URL, expected_name=EXPECTED_NAME)
    print(render(table))
    bad = failures(table)
    assert not bad, "non-conformant:\n  " + "\n  ".join(bad)
