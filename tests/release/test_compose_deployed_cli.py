"""CLI and render-helper contract for the deployed Compose overlay gate."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from genefoundry_router.release import cli_deployed, compose_render
from genefoundry_router.release.cli import app
from genefoundry_router.release.compose_render import (
    ComposeRenderError,
    placeholder_value,
    required_variables,
)
from genefoundry_router.release.vulnerabilities import ReleaseExitCode

runner = CliRunner()
DIGEST = "sha256:" + "a" * 64
TEMPLATE = "${EXAMPLE_LINK_IMAGE:?Set EXAMPLE_LINK_IMAGE to ghcr.io/x/y@sha256:<digest>}"
OVERLAY = "docker/docker-compose.npm.yml"


def _config(tmp_path: Path, **service: Any) -> Path:
    document = {
        "schema_version": 1,
        "service": {"name": "example-link", "compose_files": ["docker/docker-compose.yml"]}
        | service,
        "definitions": {"contract": "data-independent"},
    }
    path = tmp_path / "container-release.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "docker").mkdir(exist_ok=True)
    (tmp_path / OVERLAY).write_text("services:\n  app:\n    expose: ['8000']\n", encoding="utf-8")
    return path


def _compliant() -> tuple[Mapping[str, object], Mapping[str, object]]:
    rendered = {
        "name": "example-link-npm",
        "services": {
            "app": {
                "image": f"ghcr.io/example/placeholder@{DIGEST}",
                "user": "10001:10001",
                "restart": "unless-stopped",
                "expose": ["8000"],
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "healthcheck": {"test": ["CMD", "true"], "start_period": "30s"},
            }
        },
    }
    return rendered, {"name": "example-link-npm", "services": {"app": {"image": TEMPLATE}}}


def _stub_render(
    result: tuple[Mapping[str, object], Mapping[str, object]] | Exception,
) -> Any:
    def render(project_dir: Path, compose_files: Sequence[str]) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    return render


def test_compliant_overlay_passes_and_reports_the_deployed_file_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_deployed, "render_deployed_overlay", _stub_render(_compliant()))
    config = _config(tmp_path)

    result = runner.invoke(
        app,
        ["validate-deployed-overlay", "--config", str(config), "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "pass"
    assert payload["compose_files"] == [OVERLAY]


def test_violating_overlay_exits_policy_violation_and_prints_one_line_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, raw = _compliant()
    rendered["services"]["app"]["user"] = "app"  # type: ignore[index]
    rendered["services"]["app"]["restart"] = "on-failure"  # type: ignore[index]
    monkeypatch.setattr(cli_deployed, "render_deployed_overlay", _stub_render((rendered, raw)))
    config = _config(tmp_path)

    result = runner.invoke(
        app,
        ["validate-deployed-overlay", "--config", str(config), "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == ReleaseExitCode.POLICY_VIOLATION
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "policy_violation"
    assert {line.split(": ", 1)[1].split(" — ", 1)[0] for line in payload["violations"]} == {
        "numeric-user",
        "restart-policy",
    }


def test_unrenderable_overlay_exits_invalid_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ComposeRenderError("required variable EXAMPLE_LINK_IMAGE is missing a value")
    monkeypatch.setattr(cli_deployed, "render_deployed_overlay", _stub_render(error))
    config = _config(tmp_path)

    result = runner.invoke(
        app,
        ["validate-deployed-overlay", "--config", str(config), "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == ReleaseExitCode.INVALID_EVIDENCE
    assert json.loads(result.stdout)["verdict"] == "invalid_evidence"


def test_declared_deployed_compose_files_are_used_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Sequence[str]] = {}

    def render(project_dir: Path, compose_files: Sequence[str]) -> Any:
        seen["files"] = compose_files
        return _compliant()

    monkeypatch.setattr(cli_deployed, "render_deployed_overlay", render)
    config = _config(
        tmp_path,
        deployed_compose_files=["docker/docker-compose.yml", OVERLAY],
    )
    (tmp_path / "docker/docker-compose.yml").write_text("services:\n  app: {}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["validate-deployed-overlay", "--config", str(config), "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert list(seen["files"]) == ["docker/docker-compose.yml", OVERLAY]
    assert json.loads(result.stdout)["compose_files"] == ["docker/docker-compose.yml", OVERLAY]


def test_compose_file_option_overrides_the_declared_deployed_file_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_deployed, "render_deployed_overlay", _stub_render(_compliant()))
    config = _config(tmp_path)

    result = runner.invoke(
        app,
        [
            "validate-deployed-overlay",
            "--config",
            str(config),
            "--project-dir",
            str(tmp_path),
            "--compose-file",
            OVERLAY,
        ],
    )

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert json.loads(result.stdout)["compose_files"] == [OVERLAY]


def test_layered_overlay_declared_alone_is_refused_by_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_deployed, "render_deployed_overlay", _stub_render(_compliant()))
    config = _config(tmp_path)
    (tmp_path / OVERLAY).write_text("services:\n  app:\n    ports: !reset []\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["validate-deployed-overlay", "--config", str(config), "--project-dir", str(tmp_path)],
    )

    assert result.exit_code == ReleaseExitCode.POLICY_VIOLATION
    assert "deployed-file-set" in json.loads(result.stdout)["violations"][0]


@pytest.mark.parametrize(
    ("variable", "expected"),
    [
        ("EXAMPLE_LINK_IMAGE", f"ghcr.io/example/placeholder@{DIGEST}"),
        ("MAVEDB_DATA_SHA256", "a" * 64),
        ("MAVEDB_DATA_EXPANDED_SHA256", "a" * 64),
        ("CLINGEN_LINK_DATA_IDENTITY_DIGEST", DIGEST),
        ("MAVEDB_DATA_BUNDLE_URL", "https://example.test/placeholder"),
        ("NPM_SHARED_NETWORK_NAME", "example.test"),
    ],
)
def test_placeholder_values_are_shaped_like_what_the_variable_carries(
    variable: str, expected: str
) -> None:
    assert placeholder_value(variable) == expected


def test_required_variables_reads_only_the_required_column(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listing = (
        "NAME                  REQUIRED   DEFAULT VALUE   ALTERNATE VALUE\n"
        "LOG_LEVEL_API         false      INFO\n"
        "EXAMPLE_LINK_IMAGE    true\n"
        "EXAMPLE_DATA_SHA256   true\n"
    )
    monkeypatch.setattr(compose_render, "_run", lambda *_args, **_kwargs: listing)

    assert required_variables(tmp_path, [OVERLAY]) == (
        "EXAMPLE_LINK_IMAGE",
        "EXAMPLE_DATA_SHA256",
    )


def test_a_missing_overlay_file_is_a_render_error(tmp_path: Path) -> None:
    with pytest.raises(ComposeRenderError):
        compose_render.render_deployed_overlay(tmp_path, [OVERLAY])
