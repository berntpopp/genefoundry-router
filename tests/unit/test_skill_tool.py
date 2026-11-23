"""#384: one listed ``skill`` tool serves the workflow skills to tools-only clients."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app, build_server
from genefoundry_router.skill_search import skill_tool_description, skill_tools

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests" / "fixtures" / "skill-tool-description.txt"
GERMLINE = "germline-variant-classification"
_REGISTRY = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]


def _server(skills_dir: Path = ROOT / "skills"):
    fake = make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])
    settings = RouterSettings(_env_file=None, GF_SKILLS_DIR=str(skills_dir))
    return build_server(settings, _REGISTRY, proxy_targets={"gnomad": fake})


def test_description_matches_the_benchmarked_text_byte_for_byte() -> None:
    # The S3 arm's exact description; regenerate the fixture only with a new benchmark run.
    expected = GOLDEN.read_text(encoding="utf-8")
    assert skill_tool_description(skill_tools(ROOT / "skills")) == expected


async def test_skill_tool_is_listed_with_one_free_text_command() -> None:
    async with Client(_server()) as client:
        tool = next(t for t in await client.list_tools() if t.name == "skill")
    assert tool.description == GOLDEN.read_text(encoding="utf-8")
    assert tool.input_schema["required"] == ["command"]
    assert tool.input_schema["properties"] == {
        "command": {
            "type": "string",
            "description": f'The skill name (no arguments). E.g., "{GERMLINE}"',
        }
    }


async def test_skill_returns_what_call_tool_returns() -> None:
    async with Client(_server()) as client:
        direct = await client.call_tool("skill", {"command": GERMLINE})
        via = await client.call_tool("call_tool", {"name": f"skill_{GERMLINE}"})
    assert not direct.is_error
    assert direct.content[0].text == via.content[0].text  # type: ignore[union-attr]
    assert "Router note" in direct.content[0].text  # type: ignore[union-attr]


async def test_unknown_command_names_only_real_skills() -> None:
    async with Client(_server()) as client:
        result = await client.call_tool("skill", {"command": "evil-<b>"}, raise_on_error=False)
    text = result.content[0].text  # type: ignore[union-attr]
    body = json.loads(text)
    assert result.is_error and body["error_code"] == "not_found"
    assert "evil" not in text
    assert GERMLINE in body["suggestions"][0]


async def test_no_served_skills_no_skill_tool(tmp_path: Path) -> None:
    async with Client(_server(tmp_path)) as client:
        names = {t.name for t in await client.list_tools()}
        result = await client.call_tool("skill", {"command": GERMLINE}, raise_on_error=False)
    assert "skill" not in names
    assert result.is_error and json.loads(result.content[0].text)["error_code"] == "not_found"  # type: ignore[union-attr]


def test_skills_header_off_hides_and_rejects_skill() -> None:
    fake = make_fake_backend("gnomad-link", ["get_variant_details"])
    app = build_app(RouterSettings(_env_file=None), _REGISTRY, proxy_targets={"gnomad": fake})

    def post(method: str, params: dict, skills: str | None) -> dict:
        headers = {"accept": "application/json, text/event-stream"}
        if skills is not None:
            headers["x-gf-skills"] = skills
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        return client.post("/mcp", headers=headers, json=body).json()["result"]

    call = {"name": "skill", "arguments": {"command": GERMLINE}}
    with TestClient(app) as client:
        assert "skill" not in {t["name"] for t in post("tools/list", {}, "off")["tools"]}
        assert post("tools/call", call, "off")["isError"]
        assert not post("tools/call", call, None)["isError"]
