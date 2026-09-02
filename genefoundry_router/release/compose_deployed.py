"""Validate the deployed NPM overlay against the fleet Compose contract.

`validate_compose` gates the *release* Compose files listed in `container-release.json`
(`service.compose_files`). Those files are never what the fleet controller deploys: the
controller layers `docker/docker-compose.npm.yml` on top (or deploys it standalone), and
that overlay is where the deploy contract actually lives. Every rule here exists because
its absence broke a real deployment; the remedy text names the exact edit.

Each violation is one line: ``<service>: <rule> — <what to change>``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeGuard

__all__ = [
    "DEPLOYED_OVERLAY_RULES",
    "DeployedOverlayPolicy",
    "DeployedSidecar",
    "validate_deployed_file_set",
    "validate_deployed_overlay",
]

_NUMERIC_USER = re.compile(r"^[1-9][0-9]*:[1-9][0-9]*$")
_IMAGE_TEMPLATE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*):\?[^}]+\}$")
_CONTAINER_PORT = re.compile(r"^([1-9][0-9]{0,4})(?:/(?:tcp|udp))?$")
_DOCUMENT_SCOPE = "compose"

#: Rule identifier -> the failure the rule prevents. Kept next to the checks so the
#: documented contract and the enforced contract cannot drift apart.
DEPLOYED_OVERLAY_RULES: Mapping[str, str] = {
    "render": (
        "A deployed overlay that does not render into a service map is not a stack the "
        "controller can deploy at all."
    ),
    "numeric-user": (
        "A container whose declared user is a name (or absent) cannot be proven non-root "
        "from /proc, and the fleet controller refuses to deploy it."
    ),
    "restart-policy": (
        "A long-running service that is not `unless-stopped` does not come back after a "
        "host reboot or a Docker upgrade."
    ),
    "no-deploy-restart-policy": (
        "Compose applies `deploy.restart_policy` instead of `restart`, silently swapping a "
        "reboot-surviving container onto on-failure; the runtime observer then reports "
        "`restart policy differs from Compose`."
    ),
    "healthcheck-start-period": (
        "Without a healthcheck the deploy cannot wait for readiness; without a "
        "`start_period` a slow first start is reported UNHEALTHY and rolls the deploy back."
    ),
    "application-image": (
        "A tag or a literal image lets the deploy run code no one pinned; the required "
        "variable makes a missing or wrong digest a hard error."
    ),
    "sidecar-image": (
        "An undeclared third-party image in the stack means the controller cannot tell "
        "which service carries the application, so it keys on the wrong one."
    ),
    "single-application-image": (
        "A stack naming more than one application image is refused with `target Compose "
        "projection does not name one application image`."
    ),
    "volumes": (
        "A host bind mount is state the release does not carry; an image-declared VOLUME "
        "or an undeclared bind shows up as `container mounts are invalid`."
    ),
    "expose": (
        "A published host port exposes an unauthenticated backend on the public IP, and "
        "an undeclared container port reads as `exposed ports differ from Compose`."
    ),
    "cap-drop": "Retained capabilities widen a container escape into a host compromise.",
    "read-only-rootfs": (
        "A writable root filesystem lets a compromised process persist across restarts."
    ),
    "no-new-privileges": "Without it a setuid binary inside the image can regain privilege.",
    "no-extension-keys": (
        "`docker compose config` echoes top-level `x-*` keys even when they are only YAML "
        "anchors, and the controller's projection rejects any unsupported top-level field."
    ),
    "declared-sidecar-present": (
        "A sidecar declared in container-release.json but absent from the render means the "
        "declaration no longer describes what is deployed."
    ),
    "deployed-file-set": (
        "Validating an overlay alone when the controller layers it on a base file set gates "
        "a stack nobody deploys, and silently passes what is actually deployed."
    ),
}

_MERGE_DIRECTIVE = re.compile(r"!(?:reset|override)\b")


@dataclass(frozen=True, slots=True)
class DeployedSidecar:
    """One approved non-application service and the third-party image it runs."""

    name: str
    image: str


@dataclass(frozen=True, slots=True)
class DeployedOverlayPolicy:
    """Per-repository facts the deployed-overlay rules are evaluated against."""

    container_port: int = 8000
    sidecars: tuple[DeployedSidecar, ...] = ()
    seed_binds: frozenset[str] = field(default_factory=frozenset)


def _is_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, Mapping)


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _violation(scope: object, rule: str, remedy: str) -> str:
    name = scope if isinstance(scope, str) and scope else "<unnamed>"
    return f"{name}: {rule} — {remedy}"


def _init_services(services: Mapping[str, object]) -> frozenset[str]:
    """Services another service waits on to *complete* are one-shot, not long-running."""
    completed: set[str] = set()
    for definition in services.values():
        if not _is_mapping(definition):
            continue
        depends_on = definition.get("depends_on")
        if not _is_mapping(depends_on):
            continue
        for name, options in depends_on.items():
            if (
                isinstance(name, str)
                and _is_mapping(options)
                and options.get("condition") == "service_completed_successfully"
            ):
                completed.add(name)
    return frozenset(completed)


def _check_user(name: str, service: Mapping[str, object], violations: list[str]) -> None:
    user = service.get("user")
    if not isinstance(user, str) or not _NUMERIC_USER.fullmatch(user):
        violations.append(
            _violation(
                name,
                "numeric-user",
                'declare user: "<uid>:<gid>" using this image\'s own numeric uid and gid '
                "from its Dockerfile — never a name, and never copied from a sibling repo",
            )
        )


def _check_restart(
    name: str, service: Mapping[str, object], *, is_init: bool, violations: list[str]
) -> None:
    restart = service.get("restart")
    if is_init:
        if restart not in (None, "no"):
            violations.append(
                _violation(
                    name,
                    "restart-policy",
                    'set restart: "no" — a run-once service must not be restarted',
                )
            )
    elif restart != "unless-stopped":
        violations.append(
            _violation(
                name,
                "restart-policy",
                "set restart: unless-stopped so the container returns after a host reboot",
            )
        )
    deploy = service.get("deploy")
    if _is_mapping(deploy) and "restart_policy" in deploy:
        violations.append(
            _violation(
                name,
                "no-deploy-restart-policy",
                "remove deploy.restart_policy and keep the plain restart: key — Compose "
                "applies deploy.restart_policy instead of restart when both are present",
            )
        )


def _check_healthcheck(
    name: str, service: Mapping[str, object], *, is_init: bool, violations: list[str]
) -> None:
    if is_init:
        return
    health = service.get("healthcheck")
    if not _is_mapping(health) or not health.get("test") or not health.get("start_period"):
        violations.append(
            _violation(
                name,
                "healthcheck-start-period",
                "declare a healthcheck with test, interval, timeout, retries and an "
                "explicit start_period covering this service's slowest first start",
            )
        )


def _check_image(
    name: str,
    service: Mapping[str, object],
    raw_service: Mapping[str, object],
    *,
    sidecar_image: str | None,
    violations: list[str],
) -> str | None:
    """Return the application image template this service names, if it is one."""
    if sidecar_image is not None:
        if service.get("image") != sidecar_image:
            violations.append(
                _violation(
                    name,
                    "sidecar-image",
                    f"run the declared third-party image {sidecar_image!r} or correct "
                    "container-release.json service.deployed_sidecars[].image",
                )
            )
        return None
    template = raw_service.get("image")
    if not isinstance(template, str) or not _IMAGE_TEMPLATE.fullmatch(template):
        violations.append(
            _violation(
                name,
                "application-image",
                "reference the release image as "
                "${<PROJECT>_IMAGE:?Set <PROJECT>_IMAGE to ghcr.io/<owner>/<repo>@sha256:"
                "<digest>} so deploy must supply a pinned digest — or, if this service "
                "runs a third-party image, declare it in service.deployed_sidecars",
            )
        )
        return None
    return template


def _check_volumes(
    name: str,
    service: Mapping[str, object],
    *,
    seed_binds: frozenset[str],
    violations: list[str],
) -> None:
    mounts = service.get("volumes")
    if mounts is None:
        return
    if not _is_sequence(mounts):
        violations.append(_violation(name, "volumes", "declare volumes: as a list of mounts"))
        return
    for mount in mounts:
        if not _is_mapping(mount):
            violations.append(
                _violation(
                    name,
                    "volumes",
                    "use the long mount syntax ({type, source, target}) so the mount kind "
                    "is explicit",
                )
            )
            continue
        kind = mount.get("type")
        target = mount.get("target")
        if kind in ("volume", "tmpfs"):
            continue
        if kind == "bind" and mount.get("read_only") is True and target in seed_binds:
            continue
        if kind == "bind":
            violations.append(
                _violation(
                    name,
                    "volumes",
                    f"replace the bind mount at {target!r} with a named volume, or — if it "
                    "is a read-only seed directory — set read_only: true and list its "
                    "target in container-release.json service.deployed_seed_binds",
                )
            )
            continue
        violations.append(
            _violation(
                name,
                "volumes",
                f"mount type {kind!r} is not permitted; use a named volume or tmpfs",
            )
        )


def _check_ports(
    name: str,
    service: Mapping[str, object],
    *,
    require_port: int | None,
    violations: list[str],
) -> None:
    published = service.get("ports")
    if published:
        violations.append(
            _violation(
                name,
                "expose",
                "remove the published host ports with `ports: !reset []` — a plain "
                "`ports: []` merges with the base file and stays published",
            )
        )
    expose = service.get("expose")
    entries = list(expose) if _is_sequence(expose) else []
    for entry in entries:
        if not _CONTAINER_PORT.fullmatch(str(entry)):
            violations.append(
                _violation(
                    name,
                    "expose",
                    f"expose entry {entry!r} is not a bare container port; list the port "
                    "the process listens on, never a host:container mapping",
                )
            )
    if require_port is None:
        return
    if not any(str(entry).partition("/")[0] == str(require_port) for entry in entries):
        violations.append(
            _violation(
                name,
                "expose",
                f'declare expose: ["{require_port}"] so the rendered Compose model names '
                "the same container port the image already EXPOSEs",
            )
        )


def _check_hardening(name: str, service: Mapping[str, object], violations: list[str]) -> None:
    cap_drop = service.get("cap_drop")
    if not _is_sequence(cap_drop) or list(cap_drop) != ["ALL"]:
        violations.append(_violation(name, "cap-drop", "set cap_drop: [ALL]"))
    if service.get("read_only") is not True:
        violations.append(
            _violation(
                name,
                "read-only-rootfs",
                "set read_only: true and give the process a tmpfs for its scratch space",
            )
        )
    security_opt = service.get("security_opt")
    if not _is_sequence(security_opt) or list(security_opt) != ["no-new-privileges:true"]:
        violations.append(
            _violation(name, "no-new-privileges", "set security_opt: [no-new-privileges:true]")
        )


def _check_extension_keys(
    rendered: Mapping[str, object], raw: Mapping[str, object], violations: list[str]
) -> None:
    extensions = sorted(
        {key for key in (*rendered, *raw) if isinstance(key, str) and key.startswith("x-")}
    )
    for key in extensions:
        violations.append(
            _violation(
                _DOCUMENT_SCOPE,
                "no-extension-keys",
                f"remove the top-level {key!r} key and inline its content into each service "
                "— Compose echoes x-* back even when it is only a YAML anchor",
            )
        )


def validate_deployed_file_set(
    sources: Mapping[str, str], declared: Sequence[str]
) -> tuple[str, ...]:
    """Refuse to gate a single overlay that only makes sense layered on a base file set.

    `!reset` and `!override` exist solely to replace a value some *other* Compose file
    already set. An overlay that uses one is deployed layered, so validating it alone
    would gate a stack nobody runs.
    """
    if len(declared) > 1:
        return ()
    for name in sorted(sources):
        if _MERGE_DIRECTIVE.search(sources[name]):
            return (
                _violation(
                    _DOCUMENT_SCOPE,
                    "deployed-file-set",
                    f"{name} uses a !reset/!override merge directive, so it is deployed "
                    "layered on other Compose files; list every file the fleet controller "
                    "deploys, in order, in container-release.json "
                    "service.deployed_compose_files",
                ),
            )
    return ()


def validate_deployed_overlay(
    rendered: Mapping[str, object],
    raw: Mapping[str, object],
    policy: DeployedOverlayPolicy | None = None,
) -> tuple[str, ...]:
    """Return deterministic violations for the rendered deployed overlay.

    `rendered` is `docker compose config --format json` with every required variable
    resolved to a placeholder; `raw` is the same render with `--no-interpolate`, which is
    the only place the `${<PROJECT>_IMAGE:?...}` template survives.
    """
    policy = policy or DeployedOverlayPolicy()
    violations: list[str] = []
    if not _is_mapping(rendered) or not _is_mapping(raw):
        return (_violation(_DOCUMENT_SCOPE, "render", "rendered Compose must be a JSON object"),)
    services = rendered.get("services")
    raw_services = raw.get("services")
    if not _is_mapping(services) or not services or not _is_mapping(raw_services):
        return (
            _violation(
                _DOCUMENT_SCOPE, "render", "the deployed overlay must render at least one service"
            ),
        )
    _check_extension_keys(rendered, raw, violations)
    sidecar_images = {sidecar.name: sidecar.image for sidecar in policy.sidecars}
    for missing in sorted(set(sidecar_images) - set(services)):
        violations.append(
            _violation(
                missing,
                "declared-sidecar-present",
                "this service is declared in container-release.json "
                "service.deployed_sidecars but is not in the deployed render; remove the "
                "declaration or restore the service",
            )
        )
    init = _init_services(services)
    templates: dict[str, str] = {}
    for name in sorted(services):
        service = services[name]
        raw_service = raw_services.get(name, {})
        if not _is_mapping(service) or not _is_mapping(raw_service):
            violations.append(_violation(name, "render", "service must render as a mapping"))
            continue
        is_init = name in init
        is_sidecar = name in sidecar_images
        _check_user(name, service, violations)
        _check_restart(name, service, is_init=is_init, violations=violations)
        _check_healthcheck(name, service, is_init=is_init, violations=violations)
        template = _check_image(
            name,
            service,
            raw_service,
            sidecar_image=sidecar_images.get(name),
            violations=violations,
        )
        if template is not None:
            templates[name] = template
        _check_volumes(name, service, seed_binds=policy.seed_binds, violations=violations)
        _check_ports(
            name,
            service,
            require_port=None if (is_init or is_sidecar) else policy.container_port,
            violations=violations,
        )
        _check_hardening(name, service, violations)
    distinct = sorted(set(templates.values()))
    if len(distinct) > 1:
        for name in sorted(name for name, value in templates.items() if value != distinct[0]):
            violations.append(
                _violation(
                    name,
                    "single-application-image",
                    "the application services must all reference the same image variable; "
                    "declare any third-party service in container-release.json "
                    "service.deployed_sidecars instead",
                )
            )
    return tuple(dict.fromkeys(violations))
