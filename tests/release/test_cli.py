"""Tests for the stable workflow-facing release command adapters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from genefoundry_router.release import cli as release_cli
from genefoundry_router.release.cli import app
from genefoundry_router.release.source import SourceRelease
from genefoundry_router.release.vulnerabilities import ReleaseExitCode

runner = CliRunner()


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("validate-config", ["--config", "bad.json"]),
        (
            "validate-source",
            [
                "--event-name",
                "workflow_dispatch",
                "--event-ref",
                "refs/heads/main",
                "--event-sha",
                "bad",
                "--changelog",
                "bad.json",
            ],
        ),
        ("validate-compose", ["--rendered", "bad.json", "--service", "application"]),
        ("inspect-oci", ["--layout", "missing-layout"]),
        (
            "evaluate-trivy",
            ["--report", "bad.json", "--scanner-exit", "0", "--out", "verdict.json"],
        ),
        (
            "capture-definitions",
            [
                "--tools",
                "bad.json",
                "--context",
                "bad.json",
                "--contract",
                "data-bound",
                "--out-definitions",
                "definitions.json",
                "--out-context",
                "context.json",
            ],
        ),
        (
            "assemble-manifest",
            [
                "--identity",
                "bad.json",
                "--definitions",
                "bad.json",
                "--context",
                "bad.json",
                "--scanner",
                "bad.json",
                "--data",
                "bad.json",
                "--asset-dir",
                ".",
                "--out",
                "manifest.json",
            ],
        ),
        ("verify-deployment", ["--manifest", "bad.json"]),
    ],
)
def test_each_subcommand_emits_one_json_result_on_invalid_input(
    tmp_path: Path,
    command: str,
    arguments: list[str],
) -> None:
    result = runner.invoke(app, [command, *arguments], catch_exceptions=False)

    assert result.exit_code == ReleaseExitCode.INVALID_EVIDENCE
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["command"] == command
    assert payload["exit_code"] == ReleaseExitCode.INVALID_EVIDENCE
    assert payload["verdict"] == "invalid_evidence"


def test_validate_config_success_is_deterministic_json(tmp_path: Path) -> None:
    config = tmp_path / "container-release.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "service": {
                    "compose_files": ["docker/docker-compose.yml"],
                    "name": "genefoundry-router",
                    "container_port": 8000,
                    "health_path": "/health",
                    "mcp_path": "/mcp",
                    "startup_timeout_seconds": 90,
                },
                "data": {"mode": "none", "image_allowlist": []},
                "definitions": {"contract": "data-independent"},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate-config", "--config", str(config)])

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert json.loads(result.stdout) == {
        "command": "validate-config",
        "config": str(config),
        "exit_code": 0,
        "verdict": "pass",
    }


def test_scanner_infrastructure_exit_is_written_and_returned(tmp_path: Path) -> None:
    report = tmp_path / "trivy.json"
    output = tmp_path / "verdict.json"
    report.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "evaluate-trivy",
            "--report",
            str(report),
            "--scanner-exit",
            "7",
            "--out",
            str(output),
        ],
    )

    assert result.exit_code == ReleaseExitCode.INFRASTRUCTURE_FAILURE
    assert json.loads(result.stdout) == json.loads(output.read_text(encoding="utf-8"))


def test_validate_source_success_wires_cli_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_validate_source_release(**kwargs: object) -> SourceRelease:
        captured.update(kwargs)
        return SourceRelease("v1.2.3", "1.2.3", "a" * 40, "v1.2.2")

    monkeypatch.setattr(release_cli, "validate_source_release", fake_validate_source_release)
    result = runner.invoke(
        app,
        [
            "validate-source",
            "--event-name",
            "push",
            "--event-ref",
            "refs/tags/v1.2.3",
            "--event-sha",
            "a" * 40,
            "--changelog",
            str(changelog),
        ],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert captured == {
        "event_name": "push",
        "event_ref": "refs/tags/v1.2.3",
        "event_sha": "a" * 40,
        "changelog_text": "# Changelog\n",
    }
    assert json.loads(result.stdout)["source"]["tag"] == "v1.2.3"


def test_validate_compose_success_wires_render_and_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = tmp_path / "compose.json"
    rendered.write_text('{"services":{"application":{}}}', encoding="utf-8")
    captured: list[object] = []

    def fake_validate_compose(value: object, service: str, policy: object = None) -> tuple[()]:
        captured.extend((value, service, policy))
        return ()

    monkeypatch.setattr(release_cli, "validate_compose", fake_validate_compose)
    result = runner.invoke(
        app,
        ["validate-compose", "--rendered", str(rendered), "--service", "application"],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert captured == [{"services": {"application": {}}}, "application", None]
    assert json.loads(result.stdout)["violations"] == []


def test_inspect_oci_success_wires_layout_and_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = tmp_path / "oci"
    captured: list[object] = []
    report = SimpleNamespace(
        denied_paths=(),
        denied_config=(),
        to_dict=lambda: {"denied_config": [], "denied_paths": []},
    )

    def fake_inspect_oci_layout(path: Path, *, allowlist: tuple[str, ...]) -> object:
        captured.extend((path, allowlist))
        return report

    monkeypatch.setattr(release_cli, "inspect_oci_layout", fake_inspect_oci_layout)
    result = runner.invoke(
        app,
        [
            "inspect-oci",
            "--layout",
            str(layout),
            "--allowlist",
            "opt/app/schema.sql",
        ],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert captured == [layout, ("opt/app/schema.sql",)]
    assert json.loads(result.stdout)["report"] == {"denied_config": [], "denied_paths": []}


def test_capture_definitions_success_wires_inputs_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = tmp_path / "tools.json"
    context = tmp_path / "context.json"
    definitions_out = tmp_path / "definitions.json"
    context_out = tmp_path / "capture-context.json"
    tools.write_text('[{"name":"lookup"}]', encoding="utf-8")
    context.write_text('{"capture":"first"}', encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_capture_definitions(value: object, **kwargs: object) -> object:
        captured["capture"] = (value, kwargs)
        return "capture"

    evidence = SimpleNamespace(
        definitions_document={"tools": ["lookup"]},
        context_document={"captures": ["first"]},
        capture_context_sha256="b" * 64,
        definition_contract="data-bound",
        definitions_sha256="a" * 64,
    )

    def fake_verify_definition_contract(*args: object, **kwargs: object) -> object:
        captured["verify"] = (args, kwargs)
        return evidence

    monkeypatch.setattr(release_cli, "capture_definitions", fake_capture_definitions)
    monkeypatch.setattr(release_cli, "verify_definition_contract", fake_verify_definition_contract)
    result = runner.invoke(
        app,
        [
            "capture-definitions",
            "--tools",
            str(tools),
            "--context",
            str(context),
            "--contract",
            "data-bound",
            "--out-definitions",
            str(definitions_out),
            "--out-context",
            str(context_out),
            "--data-release-tag",
            "v2026.07",
            "--data-digest",
            f"sha256:{'c' * 64}",
        ],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert captured["capture"] == (
        [{"name": "lookup"}],
        {
            "context": {"capture": "first"},
            "data_release_tag": "v2026.07",
            "data_digest": f"sha256:{'c' * 64}",
            "adoption": "unadopted",
        },
    )
    assert captured["verify"] == (
        ("data-bound", ("capture",)),
        {
            "data_release_tag": "v2026.07",
            "data_digest": f"sha256:{'c' * 64}",
            "adoption": "unadopted",
        },
    )
    assert json.loads(definitions_out.read_text(encoding="utf-8")) == evidence.definitions_document
    assert json.loads(context_out.read_text(encoding="utf-8")) == evidence.context_document


def test_assemble_manifest_success_wires_evidence_and_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = tmp_path / "identity.json"
    definitions = tmp_path / "definitions.json"
    context = tmp_path / "context.json"
    scanner = tmp_path / "scanner.json"
    data = tmp_path / "data.json"
    output = tmp_path / "application-release-manifest.json"
    identity.write_text(
        json.dumps(
            {
                "repository": "owner/application",
                "version": "1.2.3",
                "source_tag": "v1.2.3",
                "source_revision": "a" * 40,
                "image_name": "ghcr.io/owner/application",
                "image_digest": f"sha256:{'b' * 64}",
                "workflow_caller": "owner/application/.github/workflows/release.yml",
                "workflow_standard": "owner/router/.github/workflows/_release.yml",
                "workflow_revision": "c" * 40,
            }
        ),
        encoding="utf-8",
    )
    for path in (definitions, context):
        path.write_text("{}", encoding="utf-8")
    scanner.write_text(
        '{"version":"0.66.0","database_updated_at":"2026-07-13T10:30:00Z"}',
        encoding="utf-8",
    )
    data.write_text('{"mode":"none","schema_compatibility":[]}', encoding="utf-8")
    captured: dict[str, object] = {}
    definition_evidence = object()
    manifest_payload = {"schema_version": 1, "assembled": True}
    manifest = SimpleNamespace(
        image=SimpleNamespace(digest=f"sha256:{'b' * 64}"),
        model_dump=lambda **_: manifest_payload,
    )

    monkeypatch.setattr(release_cli, "load_definition_evidence", lambda *_: definition_evidence)

    def fake_assemble_application_release_manifest(**kwargs: object) -> object:
        captured.update(kwargs)
        return manifest

    monkeypatch.setattr(
        release_cli,
        "assemble_application_release_manifest",
        fake_assemble_application_release_manifest,
    )
    result = runner.invoke(
        app,
        [
            "assemble-manifest",
            "--identity",
            str(identity),
            "--definitions",
            str(definitions),
            "--context",
            str(context),
            "--scanner",
            str(scanner),
            "--data",
            str(data),
            "--asset-dir",
            str(tmp_path),
            "--out",
            str(output),
        ],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert captured["definitions"] is definition_evidence
    assert captured["data_requirements"] == {"mode": "none", "schema_compatibility": []}
    assert [asset.name for asset in captured["assets"]] == list(release_cli.STANDARD_ASSETS)
    assert json.loads(output.read_text(encoding="utf-8")) == manifest_payload


def test_verify_deployment_success_wires_online_and_offline_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "application-release-manifest.json"
    manifest_path.write_text('{"schema_version":1}', encoding="utf-8")
    image_manifest = tmp_path / "image-manifest.json"
    bundle = tmp_path / "bundle.json"
    trusted_root = tmp_path / "trusted-root.json"
    reviewed = object()
    captured: dict[str, object] = {}
    verification = SimpleNamespace(
        exit_code=ReleaseExitCode.SUCCESS,
        to_dict=lambda: {"exit_code": 0, "mode": "offline", "verdict": "pass"},
    )
    monkeypatch.setattr(
        release_cli,
        "ApplicationReleaseManifest",
        SimpleNamespace(model_validate=lambda value: reviewed),
    )

    def fake_verify_deployment(value: object, **kwargs: object) -> object:
        captured.update({"manifest": value, **kwargs})
        return verification

    monkeypatch.setattr(release_cli, "verify_deployment", fake_verify_deployment)
    result = runner.invoke(
        app,
        [
            "verify-deployment",
            "--manifest",
            str(manifest_path),
            "--image-manifest",
            str(image_manifest),
            "--bundle",
            str(bundle),
            "--trusted-root",
            str(trusted_root),
        ],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert captured == {
        "manifest": reviewed,
        "image_manifest": image_manifest,
        "bundle": bundle,
        "trusted_root": trusted_root,
    }
    assert json.loads(result.stdout) == {
        "command": "verify-deployment",
        "exit_code": 0,
        "mode": "offline",
        "verdict": "pass",
    }
