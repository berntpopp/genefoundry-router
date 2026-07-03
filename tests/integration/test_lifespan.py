from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.observability import BACKEND_STATUS, BACKEND_TOOL_COUNT
from genefoundry_router.registry import BackendDef, TransformConfig
from genefoundry_router.server import build_app


def test_lifespan_runs_normalization_then_search(pubtator_fake):
    # isolate the module-level reachability cache from other tests' set_backend_up calls
    BACKEND_STATUS.clear()
    # poll disabled; normalization must still run at startup
    settings = RouterSettings(_env_file=None, GF_POLL_INTERVAL=0)
    registry = [
        BackendDef(
            name="pubtator",
            url_env="X",
            namespace="pubtator",
            tags=["literature"],
            transform=TransformConfig(strip_prefix="pubtator_"),
        )
    ]
    app = build_app(settings, registry, proxy_targets={"pubtator": pubtator_fake})
    with TestClient(app) as client:  # triggers lifespan startup + shutdown
        body = client.get("/health").json()
        assert "pubtator" in body["backends"]["namespaces"]
        # the composed lifespan seeds reachability via set_backend_up at startup
        # (the bare mcp_app.lifespan does not), proving the composed lifespan ran.
        assert body["backends"]["reachable"]["pubtator"] is True


def test_lifespan_marks_zero_tool_backend_degraded(pubtator_fake):
    """Reachability is derived from the LIVE tool harvest, not from 'a URL is configured'.
    A mounted backend that yields no tools (down / 307 / transport-broken) must read as
    down in /health, and the aggregate status must flip to 'degraded'."""
    BACKEND_STATUS.clear()
    BACKEND_TOOL_COUNT.clear()
    settings = RouterSettings(_env_file=None, GF_POLL_INTERVAL=0)
    registry = [
        BackendDef(name="pubtator", url_env="X", namespace="pubtator", tags=["literature"]),
        BackendDef(name="genereviews", url_env="Y", namespace="genereviews"),
    ]
    empty_fake = make_fake_backend("genereviews-link", [])  # reachable, but zero tools
    app = build_app(
        settings,
        registry,
        proxy_targets={"pubtator": pubtator_fake, "genereviews": empty_fake},
    )
    with TestClient(app) as client:
        body = client.get("/health").json()
        assert body["backends"]["reachable"]["pubtator"] is True
        assert body["backends"]["reachable"]["genereviews"] is False
        assert body["backends"]["tools"]["genereviews"] == 0
        assert body["backends"]["tools"]["pubtator"] >= 1
        assert body["backends"]["degraded"] == ["genereviews"]
        assert body["status"] == "degraded"
