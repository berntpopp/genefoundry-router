import pytest
from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.registry import BackendDef


@pytest.fixture
def gateway() -> FastMCP:
    return FastMCP("genefoundry")


async def _tool_names(server: FastMCP) -> set[str]:
    async with Client(server) as client:
        return {t.name for t in await client.list_tools()}


async def test_namespacing_is_collision_free(gateway, gnomad_fake, gtex_fake):
    register_backend(
        gateway,
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        proxy_target=gnomad_fake,
    )
    register_backend(
        gateway,
        BackendDef(name="gtex", url_env="X", namespace="gtex"),
        proxy_target=gtex_fake,
    )
    names = await _tool_names(gateway)
    # both backends expose search_genes; namespacing keeps them distinct
    assert "gnomad_search_genes" in names
    assert "gtex_search_genes" in names
    assert "gnomad_get_variant_details" in names
    assert "gtex_get_gene_information" in names


async def test_proxied_call_round_trips(gateway, gnomad_fake):
    register_backend(
        gateway,
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        proxy_target=gnomad_fake,
    )
    async with Client(gateway) as client:
        result = await client.call_tool("gnomad_get_variant_details", {"value": "hi"})
    assert result.data == {"tool": "get_variant_details", "server": "gnomad-link", "value": "hi"}
