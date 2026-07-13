"""Render the no-build smoke stack for each centrally implemented smoke profile.

The override pins every application-image service to the exact image built once in
this CI run, re-applies the hardening invariants, and drops published ports onto
loopback. Declared ``init`` sidecars run the same built image; a ``database``
sidecar keeps its own digest-pinned upstream image.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import yaml

from genefoundry_router.release.models import ReleaseConfig

__all__ = ["SMOKE_ENV_LINE", "parse_smoke_env", "render_smoke_override"]

#: A preparation script may only emit plain ``KEY=VALUE`` assignments. The file is
#: parsed, never sourced, so it can never introduce inline shell into the stack.
SMOKE_ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]{0,127})=([^\r\n]{0,4096})$")

_MAX_SMOKE_ENV_ENTRIES = 64
_ROUTER_ENVIRONMENT = {
    "GF_AUTH_MODE": "none",
    "GF_ALLOW_INSECURE": "true",
    "GF_ALLOWED_HOSTS": "localhost,127.0.0.1",
    "GF_HEALTHCHECK_HOST": "localhost",
    "GF_DRIFT_MODE": "warn",
}
_TMPFS = "/tmp:rw,noexec,nosuid,size=64m,mode=1777"  # noqa: S108
_LIMITS: dict[str, object] = {"cpus": "1.0", "memory": "2G", "pids": 256}
_LOGGING: dict[str, object] = {
    "driver": "json-file",
    "options": {"max-size": "10m", "max-file": "2"},
}


class _Reset:
    """The Compose ``!reset`` tag, which removes an inherited base-file value."""


class _Override:
    """The Compose ``!override`` tag, which replaces rather than merges a value."""

    def __init__(self, value: object) -> None:
        self.value = value


class _ComposeDumper(yaml.SafeDumper):
    """A dumper that can emit the Compose merge-control tags."""


def _represent_reset(dumper: yaml.SafeDumper, data: _Reset) -> yaml.Node:
    return dumper.represent_scalar("!reset", "null")


def _represent_override(dumper: yaml.SafeDumper, data: _Override) -> yaml.Node:
    node = dumper.represent_data(data.value)
    node.tag = "!override"
    return node


_ComposeDumper.add_representer(_Reset, _represent_reset)
_ComposeDumper.add_representer(_Override, _represent_override)


def parse_smoke_env(text: str) -> dict[str, str]:
    """Parse a preparation script's ``KEY=VALUE`` output, rejecting anything else."""
    environment: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        match = SMOKE_ENV_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"smoke environment line {number} is not a bounded KEY=VALUE pair")
        key, value = match.groups()
        if key in environment:
            raise ValueError(f"smoke environment key {key} is assigned more than once")
        environment[key] = value
        if len(environment) > _MAX_SMOKE_ENV_ENTRIES:
            raise ValueError("smoke environment declares too many variables")
    return environment


def _hardening(image: str | None) -> dict[str, Any]:
    service: dict[str, Any] = {
        "build": _Reset(),
        "env_file": _Override([]),
        "read_only": True,
        "tmpfs": _Override([_TMPFS]),
        "cap_drop": _Override(["ALL"]),
        "security_opt": _Override(["no-new-privileges:true"]),
        "init": True,
        "deploy": {"resources": {"limits": dict(_LIMITS)}},
        "logging": dict(_LOGGING),
    }
    if image is not None:
        service["image"] = image
        service["pull_policy"] = "never"
    return service


def _application(
    config: ReleaseConfig,
    image: str,
    host_port: int,
    environment: Mapping[str, str],
    url_env_keys: Iterable[str],
) -> dict[str, Any]:
    service = _hardening(image)
    service["environment"] = {
        **_ROUTER_ENVIRONMENT,
        **dict.fromkeys(url_env_keys, "http://127.0.0.1:9/mcp"),
        **environment,
    }
    service["ports"] = _Override([f"127.0.0.1:{host_port}:{config.service.container_port}"])
    return service


def _auxiliary(role: str, image: str, environment: Mapping[str, str]) -> dict[str, Any]:
    # An init sidecar runs the exact image built in this run; a database sidecar keeps
    # its own digest-pinned upstream image and must therefore stay pullable.
    service = _hardening(image if role == "init" else None)
    if role == "init":
        service["restart"] = "no"
    if environment:
        service["environment"] = dict(environment)
    return service


def render_smoke_override(
    config: ReleaseConfig,
    *,
    image: str,
    host_port: int,
    environment: Mapping[str, str] | None = None,
    url_env_keys: Sequence[str] = (),
) -> str:
    """Render the Compose override that runs the exact built image for this profile."""
    if (
        config.smoke.profile in {"immutable-bundle", "postgres-bundle"}
        and not config.service.auxiliary
    ):
        raise ValueError(f"smoke profile {config.smoke.profile} requires a declared sidecar")
    prepared = dict(environment or {})
    services: dict[str, Any] = {
        config.service.name: _application(config, image, host_port, prepared, url_env_keys)
    }
    for auxiliary in config.service.auxiliary:
        services[auxiliary.name] = _auxiliary(auxiliary.role, image, prepared)
    document = yaml.dump(
        {"services": services},
        Dumper=_ComposeDumper,
        default_flow_style=False,
        sort_keys=True,
    )
    return str(document)
