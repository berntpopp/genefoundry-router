"""drift.py docstring must name the CI-pinned baseline the drift workflow actually diffs."""

import genefoundry_router.drift as drift_mod


def test_docstring_names_the_ci_baseline() -> None:
    doc = drift_mod.__doc__ or ""
    assert "ci/fleet-baseline.json" in doc, "name the live CI baseline (drift.yml pins it)"
    assert "tests/fixtures/fleet_manifest.json" in doc, "keep the offline fixture correctly described"
