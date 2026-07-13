"""Security contract for the effective production Compose model."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable

import pytest

from genefoundry_router.release.compose import validate_compose

IMAGE = f"ghcr.io/berntpopp/genefoundry-router@sha256:{'a' * 64}"
_TMP = "/tmp"  # noqa: S108 -- container mount path under test
_HEALTH = [
    "CMD",
    "sh",
    "-c",
    'curl -f -H "Host: $${GF_HEALTHCHECK_HOST}" http://localhost:8000/health',
]


@pytest.fixture
def valid_render() -> dict[str, object]:
    return {
        "name": "genefoundry",
        "services": {
            "genefoundry": {
                "image": IMAGE,
                "pull_policy": "missing",
                "restart": "on-failure",
                "read_only": True,
                "init": True,
                "expose": ["8000"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "networks": {"default": None},
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],  # noqa: S108
                "volumes": ["data:/data"],
                "deploy": {
                    "resources": {"limits": {"cpus": 1, "memory": "1073741824", "pids": 256}}
                },
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "50m", "max-file": "5"},
                },
                "healthcheck": {
                    "test": _HEALTH,
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                    "start_period": "10s",
                },
            }
        },
        "volumes": {"data": {"name": "genefoundry_data"}},
        "networks": {"default": {"name": "genefoundry_default", "ipam": {}}},
    }


def test_valid_production_render_has_no_violations(valid_render: dict[str, object]) -> None:
    assert validate_compose(valid_render, "genefoundry") == ()


def test_production_rejects_effective_build(valid_render: dict[str, object]) -> None:
    service = valid_render["services"]["genefoundry"]
    service["build"] = {"context": "."}
    assert "services.genefoundry.build" in validate_compose(valid_render, "genefoundry")


def test_production_requires_digest(valid_render: dict[str, object]) -> None:
    valid_render["services"]["genefoundry"]["image"] = "ghcr.io/berntpopp/genefoundry-router:0.6.4"
    assert "digest" in " ".join(validate_compose(valid_render, "genefoundry"))


def test_production_requires_fully_qualified_registry(valid_render: dict[str, object]) -> None:
    valid_render["services"]["genefoundry"]["image"] = f"team/router@sha256:{'a' * 64}"

    assert "services.genefoundry.image" in " ".join(validate_compose(valid_render, "genefoundry"))


@pytest.mark.parametrize(
    "image",
    [
        f"ghcr.io/acme/app:latest@sha256:{'a' * 64}",
        f"ghcr.io/acme/app:v1.2.3@sha256:{'a' * 64}",
        f"ghcr.io/acme:team/app@sha256:{'a' * 64}",
        f"ghcr.io/Acme/app@sha256:{'a' * 64}",
        f"ghcr.io/acme//app@sha256:{'a' * 64}",
        f"ghcr.io/acme/app@sha256:{'A' * 64}",
    ],
)
def test_digest_image_rejects_tags_and_malformed_repository_paths(
    valid_render: dict[str, object], image: str
) -> None:
    _set_service_value(valid_render, "image", image)

    assert "services.genefoundry.image" in " ".join(validate_compose(valid_render, "genefoundry"))


@pytest.mark.parametrize(
    "image",
    [
        f"registry.example:5000/acme/app@sha256:{'a' * 64}",
        f"localhost:5000/acme/app@sha256:{'a' * 64}",
    ],
)
def test_digest_image_allows_registry_ports(valid_render: dict[str, object], image: str) -> None:
    _set_service_value(valid_render, "image", image)

    assert validate_compose(valid_render, "genefoundry") == ()


def _set_service_value(rendered: dict[str, object], key: str, value: object) -> None:
    rendered["services"]["genefoundry"][key] = value


def _delete_service_value(rendered: dict[str, object], key: str) -> None:
    del rendered["services"]["genefoundry"][key]


def _set_limit(rendered: dict[str, object], name: str, value: object) -> None:
    rendered["services"]["genefoundry"]["deploy"]["resources"]["limits"][name] = value


def _set_log_option(rendered: dict[str, object], name: str, value: object) -> None:
    rendered["services"]["genefoundry"]["logging"]["options"][name] = value


@pytest.mark.parametrize(
    ("mutate", "expected_path"),
    [
        (lambda item: _set_service_value(item, "ports", ["8000:8000"]), "ports"),
        (lambda item: _set_service_value(item, "ports", {}), "ports"),
        (lambda item: _set_service_value(item, "read_only", False), "read_only"),
        (lambda item: _set_service_value(item, "init", False), "init"),
        (lambda item: _delete_service_value(item, "expose"), "expose"),
        (lambda item: _set_service_value(item, "cap_drop", []), "cap_drop"),
        (lambda item: _set_service_value(item, "cap_add", ["SYS_ADMIN"]), "cap_add"),
        (lambda item: _set_service_value(item, "privileged", True), "privileged"),
        (lambda item: _set_service_value(item, "privileged", "true"), "privileged"),
        (lambda item: _set_service_value(item, "network_mode", "host"), "network_mode"),
        (lambda item: _set_service_value(item, "pid", "host"), "pid"),
        (lambda item: _set_service_value(item, "ipc", "host"), "ipc"),
        (lambda item: _set_service_value(item, "uts", "host"), "uts"),
        (lambda item: _set_service_value(item, "userns_mode", "host"), "userns_mode"),
        (lambda item: _set_service_value(item, "devices", ["/dev/sda:/dev/sda"]), "devices"),
        (lambda item: _set_service_value(item, "user", "root"), "user"),
        (
            lambda item: _set_service_value(item, "security_opt", []),
            "security_opt",
        ),
        (
            lambda item: _set_service_value(item, "security_opt", ["seccomp:unconfined"]),
            "security_opt",
        ),
        (
            lambda item: _set_service_value(
                item, "volumes", ["/var/run/docker.sock:/var/run/docker.sock"]
            ),
            "volumes[0]",
        ),
        (
            lambda item: _set_service_value(item, "volumes", ["./state:/data"]),
            "volumes[0]",
        ),
        (
            lambda item: _set_service_value(item, "volumes", ["undeclared:/data"]),
            "volumes[0]",
        ),
        (
            lambda item: _set_service_value(
                item,
                "volumes",
                [
                    {
                        "type": "volume",
                        "source": "data",
                        "target": "/data",
                        "read_only": "false",
                    }
                ],
            ),
            "volumes[0]",
        ),
        (lambda item: _set_service_value(item, "tmpfs", ["relative"]), "tmpfs[0]"),
        (lambda item: _delete_service_value(item, "tmpfs"), "tmpfs"),
        (
            lambda item: item["services"]["genefoundry"]["deploy"]["resources"]["limits"].pop(
                "pids"
            ),
            "deploy.resources.limits.pids",
        ),
        (
            lambda item: item["services"]["genefoundry"]["deploy"]["resources"]["limits"].pop(
                "cpus"
            ),
            "deploy.resources.limits.cpus",
        ),
        (
            lambda item: item["services"]["genefoundry"]["deploy"]["resources"]["limits"].pop(
                "memory"
            ),
            "deploy.resources.limits.memory",
        ),
        (
            lambda item: item["services"]["genefoundry"]["logging"].update({"driver": "local"}),
            "logging.driver",
        ),
        (
            lambda item: item["services"]["genefoundry"]["logging"]["options"].pop("max-size"),
            "logging.options.max-size",
        ),
        (
            lambda item: item["services"]["genefoundry"]["logging"]["options"].pop("max-file"),
            "logging.options.max-file",
        ),
    ],
    ids=[
        "inherited-ports",
        "malformed-ports",
        "writable-rootfs",
        "missing-init",
        "missing-internal-expose",
        "capabilities-not-dropped",
        "capability-added",
        "privileged",
        "malformed-privileged",
        "host-network",
        "host-pid",
        "host-ipc",
        "host-uts",
        "host-userns",
        "devices",
        "root-user",
        "missing-no-new-privileges",
        "unconfined-security-profile",
        "docker-socket-bind",
        "host-bind",
        "undeclared-volume",
        "malformed-mount-read-only",
        "invalid-tmpfs",
        "missing-tmpfs",
        "missing-pid-limit",
        "missing-cpu-limit",
        "missing-memory-limit",
        "unbounded-log-driver",
        "missing-log-size",
        "missing-log-count",
    ],
)
def test_adversarial_service_values_are_rejected(
    valid_render: dict[str, object],
    mutate: Callable[[dict[str, object]], object],
    expected_path: str,
) -> None:
    mutate(valid_render)

    assert f"services.genefoundry.{expected_path}" in " ".join(
        validate_compose(valid_render, "genefoundry")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pids", True),
        ("pids", 1.5),
        ("pids", 0),
        ("pids", -1),
        ("pids", "256"),
        ("cpus", True),
        ("cpus", 0),
        ("cpus", -0.1),
        ("cpus", float("nan")),
        ("cpus", float("inf")),
        ("cpus", "NaN"),
        ("cpus", "1e3"),
        ("memory", True),
        ("memory", 0),
        ("memory", -1),
        ("memory", "+1024"),
        ("memory", "1G"),
        ("memory", "unlimited"),
    ],
)
def test_resource_limits_use_field_specific_rendered_types(
    valid_render: dict[str, object], field: str, value: object
) -> None:
    _set_limit(valid_render, field, value)

    assert f"services.genefoundry.deploy.resources.limits.{field}" in " ".join(
        validate_compose(valid_render, "genefoundry")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max-file", 5),
        ("max-file", "0"),
        ("max-file", "-1"),
        ("max-file", "1.5"),
        ("max-file", "unlimited"),
        ("max-size", 50),
        ("max-size", "0"),
        ("max-size", "-1m"),
        ("max-size", "50"),
        ("max-size", "unlimited"),
        ("max-size", "garbage"),
    ],
)
def test_logging_bounds_require_positive_finite_docker_values(
    valid_render: dict[str, object], field: str, value: object
) -> None:
    _set_log_option(valid_render, field, value)

    assert f"services.genefoundry.logging.options.{field}" in " ".join(
        validate_compose(valid_render, "genefoundry")
    )


@pytest.mark.parametrize(
    "entry",
    [
        _TMP,
        f"{_TMP}:noexec,nosuid,size=64m",
        f"{_TMP}:rw,nosuid,size=64m",
        f"{_TMP}:rw,noexec,size=64m",
        f"{_TMP}:rw,noexec,nosuid",
        f"{_TMP}:rw,noexec,nosuid,size=0",
        f"{_TMP}:rw,noexec,nosuid,size=-1m",
        f"{_TMP}:rw,noexec,nosuid,size=unlimited",
        f"{_TMP}:rw,noexec,nosuid,size=garbage",
        f"{_TMP}:rw,ro,noexec,nosuid,size=64m",
        f"{_TMP}:rw,noexec,exec,nosuid,size=64m",
        f"{_TMP}:rw,noexec,nosuid,suid,size=64m",
        f"{_TMP}:rw,noexec,exec,nosuid,suid,size=64m",
        f"{_TMP}:rw,rw,noexec,nosuid,size=64m",
        f"{_TMP}:rw,noexec,noexec,nosuid,size=64m",
        f"{_TMP}:rw,noexec,nosuid,nosuid,size=64m",
        f"{_TMP}/../var:rw,noexec,nosuid,size=64m",
        f"{_TMP}\\escape:rw,noexec,nosuid,size=64m",
        f"{_TMP}\n/escape:rw,noexec,nosuid,size=64m",
    ],
)
def test_tmpfs_requires_normalized_hardened_bounded_entry(
    valid_render: dict[str, object], entry: str
) -> None:
    _set_service_value(valid_render, "tmpfs", [entry])

    assert "services.genefoundry.tmpfs[0]" in " ".join(
        validate_compose(valid_render, "genefoundry")
    )


@pytest.mark.parametrize("target", [_TMP, "/var/cache/app"])
def test_long_syntax_tmpfs_mounts_are_rejected(
    valid_render: dict[str, object], target: str
) -> None:
    _set_service_value(
        valid_render,
        "volumes",
        [{"type": "tmpfs", "target": target, "tmpfs": {"size": 67_108_864}}],
    )

    assert "services.genefoundry.volumes[0]" in " ".join(
        validate_compose(valid_render, "genefoundry")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gpus", "all"),
        ("gpus", [{"driver": "nvidia", "count": 1}]),
        ("device_cgroup_rules", ["c 1:3 mr"]),
    ],
)
def test_device_access_variants_are_rejected(
    valid_render: dict[str, object], field: str, value: object
) -> None:
    _set_service_value(valid_render, field, value)

    assert f"services.genefoundry.{field}" in " ".join(
        validate_compose(valid_render, "genefoundry")
    )


@pytest.mark.parametrize(
    "user",
    [0, "0", "00", "000:1000", "0:1000", "root", "root:1000", "ROOT:1000", True, 1.5, [], {}],
)
def test_root_and_malformed_user_variants_are_rejected(
    valid_render: dict[str, object], user: object
) -> None:
    _set_service_value(valid_render, "user", user)

    assert "services.genefoundry.user" in " ".join(validate_compose(valid_render, "genefoundry"))


@pytest.mark.parametrize("user", [10001, "10001", "10001:10001", "app:10001"])
def test_user_override_is_forbidden_even_when_nonroot(
    valid_render: dict[str, object], user: object
) -> None:
    _set_service_value(valid_render, "user", user)

    assert "services.genefoundry.user" in " ".join(validate_compose(valid_render, "genefoundry"))


def test_extreme_numeric_strings_are_rejected_without_throwing(
    valid_render: dict[str, object],
) -> None:
    huge_zero = "0" * 10_000
    _set_limit(valid_render, "cpus", 10**10_000)
    _set_limit(valid_render, "memory", huge_zero)
    _set_log_option(valid_render, "max-file", huge_zero)
    _set_service_value(valid_render, "user", huge_zero)

    violations = " ".join(validate_compose(valid_render, "genefoundry"))
    assert "deploy.resources.limits.memory" in violations
    assert "logging.options.max-file" in violations
    assert "services.genefoundry.user" in violations


@pytest.mark.parametrize(
    ("rendered", "expected"),
    [
        ({}, "services"),
        ({"services": []}, "services"),
        ({"services": {}}, "services.genefoundry"),
        ({"services": {"genefoundry": []}}, "services.genefoundry"),
    ],
)
def test_malformed_compose_is_reported_without_throwing(
    rendered: dict[str, object], expected: str
) -> None:
    assert expected in " ".join(validate_compose(rendered, "genefoundry"))


def test_violations_are_deterministic(valid_render: dict[str, object]) -> None:
    service = valid_render["services"]["genefoundry"]
    service.update({"privileged": True, "ports": ["8000:8000"], "read_only": False})

    first = validate_compose(valid_render, "genefoundry")
    assert first == validate_compose(valid_render, "genefoundry")
    assert first == tuple(dict.fromkeys(first))


def test_real_production_overlay_resets_development_build_and_ports() -> None:
    env = os.environ | {
        "GENEFOUNDRY_IMAGE": IMAGE,
        "GF_ALLOWED_HOSTS": "router.example.test",
        "GF_HEALTHCHECK_HOST": "router.example.test",
    }
    docker = shutil.which("docker")
    assert docker is not None
    completed = subprocess.run(  # noqa: S603 -- fixed executable and argument vector
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
    rendered = json.loads(completed.stdout)
    service = rendered["services"]["genefoundry-router"]

    assert "build" not in service
    assert not service.get("ports")
    assert service["image"] == IMAGE
    assert service["init"] is True
    assert service["expose"] == ["8000"]
    assert service["pull_policy"] == "missing"
    assert validate_compose(rendered, "genefoundry-router") == ()
