"""X-GF-Schema-Mode: slim — a token-lean pinned listing that still accepts every parameter.

Slim lists required params + at most 2 optional ones (a servers.yaml ``slim_keep`` allowlist,
else the first two non-``response_mode`` optionals), trimmed descriptions and no outputSchema.
Calls validate against the backend's FULL schema; a call that used a hidden param is flagged
in the router-owned result ``_meta`` so the bench can measure how often slim hides one.
"""

import hashlib
import json

import jsonschema
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from structlog.testing import capture_logs

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.devtools.fakes import load_manifest, make_backend_from_spec
from genefoundry_router.observability import METRICS_REGISTRY
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app, build_server
from tests.integration.test_strict_pinned_catalog import BASELINE, ROOT, _pinned_server, _tokens

SLIM = RouterSettings(_env_file=None, GF_SCHEMA_MODE="slim")
META = ("search_tools", "call_tool")
# Default pins + slim, same ~4 chars/token estimator as the pinned-listing budget. A ceiling:
# lower it as pinned schemas are trimmed, never raise it to admit growth.
SLIM_LISTING_TOKEN_BUDGET = 6_600  # measured ~6.5k on 2026-11-19 (standard: ~15.6k)


def _walk_param(node, path: str):
    """Yield (path, key) for every schema keyword, skipping property NAMES."""
    if isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk_param(item, f"{path}[{i}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            yield path, key
            if key in ("properties", "$defs") and isinstance(value, dict):
                for name, sub in value.items():
                    yield from _walk_param(sub, f"{path}.{name}")
            else:
                yield from _walk_param(value, f"{path}.{key}")


async def _listing(server) -> list:
    async with Client(server) as client:
        return await client.list_tools()


async def test_slim_listing_shape() -> None:
    server, pins = _pinned_server(SLIM)
    slim = {t.name: t for t in await _listing(server)}
    standard = {t.name: t for t in await _listing(_pinned_server()[0])}
    assert slim.keys() == standard.keys()
    for name in META:  # meta tools keep their normal schema
        assert slim[name].input_schema == standard[name].input_schema, name
    for name in set(pins) & slim.keys():
        tool, full = slim[name], (await server.get_tool(name)).parameters
        schema = tool.input_schema
        required = set(full.get("required") or ())
        props = schema.get("properties", {})
        assert required <= props.keys(), name
        optional = [p for p in props if p not in required]
        assert len(optional) <= 2, (name, optional)
        assert set(schema.get("required") or ()) == required, name
        assert "additionalProperties" not in schema, name  # hidden params must still validate
        assert tool.output_schema is None, name
        assert tool.description and len(tool.description) <= 300, name
        assert standard[name].description.startswith(tool.description.rstrip("…")), name
        for path, key in _walk_param(props, name):
            assert key not in ("examples", "title", "default"), (path, key)
        for pname, pschema in props.items():
            assert len(pschema.get("description", "")) <= 80, (name, pname)
    # fallback: first two non-response_mode optionals in schema order
    assert list(slim["hgnc_resolve_symbol"].input_schema["properties"]) == ["query"]
    gnomad = slim["gnomad_get_variant_frequencies"].input_schema["properties"]
    assert list(gnomad) == ["variant_id", "dataset", "populations"]
    # servers.yaml slim_keep allowlist wins over schema order
    gtex = slim["gtex_get_median_expression_levels"].input_schema["properties"]
    assert list(gtex) == ["gencode_id", "sort", "top_n"]


def test_slim_keep_allowlists_name_real_optional_params() -> None:
    manifest = load_manifest(BASELINE)
    for backend in load_registry(ROOT / "servers.yaml", {}):
        for leaf, keep in backend.slim_keep.items():
            assert leaf in backend.entrypoints, (backend.namespace, leaf)
            spec = next(t for t in manifest.backends[backend.namespace].tools if t.name == leaf)
            optional = set(spec.inputSchema["properties"]) - set(spec.inputSchema["required"])
            assert 0 < len(keep) <= 2 and set(keep) <= optional, (backend.namespace, leaf)


async def test_slim_listing_is_byte_stable() -> None:
    """Same mode, same bytes: across rebuilds and whatever order backends mount in."""

    def digest(tools) -> str:
        dump = [t.model_dump(mode="json", exclude_none=True) for t in tools]
        return hashlib.sha256(json.dumps(dump).encode()).hexdigest()

    server, _ = _pinned_server(SLIM)
    first = digest(await _listing(server))
    assert digest(await _listing(_pinned_server(SLIM)[0])) == first
    manifest = load_manifest(BASELINE)
    registry = [
        b
        for b in load_registry(ROOT / "servers.yaml", {})
        if b.enabled and b.namespace in manifest.backends
    ][::-1]
    targets = {
        b.namespace: make_backend_from_spec(b.namespace, manifest.backends[b.namespace])
        for b in registry
    }
    reversed_server = build_server(SLIM, registry, proxy_targets=targets, enable_search=True)
    assert digest(await _listing(reversed_server)) == first


async def test_default_pins_slim_listing_budget() -> None:
    size = _tokens(await _listing(_pinned_server(SLIM)[0]))
    assert size <= SLIM_LISTING_TOKEN_BUDGET, size


def _lookup_server(settings: RouterSettings):
    backend = FastMCP("demo")

    @backend.tool
    def lookup(query: str, limit: int = 5, offset: int = 0, deep: bool = False) -> dict:
        """Look up a thing. More prose."""
        return {"query": query, "limit": limit, "offset": offset, "deep": deep}

    registry = [BackendDef(name="demo", url_env="X", namespace="demo", entrypoints=["lookup"])]
    return build_server(settings, registry, proxy_targets={"demo": backend}, enable_search=True)


def _counter(tool: str) -> float:
    value = METRICS_REGISTRY.get_sample_value(
        "genefoundry_unadvertised_params_used_total", {"tool": tool}
    )
    return value or 0.0


async def test_hidden_param_call_succeeds_and_is_reported() -> None:
    server = _lookup_server(SLIM)
    before = _counter("demo_lookup")
    async with Client(server) as client:
        listed = {t.name: t.input_schema for t in await client.list_tools()}
        assert list(listed["demo_lookup"]["properties"]) == ["query", "limit", "offset"]
        args = {"query": "BRCA1", "deep": True}
        jsonschema.validate(args, listed["demo_lookup"])  # a validating client lets it through
        with capture_logs() as logs:
            direct = await client.call_tool("demo_lookup", args)
            via = await client.call_tool("call_tool", {"name": "demo_lookup", "arguments": args})
            plain = await client.call_tool("demo_lookup", {"query": "BRCA1"})
    for result in (direct, via):
        assert result.data["deep"] is True or json.loads(result.content[0].text)["deep"] is True
        assert result.meta["unadvertised_params_used"] == ["deep"]
    assert not (plain.meta or {}).get("unadvertised_params_used")
    events = [e for e in logs if e["event"] == "unadvertised_params_used"]
    assert [(e["tool"], e["params"]) for e in events] == [("demo_lookup", ["deep"])] * 2
    assert "BRCA1" not in str(events)  # names only, never values
    assert _counter("demo_lookup") == before + 2


async def test_slim_validation_error_names_hidden_or_missing_params() -> None:
    server = _lookup_server(SLIM)
    async with Client(server) as client:
        with capture_logs() as logs:
            missing = await client.call_tool("demo_lookup", {"limit": 3}, raise_on_error=False)
            hidden = await client.call_tool(
                "demo_lookup", {"query": "x", "deep": "nope"}, raise_on_error=False
            )
    assert missing.is_error and hidden.is_error
    events = [
        (e["tool"], e["params"]) for e in logs if e["event"] == "slim_hidden_param_validation_error"
    ]
    assert events == [("demo_lookup", ["query"]), ("demo_lookup", ["deep"])]


async def test_standard_mode_never_flags() -> None:
    server = _lookup_server(RouterSettings(_env_file=None))
    async with Client(server) as client:
        listed = {t.name: t.input_schema for t in await client.list_tools()}
        assert "deep" in listed["demo_lookup"]["properties"]
        result = await client.call_tool("demo_lookup", {"query": "x", "deep": True})
    assert not (result.meta or {}).get("unadvertised_params_used")


def _listed(client: TestClient, mode: str | None) -> dict:
    headers = {"accept": "application/json, text/event-stream"}
    if mode is not None:
        headers["x-gf-schema-mode"] = mode
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    response = client.post("/mcp", headers=headers, json=body)
    assert response.status_code == 200, response.text
    return {t["name"]: t for t in response.json()["result"]["tools"]}


def test_header_selects_slim_per_request(gnomad_fake) -> None:
    registry = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", entrypoints=["search_genes"])
    ]
    app = build_app(RouterSettings(_env_file=None), registry, proxy_targets={"gnomad": gnomad_fake})
    with TestClient(app, follow_redirects=False) as client:
        assert "outputSchema" in _listed(client, None)["gnomad_search_genes"]
        assert "outputSchema" not in _listed(client, " Slim ")["gnomad_search_genes"]
        assert "outputSchema" in _listed(client, None)["gnomad_search_genes"]  # no carry-over
