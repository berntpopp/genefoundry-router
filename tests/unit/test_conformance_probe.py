"""Unit tests for MCP Transport Standard v1 conformance probe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from genefoundry_router.conformance import (
    Report,
    _parse_body,
    check_sse_rejected,
    run_probe,
)


def test_conformance_files_are_byte_identical() -> None:
    root = Path(__file__).resolve().parents[2]
    router_probe = (root / "genefoundry_router" / "conformance.py").read_bytes()
    docs_probe = (root / "docs" / "conformance" / "conformance.py").read_bytes()
    assert router_probe == docs_probe, (
        "genefoundry_router/conformance.py and docs/conformance/conformance.py drifted"
    )


def test_parse_body_valid_json() -> None:
    resp = httpx.Response(200, json={"result": {"serverInfo": {"name": "test"}}})
    assert _parse_body(resp) == {"result": {"serverInfo": {"name": "test"}}}


def test_parse_body_malformed_json() -> None:
    resp = httpx.Response(200, text="not-json{", headers={"content-type": "application/json"})
    assert _parse_body(resp) == {}


def test_parse_body_non_dict_json() -> None:
    resp = httpx.Response(200, text="[1, 2, 3]", headers={"content-type": "application/json"})
    assert _parse_body(resp) == {}

    resp2 = httpx.Response(200, text='"hello"', headers={"content-type": "application/json"})
    assert _parse_body(resp2) == {}

    resp3 = httpx.Response(200, text="null", headers={"content-type": "application/json"})
    assert _parse_body(resp3) == {}


def test_parse_body_sse_stream() -> None:
    sse_text = 'event: message\ndata: {"result": {"ok": true}}\n\n'
    resp = httpx.Response(200, text=sse_text, headers={"content-type": "text/event-stream"})
    assert _parse_body(resp) == {"result": {"ok": True}}


def test_parse_body_sse_malformed() -> None:
    sse_text = "event: message\ndata: not-json\n\n"
    resp = httpx.Response(200, text=sse_text, headers={"content-type": "text/event-stream"})
    assert _parse_body(resp) == {}


def test_parse_body_sse_empty() -> None:
    resp = httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
    assert _parse_body(resp) == {}


def test_check_sse_rejected_streaming_and_token() -> None:
    seen_headers: dict[str, str] = {}
    stream_closed = False

    class NeverReadStream(httpx.SyncByteStream):
        def __iter__(self) -> Any:
            raise AssertionError("SSE stream body must not be read")

        def close(self) -> None:
            nonlocal stream_closed
            stream_closed = True

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update({k.lower(): v for k, v in request.headers.items()})
        assert request.method == "GET"
        assert request.url.path == "/mcp"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=NeverReadStream(),
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        rep = Report("http://test", "test-server", "stateless")
        ok = check_sse_rejected(client, "http://test", rep, token="secret-token")  # noqa: S106
        assert ok is True
        assert rep.conformant is True
        assert stream_closed is True
        assert seen_headers.get("accept") == "text/event-stream"
        assert seen_headers.get("authorization") == "Bearer secret-token"


def test_check_sse_rejected_fails_on_307() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(307, headers={"Location": "http://redirected"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        rep = Report("http://test", "test-server", "stateless")
        ok = check_sse_rejected(client, "http://test", rep)
        assert ok is False
        assert not rep.conformant
        assert any("GET /mcp does not 307" in f for f in rep.failed)


def test_run_probe_with_service_token() -> None:
    auth_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_headers.append(request.headers.get("authorization"))
        if request.url.path == "/mcp":
            if request.method == "POST":
                body = json.loads(request.read())
                if body.get("method") == "initialize":
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "result": {"serverInfo": {"name": "srv"}},
                        },
                        headers={"content-type": "application/json"},
                    )
                if body.get("method") == "tools/list":
                    if request.headers.get("mcp-protocol-version") == "1999-01-01":
                        return httpx.Response(400)
                    return httpx.Response(
                        200,
                        json={"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "t1"}]}},
                        headers={"content-type": "application/json"},
                    )
            return httpx.Response(200)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "1.0", "transport": "http"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
        rep = run_probe(
            "http://test",
            expected_name="srv",
            tier="stateless",
            service_token="my-token",  # noqa: S106
        )
        assert rep.conformant is True
        assert all(h == "Bearer my-token" for h in auth_headers if h is not None)


def test_run_probe_require_auth_remains_anonymous() -> None:
    auth_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth_headers.append(request.headers.get("authorization"))
        return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer realm="mcp"'})

    transport = httpx.MockTransport(handler)
    with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
        rep = run_probe(
            "http://test",
            expected_name="srv",
            tier="stateless",
            require_auth=True,
            service_token="secret",  # noqa: S106
        )
        assert rep.conformant is True
        assert all(h is None for h in auth_headers)


def test_run_probe_null_result_tolerated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": None},
                headers={"content-type": "application/json"},
            )
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "1.0", "transport": "http"})
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with patch("httpx.Client", return_value=httpx.Client(transport=transport)):
        rep = run_probe("http://test", expected_name="srv", tier="stateless")
        # Should not crash with AttributeError, but fail on checks
        assert not rep.conformant
