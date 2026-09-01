"""``/mcp`` must honour ``GF_ALLOWED_ORIGINS`` as CORS, not merely as a rejection list.

Production, 2026-09-01: ``OPTIONS /mcp`` answered ``405`` with ``allow: DELETE, POST`` and no
``Access-Control-*`` header at all, **including** for ``https://claude.ai`` — an origin that is
in the running container's ``GF_ALLOWED_ORIGINS``. 62 preflights reached the edge in a month;
62 were refused. The allowlist was therefore enforcement-only: it could turn a browser away
(403) but could never let one in, so every browser-hosted MCP client was blocked at the first
request and the 14-entry configuration granted nothing.

Two standards fix the shape of the fix and both are load-bearing here:

* **Fetch Standard § CORS protocol.** A CORS-preflight is an ``OPTIONS`` request carrying
  ``Access-Control-Request-Method``; the browser reads the *response's*
  ``Access-Control-Allow-Origin`` to decide whether to release the real request. Only the
  CORS-safelisted response headers are readable from script — every other response header
  (here ``Mcp-Session-Id``, ``WWW-Authenticate``, ``X-Request-ID``) must be named in
  ``Access-Control-Expose-Headers`` or the client cannot see it. ``Authorization`` is not a
  safelisted *request* header, so it must be named in ``Access-Control-Allow-Headers``.
* **MCP Streamable HTTP transport.** A server that does not offer an SSE stream on ``GET``
  MUST answer ``GET`` with ``405``. That 405 is deliberate and must survive this change;
  only the ``OPTIONS`` preflight becomes answerable. ``Mcp-Session-Id`` is the header the
  server returns on initialize and the client echoes on every later request — a browser
  client that cannot read it cannot hold a session at all, which is why it heads the
  expose list.

The Host/Origin guard (``GF_ALLOWED_HOSTS``, DNS-rebinding protection) is a separate,
deliberate control and stays outermost: a disallowed Host is still ``421`` even for a
preflight, and a disallowed Origin still gets ``403`` with no CORS grant.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP

from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

ALLOWED = "https://claude.ai"
DISALLOWED = "https://evil.example"

# A representative slice of the production allowlist. The point of the parametrised fence
# below is that every entry an operator puts in GF_ALLOWED_ORIGINS is actually served.
PRODUCTION_ORIGINS = [
    "https://claude.ai",
    "https://claude.com",
    "https://chatgpt.com",
    "https://cursor.sh",
    "https://aistudio.google.com",
    "https://vscode.dev",
]


def _app(gnomad_fake: FastMCP, origins: list[str], hosts: list[str] | None = None):
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_ORIGINS=origins,
        GF_ALLOWED_HOSTS=hosts or [],
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    return build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})


def _preflight(origin: str, method: str = "POST", headers: str = "content-type,authorization"):
    return {
        "origin": origin,
        "access-control-request-method": method,
        "access-control-request-headers": headers,
    }


def test_mcp_preflight_allowed_origin(gnomad_fake: FastMCP) -> None:
    """THE defect. A preflight from an allowlisted origin must be answered, not 405'd."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.options("/mcp", headers=_preflight(ALLOWED))

    assert response.status_code in (200, 204), response.text
    assert response.headers["access-control-allow-origin"] == ALLOWED
    allowed_methods = response.headers["access-control-allow-methods"].lower()
    assert "post" in allowed_methods
    # Authorization is NOT a CORS-safelisted request header: without it here, an
    # authenticated browser client can never send its bearer token.
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_mcp_preflight_reflects_origin_and_varies(gnomad_fake: FastMCP) -> None:
    """Never ``*``: reflect the validated origin and mark the response origin-dependent,
    so a shared cache cannot hand one origin's grant to another."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.options("/mcp", headers=_preflight(ALLOWED))

    assert response.headers["access-control-allow-origin"] != "*"
    assert "origin" in response.headers.get("vary", "").lower()


def test_mcp_preflight_disallowed_origin_gets_no_grant(gnomad_fake: FastMCP) -> None:
    """An origin outside the allowlist must not receive a CORS grant. (The Origin guard
    answers it 403 before CORS is reached; what matters is that no ACAO comes back.)"""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.options("/mcp", headers=_preflight(DISALLOWED))

    assert "access-control-allow-origin" not in response.headers
    assert response.status_code == 403


def test_mcp_post_carries_cors_headers(gnomad_fake: FastMCP) -> None:
    """The actual request must carry ACAO too — including on the error/challenge response.

    Without ACAO the response never reaches browser JS, so even the ``WWW-Authenticate``
    challenge that is supposed to start the OAuth flow is invisible to the client.
    """
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.post(
            "/mcp",
            headers={
                "origin": ALLOWED,
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

    assert response.headers.get("access-control-allow-origin") == ALLOWED
    assert "origin" in response.headers.get("vary", "").lower()


def test_mcp_exposes_session_and_challenge_headers(gnomad_fake: FastMCP) -> None:
    """``Mcp-Session-Id`` and ``WWW-Authenticate`` must be script-readable.

    Neither is CORS-safelisted, so without an explicit expose list a browser client can
    neither maintain an MCP session nor discover the auth challenge.
    """
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.options("/mcp", headers=_preflight(ALLOWED))
        actual = client.post(
            "/mcp",
            headers={
                "origin": ALLOWED,
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

    exposed = actual.headers.get("access-control-expose-headers", "").lower()
    assert "mcp-session-id" in exposed
    assert "www-authenticate" in exposed
    assert "x-request-id" in exposed
    # the preflight itself does not need to carry the expose list, but must be answerable
    assert response.status_code in (200, 204)


def test_get_mcp_still_returns_405(gnomad_fake: FastMCP) -> None:
    """Regression fence. The MCP Streamable HTTP transport REQUIRES a server that does not
    offer an SSE stream on GET to answer 405; adding CORS must not turn that into a 200."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.get("/mcp", headers={"origin": ALLOWED})

    assert response.status_code == 405
    assert "POST" in response.headers.get("allow", "")


