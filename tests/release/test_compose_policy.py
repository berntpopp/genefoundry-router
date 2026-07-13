"""Advanced and real-render tests for the production Compose policy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from genefoundry_router.release.compose import ComposePolicy, validate_compose

_DIGEST = f"ghcr.io/acme/app@sha256:{'a' * 64}"
_TMP = "/tmp"  # noqa: S108 -- container mount target under test
_HEALTH = [
    "CMD",
    "sh",
    "-c",
    'curl -f -H "Host: $${GF_HEALTHCHECK_HOST}" http://localhost:8000/health',
]


@pytest.fixture
def rendered() -> dict[str, object]:
    return {
        "name": "app",
        "services": {
            "app": {
                "image": _DIGEST,
                "pull_policy": "missing",
                "restart": "on-failure",
                "read_only": True,
                "init": True,
                "expose": ["8000"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "networks": {"default": None},
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],  # noqa: S108
                "volumes": [{"type": "volume", "source": "data", "target": "/data", "volume": {}}],
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
        "volumes": {"data": {"name": "app_data"}},
        "networks": {"default": {"name": "app_default", "ipam": {}}},
    }


@pytest.mark.parametrize(
    "definition",
    [
        {},
        {"name": "data"},
        {"name": "shared_data", "external": True},
        {"name": "app_data", "driver": "local"},
        {"name": "app_data", "driver_opts": {"type": "none", "o": "bind"}},
        {"name": "app_data", "labels": {"owner": "test"}},
        [],
    ],
)
def test_named_volume_definition_must_be_project_scoped(
    rendered: dict[str, object], definition: object
) -> None:
    rendered["volumes"]["data"] = definition

    assert "volumes.data" in " ".join(validate_compose(rendered, "app"))


def test_real_bind_backed_named_volume_is_rejected(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """\
name: bindtest
services:
  app:
    image: ${IMAGE}
    volumes:
      - data:/data
volumes:
  data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${BIND_SOURCE}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _DIGEST, "BIND_SOURCE": str(tmp_path)},
    )
    actual = json.loads(completed.stdout)

    assert actual["services"]["app"]["volumes"][0]["type"] == "volume"
    assert "volumes.data" in " ".join(
        validate_compose(actual, "app", ComposePolicy(expected_project="bindtest"))
    )


@pytest.mark.parametrize("target", ["/", "/app", "/safe/../etc", "/etc", "/bad\\path"])
def test_writable_volume_target_must_be_exactly_policy_approved(
    rendered: dict[str, object], target: str
) -> None:
    rendered["services"]["app"]["volumes"][0]["target"] = target

    assert "services.app.volumes[0]" in " ".join(validate_compose(rendered, "app"))


