"""Router profile conformance (in-process, no docker required).

Asserts the MCP Transport & Session Standard v1 "stateless" checks directly
against the built FastAPI app so no real network port is needed.

RED state (before fix):  test_router_profile fails on the 307 check because
  build_app currently calls server.http_app(path="/") + app.mount(GF_MCP_PATH,
  mcp_app) which causes Starlette to redirect POST /mcp → POST /mcp/.

GREEN state (after fix): bake GF_MCP_PATH into http_app(path=GF_MCP_PATH,
  stateless_http=True, json_response=True) and mount("/", mcp_app) → 200
  direct, no session-id, correct serverInfo.name.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "router-conformance-probe", "version": "1.0.0"},
    },
}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def router_client() -> TestClient:
    gnomad = make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])
    settings = RouterSettings(_env_file=None)
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    app = build_app(settings, registry, proxy_targets={"gnomad": gnomad})
    # follow_redirects=False: we must see 307 raw (run_probe also sets follow_redirects=False).
    with TestClient(app, follow_redirects=False) as client:
        yield client


def test_router_profile(router_client: TestClient) -> None:
    """Router Transport Standard v1: stateless+JSON, no 307, correct serverInfo.name.

    Before the fix this test FAILS at T1 (assertion on 307).
    After the fix all assertions pass.
    """
    r = router_client.post("/mcp", json=_INIT, headers=_HEADERS)

    # T1: Transport Standard v1 — no 307 redirect on POST /mcp.
    assert r.status_code != 307, (
        f"POST /mcp returned {r.status_code} "
        f"(location={r.headers.get('location')!r}) — "
        "fix: http_app(path=GF_MCP_PATH, ...) + app.mount('/', mcp_app)"
    )

    # T2: Must return 200 application/json.
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:200]}"
    ct = r.headers.get("content-type", "")
    assert ct.startswith("application/json"), f"content-type: {ct!r}"

    # T3: Stateless tier — no Mcp-Session-Id header.
    lower_headers = {k.lower() for k in r.headers}
    assert "mcp-session-id" not in lower_headers, (
        "session ID must not be assigned in stateless tier"
    )

    # T4: Correct server identity (probe checks serverInfo.name == "genefoundry").
    name = r.json().get("result", {}).get("serverInfo", {}).get("name")
    assert name == "genefoundry", f"serverInfo.name: expected 'genefoundry', got {name!r}"

    # T5: Trailing-slash path /mcp/ emits 307 → /mcp (Starlette redirect_slashes).
    # This is intentional: the canonical MCP path is /mcp (no slash).  Auth middleware and
    # the auth-contract test (test_auth_contract.py) both target /mcp so a 307 there would
    # bypass the 401 check.  Pinning this assertion makes the behaviour explicit rather than
    # surprising for future maintainers.
    r_slash = router_client.post("/mcp/", json=_INIT, headers=_HEADERS)
    assert r_slash.status_code == 307, (
        f"POST /mcp/ returned {r_slash.status_code} instead of expected 307 — "
        "if Starlette redirect_slashes behaviour changed, update test_auth_contract.py "
        "accordingly so the 401 contract is still asserted on the correct canonical path"
    )
    assert r_slash.headers.get("location", "").rstrip("/").endswith("/mcp"), (
        f"307 location should point to /mcp, got {r_slash.headers.get('location')!r}"
    )
