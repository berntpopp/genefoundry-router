from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.security import add_origin_validation


def _app(allowed: list[str]) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    add_origin_validation(app, allowed_origins=allowed)
    return TestClient(app)


def test_absent_origin_passes():
    # non-browser MCP clients send no Origin -> must not be blocked
    assert _app([]).get("/health").status_code == 200


def test_present_allowed_origin_passes():
    client = _app(["https://claude.ai"])
    assert client.get("/health", headers={"origin": "https://claude.ai"}).status_code == 200


def test_present_disallowed_origin_403():
    client = _app(["https://claude.ai"])
    assert client.get("/health", headers={"origin": "https://evil.example"}).status_code == 403
