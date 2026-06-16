"""E2E fixtures: serve ASGI apps over real HTTP in a background thread."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from genefoundry_router.devtools.fake_fleet import build_fleet_app
from genefoundry_router.devtools.fakes import Manifest, load_manifest
from genefoundry_router.registry import BackendDef

FIXTURE = "tests/fixtures/fleet_manifest.json"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve(app: object) -> tuple[uvicorn.Server, str]:
    """Serve an ASGI app on a free port in a daemon thread; return (server, base_url).

    Reusable by every e2e test (Task 8 serves the fleet; Task 9 serves the router).
    ``server.started`` is set by uvicorn only after the ASGI lifespan startup has run,
    so the composed fleet lifespan (which initializes every child MCP session manager)
    is guaranteed live before we return.
    """
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start in time")
        time.sleep(0.02)
    return server, f"http://127.0.0.1:{port}"


def dev_registry(manifest: Manifest, base_url: str) -> list[BackendDef]:
    """Build a registry whose backends point at the served fleet.

    Reusable by every e2e test. Each backend's ``url`` is the served fleet's
    ``/<ns>/mcp`` mount, so ``build_server``/``build_app`` proxy over real HTTP.
    """
    return [
        BackendDef(
            name=ns,
            namespace=ns,
            url_env=f"GF_{ns.upper()}_URL",
            tags=[],
            url=f"{base_url}/{ns}/mcp",
        )
        for ns in manifest.backends
    ]


@pytest.fixture(scope="session")
def fleet() -> Iterator[tuple[Manifest, str]]:
    """Serve the committed fake fleet over real HTTP for the whole e2e session."""
    manifest = load_manifest(FIXTURE)
    server, base_url = serve(build_fleet_app(manifest))
    try:
        yield manifest, base_url
    finally:
        server.should_exit = True
        time.sleep(0.2)
