"""Role-specific validation for approved auxiliary Compose services.

A sidecar is authorized by a *role*, never by a bare service name. Each role
declares the exact field set it permits and every permitted field is validated,
so an approved name can never smuggle in an unhardened container. Container
hardening (non-root image process, read-only rootfs, ``cap_drop: ALL``,
``no-new-privileges``, bounded resources, no published ports, digest-pinned
image) holds for sidecars exactly as it does for the application.
"""

from __future__ import annotations

from collections.abc import Mapping

from genefoundry_router.release.compose_checks import (
    has_docker_socket_mount,
    is_sequence,
    validate_allowed_fields,
    validate_depends_on,
    validate_limits,
    validate_logging,
    validate_storage,
)
from genefoundry_router.release.compose_policy import (
    ROLE_SERVICE_KEYS,
    AuxiliaryServiceRule,
    ComposePolicy,
    is_argv,
    is_digest_image,
)

__all__ = ["validate_auxiliary_service"]

_HOST_MODES = ("pid", "ipc", "uts", "userns_mode", "cgroup")
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


def _validate_hardening(
    service: Mapping[str, object],
    prefix: str,
    policy: ComposePolicy,
    rule: AuxiliaryServiceRule,
    violations: list[str],
) -> None:
    """Apply the invariants every container in the stack must satisfy."""
    if has_docker_socket_mount(service):
        violations.append(f"{prefix}.volumes: Docker socket mounts are forbidden")
    if "build" in service:
        violations.append(f"{prefix}.build: remove effective production build configuration")
    if "container_name" in service:
        violations.append(f"{prefix}.container_name: fixed container identity must be absent")
    if not is_digest_image(service.get("image")):
        violations.append(
            f"{prefix}.image: use a fully qualified untagged repository digest ending in "
            "@sha256:<64 lowercase hex>"
        )
    if service.get("pull_policy") != policy.pull_policy:
        violations.append(f"{prefix}.pull_policy: must be {policy.pull_policy}")
    if service.get("read_only") is not True:
        violations.append(f"{prefix}.read_only: must be true")
    ports = service.get("ports")
    if "ports" in service and (not is_sequence(ports) or bool(ports)):
        violations.append(f"{prefix}.ports: published host ports are forbidden")
    cap_drop = service.get("cap_drop")
    if not is_sequence(cap_drop) or list(cap_drop) != ["ALL"]:
        violations.append(f"{prefix}.cap_drop: must be exactly [ALL]")
    cap_add = service.get("cap_add")
    if "cap_add" in service and (not is_sequence(cap_add) or bool(cap_add)):
        violations.append(f"{prefix}.cap_add: must be absent or an exact empty sequence")
    security_opt = service.get("security_opt")
    if (
        not is_sequence(security_opt)
        or len(security_opt) != 1
        or security_opt[0] != "no-new-privileges:true"
    ):
        violations.append(f"{prefix}.security_opt: must be exactly [no-new-privileges:true]")
    if "privileged" in service and service.get("privileged") is not False:
        violations.append(f"{prefix}.privileged: must be absent or exactly false")
    if "use_api_socket" in service and service.get("use_api_socket") is not False:
        violations.append(
            f"{prefix}.use_api_socket: engine API socket access must be absent or exactly false"
        )
    declared_user = rule.user
    if "user" in service:
        if declared_user is None:
            violations.append(f"{prefix}.user: user override must be absent unless declared")
        elif service.get("user") != declared_user:
            violations.append(f"{prefix}.user: must be the declared non-root {declared_user}")
    elif declared_user is not None:
        violations.append(f"{prefix}.user: declared non-root {declared_user} must be applied")
    if "runtime" in service:
        violations.append(f"{prefix}.runtime: runtime override must be absent")
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
    for name in ("configs", "secrets"):
        if name in service:
            violations.append(f"{prefix}.{name}: alternate file mounts are forbidden")


def _validate_process(
    service: Mapping[str, object],
    prefix: str,
    violations: list[str],
    *,
    required: bool,
) -> None:
    """An init sidecar must name the exact one-shot argv it runs."""
    declared = False
    for name in ("command", "entrypoint"):
        value = service.get(name)
        if value is None:
            continue
        declared = True
        if not is_argv(value):
            violations.append(
                f"{prefix}.{name}: must be an explicit argv list of bounded shell-free strings"
            )
    if required and not declared:
        violations.append(
            f"{prefix}.command: an init sidecar must declare its explicit argv process"
        )


