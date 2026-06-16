from fastmcp import Client, FastMCP

from genefoundry_router.devtools.fakes import build_fake_tool

GENE_SCHEMA = {
    "type": "object",
    "properties": {
        "gene_symbol": {"type": "string", "description": "HGNC gene symbol"},
        "limit": {"type": "integer", "description": "max rows"},
    },
    "required": ["gene_symbol"],
}


async def test_build_fake_tool_advertises_exact_input_schema():
    server = FastMCP("probe")
    server.add_tool(
        build_fake_tool("search_genes", "Search genes by symbol", GENE_SCHEMA, ["gene"])
    )
    async with Client(server) as client:
        tools = await client.list_tools()
        result = await client.call_tool("search_genes", {"gene_symbol": "PKD1"})
    tool = next(t for t in tools if t.name == "search_genes")
    assert tool.inputSchema["properties"].keys() == {"gene_symbol", "limit"}
    assert tool.inputSchema["properties"]["gene_symbol"]["description"] == "HGNC gene symbol"
    assert "gene" in (tool.meta or {}).get("fastmcp", {}).get("tags", [])
    assert result.data["args"] == {"gene_symbol": "PKD1"}
