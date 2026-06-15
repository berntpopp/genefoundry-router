"""Post-adoption proof: pubtator surfaces clean names WITHOUT a strip_prefix transform.

pubtator-link adopted the GeneFoundry Tool-Naming Standard v1
(berntpopp/pubtator-link#57, PR #64): every tool dropped its ``pubtator_`` self-
prefix. The gateway therefore no longer needs the stopgap ``transform`` block for
pubtator. This test loads the REAL ``servers.yaml`` pubtator entry, mounts a backend
that emits clean leaf names, and asserts the gateway surfaces a single, correct
``pubtator_<tool>`` prefix (no double prefix, no leftover transform), tags intact.
"""

from pathlib import Path

from fastmcp import Client, FastMCP

from genefoundry_router.composition import register_backend
from genefoundry_router.config import load_registry
from genefoundry_router.normalization import apply_normalizations

ROOT = Path(__file__).resolve().parents[2]


async def test_pubtator_clean_surface_no_transform(pubtator_clean_fake):
    backends = load_registry(ROOT / "servers.yaml", {})
    pubtator = next(b for b in backends if b.name == "pubtator")
    # the stopgap transform is gone now that the source adopted Standard v1
    assert pubtator.transform is None

    gateway = FastMCP("genefoundry")
    register_backend(gateway, pubtator, proxy_target=pubtator_clean_fake)
    await apply_normalizations(gateway, [pubtator])  # no-op rename pass + tag injection

    async with Client(gateway) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}

    # clean leaf search_literature -> namespaced once -> pubtator_search_literature
    assert "pubtator_search_literature" in names
    assert "pubtator_get_passages" in names
    # no residual double-prefix from a stale transform
    assert "pubtator_pubtator_search_literature" not in names

    # backend tags still injected so BM25 can index them (over the wire under meta)
    tool = next(t for t in tools if t.name == "pubtator_search_literature")
    client_tags = set((tool.meta or {}).get("fastmcp", {}).get("tags", []))
    assert {"literature", "entity"} <= client_tags
