"""Gateway response caching tier for deterministic slow tools (issue #213).

Provides in-memory response caching with configurable per-tool/per-backend TTLs,
canonicalized argument hashing, cache invalidation, and Prometheus observability metrics.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from genefoundry_router.observability import TOOL_CACHE_HITS, TOOL_CACHE_MISSES
from genefoundry_router.registry import BackendDef

log = structlog.get_logger(__name__)

# Default TTLs (in seconds) for deterministic slow backends identified in telemetry:
# - vep (38.2s avg): static variant annotation & recoding
# - spliceai (28.7s avg): deep learning splicing predictions
# - autopvs1 (16.7s avg): static ACMG PVS1 variant classification
# - clinvar (10.5s avg): NCBI clinical significance records
DEFAULT_SLOW_BACKEND_TTLS: dict[str, int] = {
    "vep": 86400,
    "spliceai": 86400,
    "autopvs1": 86400,
    "clinvar": 86400,
}

_SYNTHETIC_TOOLS = frozenset({"search_tools", "call_tool"})


def _normalize_obj(obj: Any) -> Any:
    """Recursively sort dictionary keys for deterministic canonical hashing."""
    if isinstance(obj, dict):
        return {k: _normalize_obj(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_normalize_obj(x) for x in obj]
    return obj


def canonicalize_arguments(arguments: dict[str, Any] | None) -> str:
    """Return a deterministic SHA-256 hash of tool arguments."""
    if not arguments:
        return ""
    normalized = _normalize_obj(arguments)
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def make_cache_key(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Construct an exact, deterministic cache key from tool name and arguments."""
    arg_hash = canonicalize_arguments(arguments)
    return f"{tool_name}:{arg_hash}" if arg_hash else f"{tool_name}:_empty"


@dataclass
class CacheEntry:
    """A cached tool execution outcome."""

    result: Any
    expires_at: float
    created_at: float
    tool_name: str
    namespace: str


