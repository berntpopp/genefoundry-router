from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.observability import BACKEND_STATUS
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
