"""fleet-probe classifier: turn per-backend conformance Reports into an exit verdict.

Uses REAL conformance Report objects (a plain dataclass) — no mocks — so the test
exercises the actual pass/fail contract the live sweep depends on.
"""

from __future__ import annotations

from genefoundry_router.cli import _classify_fleet
from genefoundry_router.conformance import Report


def _conformant(name: str = "gnomad-link") -> Report:
    rep = Report("http://x", name, "stateless")
    rep.check("POST /mcp → 200", True)
    rep.check("POST /mcp does not 307", True)
    return rep


def _redirecting(name: str = "genereviews-link") -> Report:
    """The genereviews regression: 307 scheme-downgrade on POST /mcp."""
    rep = Report("http://x", name, "stateless")
    rep.check("POST /mcp does not 307", False, "got 307 Location='http://x/mcp'")
    return rep


def test_classify_fleet_all_conformant_exits_zero():
    code, lines = _classify_fleet([("gnomad", _conformant(), None)])
    assert code == 0
    assert any("PASS" in line and "gnomad" in line for line in lines)


def test_classify_fleet_flags_307_backend_nonconformant():
    code, lines = _classify_fleet(
        [("gnomad", _conformant(), None), ("genereviews", _redirecting(), None)]
    )
    assert code == 1  # a backend violating the transport contract fails the sweep
    assert any("FAIL" in line and "genereviews" in line for line in lines)
    assert any("307" in line for line in lines)  # the specific failed check is surfaced


def test_classify_fleet_transport_error_exits_two():
    code, lines = _classify_fleet([("genereviews", None, "connect timeout")])
    assert code == 2  # unreachable/transport error is distinct from a contract violation
    assert any("ERROR" in line and "connect timeout" in line for line in lines)


def test_classify_fleet_nonconformance_outranks_transport_error():
    code, _ = _classify_fleet([("a", _redirecting(), None), ("b", None, "timeout")])
    assert code == 1  # an actionable contract violation takes precedence over a transient error
