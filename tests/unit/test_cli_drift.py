"""`genefoundry-router drift` — drift vs unreachable, with exit codes 0/1/2."""

from pathlib import Path

from typer.testing import CliRunner

from genefoundry_router.cli import app
from genefoundry_router.devtools.fakes import load_manifest

runner = CliRunner()
PINNED = Path("tests/fixtures/fleet_manifest.json")


def test_drift_ok_when_live_matches_pinned(monkeypatch):
    async def fake(_registry):
        return load_manifest(PINNED), set()

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift", "--manifest", str(PINNED)])
    assert result.exit_code == 0, result.output
    assert "no tool-definition drift" in result.output.lower()


def test_changed_tool_exits_1(monkeypatch):
    live = load_manifest(PINNED).model_copy(deep=True)
    ns = next(iter(live.backends))
    live.backends[ns].tools[0].description += " <IMPORTANT>tampered</IMPORTANT>"

    async def fake(_registry):
        return live, set()

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift", "--manifest", str(PINNED)])
    assert result.exit_code == 1
    assert "CHANGED" in result.output


def test_unreachable_is_not_drift_exits_2(monkeypatch):
    pinned = load_manifest(PINNED)
    gone = next(iter(pinned.backends))
    live = pinned.model_copy(
        update={"backends": {k: v for k, v in pinned.backends.items() if k != gone}}
    )

    async def fake(_registry):
        return live, {gone}

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift", "--manifest", str(PINNED)])
    assert result.exit_code == 2  # availability, not a rug-pull
    assert "UNREACHABLE" in result.output
    assert "REMOVED" not in result.output  # the unreachable backend is NOT reported as removed


def test_drift_takes_precedence_over_unreachable(monkeypatch):
    live = load_manifest(PINNED).model_copy(deep=True)
    names = list(live.backends)
    live.backends[names[0]].tools[0].description += " tampered"
    gone = names[1]
    del live.backends[gone]

    async def fake(_registry):
        return live, {gone}

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift", "--manifest", str(PINNED)])
    assert result.exit_code == 1  # security beats availability


def test_drift_defaults_to_packaged_baseline(monkeypatch) -> None:
    baseline = Path("genefoundry_router/data/fleet-baseline.json")

    async def fake(_registry):
        return load_manifest(baseline), set()

    monkeypatch.setattr("genefoundry_router.cli._snapshot_live", fake)
    result = runner.invoke(app, ["drift"])
    assert result.exit_code == 0, result.output
