from pathlib import Path

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fake_fleet import check_dev_config
from genefoundry_router.devtools.fakes import load_manifest


def _dev_env() -> dict[str, str]:
    env: dict[str, str] = {}
    with Path(".env.dev").open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env


def test_dev_registry_resolves_to_localhost():
    registry = load_registry("servers.dev.yaml", _dev_env())
    gnomad = next(b for b in registry if b.namespace == "gnomad")
    assert gnomad.url == "http://127.0.0.1:9100/gnomad/mcp"


def test_check_dev_config_passes_for_matching_manifest():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    registry = load_registry("servers.dev.yaml", _dev_env())
    enabled = [b for b in registry if b.enabled and b.namespace in manifest.backends]
    assert check_dev_config(enabled, manifest, "127.0.0.1", 9100) == []


def test_check_dev_config_reports_mismatch():
    manifest = load_manifest("tests/fixtures/fleet_manifest.json")
    registry = load_registry("servers.dev.yaml", _dev_env())
    enabled = [b for b in registry if b.enabled and b.namespace in manifest.backends]
    problems = check_dev_config(enabled, manifest, "127.0.0.1", 9999)  # wrong port
    assert problems  # at least one URL mismatch reported
