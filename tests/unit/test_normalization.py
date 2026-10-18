from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.transforms.search.bm25 import BM25SearchTransform
from fastmcp.server.transforms.tool_transform import ToolTransform
from fastmcp.tools import Tool
from fastmcp.tools.tool_transform import ToolTransformConfig

from genefoundry_router.composition import force_tool_refresh
from genefoundry_router.normalization import (
    apply_normalizations,
    build_tool_transform,
    insert_transform,
    strip_prefix_name,
)
from genefoundry_router.registry import BackendDef, TransformConfig
from genefoundry_router.runtime_drift import RuntimeDriftGuard


def test_strip_prefix_name():
    assert strip_prefix_name("pubtator_search_literature", "pubtator_") == "search_literature"
    assert strip_prefix_name("search_genes", "pubtator_") == "search_genes"


def test_build_tool_transform_none_when_no_transform():
    b = BackendDef(name="gnomad", url_env="X", namespace="gnomad")
    assert build_tool_transform(b, present_tools=["get_variant_details"]) is None


def test_build_tool_transform_strips_prefix_for_namespaced_names():
    b = BackendDef(
        name="pubtator",
        url_env="X",
        namespace="pubtator",
        transform=TransformConfig(strip_prefix="pubtator_"),
    )
    # gateway names after namespacing are pubtator_pubtator_<tool>
    transform = build_tool_transform(b, present_tools=["pubtator_pubtator_search_literature"])
    assert transform is not None
    # the mapping renames the double-prefixed name back to single-prefixed.
    mapping = transform._transforms
    assert "pubtator_pubtator_search_literature" in mapping
    cfg = mapping["pubtator_pubtator_search_literature"]
    assert cfg.name == "pubtator_search_literature"


def test_insert_transform_orders_before_drift_and_search() -> None:
    server = FastMCP("test-server")
    drift_guard = RuntimeDriftGuard(pinned={}, mode="warn")
    search_transform = BM25SearchTransform()

    server.add_transform(drift_guard)
    server.add_transform(search_transform)

    tt = ToolTransform({"old": ToolTransformConfig(name="new")})
    insert_transform(server, tt)

    transforms = getattr(server, "_transforms", server.transforms)
    # tt must appear before drift_guard and search_transform
    tt_idx = transforms.index(tt)
    drift_idx = transforms.index(drift_guard)
    search_idx = transforms.index(search_transform)

    assert tt_idx < drift_idx < search_idx


@pytest.mark.asyncio
async def test_apply_normalizations_sets_and_resets_force_tool_refresh() -> None:
    server = FastMCP("test-server")
    saw_flag: list[bool] = []

    async def fake_list() -> list[Tool]:
        saw_flag.append(force_tool_refresh.get())
        return [Tool(name="pubtator_pubtator_search", description="", parameters={})]

    server._list_tools = AsyncMock(side_effect=fake_list)  # type: ignore[attr-defined]

    b = BackendDef(
        name="pubtator",
        url_env="X",
        namespace="pubtator",
        transform=TransformConfig(strip_prefix="pubtator_"),
    )

    tools = await apply_normalizations(server, [b])
    assert len(saw_flag) >= 1
    assert saw_flag[0] is True  # fetched with force_tool_refresh=True
    assert force_tool_refresh.get() is False  # reset back to False
    assert any(t.name == "pubtator_search" for t in tools)


@pytest.mark.asyncio
async def test_apply_normalizations_skips_already_installed_transforms() -> None:
    server = FastMCP("test-server")
    dummy = Tool(name="pubtator_pubtator_search", description="", parameters={})
    server._list_tools = AsyncMock(return_value=[dummy])  # type: ignore[attr-defined]

    b = BackendDef(
        name="pubtator",
        url_env="X",
        namespace="pubtator",
        transform=TransformConfig(strip_prefix="pubtator_"),
    )

    # First pass: installs transform
    _ = await apply_normalizations(server, [b])
    transforms1 = [
        t for t in getattr(server, "_transforms", server.transforms) if isinstance(t, ToolTransform)
    ]
    assert len(transforms1) == 1

    # Second pass: already installed, so no new transform is added
    _ = await apply_normalizations(server, [b])
    transforms2 = [
        t for t in getattr(server, "_transforms", server.transforms) if isinstance(t, ToolTransform)
    ]
    assert len(transforms2) == 1
