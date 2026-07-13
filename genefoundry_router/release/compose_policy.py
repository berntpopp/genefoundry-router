"""Immutable policy and bounded parsers for rendered Compose validation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from posixpath import normpath
from typing import Literal, TypeGuard

_CANONICAL_DECIMAL = re.compile(r"(?:0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)")
_LOG_SIZE = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)?[bBkKmMgG]")
_ASCII_DIGITS = re.compile(r"[0-9]+")
_DURATION = re.compile(r"(0|[1-9][0-9]*(?:\.[0-9]+)?)(ns|us|ms|s|m|h)")
_REPOSITORY_COMPONENT = r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
_DIGEST_IMAGE = re.compile(rf"^[^/@\s]+(?:/{_REPOSITORY_COMPONENT})+@sha256:[0-9a-f]{{64}}$")
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_DEFAULT_TMP = "/tmp"  # noqa: S108 -- required container tmpfs target
_ROUTER_HEALTHCHECK = (
    "CMD",
    "sh",
    "-c",
    'curl -f -H "Host: $${GF_HEALTHCHECK_HOST}" http://localhost:8000/health',
)
_MAX_COMPOSE_KEY_LENGTH = 128

_MAX_ARGV_ENTRIES = 64
_MAX_ARGV_ENTRY_LENGTH = 1024

ALLOWED_TOP_LEVEL_KEYS = frozenset({"name", "services", "volumes", "networks"})
ALLOWED_SERVICE_KEYS = frozenset(
    {
        "cap_drop",
        "command",
        "depends_on",
        "deploy",
        "entrypoint",
        "environment",
        "expose",
        "healthcheck",
        "image",
        "init",
        "logging",
        "networks",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "volumes",
    }
)

#: An init sidecar runs a one-shot data process. It never serves traffic, so it
#: carries no ``expose`` and no ``healthcheck``; Compose gates the application on
#: ``service_completed_successfully`` instead.
ALLOWED_INIT_SERVICE_KEYS = frozenset(
    {
        "user",
        "cap_drop",
        "command",
        "depends_on",
        "deploy",
        "entrypoint",
        "environment",
        "image",
        "init",
        "logging",
        "network_mode",
        "networks",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "volumes",
    }
)

#: A database sidecar is long-running, reachable only on an approved project
#: network, and gated by its own healthcheck. It never declares ``network_mode``.
ALLOWED_DATABASE_SERVICE_KEYS = frozenset(
    {
        "user",
        "cap_drop",
        "command",
        "depends_on",
        "deploy",
        "entrypoint",
        "environment",
        "expose",
        "healthcheck",
        "image",
        "init",
        "logging",
        "networks",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "tmpfs",
        "volumes",
    }
)

AuxiliaryRole = Literal["init", "database"]
EgressPolicy = Literal["denied", "internal", "approved-networks"]

ROLE_SERVICE_KEYS: Mapping[str, frozenset[str]] = {
    "init": ALLOWED_INIT_SERVICE_KEYS,
    "database": ALLOWED_DATABASE_SERVICE_KEYS,
}

#: The Compose dependency condition each role must be waited on with.
ROLE_DEPENDS_ON_CONDITION: Mapping[str, str] = {
    "init": "service_completed_successfully",
    "database": "service_healthy",
}

#: Host paths that may never back a sidecar bind mount, even read-only.
DENIED_BIND_SOURCE_PREFIXES = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib64",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var/lib/docker",
    "/var/run",
)


def is_safe_compose_key(value: object) -> TypeGuard[str]:
    """Return whether a key is bounded and safe to include in a diagnostic path."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= _MAX_COMPOSE_KEY_LENGTH
        and value.isascii()
        and all(character.isalnum() or character in "._-" for character in value)
    )


def _compose_key_kind(value: object) -> str:
    if isinstance(value, str):
        return "string"
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, tuple):
        return "tuple"
    if isinstance(value, Mapping):
        return "mapping"
    return "object"


def bounded_key_path(scope: str, value: object, index: int) -> str:
    """Build a deterministic bounded path without stringifying unsafe key values."""
    if is_safe_compose_key(value):
        return f"{scope}.{value}" if scope else value
    return f"{scope}[invalid-{_compose_key_kind(value)}#{index}]"


@dataclass(frozen=True)
class ExternalNetworkRule:
    """Exact external network identity requiring runtime driver inspection."""

    logical_name: str
    actual_name: str
    requires_driver_inspection: bool = True


