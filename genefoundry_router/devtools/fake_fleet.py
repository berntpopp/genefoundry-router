"""Offline multi-backend fake fleet: one Starlette app, path-routed MCP mounts."""

from __future__ import annotations

import argparse
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fakes import Manifest, load_manifest, make_backend_from_spec
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


def dev_config_warnings(
    manifest: Manifest,
    host: str,
    port: int,
    servers_file: str = "servers.dev.yaml",
    env_file: str = ".env.dev",
) -> list[str]:
    """Best-effort drift check of the committed dev config vs the fleet URLs.

    Returns warning strings (empty if the dev config files are absent or all match).
    """
    from pathlib import Path

    if not Path(servers_file).exists() or not Path(env_file).exists():
        return []
    env: dict[str, str] = {}
    for line in Path(env_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    registry = [
        b
        for b in load_registry(servers_file, env)
        if b.enabled and b.namespace in manifest.backends
    ]
    return check_dev_config(registry, manifest, host, port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline fake MCP fleet.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--manifest", default="tests/fixtures/fleet_manifest.json")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = load_manifest(args.manifest)
    for ns, url in url_map(manifest, args.host, args.port).items():
        print(f"  {ns:<10} -> {url}")
    for problem in dev_config_warnings(manifest, args.host, args.port):
        print(f"  WARN dev-config: {problem}")
    app = build_fleet_app(manifest)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
