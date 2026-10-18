"""Stopgap normalization transforms for non-compliant backends.

These exist only until each -link repo adopts the Tool-Naming Standard v1; when a
source fix lands, delete the matching ``transform`` block from servers.yaml.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import structlog
from fastmcp import FastMCP
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ArgTransformConfig, ToolTransformConfig

from genefoundry_router.composition import force_tool_refresh
from genefoundry_router.registry import BackendDef, qualified_name
from genefoundry_router.runtime_drift import RuntimeDriftGuard

log = structlog.get_logger(__name__)


def insert_transform(server: FastMCP, transform: ToolTransform) -> None:
    """Insert a ToolTransform before RuntimeDriftGuard and BM25SearchTransform."""
    transforms_list: list[Any] = getattr(server, "_transforms", server.transforms)
    index = next(
        (
            i
            for i, existing in enumerate(transforms_list)
            if isinstance(existing, (RuntimeDriftGuard, BM25SearchTransform))
        ),
        len(transforms_list),
    )
    transforms_list.insert(index, transform)


async def _list_normalized_tools(
    server: FastMCP, raw_tools: Sequence[Tool] | None = None
) -> list[Tool]:
    """Apply every security/catalog transform except the outer search and drift projections."""
    if raw_tools is None:
        tools: list[Tool] = list(await server._list_tools())  # type: ignore[attr-defined]
    else:
        tools = [t.model_copy(deep=True) for t in raw_tools]
    transforms_list: list[Any] = getattr(server, "_transforms", server.transforms)
    for transform in transforms_list:
        if isinstance(transform, (BM25SearchTransform, RuntimeDriftGuard)):
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

    Enumerates with force_tool_refresh=True to fetch raw tools from backends.
    Applies name/arg normalization, then tag injection on post-rename names.
    Skips tool names already covered by an existing ToolTransform.
    """
    raw_tools: list[Tool] | None = None

    async def enumerate_catalog() -> list[Tool]:
        nonlocal raw_tools
        if raw_tools is None:
            token = force_tool_refresh.set(True)
            try:
                raw_tools = list(await server._list_tools())  # type: ignore[attr-defined]
            except Exception as exc:  # tolerate an unreachable backend at startup
                log.warning("normalization_list_failed", error=str(exc))
                return []
            finally:
                force_tool_refresh.reset(token)
        return await _list_normalized_tools(server, raw_tools)

    # Pass 1 — name/arg transforms
    try:
        tools = await enumerate_catalog()
    except Exception as exc:
        log.warning("normalization_list_failed", error=str(exc))
        return []

    # Skip tool names already covered by an existing ToolTransform in server._transforms
    installed: set[str] = set()
    transforms_list: list[Any] = getattr(server, "_transforms", server.transforms)
    for transform in transforms_list:
        if isinstance(transform, ToolTransform):
            for orig_name, config in getattr(transform, "_transforms", {}).items():
                if config.name is not None:
                    installed.add(orig_name)

    present = [tool.name for tool in tools if tool.name not in installed]
    renamed = False
    for backend in registry:
        if backend.transform is None:
            continue
        scoped = [n for n in present if n.startswith(f"{backend.namespace}_")]
        transform = build_tool_transform(backend, scoped)
        if transform is not None:
            insert_transform(server, transform)
            renamed = True
            log.info("normalized", backend=backend.name, tools=len(scoped))

    if renamed:
        try:
            tools = await enumerate_catalog()
        except Exception as exc:
            log.warning("normalization_list_failed", error=str(exc))
            return []

    # Pass 2 — tag injection on post-rename names (union with any existing tags).
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
        insert_transform(server, ToolTransform(tag_transforms))
        try:
            tools = await enumerate_catalog()
        except Exception as exc:
            log.warning("normalization_list_failed", error=str(exc))
            return []
    return tools
