"""Gateway-side response hardening for community backends (Federation Standard v1 §5).

Every community result and error passes, in order: a streaming byte cap (raw and decoded) and
a tool-count cap, both of which quarantine the backend; NFC plus the Response Envelope v1.2
removal set on every string of ``content``, ``structuredContent`` and ``_meta``; a nonce fence
around every text block; and fixed error codes with router-written messages. Oversized
arguments are refused before any upstream request. Core backends never reach this module.
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
import zlib
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx2
import structlog
from fastmcp.server.context import Context
from fastmcp.server.providers.proxy import ProxyTool
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import PARSE_ERROR, TextContent
from pydantic import PrivateAttr, ValidationError

from genefoundry_router.egress import InvalidResponseError
from genefoundry_router.notfound_guard import _envelope
from genefoundry_router.tool_error_taxonomy import transport_failure, upstream_status

log = structlog.get_logger(__name__)

# Defaults of GF_COMMUNITY_MAX_RESPONSE_BYTES / _MAX_TOOLS / _MAX_ARG_BYTES; see configure().
MAX_RESPONSE_BYTES = 262_144
MAX_TOOLS = 32
MAX_ARG_BYTES = 8_192


# The latch lives in runtime_drift (#411); this no-op seam is replaced when it merges.
def quarantine(namespace: str, reason: str) -> None:
    return None


# Response Envelope v1.2 removal set (C0 except tab/LF/CR, C1, zero-width, bidi) plus the
# tag block and invisible marks of #398 (Federation Standard v1 §5.2).
_REMOVE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f​-‍⁠﻿‪-‮⁦-⁩\U000e0000-\U000e007f‎‏؜­]")
_MESSAGES = {
    "upstream_error": ("The community backend returned an error.", False),
    "upstream_timeout": ("The community backend did not answer in time.", True),
    "rate_limited": ("The community backend is rate limiting requests.", True),
    "unavailable": ("The community backend could not be reached.", True),
    "invalid_response": ("The community backend sent a response the router refused.", False),
    "invalid_input": ("The arguments exceed the size the router forwards.", False),
}


def configure(settings: Any) -> None:
    """Take the caps from the router settings (called once per community instance)."""
    global MAX_RESPONSE_BYTES, MAX_TOOLS, MAX_ARG_BYTES
    MAX_RESPONSE_BYTES = settings.GF_COMMUNITY_MAX_RESPONSE_BYTES
    MAX_TOOLS = settings.GF_COMMUNITY_MAX_TOOLS
    MAX_ARG_BYTES = settings.GF_COMMUNITY_MAX_ARG_BYTES


def clean(text: str) -> str:
    """NFC, then the removal set (Response Envelope v1.2 order)."""
    return _REMOVE.sub("", unicodedata.normalize("NFC", text))


def scrub(value: Any) -> Any:
    """``clean`` every string, keys included, of a JSON-like value."""
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {scrub(k): scrub(v) for k, v in value.items()}
    return value


def fence(text: str, namespace: str, nonce: str) -> str:
    body = clean(text).replace("<<<GF-", "<<<GF\\-")  # after cleaning: no hidden `<<<GF-`
    return (
        f'<<<GF-UNTRUSTED source="community:{namespace}" nonce="{nonce}">>>\n{body}\n'
        f'<<<GF-END nonce="{nonce}">>>'
    )


def failure(code: str) -> ToolResult:
    message, retryable = _MESSAGES[code]
    return _envelope(
        {"success": False, "error_code": code, "message": message, "retryable": retryable}
    )


def classify(exc: BaseException) -> str:
    """Fixed code for an upstream failure; the backend's own text is never read."""
    current: BaseException | None = exc
    for _ in range(16):
        if current is None:
            break
        if isinstance(current, InvalidResponseError | ValidationError | json.JSONDecodeError) or (
            isinstance(current, MCPError) and current.error.code == PARSE_ERROR
        ):
            return "invalid_response"
        current = current.__cause__ or current.__context__
    if upstream_status(exc) == 429:
        return "rate_limited"
    return {"backend_timeout": "upstream_timeout", "backend_unreachable": "unavailable"}.get(
        transport_failure(exc) or "", "upstream_error"
    )


