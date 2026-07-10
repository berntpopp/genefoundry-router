"""Write authorization must run before federated backend dispatch."""

from __future__ import annotations

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

import genefoundry_router.authorization as authorization
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server


@pytest.mark.asyncio
async def test_pubtator_write_scope_blocks_backend_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    backend = FastMCP("pubtator-link")

    @backend.tool(name="index_review_evidence")
    async def index_review_evidence() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"success": True}

    registry = [BackendDef(name="pubtator", namespace="pubtator", url_env="X", url="in-process")]
    gateway = build_server(
        RouterSettings(_env_file=None),
        registry,
        proxy_targets={"pubtator": backend},
        enable_search=False,
    )

    monkeypatch.setattr(authorization, "get_access_token", lambda: None)
    with pytest.raises(ToolError, match="pubtator:write"):
        await gateway.call_tool("pubtator_index_review_evidence", {})
    assert calls == 0

    monkeypatch.setattr(
        authorization,
        "get_access_token",
        lambda: AccessToken(token="x", client_id="c", scopes=["pubtator:write"]),  # noqa: S106
    )
    result = await gateway.call_tool("pubtator_index_review_evidence", {})
    assert result.structured_content == {"success": True}
    assert calls == 1
