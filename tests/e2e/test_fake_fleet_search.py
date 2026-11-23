from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.server import build_app

from .conftest import dev_registry, serve


async def test_search_and_call_over_the_wire(fleet):
    manifest, base_url = fleet
    settings = RouterSettings(_env_file=None)
    app = build_app(settings, dev_registry(manifest, base_url))  # search ON via lifespan
    server, router_url = serve(app)  # client -> gateway -> fake, all on the wire
    try:
        async with Client(f"{router_url}/mcp") as client:
            listed = {t.name for t in await client.list_tools()}
            # (a) pinned essentials stay visible even though BM25 search is on
            assert {"gnomad_resolve_variant_id", "gnomad_search_genes"} <= listed
            assert "search_tools" in listed and "call_tool" in listed

            # (b) search_tools surfaces pubtator_search_literature for a literature
            # intent (a tasks.yaml query); hits.data is a list of tool dicts.
            hits = await client.call_tool(
                "search_tools", {"query": "search biomedical literature for a gene and a disease"}
            )
            assert "pubtator_search_literature" in [h["name"] for h in hits.data]

            # (c) call_tool invokes gnomad_search_genes end-to-end; the fake echoes
            # the argument back, so the gene symbol round-trips over real HTTP.
            result = await client.call_tool(
                "call_tool",
                {"name": "gnomad_search_genes", "arguments": {"query": "PKD1"}},
            )
            assert "PKD1" in str(result.data)
    finally:
        server.should_exit = True
