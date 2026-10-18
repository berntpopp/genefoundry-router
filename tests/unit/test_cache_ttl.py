"""Unit tests for CachedProxyProvider, force_tool_refresh, and backend cache TTL."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from mcp.types import Tool as McpTool

from genefoundry_router.composition import (
    DEFAULT_CACHE_TTL,
    CachedProxyProvider,
    force_tool_refresh,
    register_backend,
)
from genefoundry_router.registry import BackendDef


def _make_dummy_tool(name: str = "test_tool") -> McpTool:
    return McpTool(
        name=name,
        description="A test tool",
        inputSchema={"type": "object", "properties": {}},
    )


@pytest.mark.asyncio
async def test_cached_proxy_provider_caches_list_tools() -> None:
    fetch_count = 0
    dummy = _make_dummy_tool("tool_a")

    async def fake_list() -> list[McpTool]:
        nonlocal fetch_count
        fetch_count += 1
        return [dummy]

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(side_effect=fake_list)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    client_factory = MagicMock(return_value=mock_client)
    provider = CachedProxyProvider(client_factory=client_factory, cache_ttl=300.0)

    # First call: fetches from upstream
    tools1 = await provider._list_tools()
    assert len(tools1) == 1
    assert tools1[0].name == "tool_a"
    assert fetch_count == 1

    # Second call (warm): returns cached tools without calling upstream again
    tools2 = await provider._list_tools()
    assert len(tools2) == 1
    assert tools2[0].name == "tool_a"
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_cached_proxy_provider_returns_deep_copies() -> None:
    dummy = _make_dummy_tool("tool_orig")

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(return_value=[dummy])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    provider = CachedProxyProvider(client_factory=lambda: mock_client, cache_ttl=300.0)

    tools1 = await provider._list_tools()
    tools1[0].description = "mutated description"

    tools2 = await provider._list_tools()
    assert tools2[0].description == "A test tool"


@pytest.mark.asyncio
async def test_cached_proxy_provider_force_tool_refresh() -> None:
    fetch_count = 0

    async def fake_list() -> list[McpTool]:
        nonlocal fetch_count
        fetch_count += 1
        return [_make_dummy_tool(f"tool_v{fetch_count}")]

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(side_effect=fake_list)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    provider = CachedProxyProvider(client_factory=lambda: mock_client, cache_ttl=300.0)

    # First fetch: warm cache
    tools1 = await provider._list_tools()
    assert tools1[0].name == "tool_v1"
    assert fetch_count == 1

    # Normal fetch: uses cache
    tools2 = await provider._list_tools()
    assert tools2[0].name == "tool_v1"
    assert fetch_count == 1

    # Forced refresh: bypasses fresh cache and fetches new upstream state
    token = force_tool_refresh.set(True)
    try:
        tools3 = await provider._list_tools()
    finally:
        force_tool_refresh.reset(token)

    assert tools3[0].name == "tool_v2"
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_cached_proxy_provider_ttl_zero_disables_cache() -> None:
    fetch_count = 0

    async def fake_list() -> list[McpTool]:
        nonlocal fetch_count
        fetch_count += 1
        return [_make_dummy_tool(f"tool_{fetch_count}")]

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(side_effect=fake_list)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    provider = CachedProxyProvider(client_factory=lambda: mock_client, cache_ttl=0.0)

    await provider._list_tools()
    assert fetch_count == 1

    await provider._list_tools()
    assert fetch_count == 2


@pytest.mark.asyncio
async def test_cached_proxy_provider_concurrent_fetches_once() -> None:
    fetch_count = 0

    async def fake_list() -> list[McpTool]:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.01)
        return [_make_dummy_tool("tool_concurrent")]

    mock_client = MagicMock()
    mock_client.list_tools = AsyncMock(side_effect=fake_list)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    provider = CachedProxyProvider(client_factory=lambda: mock_client, cache_ttl=300.0)

    # Launch 5 concurrent listing requests
    results = await asyncio.gather(*(provider._list_tools() for _ in range(5)))
    assert len(results) == 5
    for r in results:
        assert r[0].name == "tool_concurrent"
    assert fetch_count == 1


def test_register_backend_registers_cached_proxy_provider() -> None:
    server = FastMCP("test-server")
    b1 = BackendDef(
        name="gnomad",
        namespace="gnomad",
        url_env="X",
        url="https://backend.example.org/mcp",
        cache_ttl=600,
    )
    b2 = BackendDef(
        name="hgnc",
        namespace="hgnc",
        url_env="Y",
        url="https://hgnc.example.org/mcp",
    )

    register_backend(server, b1)
    register_backend(server, b2)

    providers = [getattr(p, "_inner", p) for p in server.providers]
    matching = [p for p in providers if isinstance(p, CachedProxyProvider)]
    assert [p._cache_ttl for p in matching] == [600.0, float(DEFAULT_CACHE_TTL)]
