"""Validate the effective production Docker Compose security contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from posixpath import normpath
from typing import TypeGuard

from genefoundry_router.release.compose_policy import (
    ALLOWED_SERVICE_KEYS,
    ALLOWED_TOP_LEVEL_KEYS,
    ComposePolicy,
    ExternalNetworkRule,
    bounded_key_path,
    is_cpu_limit,
    is_digest_image,
    is_memory_limit,
    is_pid_limit,
    is_positive_digits,
    is_safe_compose_key,
    parse_duration,
    parse_size,
)

_HOST_MODES = ("network_mode", "pid", "ipc", "uts", "userns_mode", "cgroup")
_RESOURCE_OVERRIDES = (
    "blkio_config",
    "cpu_count",
    "cpu_percent",
    "cpu_period",
    "cpu_quota",
    "cpu_rt_period",
    "cpu_rt_runtime",
    "cpu_shares",
    "cpus",
    "cpuset",
    "mem_limit",
    "mem_reservation",
    "mem_swappiness",
    "memswap_limit",
    "oom_kill_disable",
    "oom_score_adj",
    "pids_limit",
    "shm_size",
)

__all__ = ["ComposePolicy", "ExternalNetworkRule", "validate_compose"]


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _is_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping)


def _validate_allowed_fields[Key](
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


def _validate_volume(
    mount: object,
    path: str,
    volumes: object,
    project: str,
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
        _validate_volume_definition(fields[0], volumes, project, violations)
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
    if mount_type != "volume" or not isinstance(mount.get("source"), str):
        violations.append(f"{path}: host bind and unsupported mount types are forbidden")
        return False
    if not _volume_name_is_controlled(mount["source"], volumes):
        violations.append(f"{path}: volume source must name a declared top-level volume")
        return False
    _validate_volume_definition(mount["source"], volumes, project, violations)
    return not bool(mount.get("read_only"))


def _validate_volume_definition(
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


def _mount_target(mount: object) -> object:
    if isinstance(mount, str):
        fields = mount.split(":")
        return fields[1] if len(fields) >= 2 else None
    return mount.get("target") if isinstance(mount, Mapping) else None


def _validate_writable_target(
    target: object,
    path: str,
    policy: ComposePolicy,
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
        or target not in policy.writable_targets
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


def _validate_storage(
    rendered: Mapping[str, object],
    service: Mapping[str, object],
    prefix: str,
    project: str,
    policy: ComposePolicy,
    violations: list[str],
) -> None:
    writable_mount = False
    seen_targets: list[str] = []
    mounts = service.get("volumes", [])
    if not _is_sequence(mounts):
        violations.append(f"{prefix}.volumes: must be a list of controlled mounts")
    else:
        for index, mount in enumerate(mounts):
            path = f"{prefix}.volumes[{index}]"
            target_ok = _validate_writable_target(
                _mount_target(mount), path, policy, seen_targets, violations
            )
            writable_mount |= target_ok and _validate_volume(
                mount,
                path,
                rendered.get("volumes"),
                project,
                violations,
            )

    tmpfs = service.get("tmpfs")
    has_tmp = False
    if not _is_sequence(tmpfs):
        violations.append(f"{prefix}.tmpfs: must explicitly mount /tmp as tmpfs")
    else:
        for index, mount in enumerate(tmpfs):
            path = f"{prefix}.tmpfs[{index}]"
            target = _validate_tmpfs_entry(mount, path, policy, violations)
            if target is None:
                continue
            if not _validate_writable_target(target, path, policy, seen_targets, violations):
                continue
            if target == "/tmp":  # noqa: S108 -- required container tmpfs mount
                has_tmp = True
            writable_mount = True
        if not has_tmp:
            violations.append(f"{prefix}.tmpfs: must explicitly mount /tmp as tmpfs")

    if not writable_mount:
        violations.append(
            f"{prefix}.volumes: read-only rootfs requires explicit controlled writable storage"
        )


def _validate_limits(
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


def _validate_logging(
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


def _validate_healthcheck(
    service: Mapping[str, object],
    prefix: str,
    policy: ComposePolicy,
    violations: list[str],
) -> None:
    health = service.get("healthcheck")
    allowed = {"test", "interval", "timeout", "retries", "start_period", "start_interval"}
    valid = isinstance(health, Mapping) and set(health).issubset(allowed)
    test = health.get("test") if isinstance(health, Mapping) else None
    valid = valid and _is_sequence(test) and tuple(test) == policy.healthcheck_test
    if isinstance(health, Mapping):
        interval = parse_duration(health.get("interval"))
        timeout = parse_duration(health.get("timeout"))
        valid = (
            valid
            and interval is not None
            and (policy.health_interval_min <= interval <= policy.health_interval_max)
        )
        valid = (
            valid
            and timeout is not None
            and interval is not None
            and (policy.health_timeout_min <= timeout <= min(policy.health_timeout_max, interval))
        )
        retries = health.get("retries")
        valid = (
            valid
            and isinstance(retries, int)
            and not isinstance(retries, bool)
            and policy.health_retries_min <= retries <= policy.health_retries_max
        )
        if "start_period" in health:
            start_period = parse_duration(health.get("start_period"))
            valid = (
                valid
                and start_period is not None
                and Decimal(0) <= start_period <= policy.health_start_period_max
            )
        if "start_interval" in health:
            start_interval = parse_duration(health.get("start_interval"))
            valid = (
                valid
                and start_interval is not None
                and interval is not None
                and (
                    policy.health_start_interval_min
                    <= start_interval
                    <= min(policy.health_start_interval_max, interval)
                )
            )
    if not valid:
        violations.append(f"{prefix}.healthcheck: enabled well-formed healthcheck is required")


def _validate_networks(
    rendered: Mapping[str, object],
    service: Mapping[str, object],
    prefix: str,
    project: str,
    policy: ComposePolicy,
    violations: list[str],
) -> None:
    external = {rule.logical_name: rule for rule in policy.external_networks}
    expected = set(policy.approved_networks) | set(external)
    attachments = service.get("networks")
    if isinstance(attachments, Mapping):
        attached = set(attachments)
        valid_attachments = all(
            isinstance(name, str)
            and (options is None or (isinstance(options, Mapping) and not options))
            for name, options in attachments.items()
        )
    elif _is_sequence(attachments):
        valid_attachments = all(isinstance(name, str) for name in attachments)
        attached = set(attachments) if valid_attachments else set()
        valid_attachments = valid_attachments and len(attached) == len(attachments)
    else:
        attached = set()
        valid_attachments = False
    if not valid_attachments or attached != expected:
        violations.append(f"{prefix}.networks: must exactly match approved network attachments")

    definitions = rendered.get("networks")
    if not isinstance(definitions, Mapping) or set(definitions) != expected:
        violations.append("networks: top-level definitions must exactly match approved networks")
        return
    for logical in sorted(expected):
        definition = definitions.get(logical)
        path = f"networks.{logical}"
        if not isinstance(definition, Mapping):
            violations.append(f"{path}: definition must be a mapping")
            continue
        if logical in external:
            rule = external[logical]
            if (
                set(definition) != {"name", "external"}
                or definition.get("name") != rule.actual_name
                or definition.get("external") is not True
                or rule.actual_name in {"host", "none"}
            ):
                violations.append(f"{path}: external network identity does not match policy")
            elif rule.requires_driver_inspection:
                violations.append(
                    f"{path}: deployment inspection required for {rule.actual_name} network driver"
                )
            continue
        driver = definition.get("driver")
        ipam = definition.get("ipam", {})
        if (
            not set(definition).issubset({"name", "driver", "ipam"})
            or definition.get("name") != f"{project}_{logical}"
            or definition.get("name") in {"host", "none"}
            or (driver is not None and driver != "bridge")
            or not isinstance(ipam, Mapping)
            or bool(ipam)
        ):
            violations.append(f"{path}: must be an exact project-managed bridge network")


def validate_compose(
    rendered: Mapping[str, object],
    application_service: str,
    policy: ComposePolicy | None = None,
) -> tuple[str, ...]:
    """Return deterministic violations for an effective rendered Compose object."""
    violations: list[str] = []
    policy = policy or ComposePolicy()
    if not _is_mapping(rendered):
        return ("services: rendered Compose document must be a mapping",)
    _validate_allowed_fields(rendered, ALLOWED_TOP_LEVEL_KEYS, "compose", violations)
    services = rendered.get("services")
    if not isinstance(services, Mapping):
        violations.append("services: must be a mapping")
        return tuple(dict.fromkeys(violations))
    service_entries = list(enumerate(services))
    for index, name in sorted(
        service_entries, key=lambda entry: bounded_key_path("services", entry[1], entry[0])
    ):
        path = bounded_key_path("services", name, index)
        if not is_safe_compose_key(name):
            violations.append(
                f"{path}: malformed service key; auxiliary services require an explicit "
                "role-specific policy"
            )
        elif name != application_service:
            violations.append(
                f"{path}: auxiliary service requires an explicit role-specific policy"
            )
    if not is_safe_compose_key(application_service):
        violations.append("services[application]: requested application service name is malformed")
        return tuple(dict.fromkeys(violations))
    prefix = f"services.{application_service}"
    project = policy.expected_project or application_service
    if rendered.get("name") != project:
        violations.append(f"name: must equal expected project {project}")
    service = services.get(application_service)
    if not isinstance(service, Mapping):
        violations.append(f"{prefix}: application service must exist as a mapping")
        return tuple(dict.fromkeys(violations))
    _validate_allowed_fields(service, ALLOWED_SERVICE_KEYS, prefix, violations)

    if "build" in service:
        violations.append(f"{prefix}.build")
        violations.append(f"{prefix}.build: remove effective production build configuration")
    if "container_name" in service:
        violations.append(f"{prefix}.container_name: fixed container identity must be absent")
    image = service.get("image")
    if not is_digest_image(image):
        violations.append(
            f"{prefix}.image: use a fully qualified untagged repository digest ending in "
            "@sha256:<64 lowercase hex>"
        )
    if service.get("pull_policy") != policy.pull_policy:
        violations.append(f"{prefix}.pull_policy: must be {policy.pull_policy}")
    restart = service.get("restart")
    if not isinstance(restart, str) or restart not in policy.allowed_restart:
        violations.append(f"{prefix}.restart: must be one of the approved policies")
    ports = service.get("ports")
    if "ports" in service and (not _is_sequence(ports) or bool(ports)):
        violations.append(f"{prefix}.ports: published host ports are forbidden")
    if service.get("read_only") is not True:
        violations.append(f"{prefix}.read_only: must be true")
    if service.get("init") is not True:
        violations.append(f"{prefix}.init: must be true")
    expose = service.get("expose")
    approved_ports = {str(policy.tcp_port), f"{policy.tcp_port}/tcp"}
    if not _is_sequence(expose) or not any(
        isinstance(port, str) and port in approved_ports for port in expose
    ):
        violations.append(f"{prefix}.expose: TCP port {policy.tcp_port} must be declared")
    cap_drop = service.get("cap_drop")
    if not _is_sequence(cap_drop) or list(cap_drop) != ["ALL"]:
        violations.append(f"{prefix}.cap_drop: must be exactly [ALL]")
    cap_add = service.get("cap_add")
    if "cap_add" in service and (not _is_sequence(cap_add) or bool(cap_add)):
        violations.append(f"{prefix}.cap_add: must be absent or an exact empty sequence")
    if "privileged" in service and service.get("privileged") is not False:
        violations.append(f"{prefix}.privileged: must be absent or exactly false")
    if "use_api_socket" in service and service.get("use_api_socket") is not False:
        violations.append(
            f"{prefix}.use_api_socket: engine API socket access must be absent or exactly false"
        )
    for name in ("command", "entrypoint"):
        if service.get(name) is not None:
            violations.append(f"{prefix}.{name}: image process override is forbidden")
    for name in ("configs", "secrets"):
        if name in service:
            violations.append(f"{prefix}.{name}: alternate file mounts are forbidden")
        if name in rendered:
            violations.append(f"{name}: top-level definitions are forbidden")
    for name in _HOST_MODES:
        if name in service:
            violations.append(f"{prefix}.{name}: namespace override must be absent")
    for name in _RESOURCE_OVERRIDES:
        if name in service:
            violations.append(f"{prefix}.{name}: service-level resource override must be absent")
    for name in ("group_add", "devices", "gpus", "device_cgroup_rules", "volumes_from"):
        value = service.get(name)
        if name in service and (not _is_sequence(value) or bool(value)):
            violations.append(f"{prefix}.{name}: must be absent or an exact empty sequence")
    if "runtime" in service:
        violations.append(f"{prefix}.runtime: runtime override must be absent")
    if "user" in service:
        violations.append(f"{prefix}.user: user override must be absent")

    security_opt = service.get("security_opt")
    if (
        not _is_sequence(security_opt)
        or len(security_opt) != 1
        or security_opt[0] != "no-new-privileges:true"
    ):
        violations.append(f"{prefix}.security_opt: must be exactly [no-new-privileges:true]")

    _validate_storage(rendered, service, prefix, project, policy, violations)
    _validate_limits(service, prefix, policy, violations)
    _validate_logging(service, prefix, policy, violations)
    _validate_healthcheck(service, prefix, policy, violations)
    _validate_networks(rendered, service, prefix, project, policy, violations)
    return tuple(dict.fromkeys(violations))
