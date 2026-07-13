"""Shared bounded checks for application and auxiliary Compose services."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from posixpath import normpath
from typing import TypeGuard

from genefoundry_router.release.compose_policy import (
    ROLE_DEPENDS_ON_CONDITION,
    AuxiliaryServiceRule,
    ComposePolicy,
    bounded_key_path,
    is_cpu_limit,
    is_memory_limit,
    is_pid_limit,
    is_positive_digits,
    is_safe_bind_source,
    parse_size,
)

__all__ = [
    "has_docker_socket_mount",
    "is_mapping",
    "is_sequence",
    "mount_target",
    "validate_allowed_fields",
    "validate_depends_on",
    "validate_limits",
    "validate_logging",
    "validate_storage",
    "validate_volume_definition",
]

_DEFAULT_TMP = "/tmp"  # noqa: S108 -- required container tmpfs target


def is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def is_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping)


def validate_allowed_fields[Key](
    model: Mapping[Key, object],
    allowed: frozenset[str],
    scope: str,
    violations: list[str],
) -> None:
    entries = list(enumerate(model))
    for index, name in sorted(
        entries, key=lambda entry: bounded_key_path(scope, entry[1], entry[0])
    ):
        if not isinstance(name, str) or name not in allowed:
            path = bounded_key_path(scope, name, index)
            violations.append(f"{path}: unapproved rendered field is forbidden")


def _validate_tmpfs_entry(
    mount: object, path: str, policy: ComposePolicy, violations: list[str]
) -> str | None:
    if not isinstance(mount, str) or ":" not in mount:
        violations.append(f"{path}: hardened tmpfs short syntax is required")
        return None
    target, raw_options = mount.split(":", 1)
    unsafe_target = (
        not target.startswith("/")
        or target.startswith("//")
        or target != normpath(target)
        or "\\" in target
        or any(ord(character) < 32 or ord(character) == 127 for character in target)
    )
    if unsafe_target:
        violations.append(f"{path}: target must be a normalized absolute POSIX path")
        return None
    options = raw_options.split(",")
    required = {"rw", "noexec", "nosuid"}
    size_options = [
        option.removeprefix("size=") for option in options if option.startswith("size=")
    ]
    if (
        not required.issubset(options)
        or {"ro", "exec", "suid"}.intersection(options)
        or len(options) != len(set(options))
        or len(size_options) != 1
        or (parsed_size := parse_size(size_options[0], unit_required=False)) is None
        or parsed_size > policy.max_tmpfs_bytes
    ):
        violations.append(
            f"{path}: tmpfs requires rw,noexec,nosuid and one positive finite size= cap"
        )
        return None
    return target


def _volume_name_is_controlled(source: str, volumes: object) -> bool:
    return (
        bool(source)
        and not source.startswith(("/", ".", "~"))
        and isinstance(volumes, Mapping)
        and source in volumes
    )


def validate_volume_definition(
    source: str, volumes: object, project: str, violations: list[str]
) -> None:
    definition = volumes.get(source) if isinstance(volumes, Mapping) else None
    if (
        not isinstance(definition, Mapping)
        or set(definition) != {"name"}
        or definition.get("name") != f"{project}_{source}"
    ):
        violations.append(
            f"volumes.{source}: must be an exact project-scoped Compose-managed volume"
        )


def _validate_bind(
    mount: Mapping[str, object],
    path: str,
    read_only_targets: frozenset[str],
    violations: list[str],
) -> bool:
    """Validate a reviewed pre-seeded read-only bind and return that it is not writable."""
    target = mount.get("target")
    if mount.get("read_only") is not True or not isinstance(target, str):
        violations.append(f"{path}: a sidecar bind mount must be exactly read_only")
        return False
    if target not in read_only_targets:
        violations.append(f"{path}: bind target must exactly match a declared read-only path")
        return False
    if not is_safe_bind_source(mount.get("source")):
        violations.append(f"{path}: bind source must be a safe absolute non-system host path")
        return False
    if set(mount) - {"type", "source", "target", "read_only", "bind"}:
        violations.append(f"{path}: unapproved bind mount option is forbidden")
        return False
    return False


def _validate_volume(
    mount: object,
    path: str,
    volumes: object,
    project: str,
    read_only_targets: frozenset[str],
    allow_bind: bool,
    violations: list[str],
) -> bool:
    """Validate one mount and return whether it is an explicit writable mount."""
    if isinstance(mount, str):
        if "docker.sock" in mount:
            violations.append(f"{path}: Docker socket mounts are forbidden")
            return False
        fields = mount.split(":")
        if len(fields) < 2 or not _volume_name_is_controlled(fields[0], volumes):
            violations.append(f"{path}: must use a declared named volume, not a host bind")
            return False
        validate_volume_definition(fields[0], volumes, project, violations)
        if not fields[1].startswith("/"):
            violations.append(f"{path}: target must be an absolute container path")
            return False
        options = fields[2].split(",") if len(fields) > 2 else []
        return "ro" not in options

    if not isinstance(mount, Mapping):
        violations.append(f"{path}: mount must be a string or mapping")
        return False
    if "docker.sock" in " ".join(str(value) for value in mount.values()):
        violations.append(f"{path}: Docker socket mounts are forbidden")
        return False
    mount_type = mount.get("type")
    target = mount.get("target")
    if not isinstance(target, str) or not target.startswith("/"):
        violations.append(f"{path}: target must be an absolute container path")
        return False
    if "read_only" in mount and not isinstance(mount.get("read_only"), bool):
        violations.append(f"{path}: read_only must be a boolean")
        return False
    if mount_type == "tmpfs":
        violations.append(f"{path}: tmpfs must use the hardened service tmpfs short syntax")
        return False
    if mount_type == "bind" and allow_bind:
        return _validate_bind(mount, path, read_only_targets, violations)
    if mount_type != "volume" or not isinstance(mount.get("source"), str):
        violations.append(f"{path}: host bind and unsupported mount types are forbidden")
        return False
    if not _volume_name_is_controlled(mount["source"], volumes):
        violations.append(f"{path}: volume source must name a declared top-level volume")
        return False
    validate_volume_definition(mount["source"], volumes, project, violations)
    return not bool(mount.get("read_only"))


def mount_target(mount: object) -> object:
    if isinstance(mount, str):
        fields = mount.split(":")
        return fields[1] if len(fields) >= 2 else None
    return mount.get("target") if isinstance(mount, Mapping) else None


def _validate_mount_target(
    target: object,
    path: str,
    allowed: frozenset[str],
    seen: list[str],
    violations: list[str],
) -> bool:
    if (
        not isinstance(target, str)
        or target == "/"
        or not target.startswith("/")
        or target.startswith("//")
        or target != normpath(target)
        or "\\" in target
        or any(ord(character) < 32 or ord(character) == 127 for character in target)
        or target not in allowed
    ):
        violations.append(f"{path}: target must exactly match an approved writable path")
        return False
    if any(
        target == existing or target.startswith(f"{existing}/") or existing.startswith(f"{target}/")
        for existing in seen
    ):
        violations.append(f"{path}: writable targets must be unique and non-overlapping")
        return False
    seen.append(target)
    return True


def validate_storage(
    rendered: Mapping[str, object],
    service: Mapping[str, object],
    prefix: str,
    project: str,
    policy: ComposePolicy,
    violations: list[str],
    *,
    writable_targets: frozenset[str] | None = None,
    read_only_targets: frozenset[str] | None = None,
    allow_bind: bool = False,
    require_tmp: bool = True,
) -> bool:
    """Validate mounts and prove the read-only rootfs has controlled writable storage.

    Returns whether the service holds at least one writable declared named volume.
    """
    writable = policy.writable_targets if writable_targets is None else writable_targets
    read_only = writable if read_only_targets is None else read_only_targets
    allowed = writable | read_only
    writable_mount = False
    writable_volume = False
    seen_targets: list[str] = []
    mounts = service.get("volumes", [])
    if not is_sequence(mounts):
        violations.append(f"{prefix}.volumes: must be a list of controlled mounts")
    else:
        for index, mount in enumerate(mounts):
            path = f"{prefix}.volumes[{index}]"
            target = mount_target(mount)
            if not _validate_mount_target(target, path, allowed, seen_targets, violations):
                continue
            if not _validate_volume(
                mount,
                path,
                rendered.get("volumes"),
                project,
                read_only,
                allow_bind,
                violations,
            ):
                continue
            if target not in writable:
                violations.append(f"{path}: writable target must be declared writable")
                continue
            writable_mount = True
            writable_volume = True

    tmpfs = service.get("tmpfs")
    has_tmp = False
    if not is_sequence(tmpfs):
        if require_tmp:
            violations.append(f"{prefix}.tmpfs: must explicitly mount /tmp as tmpfs")
    else:
        for index, mount in enumerate(tmpfs):
            path = f"{prefix}.tmpfs[{index}]"
            target = _validate_tmpfs_entry(mount, path, policy, violations)
            if target is None:
                continue
            if not _validate_mount_target(target, path, writable, seen_targets, violations):
                continue
            if target == _DEFAULT_TMP:
                has_tmp = True
            writable_mount = True
        if require_tmp and not has_tmp:
            violations.append(f"{prefix}.tmpfs: must explicitly mount /tmp as tmpfs")

    if not writable_mount:
        violations.append(
            f"{prefix}.volumes: read-only rootfs requires explicit controlled writable storage"
        )
    return writable_volume


def has_docker_socket_mount(service: Mapping[str, object]) -> bool:
    """Detect a Docker socket mount independently of any target policy."""
    mounts = service.get("volumes")
    if not is_sequence(mounts):
        return False
    for mount in mounts:
        if isinstance(mount, str) and "docker.sock" in mount:
            return True
        if isinstance(mount, Mapping) and "docker.sock" in " ".join(
            str(value) for value in mount.values()
        ):
            return True
    return False


def validate_limits(
    service: Mapping[str, object],
    prefix: str,
    policy: ComposePolicy,
    violations: list[str],
) -> None:
    deploy = service.get("deploy")
    resources = deploy.get("resources") if isinstance(deploy, Mapping) else None
    limits = resources.get("limits") if isinstance(resources, Mapping) else None
    placement = deploy.get("placement") if isinstance(deploy, Mapping) else None
    if (
        not isinstance(deploy, Mapping)
        or not set(deploy).issubset({"resources", "placement"})
        or ("placement" in deploy and (not isinstance(placement, Mapping) or bool(placement)))
        or not isinstance(resources, Mapping)
        or set(resources) != {"limits"}
        or not isinstance(limits, Mapping)
        or set(limits) != {"cpus", "memory", "pids"}
    ):
        violations.append(
            f"{prefix}.deploy: must contain only exact resource limits and empty placement"
        )
    limits = limits if isinstance(limits, Mapping) else {}
    validators: dict[str, Callable[[object], bool]] = {
        "pids": lambda value: is_pid_limit(value, policy.max_pids),
        "cpus": lambda value: is_cpu_limit(value, policy.max_cpus),
        "memory": lambda value: is_memory_limit(value, policy.max_memory_bytes),
    }
    for name, validator in validators.items():
        if not validator(limits.get(name)):
            violations.append(
                f"{prefix}.deploy.resources.limits.{name}: positive limit is required"
            )


def validate_logging(
    service: Mapping[str, object],
    prefix: str,
    policy: ComposePolicy,
    violations: list[str],
) -> None:
    logging = service.get("logging")
    if not isinstance(logging, Mapping):
        logging = {}
    if logging.get("driver") != "json-file":
        violations.append(f"{prefix}.logging.driver: must be json-file")
    options = logging.get("options")
    if not isinstance(options, Mapping):
        options = {}
    max_size = options.get("max-size")
    parsed_size = parse_size(max_size, unit_required=True) if isinstance(max_size, str) else None
    if parsed_size is None or parsed_size > policy.max_log_size_bytes:
        violations.append(f"{prefix}.logging.options.max-size: bounded value is required")
    max_file = options.get("max-file")
    if (
        not isinstance(max_file, str)
        or len(max_file) > 32
        or not is_positive_digits(max_file)
        or int(max_file) > policy.max_log_files
    ):
        violations.append(f"{prefix}.logging.options.max-file: bounded value is required")


def validate_depends_on(
    service: Mapping[str, object],
    prefix: str,
    rules: Mapping[str, AuxiliaryServiceRule],
    violations: list[str],
) -> None:
    """A dependency may only wait on a declared sidecar, with the role's exact condition."""
    depends_on = service.get("depends_on")
    if "depends_on" not in service:
        return
    if not isinstance(depends_on, Mapping):
        violations.append(f"{prefix}.depends_on: must be a mapping of declared auxiliary services")
        return
    entries = list(enumerate(depends_on))
    for index, name in sorted(
        entries,
        key=lambda entry: bounded_key_path(f"{prefix}.depends_on", entry[1], entry[0]),
    ):
        path = bounded_key_path(f"{prefix}.depends_on", name, index)
        rule = rules.get(name) if isinstance(name, str) else None
        if rule is None:
            violations.append(f"{path}: may only depend on a declared auxiliary service")
            continue
        options = depends_on[name]
        if (
            not isinstance(options, Mapping)
            or not set(options).issubset({"condition", "required", "restart"})
            or options.get("condition") != ROLE_DEPENDS_ON_CONDITION[rule.role]
            or options.get("required", True) is not True
            or options.get("restart", False) is not False
        ):
            violations.append(
                f"{path}: must be a required dependency on {ROLE_DEPENDS_ON_CONDITION[rule.role]}"
            )
