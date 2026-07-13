"""Isolation and image-identity contracts for rendered Compose models."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from genefoundry_router.release.compose import (
    ComposePolicy,
    ExternalNetworkRule,
    validate_compose,
)

_IMAGE = f"ghcr.io/acme/app@sha256:{'a' * 64}"
_HEALTH = ("CMD", "true")


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
                "volumes": [{"type": "volume", "source": "data", "target": "/data", "volume": {}}],
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
                    "start_period": "10s",
                },
            }
        },
        "volumes": {"data": {"name": "app_data"}},
        "networks": {"default": {"name": "app_default", "ipam": {}}},
    }


def _policy(**changes: object) -> ComposePolicy:
    return ComposePolicy(healthcheck_test=_HEALTH, **changes)


@pytest.mark.parametrize(
    ("service_networks", "networks"),
    [
        ({"host": None}, {"host": {"name": "host", "external": True}}),
        ({"default": {"aliases": ["escape"]}}, {"default": {"name": "app_default"}}),
        ({"default": None, "extra": None}, {"default": {"name": "app_default"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "host"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "default"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "overlay"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "macvlan"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "ipvlan"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "null"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": "custom"}}),
        ({"default": None}, {"default": {"name": "app_default", "driver": []}}),
        ({"default": None}, {"default": {"name": "app_default", "driver_opts": {"x": "y"}}}),
        ({"default": None}, {"default": {"name": "custom"}}),
        ("default", {"default": {"name": "app_default"}}),
    ],
)
def test_network_topology_fails_closed(
    rendered: dict[str, object], service_networks: object, networks: object
) -> None:
    rendered["services"]["app"]["networks"] = service_networks
    rendered["networks"] = networks

    assert "network" in " ".join(validate_compose(rendered, "app", _policy()))


def test_declared_external_proxy_requires_runtime_inspection(
    rendered: dict[str, object],
) -> None:
    rendered["services"]["app"]["networks"] = {"proxy": None}
    rendered["networks"] = {"proxy": {"name": "npm_proxy", "external": True}}
    policy = _policy(
        approved_networks=frozenset(),
        external_networks=(ExternalNetworkRule("proxy", "npm_proxy", True),),
    )

    violations = " ".join(validate_compose(rendered, "app", policy))
    assert "deployment inspection required" in violations
    assert "npm_proxy" in violations


@pytest.mark.parametrize("driver", [None, "bridge"])
def test_managed_network_accepts_only_builtin_bridge_identity(
    rendered: dict[str, object], driver: str | None
) -> None:
    if driver is not None:
        rendered["networks"]["default"]["driver"] = driver

    assert validate_compose(rendered, "app", _policy()) == ()


def test_real_default_network_driver_is_preserved_and_rejected(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "default-driver.yml"
    compose.write_text(
        """\
name: app
services:
  app:
    image: ${IMAGE}
    networks: [default]
networks:
  default:
    driver: default
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _IMAGE},
    )
    actual = json.loads(completed.stdout)

    assert actual["networks"]["default"]["driver"] == "default"
    assert "networks.default" in " ".join(validate_compose(actual, "app", _policy()))


