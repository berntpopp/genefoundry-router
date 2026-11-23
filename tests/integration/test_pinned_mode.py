"""#320: GF_PINNED_MODE / X-GF-Pinned-Mode choose which pinned tools tools/list shows.

Only the listing changes: every tool stays searchable and callable through call_tool.
"""

import json

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app
from tests.integration.test_strict_pinned_catalog import (
    PINNED_LISTING_TOKEN_BUDGET,
    STRICT_LISTING_TOKEN_BUDGET,
    _pinned_server,
)
from tests.unit.test_schema_strict import assert_strict_invariants

META = {"search_tools", "call_tool", "skill"}  # skill: served skills (#384)


def _tokens(tools) -> int:  # same ~4 chars/token estimator as the budget test
    return len(json.dumps([t.model_dump(mode="json", exclude_none=True) for t in tools])) // 4


async def _listing(mode: str, schema: str = "standard"):
    settings = RouterSettings(_env_file=None, GF_PINNED_MODE=mode, GF_SCHEMA_MODE=schema)
    server, pins = _pinned_server(settings)
    async with Client(server) as client:
        return await client.list_tools(), pins


@pytest.mark.parametrize("schema", ["standard", "strict"])
async def test_each_mode_lists_its_pins_and_shrinks(schema: str) -> None:
    sizes = {}
    for mode in ("default", "minimal", "none"):
        listed, pins = await _listing(mode, schema)
        names = {t.name for t in listed}
        expected = {
            "default": set(pins),
            "minimal": set(RouterSettings(_env_file=None).GF_PINNED_MINIMAL),
            "none": set(),
        }[mode]
        assert names == META | expected, mode
        for tool in listed if schema == "strict" else ():
            assert_strict_invariants(tool.input_schema, tool.name)
        sizes[mode] = _tokens(listed)
    budget = STRICT_LISTING_TOKEN_BUDGET if schema == "strict" else PINNED_LISTING_TOKEN_BUDGET
    assert sizes["none"] < sizes["minimal"] < sizes["default"] <= budget


async def test_none_mode_still_searches_and_calls_every_tool() -> None:
    server, _ = _pinned_server(RouterSettings(_env_file=None, GF_PINNED_MODE="none"))
    async with Client(server) as client:
        hits = (await client.call_tool("search_tools", {"query": "variant frequency"})).data
        hit = next(h for h in hits if h["name"] == "gnomad_get_variant_frequencies")
        assert hit["call"]["tool"] == "call_tool"  # unlisted now, so the call form says so
        called = await client.call_tool(
            "call_tool",
            {"name": "gnomad_get_variant_frequencies", "arguments": '{"variant_id": "1-1-A-G"}'},
        )
    assert json.loads(called.content[0].text)["args"] == {"variant_id": "1-1-A-G"}


def _list_with_header(client: TestClient, value: str | None) -> set[str]:
    headers = {"accept": "application/json, text/event-stream"}
    if value is not None:
        headers["x-gf-pinned-mode"] = value
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = client.post("/mcp", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return {t["name"] for t in response.json()["result"]["tools"]}


def test_header_overrides_the_listing_per_request(gnomad_fake) -> None:
    settings = RouterSettings(_env_file=None, GF_PINNED_MINIMAL=["gnomad_search_genes"])
    registry = [
        BackendDef(
            name="gnomad",
            url_env="X",
            namespace="gnomad",
            entrypoints=["get_variant_details", "search_genes"],
        )
    ]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})
    pins = {"gnomad_get_variant_details", "gnomad_search_genes"}
    with TestClient(app, follow_redirects=False) as client:
        assert _list_with_header(client, None) == META | pins
        assert _list_with_header(client, "none") == META
        assert _list_with_header(client, " Minimal ") == META | {"gnomad_search_genes"}
        assert _list_with_header(client, "everything") == META | pins  # invalid -> default
        assert _list_with_header(client, "none") == META  # no cross-request state
        assert _list_with_header(client, "default") == META | pins


def _rpc(client: TestClient, method: str, params: dict, mode: str | None) -> dict:
    headers = {"accept": "application/json, text/event-stream"}
    if mode is not None:
        headers["x-gf-pinned-mode"] = mode
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = client.post("/mcp", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_header_also_selects_instructions_and_hint_call_form() -> None:
    """The coordinator's #320 follow-up: X-GF-Pinned-Mode filtered tools/list only, so a
    none-mode client was still told about pins (instructions) and handed bare pinned names
    (hints) it cannot see. Both now follow the request's mode; the cache keys on it."""
    from fastmcp import FastMCP

    backend = FastMCP("vep-link")

    @backend.tool(name="annotate_variant")
    async def annotate_variant(variant: str = "") -> dict:
        """Return a success whose prose names the pinned sibling."""
        return {"success": True, "message": "Try recode_variant next."}

    registry = [
        BackendDef(name="vep", url_env="X", namespace="vep", entrypoints=["recode_variant"])
    ]
    app = build_app(RouterSettings(_env_file=None), registry, proxy_targets={"vep": backend})
    init = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    }
    call = {"name": "vep_annotate_variant", "arguments": {"variant": "1-1-A-G"}}
    with TestClient(app, follow_redirects=False) as client:
        default_text = _rpc(client, "initialize", init, None)["instructions"]
        none_text = _rpc(client, "initialize", init, "none")["instructions"]
        bare = _rpc(client, "tools/call", call, None)["structuredContent"]["message"]
        routed = _rpc(client, "tools/call", call, "none")["structuredContent"]["message"]
    assert "vep_recode_variant" in default_text and "COMMON ENTRY POINTS" in default_text
    assert "vep_recode_variant" not in none_text and "through `call_tool`" in none_text
    assert bare == "Try vep_recode_variant next."
    assert routed == 'Try call_tool(name="vep_recode_variant") next.'
