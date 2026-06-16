from fastmcp import Client, FastMCP

from genefoundry_router.devtools.fakes import Manifest, build_fake_tool, make_backend_from_spec

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


def test_manifest_parses_and_builds_backend():
    raw = {
        "snapshot_meta": {
            "captured_at": "2026-06-16T00:00:00Z",
            "source": "local",
            "router_servers_file": "servers.yaml",
        },
        "backends": {
            "gnomad": {
                "version": "5.0.0",
                "tools": [
                    {
                        "name": "search_genes",
                        "description": "Search genes",
                        "tags": ["gene"],
                        "inputSchema": {
                            "type": "object",
                            "properties": {"gene_symbol": {"type": "string"}},
                        },
                    },
                ],
            },
        },
    }
    manifest = Manifest.model_validate(raw)
    assert manifest.backends["gnomad"].version == "5.0.0"
    backend = make_backend_from_spec("gnomad", manifest.backends["gnomad"])
    assert backend.name == "gnomad"


async def test_make_backend_from_spec_exposes_tools():
    spec = Manifest.model_validate(
        {
            "snapshot_meta": {"captured_at": "x", "source": "local", "router_servers_file": "s"},
            "backends": {
                "gtex": {
                    "version": "1.0.0",
                    "tools": [
                        {
                            "name": "get_gene_information",
                            "description": "d",
                            "tags": [],
                            "inputSchema": {"type": "object", "properties": {}},
                        },
                    ],
                }
            },
        }
    ).backends["gtex"]
    async with Client(make_backend_from_spec("gtex", spec)) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"get_gene_information"}
