from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def test_run_invokes_uvicorn(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")
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
