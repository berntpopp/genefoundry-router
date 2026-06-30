"""Typer CLI for the GeneFoundry router."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import TYPE_CHECKING

import typer
import uvicorn
from rich.console import Console

if TYPE_CHECKING:
    from genefoundry_router.devtools.fakes import Manifest

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.registry import MAX_QUALIFIED_NAME_LEN, BackendDef
from genefoundry_router.server import build_app

app = typer.Typer(help="GeneFoundry Router — federate the -link MCP fleet.", no_args_is_help=True)
console = Console()

DEFAULT_SERVERS = "servers.yaml"

# Hosts only the local machine can reach; auth=none is acceptable on these.
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_insecure_public_bind(auth_mode: str, host: str, allow_insecure: bool) -> bool:
    """True when serving with NO caller auth on a non-loopback bind (R-sec.1).

    An ``auth=none`` endpoint on ``0.0.0.0`` (or any routable host) is an open,
    unauthenticated MCP server. We refuse this by default; ``GF_ALLOW_INSECURE=true``
    is the explicit, logged escape hatch for local PoC use.
    """
    if allow_insecure or auth_mode != "none":
        return False
    return host not in LOOPBACK_HOSTS


LEAF_NAME_RE = re.compile(r"^[a-z0-9_]{1,50}$")
CANONICAL_VERBS = {"get", "search", "list", "resolve", "find", "compare", "compute", "map"}
# Ratified Tier-2 sanctioned domain action/compute verbs (Tool-Naming Standard v1.1).
# These are admitted fleet-wide; used only where a backend actually registers such a tool.
# Source: docs/TOOL-NAMING-STANDARD-v1.md §Tier-2, ratified 2026-06-30.
ACTION_VERB_EXCEPTIONS = {
    "predict",
    "annotate",
    "recode",  # vep-link: domain-legitimate (Ensembl Variant Recoder)
    "liftover",  # vep-link: domain-legitimate (coordinate liftover)
    "analyze",
    "score",  # reserved for compute/scoring backends
    "submit",
    "export",
    "generate",
    "download",
}
# Tags that grant an ops/meta carve-out: tools tagged ops or meta skip the verb rule
# (they still must pass charset/length/no-self-prefix). Covers check_*/health/warmup/
# diagnostics/*_help/*_quickstart and the gtex deep-research fetch/search pair.
_OPS_META_TAGS = frozenset({"ops", "meta"})


def check_leaf_name(leaf: str, tags: list[str] | None = None) -> list[str]:
    """Return Tool-Naming Standard v1.1 violations for a single leaf tool name.

    Args:
        leaf: The unprefixed tool name to validate.
        tags: Optional list of tool tags. Tools tagged ``ops`` or ``meta`` are
            exempt from the verb rule (tag carve-out) but still subject to the
            charset/length constraint.
    """
    issues: list[str] = []
    if not LEAF_NAME_RE.match(leaf):
        issues.append(f"charset/length: {leaf!r} must match ^[a-z0-9_]{{1,50}}$ (≤50)")
    # ops/meta-tagged tools skip the verb rule (v1.1 tag carve-out).
    if tags and _OPS_META_TAGS.intersection(tags):
        return issues
    verb = leaf.split("_", 1)[0]
    if verb not in CANONICAL_VERBS and verb not in ACTION_VERB_EXCEPTIONS:
        issues.append(f"verb: {leaf!r} starts with non-canonical verb {verb!r}")
    return issues


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
    if is_insecure_public_bind(settings.GF_AUTH_MODE, host, settings.GF_ALLOW_INSECURE):
        console.print(
            f"[red]Refusing to start: GF_AUTH_MODE=none on a non-loopback bind ({host}) "
            "exposes an UNAUTHENTICATED MCP endpoint. Set GF_AUTH_MODE=jwt|oauth, bind "
            "127.0.0.1, or set GF_ALLOW_INSECURE=true to override (local/PoC only).[/red]"
        )
        raise typer.Exit(2)
    if settings.GF_AUTH_MODE == "none" and host not in LOOPBACK_HOSTS:
        console.print(
            f"[yellow]WARNING: serving with GF_AUTH_MODE=none on {host} (GF_ALLOW_INSECURE "
            "set). Do not use for production or any patient-derived data.[/yellow]"
        )
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
        return {
            "name": backend.name,
            "reachable": True,
            "tools": len(tools),
            # Mirror _snapshot_live: capture per-tool tags so the doctor --strict-naming
            # loop can pass them to check_leaf_name (ops/meta carve-out).
            "leaf_tools": [
                {
                    "name": t.name,
                    "tags": list((t.meta or {}).get("fastmcp", {}).get("tags", [])),
                }
                for t in tools
            ],
            "error": None,
        }
    except Exception as exc:  # report any connection failure (broad by design)
        return {"name": backend.name, "reachable": False, "tools": 0, "error": str(exc)}


@app.command()
def doctor(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
    strict_naming: bool = typer.Option(
        False,
        "--strict-naming",
        help="Audit each backend's leaf tool names against Tool-Naming Standard v1.",
    ),
) -> None:
    """Ping each enabled backend and report reachability + tool counts.

    With ``--strict-naming``, also audit each reachable backend's leaf tool names
    against Tool-Naming Standard v1 (unprefixed verb_noun, ≤50 chars, canonical verb)
    and exit non-zero on any violation (R1.9 — the router enforcing the fleet standard).
    """
    registry = load_registry(servers_file, os.environ)
    enabled = [b for b in registry if b.enabled]
    results = asyncio.run(_gather_probes(enabled))
    unreachable = 0
    violations_found = False
    for r in results:
        if r["reachable"]:
            console.print(f"[green]OK[/green]   {r['name']}: {r['tools']} tools")
            leaf_tools = r.get("leaf_tools", [])
            if strict_naming and isinstance(leaf_tools, list):
                for leaf_tool in leaf_tools:
                    leaf = leaf_tool["name"]
                    tags: list[str] = leaf_tool.get("tags") or []
                    for issue in check_leaf_name(leaf, tags=tags):
                        violations_found = True
                        console.print(f"  [yellow]NAME[/yellow] {r['name']}/{leaf}: {issue}")
        else:
            unreachable += 1
            console.print(f"[red]FAIL[/red] {r['name']}: unreachable ({r['error']})")
    if unreachable or violations_found:
        raise typer.Exit(1)


async def _gather_probes(backends: list[BackendDef]) -> list[dict[str, object]]:
    return [await _probe_backend(b) for b in backends]


async def _list_federated_tools(settings: RouterSettings, registry: list[BackendDef]) -> list[str]:
    """Build the gateway (search disabled) and return all namespaced tool names."""
    from fastmcp import Client

    from genefoundry_router.server import build_server

    server = build_server(settings, registry, enable_search=False)
    async with Client(server) as client:
        return [t.name for t in await client.list_tools()]


@app.command("list-tools")
def list_tools(
    namespace: str = typer.Option(None, help="Filter to a single namespace."),
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Enumerate federated tools (post-namespace, post-transform); flag >64-char names."""
    settings = RouterSettings(GF_SERVERS_FILE=servers_file)
    registry = load_registry(servers_file, os.environ)
    names = asyncio.run(_list_federated_tools(settings, registry))
    if namespace:
        names = [n for n in names if n.startswith(f"{namespace}_")]
    for name in sorted(names):
        flag = "  [red]OVER 64[/red]" if len(name) > MAX_QUALIFIED_NAME_LEN else ""
        console.print(f"{name}{flag}")
    console.print(f"\n[bold]{len(names)} tools[/bold]")


