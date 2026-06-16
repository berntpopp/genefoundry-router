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

            # (b) search_tools surfaces pubtator tools when queried by their
            # description/param words ("literature"/"query"/"pmid").
            hits = await client.call_tool("search_tools", {"query": "literature query pmid"})
            # hits.data is a list of tool dicts (name/description/inputSchema/meta);
            # stringify the whole structure so tool names are matchable.
            found = str(hits.data).lower()
            assert "pubtator_search_literature" in found or "pubtator_get_passages" in found

            # (c) call_tool invokes gnomad_search_genes end-to-end; the fake echoes
            # the argument back, so the gene symbol round-trips over real HTTP.
            result = await client.call_tool(
                "call_tool",
                {"name": "gnomad_search_genes", "arguments": {"gene_symbol": "PKD1"}},
            )
            assert "PKD1" in str(result.data)
    finally:
        server.should_exit = True
