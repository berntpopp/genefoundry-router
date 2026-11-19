"""GF_SCHEMA_MODE / X-GF-Schema-Mode: tools/list serves the backends' standard schemas by
default; the OpenAI-strict projection (#293) is opt-in. Clients that validate the model's
arguments against the listed schema rejected ordinary calls when strict was the only form.
"""

import json

import jsonschema
from fastapi.testclient import TestClient
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.security import MCP_CORS_ALLOW_HEADERS
from genefoundry_router.server import build_app
from tests.integration.test_strict_pinned_catalog import _pinned_server
from tests.unit.test_schema_strict import assert_strict_invariants

PINNED = ("hpo_search_terms", "gtex_get_median_expression_levels")


async def test_default_listing_is_standard() -> None:
    server, _ = _pinned_server()
    async with Client(server) as client:
        listed = {t.name: t.input_schema for t in await client.list_tools()}
    call_tool = listed["call_tool"]
    assert call_tool["required"] == ["name"]
    arguments = call_tool["properties"]["arguments"]
    assert arguments["default"] is None
    assert {"type": "object", "additionalProperties": True} in arguments["anyOf"]
    assert "string" not in [b.get("type") for b in arguments["anyOf"]]
    for name in PINNED:
        assert listed[name] == (await server.get_tool(name)).parameters, name
        assert len(listed[name]["required"]) < len(listed[name]["properties"]), name


async def test_strict_mode_keeps_the_openai_strict_listing() -> None:
    server, _ = _pinned_server(RouterSettings(_env_file=None, GF_SCHEMA_MODE="strict"))
    async with Client(server) as client:
        listed = {t.name: t.input_schema for t in await client.list_tools()}
    for name, schema in listed.items():
        assert_strict_invariants(schema, name)
    arguments = listed["call_tool"]["properties"]["arguments"]
    assert "string" in [b.get("type") for b in arguments["anyOf"]]
    assert set(listed["call_tool"]["required"]) == {"name", "arguments"}


async def test_validating_client_calls_in_each_mode() -> None:
    """The bench failure: arguments validated against the LISTED schema before the call."""
    for mode, arguments in (("standard", {"query": "BRCA1"}), ("strict", '{"query": "BRCA1"}')):
        server, _ = _pinned_server(RouterSettings(_env_file=None, GF_SCHEMA_MODE=mode))
        async with Client(server) as client:
            listed = {t.name: t.input_schema for t in await client.list_tools()}
            call = {"name": "hgnc_get_gene", "arguments": arguments}
            jsonschema.validate(call, listed["call_tool"])
            result = await client.call_tool("call_tool", call)
            assert json.loads(result.content[0].text)["args"] == {"query": "BRCA1"}, mode
            hits = (await client.call_tool("search_tools", {"query": "pgs catalog score"})).data
            example = next(h["call"] for h in hits if h["call"]["tool"] == "call_tool")
            jsonschema.validate(example["arguments"], listed["call_tool"])  # the hit's call form
            if mode == "standard":  # only the truly required field, as a model would send
                jsonschema.validate({"query": "seizure"}, listed["hpo_search_terms"])
                # a JSON-string `arguments` still works for non-validating clients
                as_text = await client.call_tool("call_tool", {**call, "arguments": "{}"})
                assert json.loads(as_text.content[0].text)["args"] == {}
                bad = await client.call_tool(
                    "call_tool", {**call, "arguments": 42}, raise_on_error=False
                )
                assert bad.is_error


def _call_tool_schema(client: TestClient, mode: str | None) -> dict:
    headers = {"accept": "application/json, text/event-stream"}
    if mode is not None:
        headers["x-gf-schema-mode"] = mode
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = client.post("/mcp", headers=headers, json=body)
    assert response.status_code == 200, response.text
    tools = {t["name"]: t["inputSchema"] for t in response.json()["result"]["tools"]}
    return tools["call_tool"]


def test_header_opts_in_per_request(gnomad_fake) -> None:
    assert "x-gf-schema-mode" in MCP_CORS_ALLOW_HEADERS  # browser clients can send it
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(RouterSettings(_env_file=None), registry, proxy_targets={"gnomad": gnomad_fake})
    with TestClient(app, follow_redirects=False) as client:
        assert _call_tool_schema(client, None)["required"] == ["name"]
        assert set(_call_tool_schema(client, " Strict ")["required"]) == {"name", "arguments"}
        assert _call_tool_schema(client, "bogus")["required"] == ["name"]  # invalid -> default
        assert _call_tool_schema(client, None)["required"] == ["name"]  # no cross-request state
