from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.observability import (
    BACKEND_STATUS,
    BACKEND_TOOL_COUNT,
    configure_logging,
    namespace_tool_counts,
    register_health,
    set_backend_up,
)
from genefoundry_router.registry import BackendDef


def test_configure_logging_is_idempotent():
    configure_logging("INFO")
    configure_logging("DEBUG")  # must not raise on re-config


def test_health_reports_enabled_backends():
    BACKEND_STATUS.clear()
    BACKEND_TOOL_COUNT.clear()
    app = FastAPI()
    backends = [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp"),
        BackendDef(name="hgnc", url_env="Y", namespace="hgnc", enabled=False),
    ]
    register_health(app, backends)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["backends"]["enabled"] == 1
    assert "gnomad" in body["backends"]["namespaces"]
    assert "hgnc" not in body["backends"]["namespaces"]


def test_namespace_tool_counts_groups_by_namespace_prefix():
    counts = namespace_tool_counts(
        ["gnomad_search_genes", "gnomad_get_variant_details", "gtex_get_median_expression_levels"]
    )
    assert counts == {"gnomad": 2, "gtex": 1}


def test_health_degrades_when_an_enabled_backend_has_zero_tools():
    """A backend that harvested 0 tools (down / 307 / transport-broken) MUST surface as
    degraded — never silently reported healthy just because a URL is configured."""
    BACKEND_STATUS.clear()
    BACKEND_TOOL_COUNT.clear()
    up = BackendDef(name="gnomad", url_env="X", namespace="gnomad", url="https://x/mcp")
    down = BackendDef(name="genereviews", url_env="Y", namespace="genereviews", url="https://y/mcp")
    set_backend_up(up, up=True, tools=7)
    set_backend_up(down, up=False, tools=0)  # reachable host, zero usable tools

    app = FastAPI()
    register_health(app, [up, down])
    body = TestClient(app).get("/health").json()

    assert body["status"] == "degraded"
    assert body["backends"]["degraded"] == ["genereviews"]
    assert body["backends"]["tools"]["gnomad"] == 7
    assert body["backends"]["tools"]["genereviews"] == 0
    assert body["backends"]["reachable"]["genereviews"] is False
