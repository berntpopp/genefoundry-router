"""Issue #272: the router's own backend timeout must classify as upstream_unavailable."""

from __future__ import annotations

import asyncio
import json

import structlog
from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app


def _slow_backend() -> FastMCP:
    slow = FastMCP("vep-link")

    @slow.tool
    async def annotate_variants_batch(variants: list[str] | None = None) -> str:
        await asyncio.sleep(5)
        return "late"

    return slow


async def test_backend_slower_than_timeout_yields_upstream_unavailable() -> None:
    registry = [
        BackendDef(
            name="vep", url_env="X", namespace="vep", entrypoints=["annotate_variants_batch"]
        )
    ]
    settings = RouterSettings(GF_BACKEND_TIMEOUT=0.3, GF_DRIFT_MODE="off")
    app = build_app(settings, registry, proxy_targets={"vep": _slow_backend()})
    async with app.router.lifespan_context(app):
        async with Client(app.state.mcp_server) as client:
            with structlog.testing.capture_logs() as logs:
                direct = await client.call_tool(
                    "vep_annotate_variants_batch", {}, raise_on_error=False
                )
                via_meta = await client.call_tool(
                    "call_tool",
                    {"name": "vep_annotate_variants_batch", "arguments": {}},
                    raise_on_error=False,
                )
    for result in (direct, via_meta):
        assert result.is_error
        envelope = json.loads(result.content[0].text)
        assert envelope["error_code"] == "upstream_unavailable"
        assert envelope["error_subcode"] == "backend_timeout"
        assert envelope["retryable"] is True
    audits = [e for e in logs if e.get("event") == "tool_call"]
    assert audits and all(e["error_code"] == "upstream_unavailable" for e in audits)
