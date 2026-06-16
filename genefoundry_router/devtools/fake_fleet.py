"""Offline multi-backend fake fleet: one Starlette app, path-routed MCP mounts."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.routing import Mount

from genefoundry_router.devtools.fakes import Manifest, make_backend_from_spec
from genefoundry_router.registry import BackendDef


def url_map(manifest: Manifest, host: str, port: int) -> dict[str, str]:
    """Map each namespace to its localhost MCP URL."""
    return {ns: f"http://{host}:{port}/{ns}/mcp" for ns in manifest.backends}


def build_fleet_app(manifest: Manifest) -> Starlette:
    """Mount one fake FastMCP per backend at /<ns>/mcp on a single Starlette app.

    Each child ``http_app`` has its own lifespan; FastMCP requires it to be entered
    or the session manager never initializes. The outer lifespan enters every child
    via an AsyncExitStack.
    """
    children = {
        ns: make_backend_from_spec(ns, spec).http_app(path="/mcp")
        for ns, spec in manifest.backends.items()
    }

    @asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        async with AsyncExitStack() as stack:
            for child in children.values():
                await stack.enter_async_context(child.lifespan(app))
            yield

    routes = [Mount(f"/{ns}", app=child) for ns, child in children.items()]
    return Starlette(routes=routes, lifespan=lifespan)


def check_dev_config(
    registry: list[BackendDef],
    manifest: Manifest,
    host: str,
    port: int,
) -> list[str]:
    """Return human-readable mismatches between the registry URLs and the fleet URLs."""
    expected = url_map(manifest, host, port)
    problems: list[str] = []
    for backend in registry:
        want = expected.get(backend.namespace)
        if want is None:
            problems.append(f"{backend.namespace}: not served by the fleet manifest")
        elif backend.url != want:
            problems.append(f"{backend.namespace}: url {backend.url!r} != expected {want!r}")
    return problems
