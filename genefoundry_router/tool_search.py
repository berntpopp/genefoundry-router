"""BM25 tool-search surface to control tool overload across the fleet.

The default FastMCP search serializer dumps each hit's full nested ``outputSchema``
plus its repeated ``_meta`` block — discovery, not use, became the dominant token
cost in Claude's usage reports (Finding 2). ``serialize_tools_compact`` keeps the
full ``inputSchema`` (the agent's argument contract) but collapses the output schema
to a one-line ``returns`` summary and drops ``_meta``. ``search_tools(query,
detail="full")`` restores the complete dump on demand.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from fastmcp.server.context import Context
from fastmcp.server.transforms.search.base import serialize_tools_for_output_json
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform
from fastmcp.tools.base import Tool

from genefoundry_router.config import RouterSettings

log = structlog.get_logger(__name__)

# Pinned, always-listed essentials (namespaced gateway names). Spec §19 Q4 / Finding 7:
# the fleet's entry-point resolvers (symbol->ID, variant-ID normalization) that nearly
# every downstream call depends on — pinning them saves a search_tools round-trip on the
# most common first step. Every other tool is reachable via search_tools -> call_tool.
DEFAULT_ALWAYS_VISIBLE: list[str] = [
    "gnomad_resolve_variant_id",
    "gnomad_search_genes",
]

_MAX_RETURN_FIELDS = 12


def _type_label(schema: Any) -> str:
    """Terse one-token type label for a JSON-schema fragment (heuristic, for summaries)."""
    if not isinstance(schema, dict):
        return "any"
    t = schema.get("type")
    if isinstance(t, str) and t:
        if t == "array":
            return f"{_type_label(schema.get('items'))}[]"
        return t
    if "$ref" in schema or "allOf" in schema:
        return "object"
    for key in ("anyOf", "oneOf"):
        if key in schema:
            opts = [_type_label(s) for s in schema[key]]
            non_null = [o for o in dict.fromkeys(opts) if o != "null"]
            label = " | ".join(non_null) if non_null else "null"
            return f"{label}?" if "null" in opts and non_null else label
    return "object" if "properties" in schema else "any"


def summarize_returns(schema: dict[str, Any] | None) -> str:
    """One-line summary of an output schema's top-level shape (Finding 2)."""
    if not isinstance(schema, dict):
        return "any"
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        items = list(props.items())
        body = ", ".join(f"{k}: {_type_label(v)}" for k, v in items[:_MAX_RETURN_FIELDS])
        if len(items) > _MAX_RETURN_FIELDS:
            body += ", …"
        return "{" + body + "}"
    return _type_label(schema)


def _compact_entry(tool: Tool) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": tool.name}
    if tool.description:
        entry["description"] = tool.description.strip()
    if tool.parameters:
        entry["inputSchema"] = tool.parameters
    if tool.output_schema is not None:
        entry["returns"] = summarize_returns(tool.output_schema)
    if tool.tags:
        entry["tags"] = sorted(tool.tags)
    return entry


def serialize_tools_compact(tools: list[Tool]) -> list[dict[str, Any]]:
    """Lean discovery payload: full inputSchema, one-line returns, no outputSchema/_meta."""
    return [_compact_entry(t) for t in tools]


class CompactBM25SearchTransform(BM25SearchTransform):
    """BM25 search whose ``search_tools`` defaults to a token-lean serialization.

    ``detail="full"`` returns the original full JSON dump (nested outputSchema + _meta)
    for the rare case an agent needs the complete output schema.
    """

    def _make_search_tool(self) -> Tool:
        transform = self

        async def search_tools(
            query: Annotated[str, "Natural language query to search for tools"],
            detail: Annotated[
                Literal["compact", "full"],
                "compact (default): inputSchema + one-line returns; full: complete schemas",
            ] = "compact",
            ctx: Context = None,  # type: ignore[assignment]
        ) -> str | list[dict[str, Any]]:
            """Search for tools using natural language.

            Returns matching tool definitions ranked by relevance. Defaults to a
            compact form (full inputSchema, one-line ``returns`` summary); pass
            ``detail="full"`` for the complete output schema.
            """
            hidden = await transform._get_visible_tools(ctx)
            results = await transform._search(hidden, query)
            if detail == "full":
                return serialize_tools_for_output_json(results)
            return serialize_tools_compact(list(results))

        return Tool.from_function(fn=search_tools, name=self._search_tool_name)


def apply_tool_search(
    server: FastMCP,
    settings: RouterSettings,
    always_visible: list[str] | None = None,
) -> None:
    """Replace the full tool listing with search_tools + call_tool + pinned tools."""
    pinned = always_visible if always_visible is not None else DEFAULT_ALWAYS_VISIBLE
    server.add_transform(
        CompactBM25SearchTransform(
            max_results=settings.GF_SEARCH_MAX_RESULTS,
            always_visible=pinned,
        )
    )
    log.info("tool_search_enabled", max_results=settings.GF_SEARCH_MAX_RESULTS, pinned=pinned)
