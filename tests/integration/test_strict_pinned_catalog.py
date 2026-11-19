"""#293: the advertised tools/list is OpenAI-strict and budgeted; strict nulls still call.

Built from the real pinned set (servers.yaml entrypoints) over the committed fleet-baseline
schemas, so a backend adding a long enum or an optional parameter is caught here.
"""

import json
from pathlib import Path

from fastmcp import Client

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.devtools.fakes import load_manifest, make_backend_from_spec
from genefoundry_router.schema_strict import _is_nullable
from genefoundry_router.server import build_server
from genefoundry_router.tool_search import resolve_entrypoints
from tests.unit.test_schema_strict import assert_strict_invariants

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "genefoundry_router" / "data" / "fleet-baseline.json"
# ~4 chars/token, same estimator as scripts/surface.py. Ceiling for the advertised listing;
# lower it as pinned schemas are trimmed (#293), never raise it to admit growth.
PINNED_LISTING_TOKEN_BUDGET = 18_000  # measured ~17.1k on 2026-11-19 (#320; was ~18.1k)


def _pinned_server(settings: RouterSettings | None = None):
    manifest = load_manifest(BASELINE)
    registry = [
        b
        for b in load_registry(ROOT / "servers.yaml", {})
        if b.enabled and b.namespace in manifest.backends
    ]
    targets = {
        b.namespace: make_backend_from_spec(b.namespace, manifest.backends[b.namespace])
        for b in registry
    }
    settings = settings or RouterSettings(_env_file=None)
    pins = resolve_entrypoints(registry)
    return build_server(settings, registry, proxy_targets=targets, enable_search=True), pins


async def test_advertised_tools_are_openai_strict_and_budgeted() -> None:
    server, pins = _pinned_server()
    async with Client(server) as client:
        listed = await client.list_tools()
    names = {t.name for t in listed}
    assert {"search_tools", "call_tool"} <= names
    assert set(pins) & names, "pinned entry points must be listed"
    for tool in listed:
        assert_strict_invariants(tool.input_schema, tool.name)
    size = len(json.dumps([t.model_dump(mode="json", exclude_none=True) for t in listed])) // 4
    assert size <= PINNED_LISTING_TOKEN_BUDGET, f"pinned listing ~{size} tokens"


async def test_strict_null_sentinels_are_stripped_before_dispatch() -> None:
    server, _ = _pinned_server()
    async with Client(server) as client:
        hits = await client.call_tool(
            "search_tools", {"query": "variant frequency", "limit": None, "detail": None}
        )
        assert "gnomad" in str(hits.data)
        pinned = next(
            t for t in await client.list_tools() if t.name == "gnomad_get_variant_frequencies"
        )
        nulls = {k: None for k in pinned.input_schema["properties"] if k != "variant_id"}
        result = await client.call_tool(
            "gnomad_get_variant_frequencies",
            {"variant_id": "1-55051215-G-GA", **nulls},
            raise_on_error=False,
        )
    assert result.is_error is False, result.content
    # The fake echoes what reached the backend: the issue's `dataset` null is gone; a null
    # survives only where the backend's OWN schema declares the property nullable.
    reached = json.loads(result.content[0].text)["args"]
    assert reached["variant_id"] == "1-55051215-G-GA" and "dataset" not in reached
    source = (await server.get_tool("gnomad_get_variant_frequencies")).parameters["properties"]
    for key, value in reached.items():
        assert value is not None or _is_nullable(source[key]), key


def _closed_empty_maps(node: object, path: str = "root") -> list[str]:
    """Non-root objects with no properties and extras forbidden: unusable in strict mode."""
    found: list[str] = []
    if isinstance(node, dict):
        if (
            path != "root"
            and node.get("type") == "object"
            and not node.get("properties")
            and node.get("additionalProperties") is False
        ):
            found.append(path)
        for key, value in node.items():
            found += _closed_empty_maps(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _closed_empty_maps(value, f"{path}[{i}]")
    return found


async def test_open_maps_stay_expressible_in_strict_listing() -> None:
    """Regression (bench audit after #295): open maps closed to {} made call_tool unusable."""
    server, _ = _pinned_server()
    async with Client(server) as client:
        listed = {t.name: t.input_schema for t in await client.list_tools()}
    for name, schema in listed.items():
        assert not _closed_empty_maps(schema), (name, _closed_empty_maps(schema))
        assert "propertyNames" not in json.dumps(schema), name
    arguments = listed["call_tool"]["properties"]["arguments"]
    assert {"type": "string"} in [{"type": b.get("type")} for b in arguments["anyOf"]]


async def test_strict_client_can_call_any_tool_through_call_tool() -> None:
    """A strict client passes call_tool.arguments and pinned open maps as JSON strings."""
    server, _ = _pinned_server()
    async with Client(server) as client:
        via_meta = await client.call_tool(
            "call_tool",
            {"name": "hgnc_get_gene", "arguments": '{"query": "BRCA1", "response_mode": null}'},
        )
        pinned = await client.call_tool(
            "clinpgx_search_records",
            {"entity_type": "gene", "filters": '{"gene": "CYP2C19"}', "query": None},
        )
    assert json.loads(via_meta.content[0].text)["args"] == {"query": "BRCA1"}
    reached = json.loads(pinned.content[0].text)["args"]
    assert reached["filters"] == {"gene": "CYP2C19"}  # decoded back into an object
