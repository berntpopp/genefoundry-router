"""Caller-scope enforcement for state-changing PubTator tools."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

import genefoundry_router.authorization as authorization
from genefoundry_router.authorization import WriteAuthorizationMiddleware


@dataclass
class _Context:
    message: object


async def _ok(_context: object) -> str:
    return "ok"


@pytest.mark.asyncio
async def test_pubtator_write_requires_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_index_review_evidence"))
    monkeypatch.setattr(
        authorization,
        "get_access_token",
        lambda: AccessToken(token="x", client_id="c", scopes=["mcp:read"]),  # noqa: S106
    )
    with pytest.raises(ToolError, match="pubtator:write"):
        await middleware.on_call_tool(context, _ok)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pubtator_write_scope_allows_call(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_record_review_context"))
    monkeypatch.setattr(
        authorization,
        "get_access_token",
        lambda: AccessToken(token="x", client_id="c", scopes=["pubtator:write"]),  # noqa: S106
    )
    assert await middleware.on_call_tool(context, _ok) == "ok"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_readonly_pubtator_call_needs_no_write_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_search_literature"))
    monkeypatch.setattr(authorization, "get_access_token", lambda: None)
    assert await middleware.on_call_tool(context, _ok) == "ok"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_real_no_auth_dependency_denies_write_cleanly() -> None:
    middleware = WriteAuthorizationMiddleware()
    context = _Context(SimpleNamespace(name="pubtator_submit_text_annotation"))
    with pytest.raises(ToolError, match="pubtator:write"):
        await middleware.on_call_tool(context, _ok)  # type: ignore[arg-type]
