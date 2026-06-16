from pathlib import Path


def test_makefile_has_fake_fleet_targets():
    text = Path("Makefile").read_text(encoding="utf-8")
    for target in ("dev-fleet:", "run-dev:", "test-e2e:", "snapshot-fleet:", "ci-full:"):
        assert target in text, f"missing Makefile target: {target}"
