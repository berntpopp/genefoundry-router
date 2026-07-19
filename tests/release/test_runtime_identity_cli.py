"""Focused command tests for strict runtime data-identity verification."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner, Result

from genefoundry_router.release.cli import app
from genefoundry_router.release.vulnerabilities import ReleaseExitCode

runner = CliRunner()
OBSERVED_IDENTITY = {
    "release_tag": "data-clingen-2026-07-16",
    "digest": f"sha256:{'a' * 64}",
}
RUNTIME_IDENTITY = {
    "release_identity": {
        "schema_version": 1,
        "data_identity": {
            "expected": OBSERVED_IDENTITY,
            "actual": OBSERVED_IDENTITY,
        },
    }
}


def _data_bound_release_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "service": {"name": "clingen-link", "compose_files": ["docker/docker-compose.yml"]},
        "data": {
            "mode": "external-reference",
            "release_tag": "data-clingen-2026-07-16",
            "digest": f"sha256:{'a' * 64}",
        },
        "definitions": {"contract": "data-bound"},
        "data_identity_contract": "runtime-v1",
    }


def _invoke_verify(tmp_path: Path, health_value: object) -> tuple[Result, Path]:
    config = tmp_path / "config.json"
    health = tmp_path / "health.json"
    observed = tmp_path / "observed.json"
    config.write_text(json.dumps(_data_bound_release_config()), encoding="utf-8")
    health.write_text(json.dumps(health_value), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "verify-runtime-data-identity",
            "--config",
            str(config),
            "--health",
            str(health),
            "--out",
            str(observed),
        ],
    )
    return result, observed


def test_verify_runtime_data_identity_writes_only_canonical_observed_pair(tmp_path: Path) -> None:
    result, observed = _invoke_verify(tmp_path, RUNTIME_IDENTITY)

    assert result.exit_code == ReleaseExitCode.SUCCESS
    assert (
        observed.read_bytes()
        == json.dumps(
            OBSERVED_IDENTITY,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    assert json.loads(result.stdout) == {
        "command": "verify-runtime-data-identity",
        "exit_code": 0,
        "observed_identity": str(observed),
        "verdict": "pass",
    }


def test_verify_runtime_data_identity_fails_closed_without_writing(tmp_path: Path) -> None:
    result, observed = _invoke_verify(tmp_path, {})

    assert result.exit_code == ReleaseExitCode.INVALID_EVIDENCE
    assert not observed.exists()
    assert json.loads(result.stdout) == {
        "command": "verify-runtime-data-identity",
        "exit_code": int(ReleaseExitCode.INVALID_EVIDENCE),
        "reason": "input validation failed",
        "verdict": "invalid_evidence",
    }
