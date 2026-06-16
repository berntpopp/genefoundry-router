from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.server import build_server

from .conftest import dev_registry


async def test_full_catalog_matches_manifest_projection(fleet):
    manifest, base_url = fleet
    settings = RouterSettings(_env_file=None)
    registry = dev_registry(manifest, base_url)
    # search OFF so the raw federated catalog is listable (the gateway->fake hop is real HTTP)
    server = build_server(settings, registry, enable_search=False)
    async with Client(server) as client:
        names = [t.name for t in await client.list_tools()]

    expected = {
        f"{ns}_{tool.name}" for ns, spec in manifest.backends.items() for tool in spec.tools
    }
    assert set(names) == expected  # no transform: names match v1 exactly
    assert len(names) == len(set(names))  # no collisions after namespacing
    assert "gnomad_search_genes" in names and "gtex_search_genes" in names  # collision resolved
