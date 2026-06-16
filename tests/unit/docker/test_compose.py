from pathlib import Path

import yaml

DOCKER = Path(__file__).resolve().parents[3] / "docker"


def test_base_compose_defines_service_and_healthcheck():
    data = yaml.safe_load((DOCKER / "docker-compose.yml").read_text())
    svc = data["services"]["genefoundry-router"]
    assert svc["healthcheck"]["test"][-1].endswith("/health")
    assert "8000" in str(svc["ports"])


def test_base_compose_sets_explicit_project_name():
    # Isolates this stack from sibling -link repos that also root their compose at
    # docker/, which otherwise all collapse into one default "docker" project.
    data = yaml.safe_load((DOCKER / "docker-compose.yml").read_text())
    assert data["name"] == "genefoundry-router"


def test_prod_overlay_hardens():
    data = yaml.safe_load((DOCKER / "docker-compose.prod.yml").read_text())
    svc = data["services"]["genefoundry-router"]
    assert svc["read_only"] is True
    assert svc["security_opt"] == ["no-new-privileges:true"]
    assert svc["cap_drop"] == ["ALL"]


def test_npm_overlay_joins_external_network():
    data = yaml.safe_load((DOCKER / "docker-compose.npm.yml").read_text())
    assert data["networks"]["npm-network"]["external"] is True
