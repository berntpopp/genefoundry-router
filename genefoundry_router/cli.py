"""Typer CLI for the GeneFoundry router."""

from __future__ import annotations

import asyncio
import os
import sys

import typer
import uvicorn
from rich.console import Console

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_app

app = typer.Typer(help="GeneFoundry Router — federate the -link MCP fleet.", no_args_is_help=True)
console = Console()

DEFAULT_SERVERS = "servers.yaml"


@app.command()
def run(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    transport: str = typer.Option("http", help="Transport (only 'http' supported)."),
    log_level: str = typer.Option("INFO", help="Log level."),
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Start the router over Streamable HTTP."""
    if transport != "http":
        console.print(f"[red]Unsupported transport {transport!r}; only 'http' is offered.[/red]")
        raise typer.Exit(2)
    settings = RouterSettings(GF_LOG_LEVEL=log_level, GF_SERVERS_FILE=servers_file)
    registry = load_registry(servers_file, os.environ)
    application = build_app(settings, registry)
    uvicorn.run(application, host=host, port=port, log_level=log_level.lower())


async def _probe_backend(backend: BackendDef) -> dict[str, object]:
    """Connect to a backend's /mcp URL and count its tools."""
    from fastmcp import Client

    if backend.url is None:
        return {"name": backend.name, "reachable": False, "tools": 0, "error": "no URL"}
    try:
        async with Client(backend.url) as client:
            tools = await client.list_tools()
        return {"name": backend.name, "reachable": True, "tools": len(tools), "error": None}
    except Exception as exc:  # report any connection failure (broad by design)
        return {"name": backend.name, "reachable": False, "tools": 0, "error": str(exc)}


@app.command()
def doctor(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Ping each enabled backend and report reachability + tool counts."""
    registry = load_registry(servers_file, os.environ)
    enabled = [b for b in registry if b.enabled]
    results = asyncio.run(_gather_probes(enabled))
    unreachable = 0
    for r in results:
        if r["reachable"]:
            console.print(f"[green]OK[/green]   {r['name']}: {r['tools']} tools")
        else:
            unreachable += 1
            console.print(f"[red]FAIL[/red] {r['name']}: unreachable ({r['error']})")
    if unreachable:
        raise typer.Exit(1)


async def _gather_probes(backends: list[BackendDef]) -> list[dict[str, object]]:
    return [await _probe_backend(b) for b in backends]


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    sys.exit(app())