@dataclass(frozen=True)
class AuxiliaryServiceRule:
    """One explicitly approved auxiliary service and the role policy it must satisfy.

    A rule authorizes a *role*, never a bare name: every field the role permits is
    validated, so declaring a service can never smuggle in an unhardened sidecar.
    """

    name: str
    role: AuxiliaryRole
    egress: EgressPolicy = "denied"
    #: Exact writable mount targets (named volumes and tmpfs) this sidecar may hold.
    writable_targets: frozenset[str] = frozenset()
    #: The image's own non-root uid:gid, when the sidecar must skip a root entrypoint.
    user: str | None = None
    #: Exact read-only mount targets, including reviewed pre-seeded artifact binds.
    read_only_targets: frozenset[str] = frozenset()
    #: Required healthcheck argv; the ``database`` role must declare one.
    healthcheck_test: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposePolicy:
    """Immutable v1 policy for one application service and its declared sidecars.

    Auxiliary ``init`` and ``database`` roles are validated by
    :mod:`genefoundry_router.release.compose_roles`; approving a service name alone
    never authorizes an unvalidated sidecar.
    """

    expected_project: str | None = None
    writable_targets: frozenset[str] = frozenset({_DEFAULT_TMP, "/data"})
    auxiliary_services: tuple[AuxiliaryServiceRule, ...] = ()
    internal_networks: frozenset[str] = field(default_factory=frozenset)
    tcp_port: int = 8000
    max_cpus: Decimal = Decimal("64")
    max_memory_bytes: int = 256 * 1024**3
    max_pids: int = 4096
    max_tmpfs_bytes: int = 1024**3
    max_log_size_bytes: int = 256 * 1024**2
    max_log_files: int = 10
    allowed_restart: frozenset[str] = frozenset({"on-failure"})
    pull_policy: str = "missing"
    approved_networks: frozenset[str] = frozenset({"default"})
    external_networks: tuple[ExternalNetworkRule, ...] = ()
    healthcheck_test: tuple[str, ...] = _ROUTER_HEALTHCHECK
    health_interval_min: Decimal = Decimal(5)
    health_interval_max: Decimal = Decimal(300)
    health_timeout_min: Decimal = Decimal(1)
    health_timeout_max: Decimal = Decimal(60)
    health_retries_min: int = 1
    health_retries_max: int = 10
    health_start_period_max: Decimal = Decimal(600)
    health_start_interval_min: Decimal = Decimal(1)
    health_start_interval_max: Decimal = Decimal(60)


def is_normalized_absolute_path(value: object) -> TypeGuard[str]:
    """Return whether a value is a safe normalized absolute POSIX path."""
    return (
        isinstance(value, str)
        and value.startswith("/")
        and not value.startswith("//")
        and value == normpath(value)
        and "\\" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def is_argv(value: object) -> TypeGuard[Sequence[str]]:
    """Return whether a value is a bounded explicit argv list with no shell text."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        return False
    if not 0 < len(value) <= _MAX_ARGV_ENTRIES:
        return False
    return all(
        isinstance(entry, str)
        and 0 < len(entry) <= _MAX_ARGV_ENTRY_LENGTH
        and entry.isascii()
        and not any(ord(character) < 32 or ord(character) == 127 for character in entry)
        and not any(character in entry for character in ";|&$`\n\\<>")
        for entry in value
    )


def is_safe_bind_source(value: object) -> TypeGuard[str]:
    """Return whether a host path may back a read-only sidecar bind mount."""
    if not is_normalized_absolute_path(value) or value == "/":
        return False
    if "docker.sock" in value:
        return False
    return not any(
        value == prefix or value.startswith(f"{prefix}/") for prefix in DENIED_BIND_SOURCE_PREFIXES
    )


def is_pid_limit(value: object, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum


def is_cpu_limit(value: object, maximum: Decimal) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 < value <= maximum
    if isinstance(value, float):
        return isfinite(value) and 0 < Decimal(str(value)) <= maximum
    return (
        isinstance(value, str)
        and len(value) <= 32
        and _CANONICAL_DECIMAL.fullmatch(value) is not None
        and Decimal(value) <= maximum
    )


def is_positive_digits(value: str) -> bool:
    return _ASCII_DIGITS.fullmatch(value) is not None and "1" <= max(value) <= "9"


def is_memory_limit(value: object, maximum: int) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return 0 < value <= maximum
    return (
        isinstance(value, str)
        and len(value) <= 32
        and is_positive_digits(value)
        and int(value) <= maximum
    )


def parse_size(value: str, *, unit_required: bool) -> Decimal | None:
    if len(value) > 32:
        return None
    if not unit_required and _ASCII_DIGITS.fullmatch(value) is not None:
        return Decimal(value) if is_positive_digits(value) else None
    if _LOG_SIZE.fullmatch(value) is None:
        return None
    units = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    return Decimal(value[:-1]) * units[value[-1].lower()]


def parse_duration(value: object) -> Decimal | None:
    if (
        not isinstance(value, str)
        or len(value) > 32
        or (match := _DURATION.fullmatch(value)) is None
    ):
        return None
    factors = {
        "ns": Decimal("0.000000001"),
        "us": Decimal("0.000001"),
        "ms": Decimal("0.001"),
        "s": Decimal(1),
        "m": Decimal(60),
        "h": Decimal(3600),
    }
    return Decimal(match.group(1)) * factors[match.group(2)]


def is_digest_image(value: object) -> bool:
    if not isinstance(value, str) or _DIGEST_IMAGE.fullmatch(value) is None:
        return False
    registry = value.split("/", 1)[0]
    if ":" in registry:
        registry, port = registry.rsplit(":", 1)
        if (
            not port.isascii()
            or not port.isdecimal()
            or len(port) > 5
            or not 1 <= int(port) <= 65535
        ):
            return False
    if registry == "localhost":
        return True
    if len(registry) > 253:
        return False
    labels = registry.split(".")
    return len(labels) >= 2 and all(_HOST_LABEL.fullmatch(label) for label in labels)
