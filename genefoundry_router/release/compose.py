"""Validate the effective production Docker Compose security contract."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from genefoundry_router.release.compose_checks import (
    is_mapping,
    is_sequence,
    validate_allowed_fields,
    validate_depends_on,
    validate_limits,
    validate_logging,
    validate_storage,
)
from genefoundry_router.release.compose_policy import (
    ALLOWED_SERVICE_KEYS,
    ALLOWED_TOP_LEVEL_KEYS,
    AuxiliaryServiceRule,
    ComposePolicy,
    ExternalNetworkRule,
    bounded_key_path,
    is_digest_image,
    is_safe_compose_key,
    parse_duration,
)
from genefoundry_router.release.compose_roles import validate_auxiliary_service

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

__all__ = ["AuxiliaryServiceRule", "ComposePolicy", "ExternalNetworkRule", "validate_compose"]


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
    valid = valid and is_sequence(test) and tuple(test) == policy.healthcheck_test
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
    elif is_sequence(attachments):
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
        internal = definition.get("internal", False)
        if (
            not set(definition).issubset({"name", "driver", "ipam", "internal"})
            or definition.get("name") != f"{project}_{logical}"
            or definition.get("name") in {"host", "none"}
            or (driver is not None and driver != "bridge")
            or not isinstance(ipam, Mapping)
            or bool(ipam)
            or internal is not (logical in policy.internal_networks)
        ):
            violations.append(f"{path}: must be an exact project-managed bridge network")


def _validate_service_set(
    services: Mapping[object, object],
    application_service: str,
    rules: Mapping[str, AuxiliaryServiceRule],
    violations: list[str],
) -> None:
    """Reject every service that is neither the application nor a declared sidecar."""
    entries = list(enumerate(services))
    for index, name in sorted(
        entries, key=lambda entry: bounded_key_path("services", entry[1], entry[0])
    ):
        path = bounded_key_path("services", name, index)
        if not is_safe_compose_key(name):
            violations.append(
                f"{path}: malformed service key; auxiliary services require an explicit "
                "role-specific policy"
            )
        elif name != application_service and name not in rules:
            violations.append(
                f"{path}: auxiliary service requires an explicit role-specific policy"
            )


def _auxiliary_rules(
    application_service: str, policy: ComposePolicy, violations: list[str]
) -> dict[str, AuxiliaryServiceRule]:
    rules: dict[str, AuxiliaryServiceRule] = {}
    for index, rule in enumerate(policy.auxiliary_services):
        path = bounded_key_path("services", rule.name, index)
        if not is_safe_compose_key(rule.name):
            violations.append(f"{path}: auxiliary service name is malformed")
        elif rule.name == application_service:
            violations.append(f"{path}: auxiliary rule must not name the application service")
        elif rule.name in rules:
            violations.append(f"{path}: auxiliary service is declared more than once")
        else:
            rules[rule.name] = rule
    return rules


def _validate_reachability(
    services: Mapping[object, object],
    application_service: str,
    rules: Mapping[str, AuxiliaryServiceRule],
    violations: list[str],
) -> None:
    """A declared sidecar nothing depends on would never be started, or gate anything."""
    depended: set[str] = set()
    for name in (application_service, *rules):
        service = services.get(name)
        depends_on = service.get("depends_on") if isinstance(service, Mapping) else None
        if isinstance(depends_on, Mapping):
            depended.update(key for key in depends_on if isinstance(key, str))
    for name in sorted(set(rules) - depended):
        violations.append(f"services.{name}: a declared auxiliary service must be depended upon")


def validate_compose(
    rendered: Mapping[str, object],
    application_service: str,
    policy: ComposePolicy | None = None,
) -> tuple[str, ...]:
    """Return deterministic violations for an effective rendered Compose object."""
    violations: list[str] = []
    policy = policy or ComposePolicy()
    if not is_mapping(rendered):
        return ("services: rendered Compose document must be a mapping",)
    validate_allowed_fields(rendered, ALLOWED_TOP_LEVEL_KEYS, "compose", violations)
    services = rendered.get("services")
    if not isinstance(services, Mapping):
        violations.append("services: must be a mapping")
        return tuple(dict.fromkeys(violations))
    rules = _auxiliary_rules(application_service, policy, violations)
    _validate_service_set(services, application_service, rules, violations)
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
    validate_allowed_fields(service, ALLOWED_SERVICE_KEYS, prefix, violations)

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
    if "ports" in service and (not is_sequence(ports) or bool(ports)):
        violations.append(f"{prefix}.ports: published host ports are forbidden")
    if service.get("read_only") is not True:
        violations.append(f"{prefix}.read_only: must be true")
    if service.get("init") is not True:
        violations.append(f"{prefix}.init: must be true")
    expose = service.get("expose")
    approved_ports = {str(policy.tcp_port), f"{policy.tcp_port}/tcp"}
    if not is_sequence(expose) or not any(
        isinstance(port, str) and port in approved_ports for port in expose
    ):
        violations.append(f"{prefix}.expose: TCP port {policy.tcp_port} must be declared")
    cap_drop = service.get("cap_drop")
    if not is_sequence(cap_drop) or list(cap_drop) != ["ALL"]:
        violations.append(f"{prefix}.cap_drop: must be exactly [ALL]")
    cap_add = service.get("cap_add")
    if "cap_add" in service and (not is_sequence(cap_add) or bool(cap_add)):
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
        if name in service and (not is_sequence(value) or bool(value)):
            violations.append(f"{prefix}.{name}: must be absent or an exact empty sequence")
    if "runtime" in service:
        violations.append(f"{prefix}.runtime: runtime override must be absent")
    if "user" in service:
        violations.append(f"{prefix}.user: user override must be absent")

    security_opt = service.get("security_opt")
    if (
        not is_sequence(security_opt)
        or len(security_opt) != 1
        or security_opt[0] != "no-new-privileges:true"
    ):
        violations.append(f"{prefix}.security_opt: must be exactly [no-new-privileges:true]")

    validate_storage(rendered, service, prefix, project, policy, violations)
    validate_limits(service, prefix, policy, violations)
    validate_logging(service, prefix, policy, violations)
    _validate_healthcheck(service, prefix, policy, violations)
    validate_depends_on(service, prefix, rules, violations)
    _validate_networks(rendered, service, prefix, project, policy, violations)
    for rule in sorted(rules.values(), key=lambda entry: entry.name):
        violations.extend(validate_auxiliary_service(rendered, rule, project, policy, rules))
    _validate_reachability(services, application_service, rules, violations)
    return tuple(dict.fromkeys(violations))
