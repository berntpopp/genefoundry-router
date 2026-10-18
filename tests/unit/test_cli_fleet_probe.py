"""fleet-probe classifier: turn per-backend conformance Reports into an exit verdict.

Uses REAL conformance Report objects (a plain dataclass) — no mocks — so the test
exercises the actual pass/fail contract the live sweep depends on.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from typer.testing import CliRunner

from genefoundry_router.cli import _classify_fleet, app
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


def test_fleet_probe_runs_concurrently(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = CliRunner()
    yaml_file = tmp_path / "servers.yaml"
    yaml_file.write_text(
        "servers:\n"
        "  - { name: b1, url_env: U1, namespace: b1 }\n"
        "  - { name: b2, url_env: U2, namespace: b2 }\n"
    )
    monkeypatch.setenv("U1", "http://backend1/mcp")
    monkeypatch.setenv("U2", "http://backend2/mcp")

    barrier = threading.Barrier(2, timeout=2.0)

    def fake_probe(url: str, **kw: object) -> Report:
        barrier.wait()
        rep = Report(url, str(kw.get("expected_name", "test")), "stateless")
        rep.check("check", True)
        return rep

    monkeypatch.setattr("genefoundry_router.conformance.run_probe", fake_probe)
    result = runner.invoke(app, ["fleet-probe", "--servers-file", str(yaml_file)])
    assert result.exit_code == 0, result.output
    assert "2/2 enabled backends conformant" in result.output


def test_fleet_probe_honors_servers_file_envvar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = CliRunner()
    yaml_file = tmp_path / "custom_servers.yaml"
    yaml_file.write_text("servers:\n  - { name: custom, url_env: U_CUSTOM, namespace: custom }\n")
    monkeypatch.setenv("GF_SERVERS_FILE", str(yaml_file))
    monkeypatch.setenv("U_CUSTOM", "http://custom/mcp")

    def fake_probe(url: str, **kw: object) -> Report:
        rep = Report(url, "custom-link", "stateless")
        rep.check("check", True)
        return rep

    monkeypatch.setattr("genefoundry_router.conformance.run_probe", fake_probe)
    result = runner.invoke(app, ["fleet-probe"])
    assert result.exit_code == 0, result.output
    assert "1/1 enabled backends conformant" in result.output
