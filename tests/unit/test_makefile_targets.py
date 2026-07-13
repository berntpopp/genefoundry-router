from pathlib import Path


def test_makefile_has_fake_fleet_targets():
    text = Path("Makefile").read_text(encoding="utf-8")
    for target in ("dev-fleet:", "run-dev:", "test-e2e:", "snapshot-fleet:", "ci-full:"):
        assert target in text, f"missing Makefile target: {target}"


def test_coverage_target_includes_release_control_plane_tests() -> None:
    text = Path("Makefile").read_text(encoding="utf-8")
    target = text.split("test-cov:", 1)[1].split("\n\n", 1)[0]
    assert "tests/release" in target


def test_ci_local_includes_release_control_plane_tests() -> None:
    text = Path("Makefile").read_text(encoding="utf-8")
    release_target = text.split("test-release:", 1)[1].split("\n\n", 1)[0]
    ci_local_target = text.split("ci-local:", 1)[1].split("##", 1)[0]

    assert "uv run pytest tests/release -q" in release_target
    assert "test-release" in ci_local_target
