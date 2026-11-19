"""Per-account rate limit, keyed on the VERIFIED access token (post-auth layer)."""

import json

import pytest
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.limits import (
    AccountRateLimitMiddleware,
    account_rate_limit_middleware,
    ip_rate_limit_rpm,
)

_TOKENS = {
    "tok-alice": {"client_id": "claude", "sub": "alice"},
    "tok-bob": {"client_id": "claude", "sub": "bob"},
    "tok-bench": {"client_id": "gfbench", "sub": "svc-uuid-1"},
    "tok-owner": {"client_id": "claude", "sub": "owner"},
}


def _client(rpm: int = 2, overrides: dict | None = None) -> TestClient:
    async def ok(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    verifier = StaticTokenVerifier(tokens=_TOKENS)
    # Same order FastMCP's http_app builds: its auth middleware first, ours appended after.
    middleware = [
        *verifier.get_middleware(),
        Middleware(AccountRateLimitMiddleware, rate_limit_rpm=rpm, overrides=overrides),
    ]
    app = Starlette(routes=[Route("/mcp", ok, methods=["POST"])], middleware=middleware)
    return TestClient(app)


def _post(c: TestClient, token: str | None = None, **headers: str):
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", headers=headers)


def test_two_subjects_have_independent_buckets() -> None:
    c = _client(rpm=2)
    assert [_post(c, "tok-alice").status_code for _ in range(3)] == [200, 200, 429]
    assert _post(c, "tok-bob").status_code == 200


def test_same_subject_across_ips_shares_one_bucket() -> None:
    c = _client(rpm=2)
    codes = [
        _post(c, "tok-alice", **{"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_override_by_client_id_and_unlimited_by_subject() -> None:
    c = _client(rpm=1, overrides={"gfbench": 3, "owner": "unlimited"})
    assert [_post(c, "tok-bench").status_code for _ in range(4)] == [200, 200, 200, 429]
    owner = [_post(c, "tok-owner") for _ in range(10)]
    assert {r.status_code for r in owner} == {200}
    assert "ratelimit" not in owner[0].headers  # no quota to advertise
    assert [_post(c, "tok-alice").status_code for _ in range(2)] == [200, 429]


def test_subject_override_wins_over_client_id_override() -> None:
    c = _client(rpm=1, overrides={"claude": 5, "alice": 2})
    assert [_post(c, "tok-alice").status_code for _ in range(3)] == [200, 200, 429]


def test_unauthenticated_and_forged_requests_pass_to_the_ip_floor() -> None:
    # No verified token -> not this layer's call (the route 401s in prod; IP floor counted it).
    c = _client(rpm=1)
    assert {_post(c).status_code for _ in range(3)} == {200}
    assert {_post(c, "forged").status_code for _ in range(3)} == {200}


def test_unverified_headers_cannot_select_a_subject() -> None:
    c = _client(rpm=1, overrides={"owner": "unlimited"})
    spoof = {"X-Forwarded-User": "owner", "X-Auth-Subject": "owner", "X-Client-Id": "owner"}
    assert [_post(c, "tok-alice", **spoof).status_code for _ in range(2)] == [200, 429]
    unsigned = "eyJhbGciOiJub25lIn0." + "eyJzdWIiOiJvd25lciJ9."  # alg=none, sub=owner
    assert _post(c, unsigned).headers.get("ratelimit") is None  # never keyed, never limited


def test_headers_and_jsonrpc_429_body() -> None:
    c = _client(rpm=1)
    ok = _post(c, "tok-alice")
    assert ok.headers["ratelimit-policy"] == '"account";q=1;w=60'
    assert ok.headers["ratelimit"].startswith('"account";r=0;t=')
    resp = _post(c, "tok-alice")
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    body = json.loads(resp.content)
    assert body["error"]["code"] == -32000
    assert body["error"]["data"]["retry_after"] == int(resp.headers["retry-after"])


def test_ratelimited_log_never_carries_the_account(monkeypatch) -> None:
    import genefoundry_router.limits as limits_mod

    events: list[dict] = []
    monkeypatch.setattr(limits_mod.log, "warning", lambda event, **kw: events.append(kw))
    c = _client(rpm=1)
    _post(c, "tok-alice")
    _post(c, "tok-alice")
    assert events == [{"policy": "account", "limit": 1}]


@pytest.mark.parametrize(
    "raw",
    [
        '{"x": 0}',
        '{"x": -5}',
        '{"x": "lots"}',
        '{"x": 1.5}',
        '{"x": true}',
        '{"": 10}',
        '{" x": 10}',
        "[1, 2]",
        "not json",
    ],
)
def test_override_config_rejects_invalid(monkeypatch, raw: str) -> None:
    monkeypatch.setenv("GF_RATE_LIMIT_OVERRIDES", raw)
    with pytest.raises(ValueError):
        RouterSettings()


def test_override_config_parses_json_env(monkeypatch) -> None:
    monkeypatch.setenv("GF_RATE_LIMIT_OVERRIDES", '{"gfbench": 600, "owner-sub": "unlimited"}')
    assert RouterSettings().GF_RATE_LIMIT_OVERRIDES == {"gfbench": 600, "owner-sub": "unlimited"}


def test_override_must_not_name_the_shared_oauth_client(monkeypatch) -> None:
    monkeypatch.setenv("GF_OAUTH_CLIENT_ID", "genefoundry-router")
    monkeypatch.setenv("GF_RATE_LIMIT_OVERRIDES", '{"genefoundry-router": 600}')
    with pytest.raises(ValueError, match="GF_OAUTH_CLIENT_ID"):
        RouterSettings()


def test_wiring_off_without_auth_and_ip_floor_inherits() -> None:
    none = RouterSettings(GF_AUTH_MODE="none", GF_RATE_LIMIT_RPM=120)
    assert account_rate_limit_middleware(none) == []
    assert ip_rate_limit_rpm(none) == 120
    assert ip_rate_limit_rpm(RouterSettings(GF_RATE_LIMIT_RPM=120, GF_RATE_LIMIT_IP_RPM=900)) == 900


def test_fastmcp_http_app_runs_our_middleware_after_auth() -> None:
    # The wiring contract server.py relies on: http_app(middleware=...) lands INSIDE
    # FastMCP's AuthenticationMiddleware, so scope["user"] is already the verified token.
    from fastmcp import FastMCP

    mcp = FastMCP("t", auth=StaticTokenVerifier(tokens=_TOKENS))
    app = mcp.http_app(
        path="/mcp",
        stateless_http=True,
        json_response=True,
        middleware=[Middleware(AccountRateLimitMiddleware, rate_limit_rpm=1)],
    )
    with TestClient(app) as c:
        first = _post(c, "tok-alice")
        assert first.status_code != 429
        assert first.headers["ratelimit-policy"] == '"account";q=1;w=60'
        assert _post(c, "tok-alice").status_code == 429
        assert _post(c).status_code == 401  # unauthenticated: auth answers, not us
