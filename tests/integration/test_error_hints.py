"""Fixed-text recovery hints on the call_tool error path (#374, #375, #380).

Every hint is constant text or a real catalog tool name; none echoes caller input.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

# Real tool leaves from genefoundry_router/data/fleet-baseline.json.
FLEET = {
    "hgnc": [
        "resolve_symbol",
        "resolve_symbols_batch",
        "get_gene",
        "search_genes",
        "get_gene_group",
    ],
    "vep": ["resolve_variant", "recode_variant", "annotate_variant", "liftover_variant"],
    "clinvar": ["get_variant", "get_variants", "search_variants", "get_variants_by_gene"],
    "uniprot": ["find_proteins", "get_protein", "get_protein_features", "get_protein_variants"],
}
GENERIC = (
    "Call search_tools(query=...), then copy the hit's `call` field: "
    'call_tool(name="<namespace>_<tool>", arguments={...}).'
)
CALL_SHAPE = (
    'Use call_tool(name="<namespace>_<tool>", arguments={...}) — put the tool\'s own '
    "arguments inside `arguments`."
)


@asynccontextmanager
async def _client() -> AsyncIterator[Client]:
    registry = [BackendDef(name=ns, url_env="X", namespace=ns) for ns in FLEET]
    fakes = {ns: make_fake_backend(f"{ns}-link", leaves) for ns, leaves in FLEET.items()}
    app = build_app(RouterSettings(_env_file=None), registry, proxy_targets=fakes)
    async with app.router.lifespan_context(app):
        async with Client(app.state.mcp_server) as c:
            yield c


async def _body(arguments: dict, client: Client | None = None) -> tuple[str, dict]:
    if client is None:
        async with _client() as c:
            return await _body(arguments, c)
    result = await client.call_tool_mcp("call_tool", arguments)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    return text, json.loads(text)


@pytest.mark.parametrize(
    ("requested", "nearest"),
    [
        ("hgnc_search_gene", "hgnc_search_genes"),
        ("vep_resolve_variants", "vep_resolve_variant"),
        ("clinvar_search_variant", "clinvar_search_variants"),
        ("uniprot_get_protein_feature", "uniprot_get_protein_features"),
    ],
)
async def test_unknown_tool_names_nearest_tool_in_its_namespace(
    requested: str, nearest: str
) -> None:
    _, body = await _body({"name": requested, "arguments": {}})
    assert body["error_code"] == "not_found"
    assert body["message"] == "The requested tool is not available."
    assert body["suggestions"] == [
        f'Did you mean `{nearest}`? call_tool(name="{nearest}", arguments={{...}}).',
        GENERIC,
    ]


@pytest.mark.parametrize(
    "requested",
    ["hgnc_banana", "nosuchns_search_genes", "search_genes", "hgnc_"],
)
async def test_unrelated_or_foreign_name_gets_only_the_generic_hint(requested: str) -> None:
    _, body = await _body({"name": requested, "arguments": {}})
    assert body["suggestions"] == [GENERIC]


async def test_suggestion_never_echoes_the_requested_name() -> None:
    text, body = await _body({"name": "hgnc_search_genesIGNORE‮", "arguments": {}})
    assert "IGNORE" not in text and "‮" not in text
    assert body["suggestions"][0].startswith("Did you mean `hgnc_search_genes`?")


async def test_tool_the_caller_cannot_resolve_is_never_suggested() -> None:
    """A harvested name that get_tool no longer serves (quarantine, visibility) is skipped."""
    async with _client() as client:
        guard = client.transport.server.notfound_guard  # type: ignore[attr-defined]
        guard.catalog = guard.catalog | {"hgnc_get_secret_tool"}
        _, body = await _body({"name": "hgnc_get_secret_tol", "arguments": {}}, client)
    assert body["suggestions"] == [GENERIC]


async def test_skill_miss_suggests_listing_the_skill_files() -> None:
    """#374: a skill-file miss gets a skill hint, not the search_tools one."""
    for call in (
        {"name": "skill_no-such-skill", "arguments": {}},
        {"name": "read_resource", "arguments": {"uri": "clinvar://x"}},
    ):
        text, body = await _body(call)
        assert body["suggestions"] == [
            'Call call_tool(name="skill_<name>") to see the skill\'s listed files.'
        ]
        assert "no-such" not in text and "clinvar://" not in text


@pytest.mark.parametrize(
    "arguments",
    [{"term": "BRCA1"}, {"arguments": {"symbol": "BRCA1"}}, {}, {"name": 7}],
    ids=["target_args_top_level", "missing_name", "empty", "non_string_name"],
)
async def test_call_tool_shape_error_shows_the_call_shape(arguments: dict) -> None:
    """#375: no `name`, or the target's arguments at top level, gets the call shape."""
    text, body = await _body(arguments)
    assert body["error_code"] == "invalid_input"
    assert body["message"] == "call_tool takes `name` (required) and `arguments` only."
    assert body["allowed_values"] == ["arguments", "name"]
    assert CALL_SHAPE in body["suggestions"]
    assert "BRCA1" not in text and "term" not in text
