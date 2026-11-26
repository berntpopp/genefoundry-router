"""`pin` and `release`: community pins and latched quarantine (Federation Standard v1 §4)."""

from __future__ import annotations

import asyncio
from typing import Any

import typer
from rich.console import Console

from genefoundry_router.registry import BackendDef

console = Console()


async def _upstream_tools(backend: BackendDef) -> list[Any]:
    """One community backend's raw ``tools/list`` over the SSRF-safe community client."""
    from genefoundry_router.composition import COMMUNITY_HARVEST_TIMEOUT, make_proxy_client
    from genefoundry_router.egress import community_httpx_client_factory
    from genefoundry_router.runtime_drift import harvest_upstream

    url = backend.url or ""
    factory = community_httpx_client_factory()
    return await harvest_upstream(
        lambda: make_proxy_client(url, COMMUNITY_HARVEST_TIMEOUT, httpx_client_factory=factory)
    )


def pin_command(
    namespace: str = typer.Argument(..., help="Community backend namespace to pin."),
    output: str = typer.Option(..., "--output", help="Pin file to write (atomic replace)."),
    community_file: str = typer.Option("registry/community.yaml", help="Community registry."),
    tombstones: str = typer.Option("registry/tombstones.yaml", help="Tombstones file."),
) -> None:
    """Write one community backend's full upstream definitions as its reviewed pin (§4)."""
    from genefoundry_router.config import load_community_registry
    from genefoundry_router.runtime_drift import pin_json, write_atomic

    backends = load_community_registry(community_file, [], tombstones)
    backend = next((b for b in backends if b.namespace == namespace), None)
    if backend is None:
        console.print(f"[red]no community backend {namespace!r} in {community_file}[/red]")
        raise typer.Exit(2)
    tools = asyncio.run(_upstream_tools(backend))
    if not tools:
        console.print(f"[red]{namespace}: no tools harvested; nothing pinned[/red]")
        raise typer.Exit(1)
    write_atomic(output, pin_json(namespace, tools))
    console.print(f"[green]pinned[/green] {len(tools)} tools -> {output}")


def release_command(
    namespace: str = typer.Argument(..., help="Latched community backend to release."),
    state_file: str | None = typer.Option(
        None, envvar="GF_QUARANTINE_FILE", help="Latch state file of the running instance."
    ),
) -> None:
    """Maintainer release of a latched community quarantine (picked up within 5 s)."""
    from genefoundry_router.runtime_drift import release_backend

    if not state_file:
        console.print("[red]GF_QUARANTINE_FILE is not set[/red]")
        raise typer.Exit(2)
    if not release_backend(state_file, namespace):
        console.print(f"[yellow]{namespace} is not quarantined[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]released[/green] {namespace}")


def register_pin_commands(app: typer.Typer) -> None:
    app.command("pin")(pin_command)
    app.command("release")(release_command)
