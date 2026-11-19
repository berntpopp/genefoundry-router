"""Rule-by-rule contract for the deployed Compose overlay gate.

Every rule gets a passing fixture and a failing fixture: the passing one proves the rule
does not fire on the shape the fleet already deploys, the failing one proves it fires on
the exact shape that broke a deployment.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from genefoundry_router.release.compose_deployed import (
    DEPLOYED_OVERLAY_RULES,
    DeployedOverlayPolicy,
    DeployedSidecar,
    validate_deployed_file_set,
    validate_deployed_overlay,
)

DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/example/placeholder@{DIGEST}"
TEMPLATE = "${GF_IMAGE:-${EXAMPLE_LINK_IMAGE:?Set GF_IMAGE to ghcr.io/x/y@sha256:<digest>}}"


def _application(**overrides: Any) -> dict[str, Any]:
    service: dict[str, Any] = {
        "image": IMAGE,
        "user": "10001:10001",
        "restart": "unless-stopped",
        "expose": ["8000"],
        "read_only": True,
        "init": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "healthcheck": {
            "test": ["CMD", "curl", "-f", "http://127.0.0.1:8000/health"],
            "interval": "30s",
            "timeout": "10s",
            "retries": 5,
            "start_period": "30s",
        },
        "volumes": [{"type": "volume", "source": "app-data", "target": "/data"}],
    }
    service.update(overrides)
    return service


def _rendered(**services: Any) -> dict[str, Any]:
    return {"name": "example-link-npm", "services": services or {"app": _application()}}


def _raw(*names: str) -> dict[str, Any]:
    return {
        "name": "example-link-npm",
        "services": {name: {"image": TEMPLATE} for name in (names or ("app",))},
    }


def _rules(violations: tuple[str, ...]) -> set[str]:
    return {line.split(": ", 1)[1].split(" — ", 1)[0] for line in violations}


def test_compliant_single_service_overlay_passes() -> None:
    assert validate_deployed_overlay(_rendered(), _raw()) == ()


def test_every_documented_rule_has_a_one_sentence_failure_description() -> None:
    assert DEPLOYED_OVERLAY_RULES
    for rule, description in DEPLOYED_OVERLAY_RULES.items():
        assert rule == rule.lower().strip()
        assert description.endswith(".")


def test_every_violation_is_one_scoped_line_naming_a_documented_rule() -> None:
    violations = validate_deployed_overlay(
        _rendered(app=_application(user="app", restart="on-failure")), _raw()
    )

    assert violations
    for line in violations:
        scope, _, remainder = line.partition(": ")
        rule, separator, remedy = remainder.partition(" — ")
        assert scope and separator and remedy
        assert rule in DEPLOYED_OVERLAY_RULES
        assert "\n" not in line


@pytest.mark.parametrize("user", ["app", "0:0", "10001", "", None])
def test_non_numeric_user_is_refused(user: object) -> None:
    service = _application()
    if user is None:
        del service["user"]
    else:
        service["user"] = user

    assert "numeric-user" in _rules(validate_deployed_overlay(_rendered(app=service), _raw()))


@pytest.mark.parametrize("user", ["10001:10001", "999:999"])
def test_numeric_user_is_accepted(user: str) -> None:
    rendered = _rendered(app=_application(user=user))

    assert "numeric-user" not in _rules(validate_deployed_overlay(rendered, _raw()))


@pytest.mark.parametrize("restart", ["on-failure", "always", "no", None])
def test_application_restart_must_be_unless_stopped(restart: object) -> None:
    service = _application()
    if restart is None:
        del service["restart"]
    else:
        service["restart"] = restart

    assert "restart-policy" in _rules(validate_deployed_overlay(_rendered(app=service), _raw()))


def test_deploy_restart_policy_is_refused_even_beside_a_correct_restart() -> None:
    service = _application(deploy={"restart_policy": {"condition": "on-failure"}})

    violations = validate_deployed_overlay(_rendered(app=service), _raw())

    assert "no-deploy-restart-policy" in _rules(violations)


def test_deploy_resources_alone_is_accepted() -> None:
    service = _application(deploy={"resources": {"limits": {"memory": "1g"}}})

    violations = validate_deployed_overlay(_rendered(app=service), _raw())

    assert "no-deploy-restart-policy" not in _rules(violations)


@pytest.mark.parametrize(
    "healthcheck",
    [None, {"test": ["CMD", "true"]}, {"start_period": "30s"}, {"test": [], "start_period": "5s"}],
)
def test_healthcheck_with_start_period_is_required(healthcheck: object) -> None:
    service = _application()
    if healthcheck is None:
        del service["healthcheck"]
    else:
        service["healthcheck"] = healthcheck

    violations = validate_deployed_overlay(_rendered(app=service), _raw())

    assert "healthcheck-start-period" in _rules(violations)


def test_init_service_is_exempt_from_restart_and_healthcheck() -> None:
    seed = _application(restart="no")
    del seed["healthcheck"]
    del seed["expose"]
    app = _application(
        depends_on={"seed": {"condition": "service_completed_successfully"}},
    )

    violations = validate_deployed_overlay(_rendered(app=app, seed=seed), _raw("app", "seed"))

    assert violations == ()


def test_init_service_must_not_be_restarted() -> None:
    seed = _application(restart="unless-stopped")
    del seed["expose"]
    app = _application(depends_on={"seed": {"condition": "service_completed_successfully"}})

    violations = validate_deployed_overlay(_rendered(app=app, seed=seed), _raw("app", "seed"))

    assert "restart-policy" in _rules(violations)


@pytest.mark.parametrize(
    "template",
    [
        "ghcr.io/x/y:1.2.3",
        f"ghcr.io/x/y@{DIGEST}",
        "${EXAMPLE_IMAGE}",
        "${example_image:?set}",
        "${EXAMPLE_LINK_IMAGE:?set}",
        "${GF_IMAGE:-ghcr.io/x/y:latest}",
        "${OTHER:-${EXAMPLE_LINK_IMAGE:?set}}",
    ],
)
def test_application_image_must_be_a_required_variable(template: str) -> None:
    raw = {"name": "example-link-npm", "services": {"app": {"image": template}}}

    assert "application-image" in _rules(validate_deployed_overlay(_rendered(), raw))


def test_second_application_image_is_refused_as_a_two_image_stack() -> None:
    raw = {
        "name": "example-link-npm",
        "services": {
            "app": {"image": TEMPLATE},
            "db": {"image": "${GF_IMAGE:?set it}"},
        },
    }
    rendered = _rendered(app=_application(), db=_application())

    assert "single-application-image" in _rules(validate_deployed_overlay(rendered, raw))


def test_declared_sidecar_carries_its_third_party_image() -> None:
    policy = DeployedOverlayPolicy(
        sidecars=(DeployedSidecar(name="db", image="postgres:16-alpine"),)
    )
    db = _application(image="postgres:16-alpine", expose=["5432"], user="999:999")
    rendered = _rendered(app=_application(), db=db)
    raw = {
        "name": "example-link-npm",
        "services": {"app": {"image": TEMPLATE}, "db": {"image": "postgres:16-alpine"}},
    }

    assert validate_deployed_overlay(rendered, raw, policy) == ()


def test_declared_sidecar_running_the_wrong_image_is_refused() -> None:
    policy = DeployedOverlayPolicy(
        sidecars=(DeployedSidecar(name="db", image="postgres:16-alpine"),)
    )
    db = _application(image="postgres:15-alpine", expose=["5432"], user="999:999")
    rendered = _rendered(app=_application(), db=db)
    raw = {
        "name": "example-link-npm",
        "services": {"app": {"image": TEMPLATE}, "db": {"image": "postgres:15-alpine"}},
    }

    assert "sidecar-image" in _rules(validate_deployed_overlay(rendered, raw, policy))


def test_declared_sidecar_absent_from_the_render_is_refused() -> None:
    policy = DeployedOverlayPolicy(
        sidecars=(DeployedSidecar(name="db", image="postgres:16-alpine"),)
    )

    violations = validate_deployed_overlay(_rendered(), _raw(), policy)

    assert "declared-sidecar-present" in _rules(violations)


@pytest.mark.parametrize(
    "mount",
    [
        {"type": "bind", "source": "/srv/seed", "target": "/seed"},
        {"type": "bind", "source": "/srv/seed", "target": "/seed", "read_only": False},
        {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"},
        "app-data:/data",
        {"type": "npipe", "source": "x", "target": "/data"},
    ],
)
def test_bind_and_shorthand_mounts_are_refused(mount: object) -> None:
    service = _application(volumes=[mount])

    assert "volumes" in _rules(validate_deployed_overlay(_rendered(app=service), _raw()))


def test_declared_read_only_seed_bind_is_accepted() -> None:
    policy = DeployedOverlayPolicy(seed_binds=frozenset({"/seed"}))
    service = _application(
        volumes=[
            {"type": "volume", "source": "app-data", "target": "/data"},
            {"type": "bind", "source": "/srv/seed", "target": "/seed", "read_only": True},
        ]
    )

    assert validate_deployed_overlay(_rendered(app=service), _raw(), policy) == ()


def test_tmpfs_mount_is_accepted() -> None:
    service = _application(volumes=[{"type": "tmpfs", "target": "/tmp"}])  # noqa: S108

    assert validate_deployed_overlay(_rendered(app=service), _raw()) == ()


def test_published_host_ports_are_refused() -> None:
    service = _application(ports=[{"target": 8000, "published": "8010", "mode": "ingress"}])

    assert "expose" in _rules(validate_deployed_overlay(_rendered(app=service), _raw()))


@pytest.mark.parametrize("expose", [None, [], ["8010"], ["8010:8000"], ["0"]])
def test_expose_must_name_the_declared_container_port(expose: object) -> None:
    service = _application()
    if expose is None:
        del service["expose"]
    else:
        service["expose"] = expose

    assert "expose" in _rules(validate_deployed_overlay(_rendered(app=service), _raw()))


@pytest.mark.parametrize("expose", [["8000"], ["8000/tcp"], [8000]])
def test_expose_accepts_the_container_port_in_every_rendered_spelling(expose: object) -> None:
    service = _application(expose=expose)

    assert validate_deployed_overlay(_rendered(app=service), _raw()) == ()


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("cap_drop", [], "cap-drop"),
        ("cap_drop", ["NET_RAW"], "cap-drop"),
        ("read_only", False, "read-only-rootfs"),
        ("security_opt", [], "no-new-privileges"),
        ("security_opt", ["seccomp:unconfined"], "no-new-privileges"),
    ],
)
def test_container_hardening_fields_are_required(field: str, value: object, rule: str) -> None:
    service = _application(**{field: value})

    assert rule in _rules(validate_deployed_overlay(_rendered(app=service), _raw()))


@pytest.mark.parametrize("document", ["rendered", "raw"])
def test_top_level_extension_keys_are_refused(document: str) -> None:
    rendered: dict[str, Any] = _rendered()
    raw: dict[str, Any] = _raw()
    {"rendered": rendered, "raw": raw}[document]["x-image"] = {"image": TEMPLATE}

    assert "no-extension-keys" in _rules(validate_deployed_overlay(rendered, raw))


@pytest.mark.parametrize("document", [{}, {"services": {}}, {"services": []}])
def test_an_unrenderable_document_is_refused(document: Mapping[str, object]) -> None:
    violations = validate_deployed_overlay(document, _raw())

    assert _rules(violations) == {"render"}


def test_a_service_that_does_not_render_as_a_mapping_is_refused() -> None:
    violations = validate_deployed_overlay(_rendered(app="not-a-mapping"), _raw())

    assert "render" in _rules(violations)


def test_violations_are_deterministic_and_deduplicated() -> None:
    rendered = _rendered(app=_application(user="app"), b=_application(user="app"))
    raw = _raw("app", "b")

    first = validate_deployed_overlay(rendered, raw)

    assert first == validate_deployed_overlay(rendered, raw)
    assert len(first) == len(set(first))


def test_layered_overlay_alone_is_refused_as_the_deployed_file_set() -> None:
    sources = {"docker/docker-compose.npm.yml": "services:\n  app:\n    ports: !reset []\n"}

    violations = validate_deployed_file_set(sources, ["docker/docker-compose.npm.yml"])

    assert _rules(violations) == {"deployed-file-set"}


NAMED = "name: example-link\n"


def test_self_contained_overlay_alone_is_accepted_as_the_deployed_file_set() -> None:
    sources = {
        "docker/docker-compose.npm.yml": NAMED
        + "services:\n  app:\n    expose: ['8000']\n"
        + "volumes:\n  data:\n    name: ${GF_DATA_VOLUME:-example-data-${GF_DATA_RELEASE_TAG}}\n"
    }

    assert validate_deployed_file_set(sources, ["docker/docker-compose.npm.yml"]) == ()


@pytest.mark.parametrize("declared", [[], ["docker/docker-compose.yml", "docker/npm.yml"]])
def test_anything_but_one_declared_deployed_file_is_refused(declared: list[str]) -> None:
    sources = dict.fromkeys(declared, NAMED + "services:\n  app:\n    ports: !reset []\n")

    assert _rules(validate_deployed_file_set(sources, declared)) == {"single-deployed-file"}


@pytest.mark.parametrize("header", ["", "name: ${COMPOSE_NAME:-x}\n", "name: ''\n"])
def test_deployed_file_without_a_literal_project_name_is_refused(header: str) -> None:
    sources = {"docker/docker-compose.npm.yml": header + "services:\n  app: {}\n"}

    violations = validate_deployed_file_set(sources, ["docker/docker-compose.npm.yml"])

    assert _rules(violations) == {"project-name"}


@pytest.mark.parametrize("volume", ["  data:\n", "  data: {}\n", "  data:\n    external: false\n"])
def test_volume_without_an_explicit_name_is_refused(volume: str) -> None:
    sources = {"docker/docker-compose.npm.yml": NAMED + "volumes:\n" + volume}

    violations = validate_deployed_file_set(sources, ["docker/docker-compose.npm.yml"])

    assert _rules(violations) == {"volume-name"}


def _networked(**networks: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two services: `app` on every given network, `db` only on `internal`."""
    app = _application(networks=dict.fromkeys(networks))
    db = _application(networks={"internal": None})
    rendered = _rendered(app=app, db=db) | {"networks": networks}
    return rendered, _raw("app", "db")


SUBNET = {"config": [{"subnet": "10.251.3.0/28"}]}


def test_shared_internal_network_with_a_registry_subnet_passes() -> None:
    rendered, raw = _networked(
        internal={"name": "x_internal", "ipam": SUBNET},
        npm={"name": "npm_default", "external": True},
    )

    assert "internal-network" not in _rules(validate_deployed_overlay(rendered, raw))


@pytest.mark.parametrize(
    "network",
    [{"name": "x_internal"}, {"name": "x_internal", "ipam": {"config": [{}]}}],
)
def test_internal_network_without_a_subnet_is_refused(network: dict[str, Any]) -> None:
    rendered, raw = _networked(internal=network)

    assert "internal-network" in _rules(validate_deployed_overlay(rendered, raw))


def test_single_service_internal_network_is_refused() -> None:
    rendered, raw = _networked(
        internal={"name": "x_internal", "ipam": SUBNET},
        solo={"name": "x_solo", "ipam": SUBNET},
    )

    flagged = [v for v in validate_deployed_overlay(rendered, raw) if "internal-network" in v]

    assert len(flagged) == 1
    assert flagged[0].startswith("solo: internal-network")
