"""Integration tests for MCP protocol surface beyond tools and tool catalog constraints (#77 §1).

Verifies:
1. `resources/list` and `prompts/list` against mounted router return valid list responses,
   cleanly federating backend resources and prompts when exposed.
2. `build_app` advertises resources and prompts capabilities on discovery and serves clean
   protocol responses.
3. `tools/list` across all 22 backends (285 tools) in fleet-baseline.json produces deterministic,
   collision-free qualified names within the 64-character ceiling, both statically and in live
   router composition.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP
from mcp.types import TextContent

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import load_manifest, make_backend_from_spec
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app, build_server

BASELINE_PATH = Path("genefoundry_router/data/fleet-baseline.json")


async def test_client_list_resources_and_prompts_empty_on_tools_only_fleet(
    gnomad_fake: FastMCP,
) -> None:
    """Standard tool backends without resources/prompts return empty list responses without errors."""
    settings = RouterSettings(_env_file=None)  # type: ignore[call-arg]
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    server = build_server(
        settings, registry, proxy_targets={"gnomad": gnomad_fake}, enable_search=False
    )

    async with Client(server) as client:
        resources = await client.list_resources()
        assert resources == []

        prompts = await client.list_prompts()
        assert prompts == []


async def test_client_federates_resources_and_prompts_when_exposed_by_backend() -> None:
    """When a mounted backend exposes resources and prompts, the router federates them cleanly."""
    backend = FastMCP("clin-ref")

    @backend.resource("resource://clin/guidelines")
    def guidelines() -> str:
        return "ACMG Guideline Summary"

    @backend.prompt("interpret_variant")
    def interpret_variant(variant: str) -> str:
        return f"Evaluate pathogenicity of {variant}"

    settings = RouterSettings(_env_file=None)  # type: ignore[call-arg]
    registry = [BackendDef(name="clin", url_env="X", namespace="clin")]
    server = build_server(settings, registry, proxy_targets={"clin": backend}, enable_search=False)

    async with Client(server) as client:
        # 1. Verify resources/list federates the resource
        resources = await client.list_resources()
        assert len(resources) == 1
        res = resources[0]
        assert str(res.uri).startswith("resource://")
        assert res.name == "guidelines"

        # 2. Verify reading the federated resource
        res_content = await client.read_resource(str(res.uri))
        assert len(res_content) == 1
        assert res_content[0].text == "ACMG Guideline Summary"

        # 3. Verify prompts/list federates the prompt with namespace prefix
        prompts = await client.list_prompts()
        assert len(prompts) == 1
        prompt = prompts[0]
        assert prompt.name == "clin_interpret_variant"

        # 4. Verify getting the federated prompt
        prompt_data = await client.get_prompt(prompt.name, {"variant": "NM_000059.4:c.5946del"})
        assert len(prompt_data.messages) == 1
        msg_content = prompt_data.messages[0].content
        assert isinstance(msg_content, TextContent)
        assert "NM_000059.4:c.5946del" in msg_content.text


async def test_build_app_serves_valid_resources_and_prompts_protocol(
    gnomad_fake: FastMCP,
) -> None:
    """build_app discovery surfaces resource/prompt capabilities and router state handles queries."""
    settings = RouterSettings(_env_file=None)  # type: ignore[call-arg]
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})

    with TestClient(app) as test_client:
        resp = test_client.get("/mcp")
        assert resp.status_code == 200
        discovery = resp.json()
        capabilities = discovery["capabilities"]
        assert capabilities["resources"] == {"subscribe": False, "listChanged": False}
        assert capabilities["prompts"] == {"listChanged": False}
        assert capabilities["tools"] == {"listChanged": False}

    async with Client(app.state.mcp_server) as client:
        assert await client.list_resources() == []
        assert await client.list_prompts() == []


def test_fleet_baseline_tool_names_are_collision_free_and_within_client_ceiling() -> None:
    """All 285 tools across 22 backends produce collision-free names <= 64 characters."""
    manifest = load_manifest(BASELINE_PATH)
    assert len(manifest.backends) == 22

    qualified_names: list[str] = []
    for namespace, backend in manifest.backends.items():
        for tool in backend.tools:
            qualified = f"{namespace}_{tool.name}"
            qualified_names.append(qualified)
            assert len(qualified) <= 64, (
                f"Tool name {qualified!r} exceeds 64-char ceiling ({len(qualified)} > 64)"
            )

    assert len(qualified_names) == 285
    # Deterministic and collision-free
    assert len(set(qualified_names)) == 285
    # Longest tool name remains well within budget
    max_len_tool = max(qualified_names, key=len)
    assert len(max_len_tool) <= 64


async def test_live_router_composition_lists_all_285_baseline_tools_without_collisions() -> None:
    """Live Client(server) composition across all 22 backends lists 285 tools without collision."""
    manifest = load_manifest(BASELINE_PATH)
    settings = RouterSettings(_env_file=None)  # type: ignore[call-arg]

    registry: list[BackendDef] = []
    proxy_targets: dict[str, FastMCP] = {}
    for namespace, spec in manifest.backends.items():
        registry.append(BackendDef(name=namespace, url_env="X", namespace=namespace))
        proxy_targets[namespace] = make_backend_from_spec(namespace, spec)

    server = build_server(settings, registry, proxy_targets=proxy_targets, enable_search=False)

    async with Client(server) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]

        assert len(names) == 285
        assert len(set(names)) == 285
        assert all(len(name) <= 64 for name in names)

        # Confirm representation across independent backends
        assert "gnomad_get_variant_details" in names
        assert "gtex_search" in names
        assert "litvar_search_genetic_variants" in names
        assert "clinpgx_get_api_data" in names
        assert "metadome_get_diagnostics" in names
