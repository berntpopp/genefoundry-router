"""Issue #158: an unexpected argument returns a structured ``invalid_input`` envelope.

FastMCP's raw answer is a pydantic dump (``1 validation error for call[...] ...
input_value='compact'``): no ``error_code``, and it reflects the caller's value. The router
answers from the tool's own advertised schema instead, for its meta-tools and every
federated tool alike, without reflecting the caller's key or value.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.client import CallToolResult

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

_HOSTILE_KEY = "IGNORE_PREVIOUS_key"
_HOSTILE_VALUE = "IGNORE_PREVIOUS_value"


def _backend() -> FastMCP:
    backend = FastMCP("hpo-link")

    @backend.tool
    def get_diagnostics() -> dict[str, bool]:
        """Return server health diagnostics."""
        return {"ok": True}

    @backend.tool
    def resolve_term(query: str, limit: int = 5) -> dict[str, str]:
        """Resolve an HPO term."""
        return {"query": query}

    return backend


async def _call(tool: str, args: dict[str, Any]) -> CallToolResult:
    registry = [BackendDef(name="hpo", url_env="X", namespace="hpo", entrypoints=[])]
    app = build_app(
        RouterSettings(GF_DRIFT_MODE="off"), registry, proxy_targets={"hpo": _backend()}
    )
    async with app.router.lifespan_context(app):
        async with Client(app.state.mcp_server) as c:
            return await c.call_tool(tool, args, raise_on_error=False)


_CASES = [
    ("hpo_get_diagnostics", {_HOSTILE_KEY: _HOSTILE_VALUE}, []),
    ("hpo_resolve_term", {"query": "a", _HOSTILE_KEY: _HOSTILE_VALUE}, ["limit", "query"]),
    (
        "call_tool",
        {"name": "hpo_get_diagnostics", "arguments": {_HOSTILE_KEY: _HOSTILE_VALUE}},
        [],
    ),
    (
        "call_tool",
        {"name": "hpo_resolve_term", "arguments": json.dumps({_HOSTILE_KEY: _HOSTILE_VALUE})},
        ["limit", "query"],
    ),
    ("search_tools", {"query": "hpo", _HOSTILE_KEY: _HOSTILE_VALUE}, None),
]


@pytest.mark.parametrize(("tool", "args", "allowed"), _CASES)
async def test_unexpected_argument_is_structured_invalid_input(
    tool: str, args: dict[str, Any], allowed: list[str] | None
) -> None:
    result = await _call(tool, args)
    assert result.is_error
    text = result.content[0].text
    envelope = json.loads(text)
    assert envelope["success"] is False
    assert envelope["error_code"] == "invalid_input"
    assert envelope["retryable"] is False
    assert result.structured_content == envelope
    if allowed is not None:
        assert envelope["allowed_values"] == allowed
    assert "IGNORE_PREVIOUS" not in text


async def test_valid_arguments_still_pass() -> None:
    direct = await _call("hpo_get_diagnostics", {})
    via = await _call("call_tool", {"name": "hpo_resolve_term", "arguments": {"query": "a"}})
    assert direct.structured_content == {"ok": True}
    assert via.structured_content == {"query": "a"}