def _validate_egress(
    rendered: Mapping[str, object],
    service: Mapping[str, object],
    prefix: str,
    rule: AuxiliaryServiceRule,
    policy: ComposePolicy,
    violations: list[str],
) -> None:
    """Bind the sidecar to exactly the reachability its role is approved for."""
    attachments = service.get("networks")
    network_mode = service.get("network_mode")
    if rule.egress == "denied":
        if network_mode != "none":
            violations.append(f"{prefix}.network_mode: egress-denied role requires none")
        if attachments is not None and bool(attachments):
            violations.append(f"{prefix}.networks: egress-denied role must attach no network")
        return

    if "network_mode" in service:
        violations.append(f"{prefix}.network_mode: namespace override must be absent")

    approved = set(policy.approved_networks) | {
        external.logical_name for external in policy.external_networks
    }
    if rule.egress == "internal":
        approved &= set(policy.internal_networks)
    if isinstance(attachments, Mapping):
        attached = set(attachments)
        well_formed = all(
            isinstance(name, str)
            and (options is None or (isinstance(options, Mapping) and not options))
            for name, options in attachments.items()
        )
    elif is_sequence(attachments):
        well_formed = all(isinstance(name, str) for name in attachments)
        attached = set(attachments) if well_formed else set()
        well_formed = well_formed and len(attached) == len(attachments)
    else:
        attached = set()
        well_formed = False
    if not well_formed or not attached or not attached.issubset(approved):
        violations.append(f"{prefix}.networks: must attach only approved role networks")


def _validate_health(
    service: Mapping[str, object],
    prefix: str,
    rule: AuxiliaryServiceRule,
    violations: list[str],
) -> None:
    """A long-running database sidecar is gated by its declared readiness probe."""
    health = service.get("healthcheck")
    allowed = {"test", "interval", "timeout", "retries", "start_period", "start_interval"}
    test = health.get("test") if isinstance(health, Mapping) else None
    if (
        not rule.healthcheck_test
        or not isinstance(health, Mapping)
        or not set(health).issubset(allowed)
        or not is_sequence(test)
        or tuple(test) != rule.healthcheck_test
    ):
        violations.append(f"{prefix}.healthcheck: declared readiness probe is required")


def validate_auxiliary_service(
    rendered: Mapping[str, object],
    rule: AuxiliaryServiceRule,
    project: str,
    policy: ComposePolicy,
    rules: Mapping[str, AuxiliaryServiceRule] | None = None,
) -> tuple[str, ...]:
    """Return deterministic violations for one declared auxiliary service."""
    violations: list[str] = []
    prefix = f"services.{rule.name}"
    if rule.role == "database" and rule.egress != "approved-networks":
        violations.append(
            f"{prefix}: a database role must be reachable on an approved project network"
        )
    services = rendered.get("services")
    service = services.get(rule.name) if isinstance(services, Mapping) else None
    if not isinstance(service, Mapping):
        violations.append(f"{prefix}: declared auxiliary service must exist as a mapping")
        return tuple(dict.fromkeys(violations))

    validate_allowed_fields(service, ROLE_SERVICE_KEYS[rule.role], prefix, violations)
    _validate_hardening(service, prefix, policy, rule, violations)
    _validate_process(service, prefix, violations, required=rule.role == "init")
    _validate_egress(rendered, service, prefix, rule, policy, violations)
    # A sidecar may itself wait on another sidecar (a restore init waits for its
    # database), but only on the same declared, role-validated set.
    peers = {name: peer for name, peer in (rules or {}).items() if name != rule.name}
    validate_depends_on(service, prefix, peers, violations)

    restart = service.get("restart")
    if rule.role == "init":
        if restart != "no":
            violations.append(f"{prefix}.restart: a one-shot init sidecar must not restart")
    else:
        if not isinstance(restart, str) or restart not in policy.allowed_restart:
            violations.append(f"{prefix}.restart: must be one of the approved policies")
        _validate_health(service, prefix, rule, violations)

    writable_volume = validate_storage(
        rendered,
        service,
        prefix,
        project,
        policy,
        violations,
        writable_targets=rule.writable_targets,
        read_only_targets=rule.read_only_targets,
        allow_bind=rule.role == "init",
        require_tmp=False,
    )
    if not writable_volume:
        violations.append(
            f"{prefix}.volumes: an auxiliary sidecar must write to a declared named volume"
        )
    validate_limits(service, prefix, policy, violations)
    validate_logging(service, prefix, policy, violations)
    return tuple(dict.fromkeys(violations))