class ToolResponseCache:
    """In-memory TTL cache for deterministic MCP tool responses."""

    def __init__(
        self,
        registry: Sequence[BackendDef] | None = None,
        *,
        max_size: int = 10_000,
        default_ttls: dict[str, int] | None = None,
    ) -> None:
        self.max_size = max_size
        self._backend_ttls: dict[str, int] = dict(
            default_ttls if default_ttls is not None else DEFAULT_SLOW_BACKEND_TTLS
        )
        self._tool_ttls: dict[str, int] = {}
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()
        self._hits: int = 0
        self._misses: int = 0

        if registry:
            for backend in registry:
                if backend.tool_cache_ttl is not None:
                    self._backend_ttls[backend.namespace] = backend.tool_cache_ttl
                for leaf, ttl in backend.tool_cache_ttls.items():
                    qualified = (
                        leaf
                        if leaf.startswith(f"{backend.namespace}_")
                        else f"{backend.namespace}_{leaf}"
                    )
                    self._tool_ttls[qualified] = ttl

    def get_ttl(self, tool_name: str) -> int | None:
        """Resolve the TTL for a tool by checking per-tool, per-backend, or defaults."""
        if tool_name in self._tool_ttls:
            return self._tool_ttls[tool_name]
        ns = tool_name.split("_", 1)[0]
        if ns in self._backend_ttls:
            return self._backend_ttls[ns]
        return None

    def is_cacheable(self, tool_name: str) -> bool:
        """True if the tool has an active, positive TTL configured."""
        ttl = self.get_ttl(tool_name)
        return ttl is not None and ttl > 0

    def get(self, tool_name: str, arguments: dict[str, Any] | None) -> Any | None:
        """Fetch a cached response if valid and unexpired; returns None on miss."""
        if not self.is_cacheable(tool_name):
            return None

        key = make_cache_key(tool_name, arguments)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if time.time() >= entry.expires_at:
                del self._entries[key]
                return None
            self._hits += 1

        TOOL_CACHE_HITS.labels(namespace=entry.namespace).inc()
        log.debug("tool_cache_hit", tool=tool_name, namespace=entry.namespace)

        if isinstance(entry.result, ToolResult):
            copied = entry.result.model_copy(deep=True)
            if isinstance(copied.structured_content, dict):
                meta = copied.structured_content.get("_meta")
                if isinstance(meta, dict):
                    meta["cached"] = True
            return copied

        copied_dict = copy.deepcopy(entry.result)
        if isinstance(copied_dict, dict):
            meta = copied_dict.get("_meta")
            if isinstance(meta, dict):
                meta["cached"] = True
        return copied_dict

    def record_miss(self, namespace: str) -> None:
        """Record an observed cache miss."""
        with self._lock:
            self._misses += 1
        TOOL_CACHE_MISSES.labels(namespace=namespace).inc()

    def set(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        result: Any,
        ttl: int | None = None,
    ) -> None:
        """Store a successful tool result in cache."""
        if ttl is None:
            ttl = self.get_ttl(tool_name)
        if ttl is None or ttl <= 0:
            return

        # Do not cache error responses
        if isinstance(result, ToolResult) and result.is_error:
            return
        if isinstance(result, dict) and (
            result.get("isError") is True or result.get("success") is False
        ):
            return

        key = make_cache_key(tool_name, arguments)
        now = time.time()
        ns = tool_name.split("_", 1)[0]
        stored = (
            result.model_copy(deep=True)
            if isinstance(result, ToolResult)
            else copy.deepcopy(result)
        )

        with self._lock:
            if len(self._entries) >= self.max_size:
                expired = [k for k, v in self._entries.items() if now >= v.expires_at]
                for k in expired:
                    del self._entries[k]
                if len(self._entries) >= self.max_size:
                    oldest_key = next(iter(self._entries))
                    del self._entries[oldest_key]

            self._entries[key] = CacheEntry(
                result=stored,
                expires_at=now + ttl,
                created_at=now,
                tool_name=tool_name,
                namespace=ns,
            )
        log.debug("tool_cache_set", tool=tool_name, ttl=ttl)

    def invalidate(
        self,
        *,
        key: str | None = None,
        tool_name: str | None = None,
        namespace: str | None = None,
    ) -> int:
        """Invalidate cache entries matching key, tool name, or namespace."""
        with self._lock:
            to_delete: list[str] = []
            for k, entry in self._entries.items():
                if (
                    (key is not None and k == key)
                    or (tool_name is not None and entry.tool_name == tool_name)
                    or (namespace is not None and entry.namespace == namespace)
                ):
                    to_delete.append(k)
            for k in to_delete:
                del self._entries[k]
            return len(to_delete)

    def invalidate_tool(self, tool_name: str) -> int:
        """Invalidate all cached results for a specific tool."""
        return self.invalidate(tool_name=tool_name)

    def invalidate_namespace(self, namespace: str) -> int:
        """Invalidate all cached results for a specific namespace."""
        return self.invalidate(namespace=namespace)

    def clear(self) -> int:
        """Purge all cached entries."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            return count

    @property
    def stats(self) -> dict[str, int]:
        """Cache hit, miss, and size statistics."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._entries),
            }


class ToolResponseCacheMiddleware(Middleware):
    """FastMCP Middleware hooking tool invocations for response caching."""

    def __init__(self, cache: ToolResponseCache) -> None:
        self.cache = cache

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        name = getattr(context.message, "name", "") or ""
        if name in _SYNTHETIC_TOOLS or not self.cache.is_cacheable(name):
            return await call_next(context)

        arguments = getattr(context.message, "arguments", None)
        cached = self.cache.get(name, arguments)
        if cached is not None:
            return cached

        ns = name.split("_", 1)[0]
        self.cache.record_miss(ns)
        result = await call_next(context)
        self.cache.set(name, arguments, result)
        return result
