"""Deployment-profile contracts for the router.

The patient-data profile is the deployable form of the AutoPVS1 third-country-transfer
decision (router issue #32 / autopvs1-link #41): a hospital/on-prem deployment MUST NOT
forward possibly patient-derived variants to the public BGI/Ensembl upstreams. The router
disables a backend whose ``*_URL`` env var is unset (see ``server.py`` ``missing_url`` skip),
so omitting ``GF_AUTOPVS1_URL`` is what makes AutoPVS1 absent from the federated catalog.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

PATIENT_PROFILE = Path("docker/.env.patient-data.example")
PUBLIC_PROFILE = Path(".env.example")


class _ComposeLoader(yaml.SafeLoader):
    """Safe YAML loader that understands Compose's value-reset tag."""


def _construct_reset(loader: _ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, ScalarNode) and node.value.lower() in {"null", "~", ""}:
        return None
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!reset", _construct_reset)


def _assignments(text: str) -> dict[str, str]:
    """Uncommented KEY=value assignments (a leading '#' means documented-but-off)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            out[key] = value.split("#", 1)[0].strip()
    return out


def test_patient_profile_disables_autopvs1_backend() -> None:
    text = PATIENT_PROFILE.read_text(encoding="utf-8")
    # AutoPVS1 must never be wired in the patient-data profile.
    assert "GF_AUTOPVS1_URL" not in _assignments(text)
    # ...and the omission must be documented, not accidental.
    assert "AUTOPVS1" in text
    assert "disabled" in text.lower()


def test_patient_profile_is_authenticated_and_locked_down() -> None:
    assignments = _assignments(PATIENT_PROFILE.read_text(encoding="utf-8"))
    # Edge auth must be configured and never the insecure PoC defaults.
    assert assignments.get("GF_AUTH_MODE") in {"oauth", "jwt"}
    assert assignments.get("GF_ALLOW_INSECURE", "false").lower() == "false"
    # The profile must still federate the approved read-only backends.
    assert "GF_GNOMAD_URL" in assignments
    assert "GF_CLINVAR_URL" in assignments


def test_production_compose_declares_production_reachability_mode() -> None:
    compose = yaml.load(
        Path("docker/docker-compose.prod.yml").read_text(encoding="utf-8"),
        Loader=_ComposeLoader,  # noqa: S506 -- subclass of SafeLoader with !reset only
    )
    environment = compose["services"]["genefoundry-router"]["environment"]
    assert environment["GF_DEPLOYMENT_MODE"] == "production"
    assert "GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY" not in environment


def test_patient_profile_covers_every_non_autopvs1_public_backend() -> None:
    """Every backend URL offered in the public example (except AutoPVS1) is present."""
    public_urls = {
        name for name in _assignments(PUBLIC_PROFILE.read_text()) if name.endswith("_URL")
    }
    patient_urls = {
        name for name in _assignments(PATIENT_PROFILE.read_text()) if name.endswith("_URL")
    }
    assert public_urls - patient_urls == {"GF_AUTOPVS1_URL"}
