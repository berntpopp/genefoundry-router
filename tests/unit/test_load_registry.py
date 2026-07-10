from pathlib import Path

import pytest

from genefoundry_router.config import load_registry
from genefoundry_router.exceptions import RegistryError

FIX = Path(__file__).parent / "fixtures" / "servers_min.yaml"


def test_load_merges_defaults_and_resolves_urls():
    env = {
        "GF_GNOMAD_URL": "https://gnomad-link.example.org/mcp",
        "GF_PUBTATOR_URL": "https://pubtator-link.example.org/mcp",
    }
    backends = load_registry(FIX, env)
    by_name = {b.name: b for b in backends}

    assert by_name["gnomad"].url == "https://gnomad-link.example.org/mcp"
    assert by_name["gnomad"].cache_ttl == 300  # from defaults
    assert by_name["gnomad"].enabled is True

    assert by_name["pubtator"].cache_ttl == 600  # per-server override wins
    assert by_name["pubtator"].transform.strip_prefix == "pubtator_"

    # disabled backend with no env var still loads but url stays None
    assert by_name["hgnc"].enabled is False
    assert by_name["hgnc"].url is None


def test_missing_url_for_enabled_backend_leaves_url_none():
    env: dict[str, str] = {}  # no GF_GNOMAD_URL
    backends = load_registry(FIX, env)
    gnomad = next(b for b in backends if b.name == "gnomad")
    assert gnomad.enabled is True
    assert gnomad.url is None  # caller (validate/startup) decides how to warn/skip


def test_missing_file_raises_registry_error(tmp_path):
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "nope.yaml", {})


def test_registry_resolves_optional_backend_service_token(tmp_path) -> None:
    registry = tmp_path / "servers.yaml"
    registry.write_text(
        """
defaults: {transport: http}
servers:
  - name: pubtator
    namespace: pubtator
    url_env: GF_PUBTATOR_URL
    service_token_env: GF_PUBTATOR_TOKEN
""",
        encoding="utf-8",
    )
    backend = load_registry(
        registry,
        {
            "GF_PUBTATOR_URL": "https://pubtator.example/mcp",
            "GF_PUBTATOR_TOKEN": "service-secret",
        },
    )[0]
    assert backend.service_token == "service-secret"  # noqa: S105 - inert test value


def test_duplicate_namespace_raises(tmp_path):
    p = tmp_path / "dup.yaml"
    p.write_text(
        "servers:\n"
        "  - { name: a, url_env: GF_A_URL, namespace: dup }\n"
        "  - { name: b, url_env: GF_B_URL, namespace: dup }\n"
    )
    with pytest.raises(RegistryError):
        load_registry(p, {})
