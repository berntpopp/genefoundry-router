"""Typer CLI for the GeneFoundry router."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import TYPE_CHECKING, Any

import typer
import uvicorn
from rich.console import Console

if TYPE_CHECKING:
    from genefoundry_router.conformance import Report
    from genefoundry_router.devtools.fakes import Manifest

from genefoundry_router.config import RouterSettings, load_registry
from genefoundry_router.registry import MAX_QUALIFIED_NAME_LEN, BackendDef, expected_server_name
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


def is_missing_public_host_allowlist(host: str, allowed_hosts: list[str]) -> bool:
    """True when a public bind has no explicit DNS-rebinding allowlist."""
    return host not in LOOPBACK_HOSTS and not allowed_hosts


def requires_observability_controls(auth_mode: str, deployment_mode: str) -> bool:
    """Whether authenticated deployment policy requires rate-limit and metrics controls."""
    return auth_mode != "none" and deployment_mode == "production"


def should_warn_no_rate_limit(auth_mode: str, host: str, rate_limit_rpm: int) -> bool:
    """True for an authenticated, publicly-reachable deployment with no rate limit (D10/M7).

    ``GF_RATE_LIMIT_RPM`` defaults to ``0`` (off) as a deliberate operational no-op so
    existing deployments are never throttled by an upgrade. But an authenticated, public
    deployment with no per-client limit lets one caller drive the fleet's egress IPs into
    upstream (gnomAD/NCBI/Ensembl) throttling/bans (OWASP LLM10 — unbounded consumption).
    We surface this as a non-breaking startup warning; we do NOT flip the default. The
    ``auth=none`` public case is already handled by the insecure-bind guard, so it is
    excluded here to avoid a redundant second warning.
    """
    if auth_mode == "none":
        return False
    if host in LOOPBACK_HOSTS:
        return False
    return rate_limit_rpm <= 0


def should_warn_no_metrics_token(auth_mode: str, host: str, metrics_token: str | None) -> bool:
    """True for an authenticated, publicly-reachable bind exposing ``/metrics`` with no token (F-21).

    Mirrors ``should_warn_no_rate_limit``: on a non-override bind this case fails closed
    (``refuses_public_metrics_without_token``), so this warning only surfaces once
    ``GF_ALLOW_INSECURE`` has downgraded that refusal — a PoC operator is still told ``/metrics``
    is public rather than being silently exposed.
    """
    if auth_mode == "none":
        return False
    if host in LOOPBACK_HOSTS:
        return False
    return not metrics_token


def refuses_no_rate_limit(auth_mode: str, rate_limit_rpm: int, deployment_mode: str) -> bool:
    """True when authenticated production lacks a positive per-client rate limit.

    Deployment mode is explicit because a loopback listener may be published by a
    reverse proxy. Neither the bind address nor the unauthenticated-bind escape hatch
    participates in this production decision.
    """
    if not requires_observability_controls(auth_mode, deployment_mode):
        return False
    return rate_limit_rpm <= 0


def refuses_public_metrics_without_token(
    auth_mode: str, metrics_token: str | None, deployment_mode: str
) -> bool:
    """True when authenticated production would expose metrics without a token.

    This uses the explicit deployment contract rather than listener address because
    the reverse proxy, not the local socket, determines external reachability.
    """
    if not requires_observability_controls(auth_mode, deployment_mode):
        return False
    return not metrics_token


def should_warn_development_unsafe_observability(
    auth_mode: str,
    deployment_mode: str,
    development_override: bool,
    rate_limit_rpm: int,
    metrics_token: str | None,
) -> bool:
    """Warn when the named development-only observability override is active."""
    return auth_mode != "none" and deployment_mode == "development" and development_override


def development_unsafe_observability_error(
    auth_mode: str,
    deployment_mode: str,
    acknowledgement: bool,
    host: str,
    rate_limit_rpm: int,
    metrics_token: str | None,
) -> str | None:
    """Validate the narrow local-development escape hatch for observability controls."""
    missing_controls = auth_mode != "none" and (rate_limit_rpm <= 0 or not metrics_token)
    if acknowledgement and deployment_mode != "development":
        return "GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY is valid only in development mode"
    if acknowledgement and host not in LOOPBACK_HOSTS:
        return "GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY is valid only on a loopback bind"
    if missing_controls and deployment_mode == "development" and not acknowledgement:
        return (
            "authenticated development without rate limiting and/or a metrics token requires "
            "GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY=true on loopback"
        )
    return None


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
    if is_missing_public_host_allowlist(host, settings.GF_ALLOWED_HOSTS):
        console.print(
            "[red]Refusing to start: a non-loopback bind requires a nonempty "
            "GF_ALLOWED_HOSTS allowlist.[/red]"
        )
        raise typer.Exit(1)
    unsafe_observability_error = development_unsafe_observability_error(
        settings.GF_AUTH_MODE,
        settings.GF_DEPLOYMENT_MODE,
        settings.GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY,
        host,
        settings.GF_RATE_LIMIT_RPM,
        settings.GF_METRICS_TOKEN,
    )
    if unsafe_observability_error:
        console.print(f"[red]Refusing to start: {unsafe_observability_error}.[/red]")
        raise typer.Exit(1)
    if refuses_no_rate_limit(
        settings.GF_AUTH_MODE,
        settings.GF_RATE_LIMIT_RPM,
        settings.GF_DEPLOYMENT_MODE,
    ):
        console.print(
            "[red]Refusing to start: authenticated production deployment with "
            "GF_RATE_LIMIT_RPM=0 (no per-client rate limit) lets one caller drive the fleet's "
            "egress IPs into upstream throttling/bans. Set GF_RATE_LIMIT_RPM (e.g. 120).[/red]"
        )
        raise typer.Exit(1)
    if refuses_public_metrics_without_token(
        settings.GF_AUTH_MODE,
        settings.GF_METRICS_TOKEN,
        settings.GF_DEPLOYMENT_MODE,
    ):
        console.print(
            "[red]Refusing to start: authenticated production deployment would serve "
            "GET /metrics without GF_METRICS_TOKEN (public operational telemetry). Set "
            "GF_METRICS_TOKEN.[/red]"
        )
        raise typer.Exit(1)
    if should_warn_development_unsafe_observability(
        settings.GF_AUTH_MODE,
        settings.GF_DEPLOYMENT_MODE,
        settings.GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY,
        settings.GF_RATE_LIMIT_RPM,
        settings.GF_METRICS_TOKEN,
    ):
        console.print(
            "[yellow]WARNING: GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY is enabled; "
            "the authenticated development router has no production rate limit and/or "
            "metrics token. This override is development-only.[/yellow]"
        )
    if settings.GF_AUTH_MODE == "none" and host not in LOOPBACK_HOSTS:
        console.print(
            f"[yellow]WARNING: serving with GF_AUTH_MODE=none on {host} (GF_ALLOW_INSECURE "
            "set). Do not use for production or any patient-derived data.[/yellow]"
        )
    if should_warn_no_rate_limit(settings.GF_AUTH_MODE, host, settings.GF_RATE_LIMIT_RPM):
        console.print(
            f"[yellow]WARNING: authenticated public bind ({host}) with GF_RATE_LIMIT_RPM=0 "
            "(no per-client rate limit). One caller can drive the fleet's egress IPs into "
            "upstream throttling/bans. Set GF_RATE_LIMIT_RPM (e.g. 120) in production.[/yellow]"
        )
    if should_warn_no_metrics_token(settings.GF_AUTH_MODE, host, settings.GF_METRICS_TOKEN):
        console.print(
            f"[yellow]WARNING: authenticated public bind ({host}) exposes GET /metrics with no "
            "GF_METRICS_TOKEN — operational telemetry is public. Set GF_METRICS_TOKEN in "
            "production.[/yellow]"
        )
    registry = load_registry(servers_file, os.environ)
    application = build_app(settings, registry)
    uvicorn.run(application, host=host, port=port, log_level=log_level.lower())


def _backend_transport(backend: BackendDef) -> Any:
    """Target for a diagnostic client, carrying the backend's service credential.

    A backend gated by the router's service token (pubtator) answers an anonymous probe
    with 401. Without this, doctor/drift/fleet-probe report it permanently unreachable --
    blinding the drift tripwire on the one write-capable backend -- while the runtime
    proxy talks to it fine, because only composition.make_proxy_client sends the header.
    """
    from fastmcp.client.transports import StreamableHttpTransport

    if backend.url and backend.service_token:
        return StreamableHttpTransport(
            backend.url, headers={"Authorization": f"Bearer {backend.service_token}"}
        )
    return backend.url


async def _probe_backend(backend: BackendDef) -> dict[str, object]:
    """Connect to a backend's /mcp URL and count its tools."""
    from fastmcp import Client

    if backend.url is None:
        return {"name": backend.name, "reachable": False, "tools": 0, "error": "no URL"}
    try:
        async with Client(_backend_transport(backend)) as client:
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