def test_extra_tmpfs_target_is_rejected(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["tmpfs"].append("/etc:rw,noexec,nosuid,size=64m")

    assert "services.app.tmpfs[1]" in " ".join(validate_compose(rendered, "app"))


def test_duplicate_writable_target_is_rejected(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["volumes"].append(
        {"type": "volume", "source": "data", "target": "/data", "volume": {}}
    )

    assert "services.app.volumes[1]" in " ".join(validate_compose(rendered, "app"))


def test_overlapping_policy_approved_targets_are_rejected(rendered: dict[str, object]) -> None:
    rendered["volumes"]["cache"] = {"name": "app_cache"}
    rendered["services"]["app"]["volumes"].append(
        {"type": "volume", "source": "cache", "target": "/data/cache", "volume": {}}
    )
    policy = ComposePolicy(writable_targets=frozenset({_TMP, "/data", "/data/cache"}))

    assert "services.app.volumes[1]" in " ".join(validate_compose(rendered, "app", policy))


def test_policy_can_approve_leaf_specific_writable_target(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["volumes"][0]["target"] = "/app/data"
    policy = ComposePolicy(writable_targets=frozenset({_TMP, "/app/data"}))

    assert validate_compose(rendered, "app", policy) == ()


@pytest.mark.parametrize(
    ("field", "valid", "invalid"),
    [
        ("pids", 4096, 4097),
        ("cpus", "64", "64.1"),
        ("memory", str(256 * 1024**3), str(256 * 1024**3 + 1)),
    ],
)
def test_resource_ceiling_boundary(
    rendered: dict[str, object], field: str, valid: object, invalid: object
) -> None:
    limits = rendered["services"]["app"]["deploy"]["resources"]["limits"]
    limits[field] = valid
    assert validate_compose(rendered, "app") == ()

    limits[field] = invalid
    assert f"services.app.deploy.resources.limits.{field}" in " ".join(
        validate_compose(rendered, "app")
    )


@pytest.mark.parametrize(
    ("field", "valid", "invalid"),
    [("max-size", "256m", "257m"), ("max-file", "10", "11")],
)
def test_logging_ceiling_boundary(
    rendered: dict[str, object], field: str, valid: str, invalid: str
) -> None:
    options = rendered["services"]["app"]["logging"]["options"]
    options[field] = valid
    assert validate_compose(rendered, "app") == ()

    options[field] = invalid
    assert f"services.app.logging.options.{field}" in " ".join(validate_compose(rendered, "app"))


def test_tmpfs_ceiling_boundary(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["tmpfs"] = [f"{_TMP}:rw,noexec,nosuid,size=1g"]
    assert validate_compose(rendered, "app") == ()

    rendered["services"]["app"]["tmpfs"] = [f"{_TMP}:rw,noexec,nosuid,size=1025m"]
    assert "services.app.tmpfs[0]" in " ".join(validate_compose(rendered, "app"))


def test_enormous_rendered_limit_is_rejected_without_conversion(
    rendered: dict[str, object],
) -> None:
    rendered["services"]["app"]["deploy"]["resources"]["limits"]["memory"] = "9" * 10_000

    assert "deploy.resources.limits.memory" in " ".join(validate_compose(rendered, "app"))


def test_policy_ceiling_is_overrideable(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["deploy"]["resources"]["limits"]["pids"] = 101

    assert "deploy.resources.limits.pids" in " ".join(
        validate_compose(rendered, "app", ComposePolicy(max_pids=100))
    )


def test_real_rendered_extreme_limits_are_rejected(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "extreme.yml"
    compose.write_text(
        """\
name: app
services:
  app:
    image: ${IMAGE}
    read_only: true
    tmpfs: [/tmp:rw,noexec,nosuid,size=1025m]
    deploy:
      resources:
        limits: {cpus: "65", memory: 257G, pids: 4097}
    logging:
      driver: json-file
      options: {max-size: 257m, max-file: "11"}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _DIGEST},
    )
    violations = " ".join(validate_compose(json.loads(completed.stdout), "app"))

    assert "limits.cpus" in violations
    assert "limits.memory" in violations
    assert "limits.pids" in violations
    assert "tmpfs[0]" in violations
    assert "logging.options.max-size" in violations
    assert "logging.options.max-file" in violations


@pytest.mark.parametrize(
    "security_opt",
    [
        [],
        ["no-new-privileges:false"],
        ["no-new-privileges:true", "no-new-privileges:true"],
        ["no-new-privileges:true", "label:disable"],
        ["no-new-privileges:true", "seccomp:custom.json"],
        ["no-new-privileges:true", "apparmor:custom"],
        ["no-new-privileges:true", "seccomp:unconfined"],
        ["no-new-privileges:true", 7],
        "no-new-privileges:true",
        {},
    ],
)
def test_security_options_are_closed_and_exact(
    rendered: dict[str, object], security_opt: object
) -> None:
    rendered["services"]["app"]["security_opt"] = security_opt

    assert "services.app.security_opt" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("group_add", ["root"]),
        ("group_add", {}),
        ("devices", {}),
        ("gpus", None),
        ("device_cgroup_rules", ""),
        ("volumes_from", "other"),
        ("runtime", "runc"),
        ("runtime", ""),
    ],
)
def test_runtime_group_and_device_overrides_fail_closed(
    rendered: dict[str, object], field: str, value: object
) -> None:
    rendered["services"]["app"][field] = value

    assert f"services.app.{field}" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize(
    "field", ["group_add", "devices", "gpus", "device_cgroup_rules", "volumes_from"]
)
def test_unapproved_override_sequences_reject_even_when_empty(
    rendered: dict[str, object], field: str
) -> None:
    rendered["services"]["app"][field] = []

    assert f"services.app.{field}" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize("value", [None, {}, "", "SYS_ADMIN", ["SYS_ADMIN"]])
def test_cap_add_must_be_absent_or_exact_empty_sequence(
    rendered: dict[str, object], value: object
) -> None:
    rendered["services"]["app"]["cap_add"] = value

    assert "services.app.cap_add" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("network_mode", "bridge"),
        ("pid", None),
        ("ipc", []),
        ("uts", {}),
        ("userns_mode", "private"),
        ("cgroup", "private"),
    ],
)
def test_namespace_override_fields_must_be_absent(
    rendered: dict[str, object], field: str, value: object
) -> None:
    rendered["services"]["app"][field] = value

    assert f"services.app.{field}" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize("name", [None, "wrong", "", [], {}])
def test_project_identity_must_match_policy(rendered: dict[str, object], name: object) -> None:
    if name is None:
        rendered.pop("name")
    else:
        rendered["name"] = name

    assert "name" in " ".join(validate_compose(rendered, "app"))


def test_expected_project_is_overrideable(rendered: dict[str, object]) -> None:
    rendered["name"] = "deployment"
    rendered["volumes"]["data"]["name"] = "deployment_data"
    rendered["networks"]["default"]["name"] = "deployment_default"

    assert validate_compose(rendered, "app", ComposePolicy(expected_project="deployment")) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pull_policy", None),
        ("pull_policy", "always"),
        ("restart", None),
        ("restart", "unless-stopped"),
        ("restart", 7),
        ("restart", {}),
    ],
)
def test_pull_and_restart_are_closed(
    rendered: dict[str, object], field: str, value: object
) -> None:
    if value is None:
        rendered["services"]["app"].pop(field)
    else:
        rendered["services"]["app"][field] = value

    assert f"services.app.{field}" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize(
    "healthcheck",
    [
        None,
        {},
        {"disable": True},
        {"test": []},
        {"test": "CMD true", "interval": "30s", "timeout": "10s", "retries": 3},
        {"test": ["CMD", "true"], "interval": "0s", "timeout": "10s", "retries": 3},
        {"test": ["CMD", "true"], "interval": "30s", "timeout": "bad", "retries": 3},
        {"test": ["CMD", "true"], "interval": "30s", "timeout": "10s", "retries": 0},
        {
            "test": ["CMD", "true"],
            "interval": "30s",
            "timeout": "10s",
            "retries": 3,
            "start_period": "0s",
        },
    ],
)
def test_healthcheck_is_enabled_and_well_formed(
    rendered: dict[str, object], healthcheck: object
) -> None:
    if healthcheck is None:
        rendered["services"]["app"].pop("healthcheck")
    else:
        rendered["services"]["app"]["healthcheck"] = healthcheck

    assert "services.app.healthcheck" in " ".join(validate_compose(rendered, "app"))


def test_udp_exposure_does_not_satisfy_tcp_port(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["expose"] = ["8000/udp"]

    assert "services.app.expose" in " ".join(validate_compose(rendered, "app"))


def test_tcp_port_is_policy_overrideable(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["expose"] = ["8080/tcp"]

    assert validate_compose(rendered, "app", ComposePolicy(tcp_port=8080)) == ()


@pytest.mark.parametrize(
    "registry",
    [
        "REGISTRY.example",
        "registry..example",
        "-registry.example",
        "registry-.example",
        "registry.example:0",
        "registry.example:65536",
        "registry.example:https",
        "localhost:0",
        ".".join(["a" * 63] * 4),
    ],
)
def test_registry_host_and_port_are_strict(rendered: dict[str, object], registry: str) -> None:
    rendered["services"]["app"]["image"] = f"{registry}/acme/app@sha256:{'a' * 64}"

    assert "services.app.image" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize("port", [1, 65535])
def test_registry_port_boundaries_are_valid(rendered: dict[str, object], port: int) -> None:
    rendered["services"]["app"]["image"] = f"registry.example:{port}/acme/app@sha256:{'a' * 64}"

    assert validate_compose(rendered, "app") == ()


def test_compose_policy_is_immutable() -> None:
    policy = ComposePolicy()

    with pytest.raises(FrozenInstanceError):
        policy.tcp_port = 8080  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, {}, [], "false", 0])
def test_privileged_present_malformed_values_fail_closed(
    rendered: dict[str, object], value: object
) -> None:
    rendered["services"]["app"]["privileged"] = value

    assert "services.app.privileged" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize("value", ["ALL", [], ["ALL", "NET_RAW"], ["ALL", "ALL"], [7]])
def test_cap_drop_must_be_exact(rendered: dict[str, object], value: object) -> None:
    rendered["services"]["app"]["cap_drop"] = value

    assert "services.app.cap_drop" in " ".join(validate_compose(rendered, "app"))


def test_build_violation_is_actionable(rendered: dict[str, object]) -> None:
    rendered["services"]["app"]["build"] = None

    violations = validate_compose(rendered, "app")
    assert "services.app.build" in violations
    assert any("remove" in violation for violation in violations if "build" in violation)


def test_api_socket_must_be_absent_from_exact_render(rendered: dict[str, object]) -> None:
    assert validate_compose(rendered, "app") == ()

    rendered["services"]["app"]["use_api_socket"] = False
    assert "services.app.use_api_socket" in " ".join(validate_compose(rendered, "app"))


@pytest.mark.parametrize("value", [True, "true", "false", 1, 0, None, {}, []])
def test_api_socket_override_fails_closed(rendered: dict[str, object], value: object) -> None:
    rendered["services"]["app"]["use_api_socket"] = value

    assert "services.app.use_api_socket" in " ".join(validate_compose(rendered, "app"))


def test_real_render_preserves_and_rejects_api_socket(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    compose = tmp_path / "api-socket.yml"
    compose.write_text(
        """\
name: app
services:
  app:
    image: ${IMAGE}
    use_api_socket: true
""",
        encoding="utf-8",
    )
    completed = subprocess.run(  # noqa: S603
        [docker, "compose", "-f", str(compose), "config", "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ | {"IMAGE": _DIGEST},
    )
    actual = json.loads(completed.stdout)

    assert actual["services"]["app"]["use_api_socket"] is True
    assert "services.app.use_api_socket" in " ".join(validate_compose(actual, "app"))
