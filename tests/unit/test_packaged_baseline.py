from importlib.resources import files

from genefoundry_router.devtools.fakes import load_manifest


def test_reviewed_baseline_is_packaged_and_parseable() -> None:
    baseline = files("genefoundry_router.data").joinpath("fleet-baseline.json")
    with baseline.open("rb") as handle:
        manifest = load_manifest(handle)
    assert len(manifest.backends) == 21
    assert all(backend.tools for backend in manifest.backends.values())
