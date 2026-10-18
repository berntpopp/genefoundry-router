"""Unit tests for CLI envvar loading, normalization parity, and doctor probe concurrency."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastmcp import FastMCP
from typer.testing import CliRunner

import genefoundry_router.server as srv
from genefoundry_router.cli import _gather_probes, _list_federated_tools, app
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef, TransformConfig

runner = CliRunner()


def _write_registry(path: Path) -> Path:
    yaml_file = path / "servers.yaml"
    yaml_file.write_text(
        "servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n"
    )
    return yaml_file


def test_load_env_callback_loads_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GF_TEST_CALLBACK_VAR=loaded_from_dotenv\n")
    yaml_file = _write_registry(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GF_TEST_CALLBACK_VAR", raising=False)
    monkeypatch.setenv("GF_GNOMAD_URL", "http://fake")
    result = runner.invoke(app, ["validate", "--servers-file", str(yaml_file)])
    assert result.exit_code == 0, result.output
    assert os.environ.get("GF_TEST_CALLBACK_VAR") == "loaded_from_dotenv"


def test_run_passes_workers_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_file = _write_registry(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "none")
    called: dict[str, object] = {}

    def fake_run(app_obj: object, host: str, port: int, **kw: object) -> None:
        called.update(kw)

    monkeypatch.setattr("genefoundry_router.cli.uvicorn.run", fake_run)
    result = runner.invoke(app, ["run", "--servers-file", str(yaml_file)])
    assert result.exit_code == 0, result.output
    assert called.get("workers") == 1


def test_cli_envvar_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_file = _write_registry(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "none")
    monkeypatch.setenv("GF_HOST", "127.0.0.1")
    monkeypatch.setenv("GF_PORT", "9999")
    monkeypatch.setenv("GF_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GF_SERVERS_FILE", str(yaml_file))
    called: dict[str, object] = {}

    def fake_run(app_obj: object, host: str, port: int, **kw: object) -> None:
        called["host"] = host
        called["port"] = port
        called["log_level"] = kw.get("log_level")

    monkeypatch.setattr("genefoundry_router.cli.uvicorn.run", fake_run)
    result = runner.invoke(app, ["run"])
    assert result.exit_code == 0, result.output
    assert called == {"host": "127.0.0.1", "port": 9999, "log_level": "debug"}


def test_cli_flag_overrides_envvar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    yaml_file = _write_registry(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "none")
    monkeypatch.setenv("GF_PORT", "9999")
    called: dict[str, object] = {}

    def fake_run(app_obj: object, host: str, port: int, **kw: object) -> None:
        called["port"] = port

    monkeypatch.setattr("genefoundry_router.cli.uvicorn.run", fake_run)
    result = runner.invoke(app, ["run", "--servers-file", str(yaml_file), "--port", "7777"])
    assert result.exit_code == 0, result.output
    assert called["port"] == 7777


def test_fake_fleet_passes_workers_one(monkeypatch: pytest.MonkeyPatch) -> None:
    from genefoundry_router.devtools import fake_fleet

    called: dict[str, object] = {}

    def fake_run(app_obj: object, host: str, port: int, **kw: object) -> None:
        called.update(kw)

    monkeypatch.setattr("genefoundry_router.devtools.fake_fleet.uvicorn.run", fake_run)
    monkeypatch.setattr(
        "sys.argv", ["fake_fleet", "--manifest", "tests/fixtures/fleet_manifest.json"]
    )
    fake_fleet.main()
    assert called.get("workers") == 1


async def test_list_federated_tools_applies_normalizations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sub = FastMCP("sub")

    @sub.tool()
    def sub_tool() -> str:
        return "ok"

    backend = BackendDef(
        name="sub",
        namespace="sub",
        url_env="X",
        url="http://fake/mcp",
        transform=TransformConfig(strip_prefix="sub_"),
    )
    orig_register = srv.register_backend
    monkeypatch.setattr(
        srv,
        "register_backend",
        lambda s, b, proxy_target=None, **kw: orig_register(s, b, proxy_target=sub, **kw),
    )
    settings = RouterSettings()
    names = await _list_federated_tools(settings, [backend])
    assert names == ["sub_tool"]


async def test_gather_probes_runs_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    event_b1 = asyncio.Event()
    event_b2 = asyncio.Event()

    async def fake_probe(backend: BackendDef) -> dict[str, object]:
        if backend.name == "b1":
            event_b1.set()
            await asyncio.wait_for(event_b2.wait(), timeout=2.0)
        else:
            await asyncio.wait_for(event_b1.wait(), timeout=2.0)
            event_b2.set()
        return {"name": backend.name, "reachable": True, "tools": 1, "error": None}

    monkeypatch.setattr("genefoundry_router.cli._probe_backend", fake_probe)
    b1 = BackendDef(name="b1", namespace="b1", url_env="U1", url="http://1")
    b2 = BackendDef(name="b2", namespace="b2", url_env="U2", url="http://2")

    results = await _gather_probes([b1, b2])
    assert [r["name"] for r in results] == ["b1", "b2"]
