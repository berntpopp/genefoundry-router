from fastapi.testclient import TestClient
from fastmcp import FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.security import HostOriginValidationMiddleware
from genefoundry_router.server import build_app


def test_host_guard_covers_outer_routes_and_correlation_id(
    gnomad_fake: FastMCP,
) -> None:
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_HOSTS=["genefoundry.test"],
        GF_METRICS_TOKEN="metrics-secret",  # noqa: S106
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})

    with TestClient(app) as client:
        responses = (
            client.get("/health", headers={"host": "evil.example"}),
            client.get("/metrics", headers={"host": "evil.example"}),
            client.post("/mcp/", headers={"host": "evil.example"}, json={}),
        )

    for response in responses:
        assert response.status_code == 421
        assert response.headers["x-request-id"]

    guard_count = sum(
        middleware.cls is HostOriginValidationMiddleware for middleware in app.user_middleware
    )
    assert guard_count == 1


def test_router_disables_inner_fastmcp_guard(
    monkeypatch,
    gnomad_fake: FastMCP,
) -> None:
    calls: list[dict[str, object]] = []
    original = FastMCP.http_app

    def spy_http_app(self: FastMCP, *args: object, **kwargs: object):
        calls.append(dict(kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FastMCP, "http_app", spy_http_app)
    settings = RouterSettings(_env_file=None, GF_ALLOWED_HOSTS=["testserver"])
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]

    build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})

    assert calls[-1]["host_origin_protection"] is False
