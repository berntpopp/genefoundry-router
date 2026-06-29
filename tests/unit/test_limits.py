"""Inbound request limits: body-size cap (413) + per-client rate limit (429)."""

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from genefoundry_router.limits import RequestLimitMiddleware


def _client(**kw) -> TestClient:
    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/x", ok, methods=["GET", "POST"])])
    app.add_middleware(RequestLimitMiddleware, **kw)
    return TestClient(app)


def test_body_cap_rejects_oversized() -> None:
    c = _client(max_body_bytes=10, rate_limit_rpm=0)
    assert c.post("/x", content=b"x" * 100).status_code == 413


def test_body_cap_allows_small() -> None:
    c = _client(max_body_bytes=1000, rate_limit_rpm=0)
    assert c.post("/x", content=b"hi").status_code == 200


def test_rate_limit_returns_429_after_limit() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=2)
    assert c.get("/x").status_code == 200
    assert c.get("/x").status_code == 200
    assert c.get("/x").status_code == 429  # third within the window is rejected


def test_limits_disabled_by_zero() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=0)
    for _ in range(6):
        assert c.get("/x").status_code == 200
