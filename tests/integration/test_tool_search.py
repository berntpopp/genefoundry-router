from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server
from genefoundry_router.tool_search import DEFAULT_ALWAYS_VISIBLE, apply_tool_search


async def test_search_surface_hides_bulk_but_keeps_pinned(gnomad_fake, gtex_fake):
    settings = RouterSettings(_env_file=None)
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
        BackendDef(name="gtex", url_env="Y", namespace="gtex"),
    ]
    server = build_server(
        settings,
        registry,
        proxy_targets={"gnomad": gnomad_fake, "gtex": gtex_fake},
    )
    apply_tool_search(server, settings, always_visible=["gnomad_search_genes"])
    async with Client(server) as client:
        listed = {t.name for t in await client.list_tools()}
    # the BM25 surface is present
    assert "search_tools" in listed
    assert "call_tool" in listed
    # pinned essential remains directly listed
    assert "gnomad_search_genes" in listed
    # a non-pinned bulk tool is hidden from the default listing
    assert "gtex_get_gene_information" not in listed


def test_default_always_visible_is_documented():
    assert "search_tools" not in DEFAULT_ALWAYS_VISIBLE  # search_tools is synthetic
    assert DEFAULT_ALWAYS_VISIBLE  # non-empty default pinned set


async def _surface_descriptions(gnomad_fake) -> dict[str, str]:
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    server = build_server(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    apply_tool_search(server, settings)
    async with Client(server) as client:
        return {t.name: (t.description or "") for t in await client.list_tools()}


async def test_search_tools_description_frames_the_gateway(gnomad_fake):
    desc = (await _surface_descriptions(gnomad_fake))["search_tools"]
    assert "call_tool" in desc  # points at the next step
    # seeded keywords so a host-side tool search surfaces the router entrypoint
    assert "spliceai" in desc.lower()


async def test_call_tool_description_is_self_healing(gnomad_fake):
    desc = (await _surface_descriptions(gnomad_fake))["call_tool"]
    assert "<namespace>_<tool>" in desc  # the name format to pass
    # reframes host eviction ("Unknown tool") as recoverable, not router flakiness
    assert "unknown tool" in desc.lower()
    assert "search_tools" in desc
