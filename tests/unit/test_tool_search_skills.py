"""#336: search_tools returns skill:// pointers for workflow skills, never callable tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.discoverability import load_catalog
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server
from genefoundry_router.skill_search import skill_tools
from genefoundry_router.tool_search import CompactBM25SearchTransform

ROOT = Path(__file__).resolve().parents[2]
NAME = "skill_somatic-variant-interpretation"
URI = "skill://somatic-variant-interpretation/SKILL.md"
POINTER_KEYS = {"name", "skill", "description", "read"}


def _server():
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    fake = make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])
    settings = RouterSettings(_env_file=None, GF_SKILLS_DIR=str(ROOT / "skills"))
    return build_server(settings, registry, proxy_targets={"gnomad": fake})


async def _search(client: Client, query: str, detail: str = "compact") -> list[dict]:
    result = await client.call_tool("search_tools", {"query": query, "detail": detail, "limit": 5})
    return json.loads(result.content[0].text)  # type: ignore[union-attr]


async def test_query_returns_skill_pointer() -> None:
    async with Client(_server()) as client:
        hits = await _search(client, "somatic variant interpretation oncogenic actionable")
    hit = next(h for h in hits if h["name"] == NAME)
    assert hit["skill"] == URI
    assert hit["read"] == {"method": "resources/read", "uri": URI}
    assert hit["description"].startswith("Use when")
    assert set(hit) == POINTER_KEYS  # no call, no inputSchema


@pytest.mark.parametrize("detail", ["full", "strict"])
async def test_skill_pointer_shape_in_full_and_strict(detail: str) -> None:
    async with Client(_server()) as client:
        hits = await _search(client, "somatic variant interpretation oncogenic", detail)
    hit = next(h for h in hits if h["name"] == NAME)
    assert set(hit) == POINTER_KEYS and hit["skill"] == URI


async def test_call_tool_rejects_skill_name() -> None:
    async with Client(_server()) as client:
        result = await client.call_tool(
            "call_tool", {"name": NAME, "arguments": {}}, raise_on_error=False
        )
    assert result.is_error
    assert "search_tools" in result.content[0].text  # type: ignore[union-attr]


async def test_unrelated_query_unchanged() -> None:
    tools = [t for t in load_catalog() if not t.name.startswith("skill_")]
    skills = skill_tools(ROOT / "skills")
    query = "population allele frequency of a variant in gnomAD"
    plain, _, _ = await CompactBM25SearchTransform()._ranked(tools, query)
    with_skills, _, _ = await CompactBM25SearchTransform(skills=skills)._ranked(tools, query)
    assert [t.name for t in with_skills] == [t.name for t in plain]


def test_skill_tools_reads_every_folder(tmp_path: Path) -> None:
    folder = tmp_path / "demo-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: demo-skill\ndescription: Use when demo.\n---\n")
    (tmp_path / "README.md").write_text("not a skill")
    [tool] = skill_tools(tmp_path)
    assert (tool.name, tool.description) == ("skill_demo-skill", "Use when demo.")


def test_skill_tools_skips_dot_folders_like_the_provider(tmp_path: Path) -> None:
    for name in ("demo-skill", ".old-copy"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: Use when demo.\n---\n"
        )
    assert [t.name for t in skill_tools(tmp_path)] == ["skill_demo-skill"]