def test_non_preflight_options_still_405(gnomad_fake: FastMCP) -> None:
    """An ``OPTIONS`` without ``Access-Control-Request-Method`` is not a preflight (Fetch
    Standard) and must keep falling through to the transport's own 405."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.options("/mcp", headers={"origin": ALLOWED})

    assert response.status_code == 405


def test_preflight_from_disallowed_host_is_still_421(gnomad_fake: FastMCP) -> None:
    """The DNS-rebinding guard is not weakened: Host is validated before CORS is reached."""
    with TestClient(_app(gnomad_fake, [ALLOWED], hosts=["genefoundry.test"])) as client:
        response = client.options("/mcp", headers={**_preflight(ALLOWED), "host": "rebind.example"})

    assert response.status_code == 421
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", PRODUCTION_ORIGINS)
def test_allowed_origins_config_is_actually_served(gnomad_fake: FastMCP, origin: str) -> None:
    """The fence that keeps the allowlist honest: every configured origin preflights."""
    with TestClient(_app(gnomad_fake, PRODUCTION_ORIGINS)) as client:
        response = client.options("/mcp", headers=_preflight(origin))

    assert response.status_code in (200, 204)
    assert response.headers["access-control-allow-origin"] == origin


def test_empty_allowlist_grants_nothing(gnomad_fake: FastMCP) -> None:
    """``GF_ALLOWED_ORIGINS=[]`` keeps its documented meaning — reject any present Origin."""
    with TestClient(_app(gnomad_fake, [])) as client:
        response = client.options("/mcp", headers=_preflight(ALLOWED))

    assert "access-control-allow-origin" not in response.headers


def test_cors_does_not_touch_outer_oauth_and_health_routes(gnomad_fake: FastMCP) -> None:
    """Scope fence. The OAuth routes already do their own CORS (MCP SDK ``cors_middleware``,
    ``access-control-allow-origin: *``). A second, app-wide CORS layer would emit a SECOND
    ``Access-Control-Allow-Origin`` on those responses, and a duplicated ACAO is rejected by
    every browser — turning a working endpoint into a broken one. So the router's CORS is
    scoped to the mounted MCP app; ``/health`` (an outer route) is left exactly as it was."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        health = client.get("/health", headers={"origin": ALLOWED})
        preflight = client.options("/health", headers=_preflight(ALLOWED, method="GET"))

    assert health.status_code == 200
    assert "access-control-allow-origin" not in health.headers
    # unchanged: /health has no OPTIONS route and never did
    assert preflight.status_code == 404


