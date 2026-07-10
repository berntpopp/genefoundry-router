"""drift.py docstring must name the CI-pinned baseline the drift workflow actually diffs."""

import genefoundry_router.drift as drift_mod


def test_docstring_names_the_ci_baseline() -> None:
    doc = drift_mod.__doc__ or ""
    assert "genefoundry_router/data/fleet-baseline.json" in doc, (
        "name the packaged live baseline used by runtime and CI"
    )
    assert "tests/fixtures/fleet_manifest.json" in doc, (
        "keep the offline fixture correctly described"
    )
