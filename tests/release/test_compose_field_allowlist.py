"""Exact rendered-field contract for the router v1 Compose model."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from genefoundry_router.release.compose import validate_compose
from genefoundry_router.release.compose_policy import (
    ALLOWED_SERVICE_KEYS,
    ALLOWED_TOP_LEVEL_KEYS,
)

_IMAGE = f"ghcr.io/acme/app@sha256:{'a' * 64}"
_SERVICE = "genefoundry-router"


def _compose_version(docker: str) -> str:
    return subprocess.run(  # noqa: S603
        [docker, "compose", "version", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture(scope="module")
def approved_render() -> dict[str, object]:
    docker = shutil.which("docker")
    assert docker is not None
    env = os.environ | {
        "GENEFOUNDRY_IMAGE": _IMAGE,
        "GF_ALLOWED_HOSTS": "router.example.test",
        "GF_HEALTHCHECK_HOST": "router.example.test",
    }
    completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "-f",
            "docker/docker-compose.prod.yml",
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(completed.stdout)


def test_approved_real_render_matches_exact_field_allowlists(
    approved_render: dict[str, object],
) -> None:
    service = approved_render["services"][_SERVICE]

    assert set(approved_render) == ALLOWED_TOP_LEVEL_KEYS
    assert set(service) == ALLOWED_SERVICE_KEYS
    assert validate_compose(approved_render, _SERVICE) == ()


@pytest.mark.parametrize(
    ("field", "hook"),
    [
        ("post_start", [{"command": ["true"]}]),
        ("pre_stop", []),
        (
            "post_start",
            [{"command": ["sh", "-c", "id"], "user": "root", "privileged": True}],
        ),
        (
            "pre_stop",
            [{"command": ["sh", "-c", "id"], "user": "root", "privileged": True}],
        ),
    ],
)
def test_unapproved_lifecycle_hook_is_rejected_even_if_harmless(
    approved_render: dict[str, object], field: str, hook: object
) -> None:
    rendered = copy.deepcopy(approved_render)
    rendered["services"][_SERVICE][field] = hook

    violations = validate_compose(rendered, _SERVICE)
    assert f"services.{_SERVICE}.{field}" in " ".join(violations)


@pytest.mark.parametrize("field", ["post_start", "pre_stop"])
def test_real_privileged_lifecycle_hook_survives_render_and_is_rejected(
    tmp_path: Path, field: str
) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    version = _compose_version(docker)
    overlay = tmp_path / f"{field}.yml"
    overlay.write_text(
        f"""\
services:
  {_SERVICE}:
    {field}:
      - command: ["sh", "-c", "id"]
        user: root
        privileged: true
""",
        encoding="utf-8",
    )
    env = os.environ | {
        "GENEFOUNDRY_IMAGE": _IMAGE,
        "GF_ALLOWED_HOSTS": "router.example.test",
        "GF_HEALTHCHECK_HOST": "router.example.test",
    }
    completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "-f",
            "docker/docker-compose.prod.yml",
            "-f",
            str(overlay),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    actual = json.loads(completed.stdout)
    hook = actual["services"][_SERVICE][field][0]

    assert hook["command"] == ["sh", "-c", "id"], f"Docker Compose {version}"
    assert hook["user"] == "root", f"Docker Compose {version}"
    assert hook["privileged"] is True, f"Docker Compose {version}"
    violations = validate_compose(actual, _SERVICE)
    assert f"services.{_SERVICE}.{field}" in " ".join(violations), f"Docker Compose {version}"


@pytest.mark.parametrize("scope", ["top", "service"])
@pytest.mark.parametrize("key", ["future_" + "x" * 10_000, "future/" * 2_500])
def test_unknown_extreme_field_names_have_bounded_deterministic_violations(
    approved_render: dict[str, object], scope: str, key: str
) -> None:
    rendered = copy.deepcopy(approved_render)
    target = rendered if scope == "top" else rendered["services"][_SERVICE]
    target[key] = True

    first = validate_compose(rendered, _SERVICE)
    second = validate_compose(rendered, _SERVICE)

    assert first == second
    assert "invalid-string" in first[0]
    assert "unapproved rendered field" in first[0]
    assert len(first[0]) < 200


def test_unknown_top_level_path_is_preserved_when_services_are_malformed() -> None:
    violations = validate_compose({"future_model": True}, _SERVICE)

    assert violations[0].startswith("compose.future_model:")
    assert "services: must be a mapping" in violations