def _classify_fleet(
    results: list[tuple[str, Report | None, str | None]],
) -> tuple[int, list[str]]:
    """Reduce per-backend ``(name, report, transport_error)`` to ``(exit_code, lines)``.

    Exit 1 if any backend is reachable but non-conformant (e.g. a 307-redirecting ``/mcp``)
    — an actionable transport-contract violation; exit 2 if the only problems were transport
    errors (unreachable / timeout); exit 0 if every enabled backend passed. Non-conformance
    outranks a transport error so a real regression is never masked by a transient outage.
    """
    lines: list[str] = []
    any_fail = False
    any_error = False
    for name, rep, err in results:
        if rep is not None and rep.conformant:
            lines.append(f"PASS  {name} ({len(rep.passed)} checks)")
        elif rep is not None:
            any_fail = True
            lines.append(f"FAIL  {name}")
            lines.extend(f"        - {detail}" for detail in rep.failed)
        else:
            any_error = True
            lines.append(f"ERROR {name}: {err or 'no report'}")
    code = 1 if any_fail else (2 if any_error else 0)
    return code, lines


@app.command("fleet-probe")
def fleet_probe(
    servers_file: str = typer.Option(DEFAULT_SERVERS, help="Path to servers.yaml."),
    tier: str = typer.Option("stateless", help="Transport tier to assert (stateless|stateful)."),
) -> None:
    """Run the MCP Transport Standard v1 conformance probe against every enabled backend's
    LIVE ``/mcp`` (URLs from the environment).

    This is the router's prod-liveness gate: it catches the exact class of failure the
    router otherwise hides — a backend that is registered and health-200 yet 307-redirects
    (or otherwise fails the transport contract) and so harvests zero tools. CI conformance
    proves the *code* conformant; this proves the *deployed fleet* conformant. Exit 1 on any
    contract violation, 2 on transport errors only, 0 if all pass.
    """
    import httpx

    from genefoundry_router.conformance import run_probe

    registry = load_registry(servers_file, os.environ)
    results: list[tuple[str, Report | None, str | None]] = []
    for b in registry:
        if not b.enabled:
            continue
        if not b.url:
            results.append((b.name, None, f"no URL configured ({b.url_env})"))
            continue
        base = b.url[: -len("/mcp")] if b.url.endswith("/mcp") else b.url
        try:
            results.append(
                (
                    b.name,
                    run_probe(
                        base,
                        expected_name=expected_server_name(b),
                        tier=tier,
                        service_token=b.service_token,
                    ),
                    None,
                )
            )
        except httpx.HTTPError as exc:  # DNS/TLS/connect/timeout — a transport error, not a verdict
            results.append((b.name, None, str(exc)))
    code, lines = _classify_fleet(results)
    for line in lines:
        style = (
            "green" if line.startswith("PASS") else "red" if line[:5] in ("FAIL ", "ERROR") else ""
        )
        console.print(f"[{style}]{line}[/{style}]" if style else line)
    passed = sum(1 for line in lines if line.startswith("PASS"))
    console.print(
        f"\n[bold]fleet-probe:[/bold] {passed}/{len(results)} enabled backends conformant"
    )
    if code:
        raise typer.Exit(code)


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
                async with Client(_backend_transport(b), timeout=30, init_timeout=10) as client:
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
                    outputSchema=t.outputSchema,
                    annotations=(
                        t.annotations.model_dump(mode="json", exclude_none=False)
                        if t.annotations is not None
                        else None
                    ),
                    execution=(
                        t.execution.model_dump(mode="json", exclude_none=False)
                        if t.execution is not None
                        else None
                    ),
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
    manifest: str | None = typer.Option(None, help="Reviewed, pinned fleet manifest."),
) -> None:
    """Detect tool-definition drift vs the pinned manifest (rug-pull / tool-poisoning tripwire).

    Exits non-zero on any added/removed/changed tool so it can run in CI/cron. Refresh the
    pinned manifest with ``make snapshot-fleet`` only after reviewing the change.
    """
    from importlib.resources import as_file, files
    from pathlib import Path

    from genefoundry_router.devtools.fakes import load_manifest
    from genefoundry_router.drift import diff_manifests

    configured = manifest or RouterSettings().GF_DRIFT_BASELINE
    if configured is not None:
        pinned = load_manifest(Path(configured))
    else:
        resource = files("genefoundry_router.data").joinpath("fleet-baseline.json")
        with as_file(resource) as path:
            pinned = load_manifest(path)
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
