from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[3] / "docker"


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose's merge-control tags (``!reset``/``!override``).

    Compose v2.24+ uses these YAML tags to control how list/scalar fields merge across
    ``-f`` overlays; a plain SafeLoader has no constructor for them and raises. We map
    each to its underlying value so the override files load exactly as Compose resolves
    them (``!reset []`` -> ``[]``).
    """


def _construct_passthrough(loader: yaml.SafeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


for _tag in ("!reset", "!override"):
    _ComposeLoader.add_constructor(_tag, _construct_passthrough)


def _load(name: str) -> dict:
    # S506 is safe here: _ComposeLoader subclasses SafeLoader and only adds
    # value-passthrough constructors for two known Compose tags — no arbitrary
    # object instantiation, so it is as safe as yaml.safe_load.
    return yaml.load((DOCKER / name).read_text(), Loader=_ComposeLoader)  # noqa: S506


def test_base_compose_defines_service_and_healthcheck():
    data = _load("docker-compose.yml")
    svc = data["services"]["genefoundry-router"]
    assert svc["healthcheck"]["test"][-1].endswith("/health")
    assert "8000" in str(svc["ports"])


def test_base_compose_passes_local_runtime_security_settings() -> None:
    svc = _load("docker-compose.yml")["services"]["genefoundry-router"]
    environment = svc["environment"]
    assert "localhost,127.0.0.1,::1" in environment["GF_ALLOWED_HOSTS"]
    assert environment["GF_DRIFT_MODE"] == "${GF_DRIFT_MODE:-warn}"
    assert "GF_DRIFT_BASELINE" in environment
    healthcheck = " ".join(svc["healthcheck"]["test"])
    assert "Host: $${GF_HEALTHCHECK_HOST}" in healthcheck


def test_base_compose_sets_explicit_project_name():
    # Isolates this stack from sibling -link repos that also root their compose at
    # docker/, which otherwise all collapse into one default "docker" project.
    data = _load("docker-compose.yml")
    assert data["name"] == "genefoundry-router"


def test_prod_overlay_hardens():
    data = _load("docker-compose.prod.yml")
    svc = data["services"]["genefoundry-router"]
    assert svc["read_only"] is True
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["cap_drop"] == ["ALL"]
    assert svc["environment"]["GF_ALLOWED_HOSTS"] == (
        "${GF_ALLOWED_HOSTS:?set the public router hostname}"
    )
    assert svc["environment"]["GF_DRIFT_MODE"] == "enforce"
    assert svc["environment"]["GF_HEALTHCHECK_HOST"] == (
        "${GF_HEALTHCHECK_HOST:?set the public health-check Host}"
    )


def test_npm_overlay_joins_external_network():
    data = _load("docker-compose.npm.yml")
    assert data["networks"]["npm-network"]["external"] is True


def test_npm_overlay_resets_host_ports():
    # Behind nginx-proxy-manager the gateway must be expose-only. The overlay uses
    # `ports: !reset []` (not plain `[]`) because Compose MERGES list fields across
    # overlays — plain `[]` would leave the base host-port mapping published, exposing
    # the auth=none gateway on the public IP. After reset the mapping must be empty.
    data = _load("docker-compose.npm.yml")
    svc = data["services"]["genefoundry-router"]
    assert svc["ports"] == []
    assert svc["expose"] == ["8000"]
