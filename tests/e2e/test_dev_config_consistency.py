from pathlib import Path

from genefoundry_router.devtools.fake_fleet import dev_config_warnings
from genefoundry_router.devtools.fakes import load_manifest

ROOT = Path(__file__).resolve().parents[2]


def test_committed_dev_config_matches_manifest():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    problems = dev_config_warnings(manifest, "127.0.0.1", 9100)
    assert problems == [], f"dev config drift: {problems}"


def test_docker_example_requires_explicit_public_auth_choice():
    text = (ROOT / ".env.docker.example").read_text(encoding="utf-8")
    assert "\n# GF_AUTH_MODE=none\n" in text
    assert "\n# GF_ALLOW_INSECURE=true\n" in text
    assert "\nGF_AUTH_MODE=none\n" not in text
    assert "\nGF_ALLOW_INSECURE=true\n" not in text


def test_env_examples_document_hardening_settings():
    for relative_path in (".env.example", ".env.docker.example"):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "GF_TRUSTED_PROXY_HOPS=1" in text
        assert "# GF_METRICS_TOKEN=" in text
