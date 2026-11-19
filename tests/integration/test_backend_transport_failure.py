"""Issue #272: the router's own backend timeout must classify as upstream_unavailable."""

from __future__ import annotations

import asyncio
import json

import pytest
import structlog
from fastmcp import Client, FastMCP
from starlette.responses import PlainTextResponse

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


async def test_backend_answering_403_is_unavailable_with_its_status_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#325: 21 backends answered 403 for 46 h and every call read as an unknown tool. A
    non-2xx backend answer must classify as upstream_unavailable and log its status."""
    import httpx2

    from genefoundry_router import composition

    backend = FastMCP("vep-link")

    @backend.tool
    def annotate_variant(variant: str = "x") -> str:
        return "ok"

    inner = backend.http_app(path="/mcp", stateless_http=True, json_response=True)
    forbid = False

    async def edge(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http" and forbid:
            return await PlainTextResponse("forbidden", status_code=403)(scope, receive, send)
        return await inner(scope, receive, send)

    transport = httpx2.ASGITransport(app=edge)
    monkeypatch.setattr(composition, "get_shared_upstream_transport", lambda: transport)
    registry = [BackendDef(name="vep", url_env="X", namespace="vep")]
    settings = RouterSettings(GF_DRIFT_MODE="off")
    app = build_app(settings, registry, proxy_targets={"vep": "http://vep.test/mcp"})
    with structlog.testing.capture_logs() as logs:
        # build_app's logger was cached on first use, before this capture began
        monkeypatch.setattr(composition, "log", structlog.get_logger("composition"))
        async with inner.router.lifespan_context(inner), app.router.lifespan_context(app):
            async with Client(app.state.mcp_server) as client:
                ok = await client.call_tool("vep_annotate_variant", {"variant": "a"})
                assert not ok.is_error
                forbid = True
                result = await client.call_tool(
                    "vep_annotate_variant", {"variant": "b"}, raise_on_error=False
                )
    envelope = json.loads(result.content[0].text)
    assert envelope["error_code"] == "upstream_unavailable"
    assert envelope["error_subcode"] == "backend_http_error"
    assert {"namespace": "vep", "status": 403} in [
        {k: e.get(k) for k in ("namespace", "status")}
        for e in logs
        if e.get("event") == "backend_http_error"
    ]
    audit = [e for e in logs if e.get("event") == "tool_call"][-1]
    assert audit["error_code"] == "upstream_unavailable"
