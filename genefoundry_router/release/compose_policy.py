"""Immutable policy and bounded parsers for rendered Compose validation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import TypeGuard

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

ALLOWED_TOP_LEVEL_KEYS = frozenset({"name", "services", "volumes", "networks"})
ALLOWED_SERVICE_KEYS = frozenset(
    {
        "cap_drop",
        "command",
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
class ComposePolicy:
    """Immutable v1 policy for exactly one effective application service.

    Auxiliary database, init, and proxy roles require future role-specific validation;
    approving a service name alone must never authorize an unvalidated sidecar.
    """

    expected_project: str | None = None
    writable_targets: frozenset[str] = frozenset({_DEFAULT_TMP, "/data"})
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
