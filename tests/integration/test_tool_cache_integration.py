from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app, build_server


@pytest.fixture
def slow_backend_fakes():
    vep = FastMCP("vep")
    vep_call_counts = {"recode_variant": 0}

    @vep.tool()
    def recode_variant(variant: str) -> dict:
        vep_call_counts["recode_variant"] += 1
        return {
            "success": True,
            "variant": variant,
            "assembly": "GRCh38",
            "_meta": {"tool": "recode_variant", "elapsed_ms": 38000},
        }

    gnomad = FastMCP("gnomad")
    gnomad_call_counts = {"search_genes": 0}

    @gnomad.tool()
    def search_genes(query: str) -> dict:
        gnomad_call_counts["search_genes"] += 1
        return {
            "success": True,
            "genes": [query],
            "_meta": {"tool": "search_genes"},
        }

    return {
        "vep": vep,
        "vep_counts": vep_call_counts,
        "gnomad": gnomad,
        "gnomad_counts": gnomad_call_counts,
    }


@pytest.mark.asyncio
async def test_tool_cache_end_to_end_via_build_server(slow_backend_fakes) -> None:
    settings = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    registry = [
        BackendDef(name="vep", url_env="GF_VEP_URL", namespace="vep", tool_cache_ttl=86400),
        BackendDef(name="gnomad", url_env="GF_GNOMAD_URL", namespace="gnomad"),  # uncached
    ]
    server = build_server(
        settings,
        registry,
        proxy_targets={
            "vep": slow_backend_fakes["vep"],
            "gnomad": slow_backend_fakes["gnomad"],
        },
        enable_search=False,
    )

    # 1. Call vep_recode_variant first time -> cache miss
    res1 = await server.call_tool("vep_recode_variant", {"variant": "13-32315474-C-T"})
    assert slow_backend_fakes["vep_counts"]["recode_variant"] == 1
    assert res1.structured_content["variant"] == "13-32315474-C-T"
    assert res1.structured_content["_meta"].get("cached") is None

    # 2. Call vep_recode_variant second time with same args -> cache hit!
    res2 = await server.call_tool("vep_recode_variant", {"variant": "13-32315474-C-T"})
    assert slow_backend_fakes["vep_counts"]["recode_variant"] == 1  # backend NOT called again!
    assert res2.structured_content["variant"] == "13-32315474-C-T"
    assert res2.structured_content["_meta"].get("cached") is True

    # 3. Call with different args -> cache miss
    res3 = await server.call_tool("vep_recode_variant", {"variant": "7-140753336-A-T"})
    assert slow_backend_fakes["vep_counts"]["recode_variant"] == 2
    assert res3.structured_content["variant"] == "7-140753336-A-T"

    # 4. Gnomad is not configured for caching -> always invokes backend
    await server.call_tool("gnomad_search_genes", {"query": "BRCA1"})
    await server.call_tool("gnomad_search_genes", {"query": "BRCA1"})
    assert slow_backend_fakes["gnomad_counts"]["search_genes"] == 2