@pytest.mark.parametrize(
    "network_yaml",
    [
        "external: true\n    name: host",
        "driver: host",
        "driver: macvlan",
    ],
)
def test_real_unsafe_network_render_is_rejected(tmp_path: Path, network_yaml: str) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "network.yml"
    compose.write_text(
        f"""\
name: app
services:
  app:
    image: ${{IMAGE}}
    networks: [default]
networks:
  default:
    {network_yaml}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _IMAGE},
    )

    assert "network" in " ".join(validate_compose(json.loads(completed.stdout), "app", _policy()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", ["sh", "-c", "id"]),
        ("command", "id"),
        ("entrypoint", ["/bin/sh"]),
        ("entrypoint", ""),
        ("configs", []),
        ("configs", ["runtime-config"]),
        ("configs", {}),
        ("secrets", []),
        ("secrets", ["runtime-secret"]),
        ("secrets", None),
    ],
)
def test_image_process_and_alternate_mount_overrides_are_forbidden(
    rendered: dict[str, object], field: str, value: object
) -> None:
    rendered["services"]["app"][field] = value

    assert f"services.app.{field}" in " ".join(validate_compose(rendered, "app", _policy()))


@pytest.mark.parametrize("field", ["configs", "secrets"])
def test_top_level_config_and_secret_definitions_are_forbidden(
    rendered: dict[str, object], field: str
) -> None:
    rendered[field] = {"payload": {"content": "escape"}}

    assert field in " ".join(validate_compose(rendered, "app", _policy()))


@pytest.mark.parametrize(
    ("service_fragment", "top_fragment", "expected"),
    [
        ('command: ["sh", "-c", "id"]', "", "services.app.command"),
        ('entrypoint: ["/bin/sh"]', "", "services.app.entrypoint"),
        ("configs: [payload]", "configs:\n  payload:\n    content: escape", "configs"),
        (
            "configs: [payload]",
            "configs:\n  payload:\n    file: ${PAYLOAD_FILE}",
            "configs",
        ),
        (
            "secrets: [payload]",
            "secrets:\n  payload:\n    file: ${PAYLOAD_FILE}",
            "secrets",
        ),
    ],
)
def test_real_process_and_alternate_mount_render_is_rejected(
    tmp_path: Path, service_fragment: str, top_fragment: str, expected: str
) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    payload = tmp_path / "payload"
    payload.write_text("escape", encoding="utf-8")
    compose = tmp_path / "identity.yml"
    compose.write_text(
        f"""\
name: app
services:
  app:
    image: ${{IMAGE}}
    {service_fragment}
{top_fragment}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _IMAGE, "PAYLOAD_FILE": str(payload)},
    )

    assert expected in " ".join(validate_compose(json.loads(completed.stdout), "app", _policy()))


@pytest.mark.parametrize(
    "deploy",
    [
        [],
        {},
        {"resources": {"reservations": {"memory": "1024"}}},
        {
            "resources": {
                "limits": {"cpus": 1, "memory": "1073741824", "pids": 256},
                "reservations": {"devices": [{"capabilities": ["gpu"]}]},
            }
        },
        {
            "resources": {
                "limits": {
                    "cpus": 1,
                    "memory": "1073741824",
                    "pids": 256,
                    "unexpected": 1,
                }
            }
        },
        {
            "resources": {"limits": {"cpus": 1, "memory": "1073741824", "pids": 256}},
            "replicas": 2,
        },
        {
            "resources": {"limits": {"cpus": 1, "memory": "1073741824", "pids": 256}},
            "placement": {"constraints": ["node.role==manager"]},
        },
    ],
)
def test_deploy_shape_is_closed(rendered: dict[str, object], deploy: object) -> None:
    rendered["services"]["app"]["deploy"] = deploy

    assert "services.app.deploy" in " ".join(validate_compose(rendered, "app", _policy()))


@pytest.mark.parametrize(
    "field",
    [
        "memswap_limit",
        "mem_swappiness",
        "oom_kill_disable",
        "oom_score_adj",
        "shm_size",
        "mem_limit",
        "mem_reservation",
        "pids_limit",
        "cpus",
        "cpu_count",
        "cpu_percent",
        "cpu_period",
        "cpu_quota",
        "cpu_rt_runtime",
        "cpu_rt_period",
        "cpu_shares",
        "cpuset",
        "blkio_config",
    ],
)
def test_service_resource_override_must_be_absent(rendered: dict[str, object], field: str) -> None:
    rendered["services"]["app"][field] = None

    assert f"services.app.{field}" in " ".join(validate_compose(rendered, "app", _policy()))


