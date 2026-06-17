from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app, build_server


def test_build_server_skips_disabled_and_urlless(gnomad_fake):
    settings = RouterSettings(_env_file=None)
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        BackendDef(name="hgnc", url_env="Y", namespace="hgnc", enabled=False),
        BackendDef(name="gtex", url_env="Z", namespace="gtex"),  # url=None -> skipped
    ]
    server = build_server(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    assert isinstance(server, FastMCP)


async def test_built_server_exposes_namespaced_tools(gnomad_fake):
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    # enable_search=False so raw namespaced names are listed (search would hide them).
    server = build_server(
        settings, registry, proxy_targets={"gnomad": gnomad_fake}, enable_search=False
    )
    async with Client(server) as client:
        names = {t.name for t in await client.list_tools()}
    assert "gnomad_get_variant_details" in names


async def test_server_surfaces_discovery_instructions(gnomad_fake):
    # issue #3: the host's model must be oriented on the search surface via the
    # MCP `instructions` field, listing only enabled namespaces.
    settings = RouterSettings(_env_file=None)
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        BackendDef(name="hgnc", url_env="Y", namespace="hgnc", enabled=False),
    ]
    server = build_server(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    async with Client(server) as client:
        instructions = client.initialize_result.instructions or ""
    assert "search_tools" in instructions
    assert "call_tool" in instructions
    assert "gnomad" in instructions
    assert "hgnc" not in instructions  # disabled backend is not advertised


def test_build_app_serves_health(gnomad_fake):
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "healthy"
