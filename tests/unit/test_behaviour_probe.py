"""Unit tests for Behaviour Conformance v1 probe and related workflow guards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from docs.conformance.behaviour import (
    Probe,
    ProtocolError,
    Report,
    _grouped_collection,
    check_argument_error,
    is_error_result,
    rows,
)


def test_probe_rpc_malformed_json_raises_decoding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="not-valid-json{", headers={"content-type": "application/json"}
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.DecodingError, match="Invalid JSON-RPC response object"):
        probe._rpc("test_method")


def test_probe_rpc_non_dict_json_raises_decoding_error() -> None:
    for payload in ("[1, 2, 3]", '"string"', "123", "null", "true"):

        def handler(request: httpx.Request, p=payload) -> httpx.Response:
            return httpx.Response(200, text=p, headers={"content-type": "application/json"})

        probe = Probe("http://test")
        probe.client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.DecodingError, match="Invalid JSON-RPC response object"):
            probe._rpc("test_method")


def test_probe_rpc_null_result_raises_decoding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": None},
            headers={"content-type": "application/json"},
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.DecodingError, match="Invalid JSON-RPC response object"):
        probe._rpc("test_method")


def test_probe_rpc_non_dict_error_raises_decoding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": "something went wrong"},
            headers={"content-type": "application/json"},
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.DecodingError, match="Invalid JSON-RPC response object"):
        probe._rpc("test_method")


def test_probe_rpc_valid_error_raises_protocol_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "Invalid request"},
            },
            headers={"content-type": "application/json"},
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProtocolError):
        probe._rpc("test_method")


def test_probe_rpc_valid_result_returns_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "test"}}},
            headers={"content-type": "application/json"},
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    result = probe._rpc("test_method")
    assert result == {"serverInfo": {"name": "test"}}


def test_probe_rpc_sse_empty_or_malformed_raises_decoding_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="event: ping\n\n", headers={"content-type": "text/event-stream"}
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.DecodingError, match="Invalid JSON-RPC response object"):
        probe._rpc("test_method")


def test_rows_filters_out_scalar_lists() -> None:
    env_scalars: dict[str, Any] = {"items": ["str1", "str2"], "total_count": 2}
    assert rows(env_scalars) is None

    env_mixed: dict[str, Any] = {"items": [{"id": 1}, "str2"], "total_count": 2}
    assert rows(env_mixed) is None

    env_dicts: dict[str, Any] = {"items": [{"id": 1}, {"id": 2}], "total_count": 2}
    assert rows(env_dicts) == [{"id": 1}, {"id": 2}]


def test_grouped_collection_ignores_scalar_facets() -> None:
    env_facets: dict[str, Any] = {
        "facets": {"tags": ["tag1", "tag2", "tag3"]},
        "results": [{"id": 1}],
        "total_count": 1,
    }
    assert _grouped_collection(env_facets) is None
    # Real records win over scalar facets
    assert rows(env_facets) == [{"id": 1}]


def test_grouped_collection_handles_valid_groups_and_empty() -> None:
    env_valid: dict[str, Any] = {
        "mappings": {"OMIM": [{"id": 100}], "ICD10": [{"id": 200}]},
    }
    grouped = _grouped_collection(env_valid)
    assert grouped == [{"id": 100}, {"id": 200}]

    env_empty: dict[str, Any] = {"mappings": {}}
    assert _grouped_collection(env_empty) == []


def test_is_error_result() -> None:
    result_plain_text: dict[str, Any] = {
        "isError": True,
        "content": [{"type": "text", "text": "Field required"}],
    }
    assert is_error_result(result_plain_text, {}) is True

    result_env_error: dict[str, Any] = {
        "isError": False,
        "structuredContent": {"success": False, "error_code": "invalid_input"},
    }
    env_error = {"success": False, "error_code": "invalid_input"}
    assert is_error_result(result_env_error, env_error) is True

    result_success: dict[str, Any] = {"isError": False}
    env_success = {"success": True, "results": []}
    assert is_error_result(result_success, env_success) is False


def test_check_argument_error_plain_text_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": "Field required: unknown argument"}],
                },
            },
            headers={"content-type": "application/json"},
        )

    probe = Probe("http://test")
    probe.client = httpx.Client(transport=httpx.MockTransport(handler))
    rep = Report("http://test", "test-server")
    tool = {"name": "test_tool", "inputSchema": {"properties": {}}}

    check_argument_error(rep, probe, tool)
    # Must NOT report "accepted an unknown argument"
    assert not any("ACCEPTED an unknown argument" in f for f in rep.failed)
    # Must report missing fleet error envelope
    assert any("tool error includes a fleet error envelope" in f for f in rep.failed)


def test_fleet_probe_workflow_guard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from genefoundry_router.cli import app

    root = Path(__file__).resolve().parents[2]
    content = (root / ".github" / "workflows" / "fleet-probe.yml").read_text()
    assert "set +e" not in content
    assert "run: uv run genefoundry-router fleet-probe\n" in content
    assert "FLEET_PROBE_HEARTBEAT_URL" in content
    assert "success()" in content
    assert "|| true" not in content

    # Verify the exact CLI arguments invoked by the workflow execute cleanly via CliRunner
    yaml_file = tmp_path / "servers.yaml"
    yaml_file.write_text(
        "servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad, enabled: false }\n"
    )
    monkeypatch.setenv("GF_SERVERS_FILE", str(yaml_file))
    res = CliRunner().invoke(app, ["fleet-probe"])
    assert res.exit_code == 0, res.output


def test_mcp_registry_workflow_guard() -> None:
    root = Path(__file__).resolve().parents[2]
    content = (root / ".github" / "workflows" / "mcp-registry.yml").read_text()
    assert "pipx install uv" not in content
    assert "astral-sh/setup-uv@" in content
    assert 'version: "0.8.7"' in content
    assert "version=1.8.1" in content
    assert "a06c9096dcb9727c13555b6be26c7effa707b01f06a4c561ba7a3635443cf2cc" in content
    assert "sha256sum -c" in content
