from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from genefoundry_router.cache import (
    ToolResponseCache,
    ToolResponseCacheMiddleware,
    canonicalize_arguments,
    make_cache_key,
)
from genefoundry_router.registry import BackendDef


def test_canonicalize_arguments_ordering_and_nesting() -> None:
    arg1 = {"b": 2, "a": 1, "nested": {"y": [1, 2], "x": "val"}}
    arg2 = {"a": 1, "nested": {"x": "val", "y": [1, 2]}, "b": 2}
    assert canonicalize_arguments(arg1) == canonicalize_arguments(arg2)
    assert make_cache_key("test_tool", arg1) == make_cache_key("test_tool", arg2)


def test_canonicalize_arguments_empty() -> None:
    assert canonicalize_arguments(None) == ""
    assert canonicalize_arguments({}) == ""
    assert make_cache_key("test_tool", None) == "test_tool:_empty"
    assert make_cache_key("test_tool", {}) == "test_tool:_empty"


def test_canonicalize_arguments_different_args() -> None:
    key1 = make_cache_key("tool", {"gene": "BRCA1"})
    key2 = make_cache_key("tool", {"gene": "BRCA2"})
    assert key1 != key2


def test_ttl_defaults_for_slow_backends() -> None:
    cache = ToolResponseCache()
    assert cache.get_ttl("vep_recode_variant") == 86400
    assert cache.get_ttl("spliceai_predict_splicing") == 86400
    assert cache.get_ttl("autopvs1_get_variant_pvs1_data") == 86400
    assert cache.get_ttl("clinvar_get_variant") == 86400
    assert cache.get_ttl("gnomad_search_genes") is None
    assert cache.is_cacheable("vep_recode_variant") is True
    assert cache.is_cacheable("gnomad_search_genes") is False


def test_ttl_overrides_from_registry() -> None:
    backends = [
        BackendDef(
            name="vep",
            url_env="X",
            namespace="vep",
            tool_cache_ttl=3600,
            tool_cache_ttls={"annotate_variant": 7200},
        ),
        BackendDef(
            name="spliceai",
            url_env="Y",
            namespace="spliceai",
            tool_cache_ttl=0,  # disabled
        ),
        BackendDef(
            name="gnomad",
            url_env="Z",
            namespace="gnomad",
            tool_cache_ttl=1800,  # enabled for custom backend
        ),
    ]
    cache = ToolResponseCache(backends)
    # Tool-specific override wins
    assert cache.get_ttl("vep_annotate_variant") == 7200
    # Backend-level override wins over built-in 86400
    assert cache.get_ttl("vep_recode_variant") == 3600
    # Disabled (ttl=0)
    assert cache.get_ttl("spliceai_predict_splicing") == 0
    assert cache.is_cacheable("spliceai_predict_splicing") is False
    # Custom backend enabled
    assert cache.get_ttl("gnomad_search_genes") == 1800
    assert cache.is_cacheable("gnomad_search_genes") is True


def test_cache_set_get_and_meta_injection() -> None:
    cache = ToolResponseCache()
    payload = {"gene": "BRCA1", "_meta": {"tool": "vep_recode_variant", "elapsed_ms": 38000}}
    result = ToolResult(
        structured_content=payload,
        content=[TextContent(type="text", text="BRCA1")],
        is_error=False,
    )

    args = {"variant": "13-32315474-C-T"}
    cache.set("vep_recode_variant", args, result)

    cached = cache.get("vep_recode_variant", args)
    assert cached is not None
    assert isinstance(cached, ToolResult)
    assert cached.structured_content["gene"] == "BRCA1"
    # _meta should have cached=True injected
    assert cached.structured_content["_meta"]["cached"] is True
    # Original stored copy was not corrupted
    assert cache.stats["hits"] == 1


def test_cache_dict_set_and_get() -> None:
    cache = ToolResponseCache()
    payload = {"data": [1, 2, 3], "_meta": {"tool": "clinvar_get_variant"}}
    cache.set("clinvar_get_variant", {"id": "123"}, payload)

    cached = cache.get("clinvar_get_variant", {"id": "123"})
    assert isinstance(cached, dict)
    assert cached["data"] == [1, 2, 3]
    assert cached["_meta"]["cached"] is True


