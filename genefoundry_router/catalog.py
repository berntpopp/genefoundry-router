"""One definition of "the fleet catalog": the router's normalized tool set.

Everything that reasons about tool definitions -- the runtime drift guard, the ``drift``
CLI, and the reviewed pin written by ``scripts/snapshot_fleet.py`` -- must look at the
SAME definitions, or the tripwire compares apples to oranges.

The definitions that matter are the ones the model actually receives: post-proxy and
post-normalization. They are not the raw definitions a backend advertises, because FastMCP
rebuilds a tool's schema whenever the normalization pass applies a ToolTransform (tag
injection / renames) -- reordering ``required``, emitting ``"required": []``, and adding
``additionalProperties: false``. Snapshotting backends directly yields a catalog that no
component ever serves, which is how ``GF_DRIFT_MODE=enforce`` came to refuse startup on a
fleet that had not drifted at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genefoundry_router.devtools.fakes import ToolSpec
    from genefoundry_router.registry import BackendDef


async def capture_normalized_catalog(
    registry: list[BackendDef],
) -> tuple[dict[str, list[ToolSpec]], set[str]]:
    """Build the aggregated router over the live fleet and return its normalized catalog.

    Returns ``(tools_by_namespace, unreachable_namespaces)``. A backend that harvested no
    tools is reported unreachable rather than as a removed tool, so an outage never reads
    as drift.
    """
    from genefoundry_router.config import RouterSettings
    from genefoundry_router.devtools.fakes import ToolSpec
    from genefoundry_router.normalization import apply_normalizations
    from genefoundry_router.runtime_drift import _model_dict
    from genefoundry_router.server import _seed_reachability, build_server

    enabled = [backend for backend in registry if backend.enabled]
    server = build_server(RouterSettings(), enabled, enable_search=False)
    tools = await apply_normalizations(server, enabled)
    unreachable = _seed_reachability(enabled, tools)

    by_namespace: dict[str, list[ToolSpec]] = {}
    for tool in tools:
        namespace, _, leaf = tool.name.partition("_")
        by_namespace.setdefault(namespace, []).append(
            ToolSpec(
                name=leaf,
                description=tool.description or "",
                inputSchema=tool.parameters or {"type": "object", "properties": {}},
                outputSchema=tool.output_schema,
                annotations=_model_dict(tool.annotations),
                execution=_model_dict(tool.execution),
                tags=sorted(tool.tags or []),
            )
        )
    return by_namespace, unreachable
