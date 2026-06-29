"""MCP Transport & Session Standard v1 — conformance probe.

Self-contained (httpx only). Vendored into every -link repo's tests/conformance/
and used by the router. Run against a live server:

    python -m genefoundry_router.conformance http://127.0.0.1:8005 --name gtex-link --tier stateless

Exit code: 0 conformant, 1 non-conformant, 2 transport/probe error.

Note: the probe omits the post-initialize ``notifications/initialized`` notification and
issues ``tools/list`` directly; this works against FastMCP and is documented for transparency.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

SUPPORTED_PROTOCOL = "2025-06-18"
UNSUPPORTED_PROTOCOL = "1999-01-01"

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": SUPPORTED_PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "mcp-conformance-probe", "version": "1.0.0"},
    },
}
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@dataclass
class Report:
    base_url: str
    name: str
    tier: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        self.passed.append(label) if ok else self.failed.append(f"{label} — {detail}")
        return ok

    @property
    def conformant(self) -> bool:
        return not self.failed


def _jsonrpc(resp: httpx.Response) -> dict[str, Any]:
    """Return the JSON-RPC payload, tolerating an SSE-framed body."""
    if "text/event-stream" in resp.headers.get("content-type", ""):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                parsed: dict[str, Any] = json.loads(line[5:].strip())
                return parsed
        return {}
    try:
        result: dict[str, Any] = resp.json()
        return result
    except json.JSONDecodeError:
        return {}


def run_probe(
    base_url: str, *, expected_name: str, tier: str, require_auth: bool = False
) -> Report:
    base = base_url.rstrip("/")
    rep = Report(base, expected_name, tier)
    is_router = expected_name == "genefoundry"
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        init = client.post(f"{base}/mcp", json=_INIT, headers=_HEADERS)

        if require_auth:
            rep.check(
                "auth: unauthenticated MCP call → 401",
                init.status_code == 401,
                f"got {init.status_code}",
            )
            if init.status_code == 401:
                rep.check(
                    "auth: 401 carries WWW-Authenticate",
                    "www-authenticate" in {k.lower() for k in init.headers},
                    "missing WWW-Authenticate header",
                )
            return rep

        rep.check(
            "POST /mcp does not 307",
            init.status_code != 307,
            f"got {init.status_code} Location={init.headers.get('location')!r}",
        )
        rep.check("POST /mcp → 200", init.status_code == 200, f"got {init.status_code}")
        rep.check(
            "init Content-Type is application/json",
            init.headers.get("content-type", "").startswith("application/json"),
            init.headers.get("content-type", ""),
        )
        if tier == "stateless":
            rep.check(
                "stateless: no Mcp-Session-Id header",
                "mcp-session-id" not in {k.lower() for k in init.headers},
                "session id assigned",
            )

        result = _jsonrpc(init).get("result", {})
        name = result.get("serverInfo", {}).get("name")
        rep.check(f"serverInfo.name == {expected_name!r}", name == expected_name, f"got {name!r}")

        tl = client.post(f"{base}/mcp", json=_TOOLS_LIST, headers=_HEADERS)
        tools = _jsonrpc(tl).get("result", {}).get("tools", [])
        rep.check("tools/list returns ≥ 1 tool", len(tools) >= 1, f"{len(tools)} tools")

        bad = client.post(
            f"{base}/mcp",
            json=_TOOLS_LIST,
            headers={**_HEADERS, "MCP-Protocol-Version": UNSUPPORTED_PROTOCOL},
        )
        rep.check(
            "unsupported MCP-Protocol-Version → 400 (post-init)",
            bad.status_code == 400,
            f"got {bad.status_code}",
        )

        get = client.get(f"{base}/mcp", headers={"Accept": "text/event-stream"})
        rep.check("GET /mcp does not 307", get.status_code != 307, f"got {get.status_code}")

        health = client.get(f"{base}/health")
        rep.check("GET /health → 200", health.status_code == 200, f"got {health.status_code}")
        body = _jsonrpc(health) if health.status_code == 200 else {}
        rep.check("/health has 'status'", "status" in body, str(body)[:120])
        if not is_router:
            for key in ("version", "transport"):
                rep.check(f"/health has {key!r}", key in body, "missing (backend MUST include it)")
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCP Transport Standard v1 conformance probe")
    parser.add_argument("base_url")
    parser.add_argument("--name", required=True, help="expected serverInfo.name")
    parser.add_argument("--tier", choices=["stateless", "stateful"], default="stateless")
    parser.add_argument("--require-auth", action="store_true")
    args = parser.parse_args(argv)
    try:
        rep = run_probe(
            args.base_url,
            expected_name=args.name,
            tier=args.tier,
            require_auth=args.require_auth,
        )
    except httpx.HTTPError as exc:
        print(f"TRANSPORT ERROR: {exc}", file=sys.stderr)
        return 2
    for line in rep.passed:
        print(f"  PASS  {line}")
    for line in rep.failed:
        print(f"  FAIL  {line}")
    verdict = "CONFORMANT" if rep.conformant else "NON-CONFORMANT"
    print(
        f"\n{verdict}: {rep.name} @ {rep.base_url} ({len(rep.passed)} pass, {len(rep.failed)} fail)"
    )
    return 0 if rep.conformant else 1


if __name__ == "__main__":
    raise SystemExit(main())
