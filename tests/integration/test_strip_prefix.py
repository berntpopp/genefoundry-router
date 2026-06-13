from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.normalization import apply_normalizations
from genefoundry_router.registry import BackendDef, TransformConfig


async def test_pubtator_prefix_stripped_at_gateway(pubtator_fake):
    gateway = FastMCP("genefoundry")
    backend = BackendDef(
        name="pubtator",
        url_env="X",
        namespace="pubtator",
        tags=["literature", "entity"],
        transform=TransformConfig(strip_prefix="pubtator_"),
    )
    register_backend(gateway, backend, proxy_target=pubtator_fake)
    await apply_normalizations(gateway, [backend])  # async post-mount pass
    async with Client(gateway) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    # leaf was pubtator_search_literature -> namespaced pubtator_pubtator_search_literature
    # -> stripped back to pubtator_search_literature (single, correct prefix)
    assert "pubtator_search_literature" in names
    assert "pubtator_pubtator_search_literature" not in names
    # tags injected so BM25 can index them. Over the MCP wire, FastMCP exposes tags
    # under tool.meta["fastmcp"]["tags"] (the client-side Tool has no .tags attribute).
    stripped = next(t for t in tools if t.name == "pubtator_search_literature")
    client_tags = set((stripped.meta or {}).get("fastmcp", {}).get("tags", []))
    assert {"literature", "entity"} <= client_tags
