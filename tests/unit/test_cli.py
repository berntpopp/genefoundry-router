from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def _write_registry(tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")
    return yaml


def test_run_invokes_uvicorn(monkeypatch, tmp_path):
    yaml = _write_registry(tmp_path)
    # Binding 0.0.0.0 with auth=none is the insecure combo; this test only checks host/port
    # passthrough, so it opts into the explicit escape hatch.
    monkeypatch.setenv("GF_AUTH_MODE", "none")
    monkeypatch.setenv("GF_ALLOW_INSECURE", "true")
    called = {}

    def fake_run(app_obj, host, port, **kw):
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr("genefoundry_router.cli.uvicorn.run", fake_run)
    result = runner.invoke(
        app,
        ["run", "--servers-file", str(yaml), "--host", "0.0.0.0", "--port", "8123"],  # noqa: S104
    )
    assert result.exit_code == 0, result.output
    assert called == {"host": "0.0.0.0", "port": 8123}  # noqa: S104


def test_run_refuses_unauthenticated_public_bind(monkeypatch, tmp_path):
    yaml = _write_registry(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "none")
    monkeypatch.delenv("GF_ALLOW_INSECURE", raising=False)
    called = {}
    monkeypatch.setattr(
        "genefoundry_router.cli.uvicorn.run",
        lambda *a, **k: called.setdefault("ran", True),
    )
    result = runner.invoke(
        app,
        ["run", "--servers-file", str(yaml), "--host", "0.0.0.0"],  # noqa: S104
    )
    assert result.exit_code != 0  # refused before serving
    assert "GF_ALLOW_INSECURE" in result.output  # tells the operator how to override
    assert called == {}  # uvicorn never started


def test_run_serves_loopback_without_auth(monkeypatch, tmp_path):
    yaml = _write_registry(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "none")
    monkeypatch.delenv("GF_ALLOW_INSECURE", raising=False)
    called = {}
    monkeypatch.setattr(
        "genefoundry_router.cli.uvicorn.run",
        lambda *a, **k: called.setdefault("ran", True),
    )
    result = runner.invoke(app, ["run", "--servers-file", str(yaml), "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output  # loopback is safe without auth
    assert called == {"ran": True}


def test_doctor_reports_unreachable(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")
    monkeypatch.setenv("GF_GNOMAD_URL", "https://unreachable.invalid/mcp")

    async def fake_probe(backend):
        return {"name": backend.name, "reachable": False, "tools": 0, "error": "boom"}

    monkeypatch.setattr("genefoundry_router.cli._probe_backend", fake_probe)
    result = runner.invoke(app, ["doctor", "--servers-file", str(yaml)])
    assert result.exit_code == 1  # at least one backend unreachable -> non-zero
    assert "gnomad" in result.output
    assert "unreachable" in result.output.lower()
