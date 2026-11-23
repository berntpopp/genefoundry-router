"""#336: the router serves skills/ over MCP (skill:// resources + SEP-2640 skills/list)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.exceptions import RouterError
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


_CLINVAR = BackendDef(name="clinvar", url_env="Y", namespace="clinvar", entrypoints=["get_variant"])
_POINTER = 'call_tool(name="skill_germline-variant-classification")'


def test_server_instructions_name_served_skills(gnomad_fake: FastMCP) -> None:
    server = build_server(
        RouterSettings(_env_file=None), _REGISTRY, proxy_targets={"gnomad": gnomad_fake}
    )
    assert "germline-variant-classification" in (server.instructions or "").split("\n\n")[0]


def test_skills_header_off_drops_skill_pointers(gnomad_fake: FastMCP) -> None:
    # #376: pinned tool descriptions and instructions name the matching skill unless off.
    clinvar = make_fake_backend("clinvar-link", ["get_variant"])
    app = build_app(
        RouterSettings(_env_file=None),
        [*_REGISTRY, _CLINVAR],
        proxy_targets={"gnomad": gnomad_fake, "clinvar": clinvar},
    )

    def post(method: str, params: dict, skills: str | None) -> dict:
        headers = {"accept": "application/json, text/event-stream"}
        if skills is not None:
            headers["x-gf-skills"] = skills
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        resp = client.post("/mcp", headers=headers, json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["result"]

    def pinned(skills: str | None) -> str:
        tools = post("tools/list", {}, skills)["tools"]
        return next(t for t in tools if t["name"] == "clinvar_get_variant").get("description", "")

    init = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    }
    with TestClient(app) as client:
        assert _POINTER in pinned(None)
        assert "skill" not in pinned("off")
        assert "WORKFLOW SKILLS" in post("initialize", init, None)["instructions"]
        assert "WORKFLOW SKILLS" not in post("initialize", init, "off")["instructions"]
        assert _POINTER in pinned(None)  # no cross-request state


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
