"""BM25 tool-search surface to control tool overload across the fleet."""

from __future__ import annotations

import structlog
from fastmcp import FastMCP
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform

from genefoundry_router.config import RouterSettings

log = structlog.get_logger(__name__)

# Pinned, always-listed essentials (namespaced gateway names). Spec §19 Q4.
DEFAULT_ALWAYS_VISIBLE: list[str] = [
    "gnomad_resolve_variant_id",
    "gnomad_search_genes",
]


def apply_tool_search(
    server: FastMCP,
    settings: RouterSettings,
    always_visible: list[str] | None = None,
) -> None:
    """Replace the full tool listing with search_tools + call_tool + pinned tools."""
    pinned = always_visible if always_visible is not None else DEFAULT_ALWAYS_VISIBLE
    server.add_transform(
        BM25SearchTransform(
            max_results=settings.GF_SEARCH_MAX_RESULTS,
            always_visible=pinned,
        )
    )
    log.info("tool_search_enabled", max_results=settings.GF_SEARCH_MAX_RESULTS, pinned=pinned)
