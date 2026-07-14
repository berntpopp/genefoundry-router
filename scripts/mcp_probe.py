"""
Fleet MCP probe
===============
A dependency-free MCP client for exercising the fleet the way a real client does:
over public HTTPS, through NPM, against the deployed image.

    python scripts/mcp_probe.py tools gtex-link
    python scripts/mcp_probe.py call gtex-link get_gene_information '{"gene_ids": ["BRCA1"]}'
    python scripts/mcp_probe.py servers

Why not reuse the router's client: the router federates the fleet from *inside* the
Docker network. That path cannot see NPM, TLS, the Host-header allowlist, or the public
auth gates -- i.e. exactly the layers a user's client hits first. This probe speaks
Streamable HTTP to the same URL a user would configure.

Auth: a server that needs a bearer token reads it from MCP_PROBE_TOKEN_<NAME> (dashes to
underscores, uppercased), e.g. MCP_PROBE_TOKEN_PUBTATOR_LINK. Tokens are never printed.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "genefoundry-fleet-probe", "version": "1.0.0"}
DEFAULT_TIMEOUT = 90.0


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


ROUTER_SERVICE = "genefoundry-router"
ROUTER_URL = "https://genefoundry.org/mcp"


def load_servers() -> dict[str, str]:
    """Map every fleet service to the public MCP URL a real client would configure.

    Derived from this repo's own ``servers.yaml`` — the committed backend registry — so the
    probe needs no lockfile from the deployment repo. Each entry's ``repo`` field carries the
    canonical service slug (``berntpopp/gtex-link`` → ``gtex-link``), which is also its public
    hostname. The router itself is served from the apex.
    """
    registry = yaml.safe_load((_root() / "servers.yaml").read_text())
    servers: dict[str, str] = {ROUTER_SERVICE: ROUTER_URL}
    for entry in registry.get("servers", []):
        slug = str(entry["repo"]).split("/")[-1]
        servers[slug] = f"https://{slug}.genefoundry.org/mcp"
    return servers


# A URL, not a credential -- ruff's S105 matches on the substring "token" in the name.
KEYCLOAK_TOKEN_URL = "https://auth.genefoundry.org/realms/genefoundry/protocol/openid-connect/token"  # noqa: S105
_router_token: str | None = None


def _mint_router_token(secret: str) -> str:
    """Mint an audience-bound access token for the router via the `router-test` client.

    The router is a protected resource: it rejects any token not minted for the audience
    `https://genefoundry.org/mcp`. The realm ships a service-account client whose sole
    purpose is automated verification, and whose audience mapper stamps exactly that
    claim -- so this exercises the real public auth path rather than bypassing it.
    """
    global _router_token
    if _router_token:
        return _router_token

    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": "router-test",
            "client_secret": secret,
        }
    ).encode()
    request = urllib.request.Request(  # noqa: S310 - constant https:// URL above
        KEYCLOAK_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            _router_token = json.loads(response.read())["access_token"]
    except urllib.error.HTTPError as exc:
        raise MCPError(f"could not mint router token: HTTP {exc.code}") from exc
    return _router_token


def _token_for(name: str) -> str | None:
    """Bearer token for a gated server.

    Tokens live OUTSIDE the repo (~/.config/genefoundry/probe-tokens.env by default) so a
    probe run can never stage a secret into a commit, and so callers -- including
    subagents -- never have to handle the value themselves.
    """
    var = "MCP_PROBE_TOKEN_" + name.replace("-", "_").upper()
    if var in os.environ:
        return os.environ[var]

    tokens = Path(
        os.environ.get(
            "MCP_PROBE_TOKENS",
            str(Path.home() / ".config" / "genefoundry" / "probe-tokens.env"),
        )
    )
    entries: dict[str, str] = {}
    if tokens.is_file():
        for raw in tokens.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                entries[key] = value.strip()

    if var in entries:
        return entries[var]

    # The router takes no static token -- it takes an audience-bound OAuth one.
    if name == "genefoundry-router" and "GF_ROUTER_TEST_SECRET" in entries:
        return _mint_router_token(entries["GF_ROUTER_TEST_SECRET"])
    return None


class MCPError(RuntimeError):
    """A transport- or protocol-level failure (not a tool returning an error result)."""


class MCPSession:
    """One Streamable-HTTP MCP session: initialize, then call."""

    def __init__(self, name: str, url: str, timeout: float = DEFAULT_TIMEOUT):
        self.name = name
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self._next_id = 0

    def _post(self, payload: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            # Streamable HTTP servers may answer either way; a client must accept both.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        token = _token_for(self.name)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        request = urllib.request.Request(  # noqa: S310 - https:// URL from fleet.lock.yaml
            self.url, data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode(errors="replace")
        except Exception as exc:
            raise MCPError(f"{type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _parse(status: int, body: str) -> dict[str, Any]:
        """Decode a JSON-RPC response from either a JSON or an SSE body."""
        text = body.strip()
        if not text:
            raise MCPError(f"empty body (HTTP {status})")
        if text.startswith("{"):
            return json.loads(text)
        # SSE: one or more `event:`/`data:` frames. The JSON-RPC reply is the last data.
        payloads = [
            line[len("data:") :].strip() for line in text.splitlines() if line.startswith("data:")
        ]
        if not payloads:
            raise MCPError(f"HTTP {status}: no JSON-RPC payload in body: {text[:400]}")
        return json.loads(payloads[-1])

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        status, headers, body = self._post(payload)
        if status >= 400:
            raise MCPError(f"HTTP {status} on {method}: {body[:400]}")
        if not self.session_id:
            for key, value in headers.items():
                if key.lower() == "mcp-session-id":
                    self.session_id = value
        message = self._parse(status, body)
        if "error" in message:
            error = message["error"]
            raise MCPError(f"JSON-RPC error on {method}: {json.dumps(error)[:600]}")
        return message.get("result", {})

    def _notify(self, method: str) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        self._post(payload)

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.server_info = result.get("serverInfo", {})
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._rpc("tools/call", {"name": tool, "arguments": arguments})


def _open(name: str, timeout: float) -> MCPSession:
    servers = load_servers()
    if name not in servers:
        raise SystemExit(f"unknown server {name!r}; known: {', '.join(sorted(servers))}")
    session = MCPSession(name, servers[name], timeout)
    session.initialize()
    return session


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a fleet MCP server over public HTTPS")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--full", action="store_true", help="do not truncate tool output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("servers", help="list every fleet server and its public MCP URL")

    p_tools = sub.add_parser("tools", help="list a server's tools (name, description, schema)")
    p_tools.add_argument("server")
    p_tools.add_argument("--names-only", action="store_true")

    p_call = sub.add_parser("call", help="call one tool and print the raw MCP result")
    p_call.add_argument("server")
    p_call.add_argument("tool")
    p_call.add_argument("arguments", nargs="?", default="{}", help="JSON object")
    p_call.add_argument("--full", action="store_true", help="do not truncate tool output")

    args = parser.parse_args()

    if args.command == "servers":
        for name, url in sorted(load_servers().items()):
            print(f"{name}\t{url}")
        return 0

    if args.command == "tools":
        try:
            session = _open(args.server, args.timeout)
            tools = session.list_tools()
        except MCPError as exc:
            # An unreachable or gated server is a result, not a crash.
            print(json.dumps({"ok": False, "server": args.server, "failure": str(exc)}, indent=2))
            return 1
        if args.names_only:
            for tool in tools:
                print(tool["name"])
        else:
            print(json.dumps({"serverInfo": session.server_info, "tools": tools}, indent=2))
        return 0

    if args.command == "call":
        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"arguments must be a JSON object: {exc}") from exc

        started = time.monotonic()
        try:
            session = _open(args.server, args.timeout)
            result = session.call_tool(args.tool, arguments)
            elapsed = time.monotonic() - started
        except MCPError as exc:
            elapsed = time.monotonic() - started
            # A protocol-level failure IS a finding -- report it, do not raise.
            print(
                json.dumps(
                    {"ok": False, "elapsed_s": round(elapsed, 3), "failure": str(exc)},
                    indent=2,
                )
            )
            return 1

        text = json.dumps(result, indent=2)
        if not args.full and len(text) > 20000:
            text = text[:20000] + f"\n... [truncated; {len(text)} chars total, use --full]"
        print(
            json.dumps(
                {
                    "ok": True,
                    "elapsed_s": round(elapsed, 3),
                    "isError": result.get("isError", False),
                }
            )
        )
        print(text)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
