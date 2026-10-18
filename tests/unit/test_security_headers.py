from __future__ import annotations

from fastapi.testclient import TestClient

from genefoundry_router.config import RouterSettings
from genefoundry_router.server import build_app

ALLOWED_ORIGIN = "https://ui.genefoundry.example"
DISALLOWED_ORIGIN = "https://evil.example"


def _create_app():
    settings = RouterSettings(
        _env_file=None,
        GF_AUTH_MODE="none",
        GF_ALLOWED_ORIGINS=[ALLOWED_ORIGIN],
    )
    return build_app(settings, [])


def test_mcp_discovery_sse_accept_returns_405_with_cors() -> None:
    app = _create_app()
    with TestClient(app) as client:
        response = client.get(
            "/mcp",
            headers={"accept": "text/event-stream", "origin": ALLOWED_ORIGIN},
        )

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert "origin" in response.headers.get("vary", "").lower()


def test_mcp_discovery_sse_accept_returns_405_without_cors_for_absent_origin() -> None:
    app = _create_app()
    with TestClient(app) as client:
        response = client.get(
            "/mcp",
            headers={"accept": "text/event-stream"},
        )

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert "access-control-allow-origin" not in response.headers


def test_mcp_discovery_sse_accept_returns_403_for_disallowed_origin() -> None:
    app = _create_app()
    with TestClient(app) as client:
        response = client.get(
            "/mcp",
            headers={"accept": "text/event-stream", "origin": DISALLOWED_ORIGIN},
        )

    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_mcp_discovery_json_accept_returns_200_with_cors() -> None:
    app = _create_app()
    with TestClient(app) as client:
        response = client.get(
            "/mcp",
            headers={"accept": "application/json", "origin": ALLOWED_ORIGIN},
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
    assert "origin" in response.headers.get("vary", "").lower()
