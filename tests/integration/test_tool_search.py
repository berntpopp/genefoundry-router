from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server
from genefoundry_router.tool_search import (
    DEFAULT_ALWAYS_VISIBLE,
    apply_tool_search,
    resolve_entrypoints,
)


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


def test_resolve_entrypoints_namespaces_and_skips_disabled():
    registry = [
        BackendDef(
            name="gnomad",
            url_env="X",
            namespace="gnomad",
            entrypoints=["resolve_variant_id", "search_genes"],
        ),
        BackendDef(name="mondo", url_env="Y", namespace="mondo", entrypoints=["resolve_disease"]),
        BackendDef(
            name="hgnc",
            url_env="Z",
            namespace="hgnc",
            enabled=False,
            entrypoints=["resolve_symbol"],
        ),
    ]
    assert resolve_entrypoints(registry) == [
        "gnomad_resolve_variant_id",
        "gnomad_search_genes",
        "mondo_resolve_disease",
    ]


def test_resolve_entrypoints_falls_back_to_default():
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    assert resolve_entrypoints(registry) == list(DEFAULT_ALWAYS_VISIBLE)


async def test_entrypoint_pin_bypasses_search_ranking():
    # The deep-research fix: a canonical resolver pinned via entrypoints is listed
    # deterministically, so it never depends on BM25 ranking surfacing it.
    settings = RouterSettings(_env_file=None)
    fake = make_fake_backend("mondo-link", ["resolve_disease", "get_disease", "search_diseases"])
    registry = [
        BackendDef(name="mondo", url_env="X", namespace="mondo", entrypoints=["resolve_disease"])
    ]
    server = build_server(settings, registry, proxy_targets={"mondo": fake})
    async with Client(server) as client:
        listed = {t.name for t in await client.list_tools()}
    assert "mondo_resolve_disease" in listed  # pinned -> deterministically visible
    assert "mondo_get_disease" not in listed  # non-entrypoint stays behind search


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