@pytest.mark.parametrize(
    ("service_fragment", "expected"),
    [
        ("memswap_limit: -1", "memswap_limit"),
        ("oom_kill_disable: true", "oom_kill_disable"),
        ("shm_size: 10gb", "shm_size"),
        (
            "deploy:\n      resources:\n        reservations:\n          devices:\n"
            "            - capabilities: [gpu]",
            "deploy",
        ),
    ],
)
def test_real_alternate_resource_control_is_rejected(
    tmp_path: Path, service_fragment: str, expected: str
) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "resources.yml"
    compose.write_text(
        f"""\
name: app
services:
  app:
    image: ${{IMAGE}}
    {service_fragment}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _IMAGE},
    )

    assert expected in " ".join(validate_compose(json.loads(completed.stdout), "app", _policy()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("test", ["CMD", "false"]),
        ("test", ["CMD-SHELL", "true"]),
        ("interval", "1ns"),
        ("interval", "4s"),
        ("interval", "301s"),
        ("interval", "05s"),
        ("interval", "1e3s"),
        ("interval", "999999999999999999999h"),
        ("timeout", "0s"),
        ("timeout", "31s"),
        ("timeout", "61s"),
        ("retries", 0),
        ("retries", 11),
        ("retries", True),
        ("start_period", "601s"),
        ("start_period", "-1s"),
        ("start_interval", "1ns"),
        ("start_interval", "31s"),
        ("start_interval", "61s"),
    ],
)
def test_healthcheck_is_exact_and_bounded(
    rendered: dict[str, object], field: str, value: object
) -> None:
    rendered["services"]["app"]["healthcheck"][field] = value

    assert "services.app.healthcheck" in " ".join(validate_compose(rendered, "app", _policy()))


def test_zero_start_period_is_explicitly_allowed(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["healthcheck"]["start_period"] = "0s"

    assert "services.app.healthcheck" not in " ".join(validate_compose(rendered, "app", _policy()))


@pytest.mark.parametrize(
    "health_fragment",
    [
        'test: [CMD, "false"]',
        "interval: 1ns",
        "timeout: 1h",
        "retries: 100",
    ],
)
def test_real_unsafe_healthcheck_render_is_rejected(tmp_path: Path, health_fragment: str) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "health.yml"
    compose.write_text(
        f"""\
name: app
services:
  app:
    image: ${{IMAGE}}
    healthcheck:
      {health_fragment}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _IMAGE},
    )

    assert "services.app.healthcheck" in " ".join(
        validate_compose(json.loads(completed.stdout), "app", _policy())
    )


@pytest.mark.parametrize("value", [None, "app", "shared-container"])
def test_container_name_must_be_absent(rendered: dict[str, object], value: object) -> None:
    rendered["services"]["app"]["container_name"] = value

    assert "services.app.container_name" in " ".join(validate_compose(rendered, "app", _policy()))


def test_real_compose_projects_keep_container_identity_isolated(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "project.yml"
    compose.write_text(
        """\
services:
  app:
    image: ${IMAGE}
""",
        encoding="utf-8",
    )

    rendered_projects = []
    for project in ("alpha", "beta"):
        completed = subprocess.run(  # noqa: S603
            [
                docker,
                "compose",
                "-p",
                project,
                "-f",
                str(compose),
                "config",
                "--format",
                "json",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ | {"IMAGE": _IMAGE},
        )
        rendered_projects.append(json.loads(completed.stdout))

    assert [model["name"] for model in rendered_projects] == ["alpha", "beta"]
    assert all("container_name" not in model["services"]["app"] for model in rendered_projects)


def test_real_fixed_container_name_is_rejected(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "container-name.yml"
    compose.write_text(
        """\
name: app
services:
  app:
    image: ${IMAGE}
    container_name: shared-container
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _IMAGE},
    )

    assert "services.app.container_name" in " ".join(
        validate_compose(json.loads(completed.stdout), "app", _policy())
    )
