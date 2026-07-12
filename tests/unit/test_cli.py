from typer.testing import CliRunner

from genefoundry_router.cli import app, is_missing_public_host_allowlist

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
    monkeypatch.setenv("GF_ALLOWED_HOSTS", "router.test")
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


def test_run_refuses_production_loopback_without_controls(monkeypatch, tmp_path) -> None:
    yaml = _write_registry(tmp_path)
    monkeypatch.setenv("GF_DEPLOYMENT_MODE", "production")
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.setenv("GF_RATE_LIMIT_RPM", "0")
    monkeypatch.delenv("GF_METRICS_TOKEN", raising=False)
    called: dict[str, bool] = {}
    monkeypatch.setattr(
        "genefoundry_router.cli.uvicorn.run",
        lambda *_args, **_kwargs: called.setdefault("ran", True),
    )

    result = runner.invoke(app, ["run", "--servers-file", str(yaml), "--host", "127.0.0.1"])

    assert result.exit_code == 1
    assert "GF_RATE_LIMIT_RPM" in result.output
    assert called == {}


def test_public_bind_requires_nonempty_allowed_hosts() -> None:
    assert is_missing_public_host_allowlist("0.0.0.0", [])  # noqa: S104


def test_loopback_bind_allows_empty_allowed_hosts() -> None:
    assert not is_missing_public_host_allowlist("127.0.0.1", [])


def test_run_refuses_public_bind_without_host_allowlist(monkeypatch, tmp_path) -> None:
    yaml = _write_registry(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    monkeypatch.delenv("GF_ALLOWED_HOSTS", raising=False)
    called: dict[str, bool] = {}
    monkeypatch.setattr(
        "genefoundry_router.cli.build_app",
        lambda *_args, **_kwargs: called.setdefault("built", True),
    )
    monkeypatch.setattr(
        "genefoundry_router.cli.uvicorn.run",
        lambda *_args, **_kwargs: called.setdefault("ran", True),
    )

    result = runner.invoke(
        app,
        ["run", "--servers-file", str(yaml), "--host", "0.0.0.0"],  # noqa: S104
    )

    assert result.exit_code == 1
    assert "GF_ALLOWED_HOSTS" in result.output
    assert called == {}


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


def test_doctor_strict_naming_ops_tagged_not_flagged(monkeypatch, tmp_path):
    """doctor --strict-naming must not flag ops/meta-tagged tools as naming violations.

    Regression test for the _probe_backend tag-drop bug: before the fix, _probe_backend
    captured only leaf names (dropping tag metadata), so the doctor loop always called
    check_leaf_name without tags — ops-tagged tools like check_upstream_health were
    incorrectly flagged as verb violations despite the carve-out.

    After the fix, _probe_backend also captures per-tool tags (mirroring _snapshot_live),
    and the doctor loop passes them to check_leaf_name:
      - frobnicate_thing (untagged, non-canonical verb) IS flagged
      - check_upstream_health (ops-tagged) is NOT flagged
    """
    yaml = _write_registry(tmp_path)

    async def fake_probe(backend):
        return {
            "name": backend.name,
            "reachable": True,
            "tools": 2,
            # New format: leaf_tools carries per-tool name + tags (mirrors _snapshot_live).
            "leaf_tools": [
                {"name": "check_upstream_health", "tags": ["ops"]},
                {"name": "frobnicate_thing", "tags": []},
            ],
        }

    monkeypatch.setattr("genefoundry_router.cli._probe_backend", fake_probe)
    result = runner.invoke(app, ["doctor", "--strict-naming", "--servers-file", str(yaml)])
    # frobnicate_thing (untagged, non-canonical verb) MUST be reported as a NAME violation
    assert "frobnicate_thing" in result.output, (
        f"Expected frobnicate_thing violation in output;\ngot:\n{result.output}"
    )
    # check_upstream_health (ops-tagged) MUST NOT be reported as a NAME violation
    assert "check_upstream_health" not in result.output, (
        f"check_upstream_health (ops tag) must not be flagged;\ngot:\n{result.output}"
    )
    assert result.exit_code == 1  # frobnicate_thing violation -> non-zero