def test_cache_invalidation_api_endpoints(slow_backend_fakes) -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="none",
        GF_METRICS_TOKEN="secret-ops-token",  # noqa: S106
    )
    registry = [
        BackendDef(name="vep", url_env="GF_VEP_URL", namespace="vep", tool_cache_ttl=86400),
    ]
    app = build_app(
        settings,
        registry,
        proxy_targets={"vep": slow_backend_fakes["vep"]},
    )
    auth_headers = {"Authorization": "Bearer secret-ops-token"}

    with TestClient(app) as client:
        # Prepopulate cache in app.state.tool_cache
        cache = app.state.tool_cache
        assert cache is not None
        cache.set("vep_recode_variant", {"v": "1"}, {"ok": True})
        cache.set("vep_recode_variant", {"v": "2"}, {"ok": True})
        assert cache.stats["size"] == 2

        # Invalidate by tool via query params
        resp = client.post(
            "/api/cache/invalidate",
            params={"tool": "vep_recode_variant"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["invalidated"] == 2
        assert data["stats"]["size"] == 0

        # Repopulate and invalidate by tool via JSON body
        cache.set("vep_recode_variant", {"v": "10"}, {"ok": True})
        assert cache.stats["size"] == 1
        resp = client.post(
            "/api/cache/invalidate",
            json={"tool": "vep_recode_variant"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["invalidated"] == 1
        assert cache.stats["size"] == 0

        # Repopulate and clear
        cache.set("vep_recode_variant", {"v": "3"}, {"ok": True})
        resp = client.post("/api/cache/clear", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 1
        assert cache.stats["size"] == 0


@pytest.mark.parametrize("path", ["/api/cache/clear", "/api/cache/invalidate"])
def test_cache_admin_denies_without_configured_token(path, slow_backend_fakes) -> None:
    settings = RouterSettings(_env_file=None, GF_AUTH_MODE="none", GF_METRICS_TOKEN=None)
    app = build_app(settings, [], proxy_targets={})
    cache = app.state.tool_cache
    assert cache is not None
    cache.set("vep_recode_variant", {"v": "1"}, {"ok": True})

    with TestClient(app) as client:
        response = client.post(path, json={"tool": "vep_recode_variant"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert cache.stats["size"] == 1
        assert client.get("/metrics").status_code == 200


def test_cache_admin_endpoints_require_token_when_configured(slow_backend_fakes) -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="none",
        GF_METRICS_TOKEN="secret-ops-token",  # noqa: S106
    )
    registry = [
        BackendDef(name="vep", url_env="GF_VEP_URL", namespace="vep", tool_cache_ttl=86400),
    ]
    app = build_app(
        settings,
        registry,
        proxy_targets={"vep": slow_backend_fakes["vep"]},
    )

    with TestClient(app) as client:
        # 1. Unauthenticated requests rejected with 401
        res1 = client.post("/api/cache/clear")
        assert res1.status_code == 401
        assert res1.headers.get("www-authenticate") == "Bearer"

        res2 = client.post("/api/cache/invalidate", json={"tool": "vep_recode_variant"})
        assert res2.status_code == 401

        # 2. Authenticated request with Bearer token succeeds
        headers = {"Authorization": "Bearer secret-ops-token"}
        res3 = client.post("/api/cache/clear", headers=headers)
        assert res3.status_code == 200
        assert res3.json()["status"] == "ok"

        res4 = client.post(
            "/api/cache/invalidate", json={"tool": "vep_recode_variant"}, headers=headers
        )
        assert res4.status_code == 200
        assert res4.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_tool_cache_rewrites_hints_before_caching() -> None:
    vep = FastMCP("vep")
    vep_calls = 0

    @vep.tool()
    def recode_variant(v: str) -> dict:
        nonlocal vep_calls
        vep_calls += 1
        return {
            "success": True,
            "fallback_tool": "recode_variant",
        }

    registry = [BackendDef(name="vep", url_env="GF_VEP_URL", namespace="vep", tool_cache_ttl=86400)]
    settings = RouterSettings(_env_file=None, GF_AUTH_MODE="none", GF_REWRITE_HINTS=True)
    server = build_server(settings, registry, proxy_targets={"vep": vep})

    res1 = await server.call_tool("vep_recode_variant", {"v": "1"})
    assert vep_calls == 1
    assert res1.structured_content["fallback_tool"] == "vep_recode_variant"

    res2 = await server.call_tool("vep_recode_variant", {"v": "1"})
    assert vep_calls == 1
    assert res2.structured_content["fallback_tool"] == "vep_recode_variant"


@pytest.mark.asyncio
async def test_tool_cache_does_not_cache_text_block_errors() -> None:
    vep = FastMCP("vep")
    vep_calls = 0

    @vep.tool()
    def recode_variant(v: str) -> ToolResult:
        nonlocal vep_calls
        vep_calls += 1
        return ToolResult(
            content=[TextContent(type="text", text='{"error": "variant not found"}')],
            structured_content=None,
        )

    registry = [BackendDef(name="vep", url_env="GF_VEP_URL", namespace="vep", tool_cache_ttl=86400)]
    settings = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    server = build_server(settings, registry, proxy_targets={"vep": vep})

    await server.call_tool("vep_recode_variant", {"v": "bad"})
    assert vep_calls == 1

    await server.call_tool("vep_recode_variant", {"v": "bad"})
    assert vep_calls == 2
