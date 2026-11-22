"""#336/#368: search_tools returns skill:// pointers for workflow skills; call_tool reads them."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
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
GERMLINE = "skill://germline-variant-classification"
POINTER_KEYS = {"name", "skill", "description", "read", "call"}


def _server(schema_mode: str = "standard"):
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    fake = make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])
    settings = RouterSettings(
        _env_file=None, GF_SKILLS_DIR=str(ROOT / "skills"), GF_SCHEMA_MODE=schema_mode
    )
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
    assert hit["call"] == {"tool": "call_tool", "arguments": {"name": NAME, "arguments": {}}}
    assert hit["description"].startswith("Use when")
    assert set(hit) == POINTER_KEYS  # no call, no inputSchema


@pytest.mark.parametrize("detail", ["full", "strict"])
async def test_skill_pointer_shape_in_full_and_strict(detail: str) -> None:
    async with Client(_server()) as client:
        hits = await _search(client, "somatic variant interpretation oncogenic", detail)
    hit = next(h for h in hits if h["name"] == NAME)
    assert set(hit) == POINTER_KEYS and hit["skill"] == URI


@pytest.mark.parametrize(
    "call",
    [
        {"name": NAME, "arguments": {}},
        {"name": "read_resource", "arguments": {"uri": URI}},
        {"name": "resources/read", "arguments": {"uri": URI}},
    ],
    ids=["skill_name", "read_resource", "resources_read"],
)
async def test_call_tool_reads_skill_for_tools_only_clients(call: dict) -> None:
    async with Client(_server()) as client:
        result = await client.call_tool("call_tool", call)
        served = (await client.read_resource(URI))[0].text  # type: ignore[union-attr]
    assert not result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith(served) and len(text) > len(served)  # SKILL.md + router footer


async def test_call_tool_reads_a_skill_reference_file_verbatim() -> None:
    """#370: a supporting file opens through call_tool, byte-identical to resources/read."""
    uri = f"{GERMLINE}/references/derivation.md"
    async with Client(_server()) as client:
        result = await client.call_tool(
            "call_tool", {"name": "read_resource", "arguments": {"uri": uri}}
        )
        served = (await client.read_resource(uri))[0].text  # type: ignore[union-attr]
    assert not result.is_error
    assert result.content[0].text == served  # type: ignore[union-attr]


async def test_skill_md_footer_lists_supporting_files_on_call_tool_path_only() -> None:
    """#370: the footer names every supporting file with its exact call; resources/read
    stays the file itself."""
    async with Client(_server()) as client:
        result = await client.call_tool(
            "call_tool", {"name": "skill_germline-variant-classification"}
        )
        served = (await client.read_resource(f"{GERMLINE}/SKILL.md"))[0].text  # type: ignore[union-attr]
        plain = await client.call_tool("call_tool", {"name": "skill_lay-summary"})
        lay = (await client.read_resource("skill://lay-summary/SKILL.md"))[0].text  # type: ignore[union-attr]
    text = result.content[0].text  # type: ignore[union-attr]
    raw = (ROOT / "skills/germline-variant-classification/SKILL.md").read_text()
    assert served == raw and text.startswith(served)
    footer = text[len(served) :]
    for ref in [
        "derivation",
        "functional",
        "mitochondrial",
        "pvs1",
        "repeat-expansion",
        "splicing",
    ]:
        call = f'call_tool(name="read_resource", arguments={{"uri": "{GERMLINE}/references/{ref}.md"}})'
        assert call in footer
    assert "_manifest" not in footer
    assert plain.content[0].text == lay  # type: ignore[union-attr]  # no supporting files, no footer


async def test_skill_call_validates_against_strict_listing() -> None:
    async with Client(_server("strict")) as client:
        listed = {t.name: t.input_schema for t in await client.list_tools()}
        hits = await _search(client, "somatic variant interpretation oncogenic")
        call = next(h["call"] for h in hits if h["name"] == NAME)
        jsonschema.validate(call["arguments"], listed["call_tool"])
        result = await client.call_tool("call_tool", call["arguments"])
    assert not result.is_error


@pytest.mark.parametrize(
    "call",
    [
        {"name": "skill_no-such-skill", "arguments": {}},
        {"name": "read_resource", "arguments": {"uri": "skill://no-such-skill/SKILL.md"}},
        {"name": "read_resource", "arguments": {"uri": "file:///etc/passwd"}},
        {"name": "resources/read", "arguments": {"uri": "skill://x/../../SKILL.md"}},
        {"name": "read_resource", "arguments": {"uri": f"{GERMLINE}/references/../SKILL.md"}},
        {"name": "read_resource", "arguments": {"uri": f"{GERMLINE}/references/nope.md"}},
        {"name": "read_resource", "arguments": {"uri": f"{GERMLINE}/../lay-summary/SKILL.md"}},
        {"name": "read_resource", "arguments": {"uri": f"{GERMLINE}/_manifest"}},
    ],
    ids=[
        "unknown_skill",
        "unknown_skill_uri",
        "non_skill_uri",
        "traversal",
        "ref_traversal",
        "unknown_file",
        "skill_traversal",
        "manifest",
    ],
)
async def test_call_tool_rejects_unknown_skill_or_non_skill_uri(call: dict) -> None:
    async with Client(_server()) as client:
        result = await client.call_tool("call_tool", call, raise_on_error=False)
    assert result.is_error
    assert '"not_found"' in result.content[0].text  # type: ignore[union-attr]


async def test_skill_hit_does_not_consume_limit() -> None:
    tools = [t for t in load_catalog() if not t.name.startswith("skill_")]
    transform = CompactBM25SearchTransform(skills=skill_tools(ROOT / "skills"))
    query = "candidate genes from patient phenotype"
    plain, _, _ = await CompactBM25SearchTransform()._ranked(tools, query, 3)
    hits, _, _ = await transform._ranked(tools, query, 3)
    assert [t.name for t in hits if not t.name.startswith("skill_")] == [t.name for t in plain]
    assert [t.name for t in hits if t.name.startswith("skill_")] == [
        "skill_phenotype-to-candidate-genes"
    ]


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
