"""Community admission checks (Federation Standard v1 §8), run by community-admission.yml.

    static                        registry and pin lint; never touches the network
    changed --base F --paths F    namespaces a PR adds or changes (fails on non-registry paths)
    probe --author L --namespaces "a b"
                                  ownership proof, GTS core conformance, live == pin

The probe resolves the candidate first and refuses a non-global address before any request;
every later connection passes the router's connect-time IP guard. See
docs/community-admission.md.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import socket
import sys
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from genefoundry_router.community_guard import _REMOVE
from genefoundry_router.config import load_community_registry, load_registry
from genefoundry_router.egress import check_community_url, is_public_ip, resolve_host
from genefoundry_router.exceptions import RegistryError
from genefoundry_router.registry import BackendDef
from genefoundry_router.runtime_drift import load_pin
from genefoundry_router.schema_slim import TOOL_DESCRIPTION_CHARS

COMMUNITY = Path("registry/community.yaml")
TOMBSTONES = Path("registry/tombstones.yaml")
PINS = Path("registry/pins/community")
ALLOWED_PATHS = (str(COMMUNITY), str(TOMBSTONES))
MAX_PROOF_BYTES = 65_536


# --- static ---------------------------------------------------------------------------------


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def lint(label: str, value: Any) -> list[str]:
    """Every string must be NFC and free of the Response Envelope v1.2 removal set."""
    errors = []
    for text in _strings(value):
        if match := _REMOVE.search(text):
            errors.append(f"{label}: forbidden character U+{ord(match.group()):04X} in {text!r}")
        elif unicodedata.normalize("NFC", text) != text:
            errors.append(f"{label}: text is not NFC: {text!r}")
    return errors


def load_entries(community: Path, tombstones: Path) -> list[BackendDef]:
    return load_community_registry(community, [], tombstones)


def static_errors(community: Path, servers: Path, tombstones: Path, pins: Path) -> list[str]:
    """Schema, forbidden keys, free namespace (loader), pin per entry, description lint."""
    try:
        backends = load_community_registry(community, load_registry(servers, {}), tombstones)
    except RegistryError as exc:
        return [str(exc)]
    raw = yaml.safe_load(community.read_text(encoding="utf-8")) or {}
    entries = [*(raw.get("servers") or []), *(raw.get("disabled") or [])]
    errors = [e for entry in raw.get("servers") or [] for e in lint(entry["namespace"], entry)]
    for backend in backends:
        path = pins / f"{backend.namespace}.json"
        if not path.exists():
            errors.append(f"{backend.namespace}: pin file {path} is missing")
            continue
        try:
            load_pin(path, backend.namespace)
            tools = json.loads(path.read_text(encoding="utf-8"))["tools"]
        except RegistryError as exc:
            errors.append(str(exc))
            continue
        for tool in tools:
            description = tool.get("description") or ""
            if len(description) > TOOL_DESCRIPTION_CHARS:
                errors.append(
                    f"{tool['name']}: description is {len(description)} characters "
                    f"(at most {TOOL_DESCRIPTION_CHARS})"
                )
            errors += lint(tool["name"], tool)
    reserved = {str(entry.get("namespace")) for entry in entries}
    errors += [
        f"{path.name}: pin file has no community entry"
        for path in sorted(pins.glob("*.json"))
        if path.stem not in reserved
    ]
    return errors


def _servers(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return {str(e.get("namespace")): e for e in (raw or {}).get("servers") or []}


def changed_namespaces(base: Path, head: Path, paths: list[str]) -> list[str]:
    """Served namespaces whose entry or pin file the PR adds or changes."""
    before, after = _servers(base), _servers(head)
    return sorted(
        ns for ns, entry in after.items() if before.get(ns) != entry or f"{PINS}/{ns}.json" in paths
    )


def foreign_paths(paths: list[str]) -> list[str]:
    """Paths outside the registry: an admission PR changes nothing else."""
    return [p for p in paths if p not in ALLOWED_PATHS and not p.startswith(f"{PINS}/")]


# --- network --------------------------------------------------------------------------------


@contextmanager
def guarded_dns() -> Iterator[None]:
    """Refuse, at connect time, any lookup that is not port 443 or yields a non-global address.

    Covers clients that do not use the router transport (the conformance kit uses httpx).
    """
    real = socket.getaddrinfo

    def guard(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        if str(port) not in ("443", "https"):
            raise OSError(f"admission egress refused {host}: port {port} is not 443")
        infos = real(host, port, *args, **kwargs)
        if bad := [str(info[4][0]) for info in infos if not is_public_ip(str(info[4][0]))]:
            raise OSError(f"admission egress refused {host}: non-global address {bad[0]}")
        return infos

    socket.getaddrinfo = guard
    try:
        yield
    finally:
        socket.getaddrinfo = real


def txt_records(name: str) -> list[str]:
    import dns.resolver  # ponytail: dnspython arrives via email-validator; pin it if that changes

    try:
        answer = dns.resolver.resolve(name, "TXT", lifetime=5)
    except dns.resolver.NXDOMAIN, dns.resolver.NoAnswer:
        return []
    return [b"".join(record.strings).decode() for record in answer]


def well_known(host: str) -> Any:
    from genefoundry_router.egress import community_httpx_client_factory

    async def get() -> bytes:
        async with community_httpx_client_factory()() as client:
            url = f"https://{host}/.well-known/genefoundry.json"
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_PROOF_BYTES:
                        raise ValueError(f"larger than {MAX_PROOF_BYTES} bytes")
        return body

    return json.loads(asyncio.run(get()))


def conformance(backend: BackendDef) -> int:
    from gts_conformance.cli import main

    args = [backend.url or "", "--level", "core", "--name", str(backend.server_name)]
    with guarded_dns():  # the kit uses plain httpx, not the router's guarded transport
        return int(main([*args, "--delay", "1"]))


def live_pin(backend: BackendDef) -> str:
    from genefoundry_router.pin_cli import _upstream_tools
    from genefoundry_router.runtime_drift import pin_json

    return pin_json(backend.namespace, asyncio.run(_upstream_tools(backend)))


def _parse_txt(text: str) -> dict[str, Any]:
    fields = dict(part.strip().partition("=")[::2] for part in text.split(";") if part.strip())
    return {**fields, "probe_consent": fields.get("probe_consent") == "true"}


def _mismatch(proof: Any, namespace: str, author: str) -> str | None:
    if not isinstance(proof, dict):
        return "not a JSON object"
    if proof.get("namespace") != namespace:
        return f"names namespace {proof.get('namespace')!r}"
    if str(proof.get("github_login", "")).lower() != author.lower():
        return f"names GitHub login {proof.get('github_login')!r}"
    if proof.get("probe_consent") is not True:
        return "probe_consent is not true"
    return None


def ownership_error(
    namespace: str,
    host: str,
    author: str,
    well_known: Callable[[str], Any],
    txt_records: Callable[[str], list[str]],
) -> str | None:
    reasons = []
    sources: list[tuple[str, Callable[[], list[Any]]]] = [
        ("DNS TXT", lambda: [_parse_txt(t) for t in txt_records(f"_genefoundry.{host}")]),
        ("well-known", lambda: [well_known(host)]),
    ]
    for source, fetch in sources:
        try:
            proofs = fetch()
        except Exception as exc:  # a candidate's answer is untrusted: any failure is "no proof"
            reasons.append(f"{source}: {type(exc).__name__}: {exc}")
            continue
        for proof in proofs:
            if (reason := _mismatch(proof, namespace, author)) is None:
                return None
            reasons.append(f"{source}: {reason}")
    return (
        f"{namespace}: no ownership proof naming namespace {namespace!r}, GitHub login "
        f"{author!r} and probe consent ({'; '.join(reasons) or 'none found'})"
    )


def _canonical(text: str) -> list[str]:
    return json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False).splitlines()


def probe_errors(
    backend: BackendDef,
    author: str,
    pins: Path,
    *,
    resolve: Callable[[str, int], list[str]] = resolve_host,
    well_known: Callable[[str], Any] = well_known,
    txt_records: Callable[[str], list[str]] = txt_records,
    conformance: Callable[[BackendDef], int] = conformance,
    live_pin: Callable[[BackendDef], str] = live_pin,
) -> list[str]:
    namespace, url = backend.namespace, backend.url or ""
    try:
        check_community_url(url, resolve)  # before any request to the candidate
    except ValueError as exc:
        return [f"{namespace}: {exc}"]
    host = urlsplit(url).hostname or ""
    if error := ownership_error(namespace, host, author, well_known, txt_records):
        return [error]  # no tool is called without the owner's consent
    errors = []
    if conformance(backend) != 0:
        errors.append(f"{namespace}: GTS core conformance probe failed (report above)")
    pin = pins / f"{namespace}.json"
    diff = list(
        difflib.unified_diff(
            _canonical(pin.read_text(encoding="utf-8")),
            _canonical(live_pin(backend)),
            fromfile=str(pin),
            tofile=f"live {url}",
            lineterm="",
        )
    )
    if diff:
        errors.append(f"{namespace}: pin file differs from live definitions:\n" + "\n".join(diff))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("static")
    changed = sub.add_parser("changed")
    changed.add_argument("--base", type=Path, required=True)
    changed.add_argument("--paths", type=Path, required=True)
    probe = sub.add_parser("probe")
    probe.add_argument("--author", required=True)
    probe.add_argument("--namespaces", required=True)
    args = parser.parse_args(argv)

    if args.command == "changed":
        paths = args.paths.read_text(encoding="utf-8").split()
        if foreign := foreign_paths(paths):
            print(
                f"admission PRs change only the registry; also changed: {foreign}", file=sys.stderr
            )
            return 1
        print(" ".join(changed_namespaces(args.base, COMMUNITY, paths)))
        return 0
    if args.command == "static":
        errors = static_errors(COMMUNITY, Path("servers.yaml"), TOMBSTONES, PINS)
    else:
        wanted = set(args.namespaces.split())
        backends = [b for b in load_entries(COMMUNITY, TOMBSTONES) if b.namespace in wanted]
        if missing := wanted - {b.namespace for b in backends}:
            print(f"unknown namespaces: {sorted(missing)}", file=sys.stderr)
            return 1
        errors = [e for b in backends for e in probe_errors(b, args.author, PINS)]
    for error in errors:
        print(f"::error::{error.splitlines()[0]}\n{error}", file=sys.stderr)
    print("admission check passed" if not errors else f"{len(errors)} admission error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
