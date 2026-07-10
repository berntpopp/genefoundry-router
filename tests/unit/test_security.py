from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.security import add_host_origin_validation


def _client(hosts: list[str], origins: list[str]) -> TestClient:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    add_host_origin_validation(app, allowed_hosts=hosts, allowed_origins=origins)
    return TestClient(app)


def test_allowed_host_with_port_passes() -> None:
    response = _client(["genefoundry.org"], []).get(
        "/health", headers={"host": "GeneFoundry.org:443"}
    )
    assert response.status_code == 200


def test_disallowed_host_returns_421() -> None:
    response = _client(["genefoundry.org"], []).get("/health", headers={"host": "rebind.example"})
    assert response.status_code == 421
    assert response.json() == {"error": "misdirected request"}


def test_ipv6_loopback_host_passes() -> None:
    response = _client(["::1"], []).get("/health", headers={"host": "[::1]:8000"})
    assert response.status_code == 200


def test_absent_origin_passes_for_non_browser_client() -> None:
    assert _client(["testserver"], []).get("/health").status_code == 200


def test_present_allowed_origin_passes() -> None:
    response = _client(["testserver"], ["https://claude.ai"]).get(
        "/health", headers={"origin": "https://claude.ai"}
    )
    assert response.status_code == 200


def test_present_disallowed_origin_returns_403() -> None:
    response = _client(["testserver"], ["https://claude.ai"]).get(
        "/health", headers={"origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json() == {"error": "forbidden origin"}