class ResponseTooLargeError(InvalidResponseError):
    """A community response passed GF_COMMUNITY_MAX_RESPONSE_BYTES (raw or decoded)."""


class _CappedStream(httpx2.AsyncByteStream):
    """Count raw and decoded bytes as they stream; abort and quarantine past the cap."""

    def __init__(self, inner: Any, encoding: str, namespace: str) -> None:
        if encoding not in ("", "identity", "gzip", "deflate"):
            raise InvalidResponseError(f"content-encoding {encoding!r} refused")
        self._inner, self._namespace = inner, namespace
        self._decoder = zlib.decompressobj(47) if encoding in ("gzip", "deflate") else None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        raw = decoded = 0
        async for chunk in self._inner:
            raw += len(chunk)
            data = chunk
            while self._decoder is not None and data and decoded <= MAX_RESPONSE_BYTES:
                try:  # bounded: never inflates more than one byte past the cap
                    decoded += len(self._decoder.decompress(data, MAX_RESPONSE_BYTES + 1 - decoded))
                except zlib.error as exc:
                    raise InvalidResponseError("undecodable body") from exc
                data = self._decoder.unconsumed_tail
            if max(raw, decoded) > MAX_RESPONSE_BYTES:
                log.warning("community_response_too_large", namespace=self._namespace)
                quarantine(self._namespace, "response_too_large")
                raise ResponseTooLargeError("community response exceeds the byte cap")
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


def cap_responses(factory: Any, namespace: str) -> Any:
    """Wrap a client factory so every community response streams through the byte cap."""

    async def on_request(request: httpx2.Request) -> None:
        request.headers["accept-encoding"] = "gzip, deflate"

    async def on_response(response: httpx2.Response) -> None:
        encoding = response.headers.get("content-encoding", "").strip().lower()
        response.stream = _CappedStream(response.stream, encoding, namespace)

    def capped(**kwargs: Any) -> httpx2.AsyncClient:
        client: httpx2.AsyncClient = factory(**kwargs)
        client.event_hooks["request"].append(on_request)
        client.event_hooks["response"].append(on_response)
        return client

    capped.__dict__.update(factory.__dict__)  # keep the factory's marks (``community``)
    return capped


class CommunityProxyTool(ProxyTool):
    """A proxied community tool: argument cap before, hardening after the upstream call."""

    _community_namespace: str = PrivateAttr(default="")

    async def run(self, arguments: dict[str, Any], context: Context | None = None) -> ToolResult:
        size = len(json.dumps(arguments, separators=(",", ":"), ensure_ascii=False).encode())
        if size > MAX_ARG_BYTES:
            return failure("invalid_input")
        try:
            result = await super().run(arguments, context)
        except Exception as exc:
            code = classify(exc)
            log.warning("community_call_failed", namespace=self._community_namespace, code=code)
            return failure(code)
        if result.is_error:
            return failure("upstream_error")
        nonce = secrets.token_hex(16)
        content = [
            TextContent(type="text", text=fence(b.text, self._community_namespace, nonce))
            if isinstance(b, TextContent)
            else type(b).model_validate(scrub(b.model_dump()))
            for b in result.content
        ]
        return ToolResult(
            content=content,
            structured_content=scrub(result.structured_content),
            meta=scrub(result.meta),
        )


def harden_tools(tools: Any, namespace: str) -> Any:
    """Enforce GF_COMMUNITY_MAX_TOOLS and make every listed tool a ``CommunityProxyTool``."""
    if len(tools) > MAX_TOOLS:
        log.warning("community_too_many_tools", namespace=namespace, tools=len(tools))
        quarantine(namespace, "too_many_tools")
        raise InvalidResponseError("community tools/list exceeds the tool cap")
    for tool in tools:
        if isinstance(tool, ProxyTool):
            tool.__class__ = CommunityProxyTool
            cast(CommunityProxyTool, tool)._community_namespace = namespace
    return tools