def test_cors_is_scoped_to_the_mcp_path(gnomad_fake: FastMCP) -> None:
    """The MCP app is mounted at ``/`` and is therefore also the catch-all for unrouted
    paths. Wrapping the mount wholesale would make ``OPTIONS`` on ANY path answer a
    preflight — a CORS grant for endpoints that do not exist. Only ``/mcp`` is in scope."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        unrouted = client.options("/definitely-not-a-route", headers=_preflight(ALLOWED))

    assert unrouted.status_code != 200
    assert "access-control-allow-origin" not in unrouted.headers


def test_mcp_response_has_exactly_one_allow_origin_header(gnomad_fake: FastMCP) -> None:
    """A duplicated ``Access-Control-Allow-Origin`` is a hard browser error, so assert the
    count, not merely the presence — this is the failure mode an app-wide CORS layer would
    have introduced on the endpoints that already do their own CORS."""
    with TestClient(_app(gnomad_fake, [ALLOWED])) as client:
        response = client.post(
            "/mcp",
            headers={
                "origin": ALLOWED,
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )

    values = response.headers.get_list("access-control-allow-origin")
    assert values == [ALLOWED], values


def _jwt_app(gnomad_fake: FastMCP, origins: list[str]):
    """An auth-enabled router, so the 401 + ``WWW-Authenticate`` challenge is real."""
    settings = RouterSettings(
        _env_file=None,
        GF_ALLOWED_ORIGINS=origins,
        GF_AUTH_MODE="jwt",
        GF_JWT_ISSUER="https://idp.example.org/",
        GF_JWT_JWKS_URL="https://idp.example.org/.well-known/jwks.json",
        GF_JWT_AUDIENCE="https://genefoundry.example.org/mcp",
        GF_PUBLIC_BASE_URL="https://genefoundry.example.org/mcp",
    )
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    return build_app(settings, registry, proxy_targets={"gnomad": gnomad_fake})


def test_unauthenticated_401_challenge_is_readable_by_a_browser(gnomad_fake: FastMCP) -> None:
    """The case the issue is actually about, on a REAL 401.

    Without ``Access-Control-Allow-Origin`` the 401 never reaches browser JS, so the
    ``WWW-Authenticate`` challenge that is supposed to START the OAuth flow is invisible and
    the client cannot even discover that it needs to authenticate. Exposing the header is
    necessary but not sufficient — the response carrying it must itself be readable.
    """
    with TestClient(_jwt_app(gnomad_fake, [ALLOWED]), follow_redirects=False) as client:
        response = client.post(
            "/mcp",
            headers={
                "origin": ALLOWED,
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert response.status_code == 401
    assert "www-authenticate" in {k.lower() for k in response.headers}
    assert response.headers.get("access-control-allow-origin") == ALLOWED
    assert "www-authenticate" in response.headers.get("access-control-expose-headers", "").lower()


def test_preflight_precedes_auth_and_is_not_challenged(gnomad_fake: FastMCP) -> None:
    """A CORS-preflight carries no credentials (Fetch Standard: its credentials mode is
    always "same-origin"), so answering it with a 401 would deadlock the browser: it can
    never attach the token the challenge asks for. The preflight must be answered before
    authentication runs."""
    with TestClient(_jwt_app(gnomad_fake, [ALLOWED]), follow_redirects=False) as client:
        response = client.options("/mcp", headers=_preflight(ALLOWED))

    assert response.status_code in (200, 204)
    assert response.headers["access-control-allow-origin"] == ALLOWED
