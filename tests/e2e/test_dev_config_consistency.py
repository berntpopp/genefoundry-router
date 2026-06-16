from genefoundry_router.devtools.fake_fleet import dev_config_warnings
from genefoundry_router.devtools.fakes import load_manifest


def test_committed_dev_config_matches_manifest():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    problems = dev_config_warnings(manifest, "127.0.0.1", 9100)
    assert problems == [], f"dev config drift: {problems}"