def test_cache_expiration() -> None:
    backends = [BackendDef(name="vep", url_env="X", namespace="vep", tool_cache_ttl=1)]
    cache = ToolResponseCache(backends)
    result = ToolResult(structured_content={"ok": True}, content=[])
    cache.set("vep_recode_variant", {"v": "1"}, result)

    # Immediately available
    assert cache.get("vep_recode_variant", {"v": "1"}) is not None

    # Wait for expiration
    time.sleep(1.05)
    assert cache.get("vep_recode_variant", {"v": "1"}) is None


def test_cache_does_not_cache_errors() -> None:
    cache = ToolResponseCache()
    error_result = ToolResult(
        structured_content={"success": False, "error_code": "not_found"},
        content=[TextContent(type="text", text="Not found")],
        is_error=True,
    )
    cache.set("vep_recode_variant", {"v": "bad"}, error_result)
    assert cache.get("vep_recode_variant", {"v": "bad"}) is None

    error_dict = {"success": False, "isError": True, "message": "fail"}
    cache.set("vep_recode_variant", {"v": "bad2"}, error_dict)
    assert cache.get("vep_recode_variant", {"v": "bad2"}) is None


def test_cache_invalidation() -> None:
    cache = ToolResponseCache()
    cache.set("vep_recode_variant", {"v": "1"}, ToolResult(structured_content={"r": 1}, content=[]))
    cache.set(
        "vep_annotate_variant", {"v": "2"}, ToolResult(structured_content={"r": 2}, content=[])
    )
    cache.set(
        "spliceai_predict_splicing", {"v": "3"}, ToolResult(structured_content={"r": 3}, content=[])
    )

    assert cache.stats["size"] == 3

    # Invalidate specific tool
    count = cache.invalidate_tool("vep_recode_variant")
    assert count == 1
    assert cache.get("vep_recode_variant", {"v": "1"}) is None
    assert cache.get("vep_annotate_variant", {"v": "2"}) is not None

    # Invalidate by namespace
    count = cache.invalidate_namespace("vep")
    assert count == 1
    assert cache.get("vep_annotate_variant", {"v": "2"}) is None
    assert cache.get("spliceai_predict_splicing", {"v": "3"}) is not None

    # Clear all
    cache.clear()
    assert cache.stats["size"] == 0
    assert cache.get("spliceai_predict_splicing", {"v": "3"}) is None


def test_cache_max_size_eviction() -> None:
    cache = ToolResponseCache(max_size=3)
    for i in range(5):
        cache.set("vep_tool", {"i": i}, ToolResult(structured_content={"i": i}, content=[]))
    assert cache.stats["size"] <= 3


@pytest.mark.asyncio
async def test_cache_middleware_intercepts_and_caches() -> None:
    cache = ToolResponseCache()
    middleware = ToolResponseCacheMiddleware(cache)

    mock_result = ToolResult(structured_content={"score": 0.99}, content=[], is_error=False)
    call_next = AsyncMock(return_value=mock_result)

    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "spliceai_predict_splicing"
    context.message.arguments = {"variant": "1-100-A-T"}

    # First call: miss -> executes call_next
    res1 = await middleware.on_call_tool(context, call_next)
    assert call_next.call_count == 1
    assert res1.structured_content["score"] == 0.99
    assert cache.stats["misses"] == 1
    assert cache.stats["hits"] == 0

    # Second call with same arguments: hit -> does NOT execute call_next
    res2 = await middleware.on_call_tool(context, call_next)
    assert call_next.call_count == 1  # unchanged!
    assert res2.structured_content["score"] == 0.99
    assert cache.stats["hits"] == 1


@pytest.mark.asyncio
async def test_cache_middleware_ignores_synthetic_tools() -> None:
    cache = ToolResponseCache()
    middleware = ToolResponseCacheMiddleware(cache)

    call_next = AsyncMock(return_value={"tools": []})
    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "search_tools"

    await middleware.on_call_tool(context, call_next)
    assert call_next.call_count == 1
    assert cache.stats["hits"] == 0
    assert cache.stats["misses"] == 0