@app.command()
def validate(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
) -> None:
    """Validate servers.yaml + env; report missing URLs and invalid namespaces."""
    registry = load_registry(servers_file, os.environ)
    problems: list[str] = []
    for b in registry:
        if b.enabled and b.url is None:
            problems.append(f"{b.name}: missing URL (set {b.url_env})")
    if problems:
        for p in problems:
            console.print(f"[red]FAIL[/red] {p}")
        raise typer.Exit(1)
    console.print(
        f"[green]OK[/green] {len(registry)} backends valid "
        f"({sum(b.enabled for b in registry)} enabled)"
    )


async def _snapshot_live(
    registry: list[BackendDef], attempts: int = 2
) -> tuple[Manifest, set[str]]:
    """Snapshot reachable backends' tools; return (live_manifest, unreachable_namespaces).

    A backend is *unreachable* if it is enabled but has no URL, or if listing its tools
    fails after ``attempts`` tries. Unreachable backends are excluded from the manifest and
    reported separately, so an outage (or a missing ``GF_*_URL``) is never mistaken for a
    removed tool. Per-backend timeouts keep one hung backend from stalling the whole run.
    """
    from fastmcp import Client

    from genefoundry_router.devtools.fakes import (
        BackendSpec,
        Manifest,
        SnapshotMeta,
        ToolSpec,
    )

    backends: dict[str, BackendSpec] = {}
    unreachable: set[str] = set()
    for b in registry:
        if not b.enabled:
            continue
        if not b.url:  # enabled but unconfigured: unreachable, NOT a removed tool
            unreachable.add(b.namespace)
            console.print(f"[yellow]WARN[/yellow] {b.name}: no URL configured ({b.url_env})")
            continue
        tools = None
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                # Bounded so one hung backend can't exceed the CI job timeout.
                async with Client(b.url, timeout=30, init_timeout=10) as client:
                    tools = await client.list_tools()
                break
            except Exception as exc:  # transient: retry, then mark unreachable
                last_exc = exc
        if tools is None:
            unreachable.add(b.namespace)
            console.print(f"[yellow]WARN[/yellow] {b.name} unreachable: {last_exc}")
            continue
        backends[b.namespace] = BackendSpec(
            version=None,
            tools=[
                ToolSpec(
                    name=t.name,
                    description=t.description or "",
                    inputSchema=t.inputSchema or {"type": "object", "properties": {}},
                    tags=list((t.meta or {}).get("fastmcp", {}).get("tags", [])),
                )
                for t in tools
            ],
        )
    manifest = Manifest(
        snapshot_meta=SnapshotMeta(captured_at="live", source="live", router_servers_file=""),
        backends=backends,
    )
    return manifest, unreachable


