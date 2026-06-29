"""`genefoundry-router drift` — compares live backends to the pinned manifest (rug-pull tripwire)."""

from pathlib import Path

from typer.testing import CliRunner

from genefoundry_router.cli import app
from genefoundry_router.devtools.fakes import Manifest, SnapshotMeta, load_manifest

runner = CliRunner()
PINNED = Path("tests/fixtures/fleet_manifest.json")


def test_drift_ok_when_live_matches_pinned(monkeypatch):
    async def fake_snapshot(_registry):
        return load_manifest(PINNED)

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake_snapshot)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 0, result.output
    assert "no tool-definition drift" in result.output.lower()


def test_drift_exits_nonzero_on_drift(monkeypatch):
    async def fake_snapshot(_registry):
        return Manifest(
            snapshot_meta=SnapshotMeta(captured_at="t", source="live", router_servers_file="s"),
            backends={},  # everything in the pinned manifest is now "removed"
        )

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake_snapshot)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 1
    assert "REMOVED" in result.output
