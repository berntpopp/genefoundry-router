"""Inbound request limits: body-size cap (413) + per-client rate limit (429)."""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Scope

import genefoundry_router.limits as limits_mod
from genefoundry_router.limits import RequestLimitMiddleware, _client_key


async def _noop_app(_scope, _receive, _send) -> None:
    return None


def _scope(
    headers: list[tuple[bytes, bytes]] | None = None,
    method: str = "GET",
    path: str = "/x",
    client_host: str = "10.0.0.9",
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": (client_host, 12345),
        "server": ("testserver", 80),
    }


def _client(**kw) -> TestClient:
    async def ok(_request: Request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/x", ok, methods=["GET", "POST"])])
    app.add_middleware(RequestLimitMiddleware, **kw)
    return TestClient(app)


class _SpyLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.events.append((event, kwargs))


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
    resp = c.get("/x")
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "60"


def test_spoofed_leftmost_xff_values_share_bucket() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=1, trusted_proxy_hops=1)
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-a, 198.51.100.10"}).status_code == 200
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-b, 198.51.100.10"}).status_code == 429


def test_xff_rotation_no_longer_bypasses_rate_limit() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=2, trusted_proxy_hops=1)
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-1, 203.0.113.9"}).status_code == 200
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-2, 203.0.113.9"}).status_code == 200
    assert c.get("/x", headers={"X-Forwarded-For": "spoof-3, 203.0.113.9"}).status_code == 429


def test_trusted_proxy_hops_two_selects_second_from_right() -> None:
    scope = _scope(
        headers=[(b"x-forwarded-for", b"1.2.3.4, 203.0.113.55, 198.51.100.7")],
        client_host="10.0.0.5",
    )
    assert _client_key(scope, trusted_proxy_hops=2) == "203.0.113.55"


def test_insufficient_hop_depth_and_zero_hops_fall_back_to_scope_client() -> None:
    scope = _scope(
        headers=[(b"x-forwarded-for", b"198.51.100.10")],
        client_host="10.0.0.42",
    )
    assert _client_key(scope, trusted_proxy_hops=2) == "10.0.0.42"
    assert _client_key(scope, trusted_proxy_hops=0) == "10.0.0.42"


def test_hits_clear_across_windows(monkeypatch) -> None:
    middleware = RequestLimitMiddleware(_noop_app, max_body_bytes=0, rate_limit_rpm=10)

    monkeypatch.setattr(limits_mod.time, "monotonic", lambda: 1.0)
    assert middleware._increment("198.51.100.1", limits_mod.time.monotonic()) is True
    assert middleware._hits == {"198.51.100.1": 1}

    monkeypatch.setattr(limits_mod.time, "monotonic", lambda: 61.0)
    assert middleware._increment("198.51.100.2", limits_mod.time.monotonic()) is True
    assert middleware._hits == {"198.51.100.2": 1}


def test_max_tracked_ceiling_fails_open_once_per_window(monkeypatch) -> None:
    spy = _SpyLog()
    monkeypatch.setattr(limits_mod, "_MAX_TRACKED", 2)
    monkeypatch.setattr(limits_mod, "log", spy)
    middleware = RequestLimitMiddleware(_noop_app, max_body_bytes=0, rate_limit_rpm=1)

    assert middleware._increment("a", 1.0) is True
    assert middleware._increment("b", 1.0) is True
    assert len(middleware._hits) == 2
    assert middleware._increment("c", 1.0) is True
    assert middleware._increment("d", 1.0) is True
    assert len(middleware._hits) == 2
    assert [event for event, _ in spy.events].count("rate_limit_tracking_ceiling") == 1
    assert spy.events[0][1]["max_tracked"] == 2
    assert middleware._increment("a", 1.0) is False

    assert middleware._increment("e", 61.0) is True
    assert middleware._increment("f", 61.0) is True
    assert middleware._increment("g", 61.0) is True
    assert middleware._hits == {"e": 1, "f": 1}
    assert [event for event, _ in spy.events].count("rate_limit_tracking_ceiling") == 2


def test_limits_disabled_by_zero() -> None:
    c = _client(max_body_bytes=0, rate_limit_rpm=0)
    for _ in range(6):
        assert c.get("/x").status_code == 200
