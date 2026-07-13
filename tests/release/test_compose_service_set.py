"""Single-service trust-boundary contract for rendered Compose models."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from genefoundry_router.release.compose import ComposePolicy, validate_compose

_IMAGE = f"ghcr.io/acme/app@sha256:{'a' * 64}"
_HEALTH = ("CMD", "true")


class _HashableMapping(dict[object, object]):
    __hash__ = object.__hash__


@pytest.fixture
def rendered() -> dict[str, object]:
    return {
        "name": "app",
        "services": {
            "app": {
                "image": _IMAGE,
                "pull_policy": "missing",
                "restart": "on-failure",
                "read_only": True,
                "init": True,
                "expose": ["8000"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],  # noqa: S108
                "volumes": [{"type": "volume", "source": "data", "target": "/data"}],
                "networks": {"default": None},
                "deploy": {
                    "resources": {"limits": {"cpus": 1, "memory": "1073741824", "pids": 256}}
                },
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "50m", "max-file": "5"},
                },
                "healthcheck": {
                    "test": list(_HEALTH),
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                },
            }
        },
        "volumes": {"data": {"name": "app_data"}},
        "networks": {"default": {"name": "app_default", "ipam": {}}},
    }


def _policy() -> ComposePolicy:
    return ComposePolicy(healthcheck_test=_HEALTH)


@pytest.mark.parametrize(
    "sidecar",
    [
        {"image": _IMAGE},
        {
            "image": _IMAGE,
            "privileged": True,
            "network_mode": "host",
            "user": "0",
            "volumes": ["/:/host", "/var/run/docker.sock:/var/run/docker.sock"],
        },
    ],
)
def test_unmodeled_auxiliary_service_requires_role_policy(
    rendered: dict[str, object], sidecar: dict[str, object]
) -> None:
    rendered["services"]["sidecar"] = sidecar

    violations = validate_compose(rendered, "app", _policy())
    assert violations[0].startswith("services.sidecar:")
    assert "explicit role-specific policy" in violations[0]


@pytest.mark.parametrize("service_key", [None, 1, True, "", "bad/name", ("tuple",)])
def test_malformed_service_key_fails_closed(
    rendered: dict[str, object], service_key: object
) -> None:
    rendered["services"][service_key] = {"image": _IMAGE}

    violations = validate_compose(rendered, "app", _policy())
    assert violations[0].startswith("services[")
    assert "service key" in violations[0]


@pytest.mark.parametrize("service_key", ["", "bad/name"])
def test_malformed_application_service_key_fails_closed(
    rendered: dict[str, object], service_key: str
) -> None:
    rendered["services"][service_key] = rendered["services"].pop("app")

    violations = validate_compose(rendered, service_key, _policy())
    assert violations[0].startswith("services[")
    assert "service key" in violations[0]


@pytest.mark.parametrize(
    "key_factory",
    [
        pytest.param(lambda: 10**10_000, id="extreme-integer"),
        pytest.param(lambda: "a" * 10_000, id="huge-string"),
        pytest.param(lambda: "bad/" * 2_500, id="huge-malformed-string"),
        pytest.param(lambda: ("tuple", 1), id="tuple"),
        pytest.param(lambda: _HashableMapping({"key": "value"}), id="mapping"),
    ],
)
def test_extreme_service_keys_have_bounded_deterministic_violations(
    rendered: dict[str, object], key_factory: Callable[[], object]
) -> None:
    key = key_factory()
    rendered["services"][key] = {"image": _IMAGE}

    first = validate_compose(rendered, "app", _policy())
    second = validate_compose(rendered, "app", _policy())

    assert first == second
    assert first[0].startswith("services[invalid-")
    assert "service key" in first[0]
    assert len(first[0]) < 200


def test_real_privileged_host_sidecar_is_rendered_and_rejected(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    overlay = tmp_path / "sidecar.yml"
    overlay.write_text(
        """\
services:
  sidecar:
    image: ${SIDECAR_IMAGE}
    privileged: true
    network_mode: host
    user: "0"
    volumes:
      - /:/host
      - /var/run/docker.sock:/var/run/docker.sock
""",
        encoding="utf-8",
    )
    env = os.environ | {
        "GENEFOUNDRY_IMAGE": _IMAGE,
        "SIDECAR_IMAGE": _IMAGE,
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
    sidecar = actual["services"]["sidecar"]

    assert sidecar["privileged"] is True
    assert sidecar["network_mode"] == "host"
    assert sidecar["user"] == "0"
    assert any("docker.sock" in mount["source"] for mount in sidecar["volumes"])
    violations = validate_compose(actual, "genefoundry-router")
    assert violations[0].startswith("services.sidecar:")
    assert "explicit role-specific policy" in violations[0]
