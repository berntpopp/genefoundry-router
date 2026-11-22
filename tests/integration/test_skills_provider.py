"""#336: the router serves skills/ over MCP (skill:// resources + SEP-2640 skills/list)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.exceptions import RouterError
from genefoundry_router.instructions import build_instructions
from genefoundry_router.registry import BackendDef
from genefoundry_router.security import MCP_CORS_ALLOW_HEADERS
from genefoundry_router.server import build_app, build_server

SOMATIC = "skill://somatic-variant-interpretation/SKILL.md"
_VERSION = "2026-07-28"
_REGISTRY = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]


async def test_resources_list_serves_skills(gnomad_fake: FastMCP) -> None:
    server = build_server(
        RouterSettings(_env_file=None), _REGISTRY, proxy_targets={"gnomad": gnomad_fake}
    )
    async with Client(server) as client:
        uris = {str(r.uri) for r in await client.list_resources()}
        assert SOMATIC in uris
        text = (await client.read_resource(SOMATIC))[0].text  # type: ignore[union-attr]
        assert "name: somatic-variant-interpretation" in text


def test_skills_extension_on_modern_protocol(gnomad_fake: FastMCP) -> None:
    app = build_app(
        RouterSettings(_env_file=None), _REGISTRY, proxy_targets={"gnomad": gnomad_fake}
    )
    meta = {
        "io.modelcontextprotocol/protocolVersion": _VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "skills-test", "version": "1.0.0"},
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": _VERSION,
        "mcp-method": "skills/list",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "skills/list", "params": {"_meta": meta}}
    with TestClient(app) as client:
        resp = client.post("/mcp", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    assert SOMATIC in {s["uri"] for s in resp.json()["result"]["skills"]}


def test_missing_skills_dir_fails() -> None:
    settings = RouterSettings(_env_file=None, GF_SKILLS_DIR="does-not-exist")
    with pytest.raises(RouterError, match="does-not-exist"):
        build_server(settings, _REGISTRY, proxy_targets={"gnomad": FastMCP("x")})


def test_instructions_mention_skills() -> None:
    assert "skill://" in build_instructions(_REGISTRY)[:600]


def test_skills_header_off_hides_skill_hits(gnomad_fake: FastMCP) -> None:
    assert "x-gf-skills" in MCP_CORS_ALLOW_HEADERS  # browser clients can send it
    app = build_app(
        RouterSettings(_env_file=None), _REGISTRY, proxy_targets={"gnomad": gnomad_fake}
    )
    query = {"name": "search_tools", "arguments": {"query": "somatic variant interpretation"}}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": query}

    def names(skills: str | None) -> list[str]:
        headers = {"accept": "application/json, text/event-stream"}
        if skills is not None:
            headers["x-gf-skills"] = skills
        resp = client.post("/mcp", headers=headers, json=body)
        assert resp.status_code == 200, resp.text
        return [h["name"] for h in resp.json()["result"]["structuredContent"]["result"]]

    with TestClient(app) as client:
        assert "skill_somatic-variant-interpretation" in names(None)
        assert not [n for n in names(" OFF ") if n.startswith("skill_")]
        assert "skill_somatic-variant-interpretation" in names(None)  # no cross-request state
