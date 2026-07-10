"""Stopgap normalization transforms for non-compliant backends.

These exist only until each -link repo adopts the Tool-Naming Standard v1; when a
source fix lands, delete the matching ``transform`` block from servers.yaml.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from fastmcp import FastMCP
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

from genefoundry_router.registry import BackendDef, qualified_name

log = structlog.get_logger(__name__)


async def _list_normalized_tools(server: FastMCP) -> list[Tool]:
    """Apply every security/catalog transform except the outer search projection."""
    tools: list[Tool] = list(await server._list_tools())  # type: ignore[attr-defined]
    for transform in server.transforms:
        if isinstance(transform, BM25SearchTransform):
            continue
        tools = list(await transform.list_tools(tools))
    return tools


def strip_prefix_name(tool_name: str, prefix: str) -> str:
    """Remove ``prefix`` from the start of ``tool_name`` if present."""
    return tool_name[len(prefix) :] if tool_name.startswith(prefix) else tool_name


def build_tool_transform(
    backend: BackendDef,
    present_tools: Iterable[str],
) -> ToolTransform | None:
    """Build a ToolTransform for a backend's gateway-visible (namespaced) tools.

    ``present_tools`` are the already-namespaced names (``<ns>_<leaf>``). Returns
    None when the backend declares no transform.
    """
    tc = backend.transform
    if tc is None:
        return None

    ns = backend.namespace
    transforms: dict[str, ToolTransformConfig] = {}
    for current in present_tools:
        leaf = current[len(ns) + 1 :] if current.startswith(f"{ns}_") else current
        new_leaf = leaf
        if tc.strip_prefix:
            new_leaf = strip_prefix_name(new_leaf, tc.strip_prefix)
        if leaf in tc.rename:
            new_leaf = tc.rename[leaf]
        args = {
            old: ArgTransformConfig(name=new) for old, new in tc.arg_rename.get(leaf, {}).items()
        }
        if new_leaf == leaf and not args:
            continue  # nothing to change for this tool
        # arguments must be a dict (None is rejected by ToolTransformConfig in 3.4.2);
        # an empty dict is the valid "no arg changes" form.
        transforms[current] = ToolTransformConfig(
            name=qualified_name(ns, new_leaf),
            arguments=args,
        )
    return ToolTransform(transforms) if transforms else None


async def apply_normalizations(server: FastMCP, registry: list[BackendDef]) -> list[Tool]:
    """Async post-mount pass: rename non-compliant tools, then inject backend tags.

    Enumerates with the PUBLIC ``await server.list_tools()``. Two passes so tag
    injection sees post-rename names. Resilient: a backend that fails to enumerate
    (unreachable proxy) is skipped and retried on the next poll (Task 22).
    """

    async def enumerate_catalog() -> list[Tool]:
        return await _list_normalized_tools(server)

    # Pass 1 — name/arg transforms
    try:
        tools = await enumerate_catalog()
    except Exception as exc:  # tolerate an unreachable backend at startup
        log.warning("normalization_list_failed", error=str(exc))
        return []
    present = [tool.name for tool in tools]
    renamed = False
    for backend in registry:
        if backend.transform is None:
            continue
        scoped = [n for n in present if n.startswith(f"{backend.namespace}_")]
        transform = build_tool_transform(backend, scoped)
        if transform is not None:
            server.add_transform(transform)
            renamed = True
            log.info("normalized", backend=backend.name, tools=len(scoped))

    if renamed:
        try:
            tools = await enumerate_catalog()
        except Exception as exc:
            log.warning("normalization_list_failed", error=str(exc))
            return []

    # Pass 2 — tag injection on post-rename names (union with any existing tags).
    # Uses add_transform(ToolTransform({...})) — the non-deprecated 3.4.2 path
    # (add_tool_transformation is deprecated). One batched transform for all tools.
    by_ns = {b.namespace: b for b in registry if b.tags}
    if not by_ns:
        return tools
    tag_transforms: dict[str, ToolTransformConfig] = {}
    for tool in tools:
        ns = tool.name.split("_", 1)[0]
        matched = by_ns.get(ns)
        if matched is None:
            continue
        merged = sorted(set(tool.tags or []) | set(matched.tags))
        if set(merged) == set(tool.tags or []):
            continue
        tag_transforms[tool.name] = ToolTransformConfig(tags=set(merged))
    if tag_transforms:
        server.add_transform(ToolTransform(tag_transforms))
        try:
            tools = await enumerate_catalog()
        except Exception as exc:
            log.warning("normalization_list_failed", error=str(exc))
            return []
    return tools