@app.command()
def drift(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
    manifest: str = typer.Option(
        "tests/fixtures/fleet_manifest.json", help="Reviewed, pinned fleet manifest."
    ),
) -> None:
    """Detect tool-definition drift vs the pinned manifest (rug-pull / tool-poisoning tripwire).

    Exits non-zero on any added/removed/changed tool so it can run in CI/cron. Refresh the
    pinned manifest with ``make snapshot-fleet`` only after reviewing the change.
    """
    from pathlib import Path

    from genefoundry_router.devtools.fakes import load_manifest
    from genefoundry_router.drift import diff_manifests

    pinned = load_manifest(Path(manifest))
    live, unreachable = asyncio.run(_snapshot_live(load_registry(servers_file, os.environ)))
    # Exclude unreachable backends from BOTH sides so an outage isn't read as "removed".
    pinned_reachable = pinned.model_copy(
        update={"backends": {ns: s for ns, s in pinned.backends.items() if ns not in unreachable}}
    )
    report = diff_manifests(pinned_reachable, live)
    for k in report.changed:
        console.print(f"[red]CHANGED[/red] {k}")
    for k in report.added:
        console.print(f"[yellow]ADDED[/yellow] {k}")
    for k in report.removed:
        console.print(f"[yellow]REMOVED[/yellow] {k}")
    if unreachable:
        console.print(f"[yellow]UNREACHABLE[/yellow]: {', '.join(sorted(unreachable))}")
    if report.has_drift:
        console.print("[red]tool-definition drift detected[/red] — review before refreshing pin")
        raise typer.Exit(1)
    if unreachable:
        console.print("[yellow]no drift, but some backends were unreachable[/yellow]")
        raise typer.Exit(2)
    console.print("[green]OK[/green] no tool-definition drift")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":
    sys.exit(app())
