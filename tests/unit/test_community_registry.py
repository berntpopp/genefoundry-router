"""Federation Standard v1 §3: community loader, forced trust and startup guards (#410)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from genefoundry_router.config import (
    COMMUNITY_REQUIRED,
    RouterSettings,
    load_community_registry,
    load_registry,
    load_serving_registry,
)
from genefoundry_router.exceptions import ConfigurationError, RegistryError
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server


def _core() -> list[BackendDef]:
    return [BackendDef(name="gnomad", namespace="gnomad", url_env="GF_GNOMAD_URL")]


def _entry(**over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "acme",
        "namespace": "acme",
        "url": "https://mcp.acme.example/mcp",
        "operator": "Acme Lab",
        "contact": "ops@acme.example",
        "ownership": "dns-txt",
        "server_name": "acme-link",
        "source_name": "Acme DB",
        "data_egress": {
            "retention": "none",
            "subprocessors": [],
            "tools_sending_free_text": [],
        },
    }
    entry.update(over)
    return {k: v for k, v in entry.items() if v is not None}


def _load(
    tmp_path: Path,
    servers: list[dict[str, Any]],
    *,
    disabled: list[dict[str, Any]] | None = None,
    tombstones: list[dict[str, Any]] | None = None,
    disabled_env: list[str] | None = None,
) -> list[BackendDef]:
    community = tmp_path / "community.yaml"
    community.write_text(yaml.safe_dump({"servers": servers, "disabled": disabled or []}))
    tomb = tmp_path / "tombstones.yaml"
    tomb.write_text(yaml.safe_dump({"tombstones": tombstones or []}))
    return load_community_registry(community, _core(), tomb, disabled_env or [])


def test_entry_without_trust_loads_as_community(tmp_path: Path) -> None:
    [backend] = _load(tmp_path, [_entry()])
    assert backend.trust == "community"
    assert backend.url == "https://mcp.acme.example/mcp"
    assert backend.operator == "Acme Lab"
    assert backend.data_egress is not None and backend.data_egress.retention == "none"


def test_core_entries_default_to_core_trust() -> None:
    assert _core()[0].trust == "core"


@pytest.mark.parametrize("value", ["core", "community"])
def test_trust_key_is_a_load_error(tmp_path: Path, value: str) -> None:
    with pytest.raises(RegistryError, match="'trust'"):
        _load(tmp_path, [_entry(trust=value)])


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("entrypoints", ["search"]),
        ("transform", {"strip_prefix": "acme_"}),
        ("url_env", "GF_ACME_URL"),
        ("service_token_env", "GF_ACME_TOKEN"),
        ("enabled", False),
    ],
)
def test_forbidden_key_is_a_load_error(tmp_path: Path, key: str, value: object) -> None:
    with pytest.raises(RegistryError, match=f"forbidden key.*'{key}'"):
        _load(tmp_path, [_entry(**{key: value})])


@pytest.mark.parametrize(
    "url",
    [
        "http://mcp.acme.example/mcp",
        "https://mcp.acme.example:8443/mcp",
        "https://mcp.acme.example/other",
        "https://mcp.acme.example/mcp?x=1",
        "https://user@mcp.acme.example/mcp",
        "https:///mcp",
    ],
)
def test_non_conforming_url_is_a_load_error(tmp_path: Path, url: str) -> None:
    with pytest.raises(RegistryError, match="https://<host>/mcp"):
        _load(tmp_path, [_entry(url=url)])


def test_explicit_port_443_is_accepted(tmp_path: Path) -> None:
    [backend] = _load(tmp_path, [_entry(url="https://mcp.acme.example:443/mcp")])
    assert backend.url == "https://mcp.acme.example:443/mcp"


@pytest.mark.parametrize("field", ["name", "namespace"])
def test_core_name_or_namespace_is_a_load_error(tmp_path: Path, field: str) -> None:
    with pytest.raises(RegistryError, match="gnomad"):
        _load(tmp_path, [_entry(**{field: "gnomad"})])


@pytest.mark.parametrize("field", ["name", "namespace"])
def test_tombstoned_name_or_namespace_is_a_load_error(tmp_path: Path, field: str) -> None:
    entry = _entry(name="acme-db", namespace="acmedb")
    with pytest.raises(RegistryError, match="tombstone"):
        _load(tmp_path, [entry], tombstones=[{field: entry[field], "reason": "delisted"}])


@pytest.mark.parametrize("missing", COMMUNITY_REQUIRED)
def test_missing_required_key_is_a_load_error(tmp_path: Path, missing: str) -> None:
    entry = _entry()
    del entry[missing]
    with pytest.raises(RegistryError, match=f"'{missing}'"):
        _load(tmp_path, [entry])


def test_required_keys_are_the_standard_list() -> None:
    assert set(COMMUNITY_REQUIRED) == {
        "name",
        "namespace",
        "url",
        "operator",
        "contact",
        "ownership",
        "server_name",
        "source_name",
        "data_egress",
    }


def test_at_most_ten_community_backends(tmp_path: Path) -> None:
    entries = [_entry(name=f"acme{i}", namespace=f"acme{i}") for i in range(11)]
    assert len(_load(tmp_path, entries[:10])) == 10
    with pytest.raises(RegistryError, match="at most 10"):
        _load(tmp_path, entries)


@pytest.mark.parametrize("trust", ["community", "core"])
def test_core_registry_rejects_a_trust_key(tmp_path: Path, trust: str) -> None:
    servers = tmp_path / "servers.yaml"
    servers.write_text(
        f"servers:\n  - {{name: acme, namespace: acme, url_env: GF_ACME_URL, trust: {trust}}}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="GF_COMMUNITY_SERVERS_FILE"):
        load_registry(servers, {})


def test_core_registry_rejects_trust_in_defaults(tmp_path: Path) -> None:
    servers = tmp_path / "servers.yaml"
    servers.write_text(
        "defaults: {trust: community}\n"
        "servers:\n  - {name: acme, namespace: acme, url_env: GF_ACME_URL}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="GF_COMMUNITY_SERVERS_FILE"):
        load_registry(servers, {})


def test_incomplete_data_egress_is_a_load_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="tools_sending_free_text"):
        _load(tmp_path, [_entry(data_egress={"retention": "none", "subprocessors": []})])


def test_duplicate_within_community_and_disabled_lists_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="duplicate"):
        _load(tmp_path, [_entry(), _entry(name="acme2")])
    with pytest.raises(RegistryError, match="duplicate"):
        _load(tmp_path, [_entry()], disabled=[{"name": "acme", "namespace": "other"}])


def test_disabled_list_and_env_disable_backends(tmp_path: Path) -> None:
    loaded = _load(
        tmp_path,
        [_entry(), _entry(name="beta", namespace="beta")],
        disabled=[{"name": "gone", "namespace": "gone", "reason": "unreachable"}],
        disabled_env=["beta"],
    )
    assert {b.name: b.enabled for b in loaded} == {"acme": True, "beta": False}


# --- startup guards (RouterSettings) -------------------------------------------------


def _settings(**kwargs: Any) -> RouterSettings:
    return RouterSettings(_env_file=None, **kwargs)  # type: ignore[call-arg]


COMMUNITY = {
    "GF_COMMUNITY_SERVERS_FILE": "registry/community.yaml",
    "GF_QUARANTINE_FILE": "state/quarantine.json",
}


def test_defaults_keep_community_off() -> None:
    settings = _settings()
    assert settings.GF_ALLOW_COMMUNITY_BACKENDS is False
    assert settings.GF_COMMUNITY_SERVERS_FILE is None
    assert settings.GF_PRIVACY_PROFILE == "default"


def test_community_file_without_allow_fails_startup() -> None:
    with pytest.raises(ValidationError, match="GF_ALLOW_COMMUNITY_BACKENDS"):
        _settings(**COMMUNITY, GF_DRIFT_MODE="enforce")


@pytest.mark.parametrize("mode", ["warn", "off"])
def test_community_file_requires_enforced_drift(mode: str) -> None:
    with pytest.raises(ValidationError, match="GF_DRIFT_MODE=enforce"):
        _settings(**COMMUNITY, GF_ALLOW_COMMUNITY_BACKENDS=True, GF_DRIFT_MODE=mode)


def test_community_file_with_allow_and_enforce_starts() -> None:
    _settings(**COMMUNITY, GF_ALLOW_COMMUNITY_BACKENDS=True, GF_DRIFT_MODE="enforce")


def test_community_instance_requires_a_quarantine_file() -> None:
    """#412: without GF_QUARANTINE_FILE the latch is in memory and a restart clears it."""
    with pytest.raises(ValidationError, match="GF_QUARANTINE_FILE"):
        _settings(
            GF_COMMUNITY_SERVERS_FILE="registry/community.yaml",
            GF_ALLOW_COMMUNITY_BACKENDS=True,
            GF_DRIFT_MODE="enforce",
        )


def test_patient_profile_refuses_community_backends() -> None:
    with pytest.raises(ValidationError, match="GF_PRIVACY_PROFILE=patient"):
        _settings(
            **COMMUNITY,
            GF_ALLOW_COMMUNITY_BACKENDS=True,
            GF_DRIFT_MODE="enforce",
            GF_PRIVACY_PROFILE="patient",
        )


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://api.example.org/api/v1/systemone", False),
        ("http://8.8.8.8:8000/api", False),
        ("http://reranker:8000/api/v1/systemone", True),
        ("http://localhost:8000/api", True),
        ("http://127.0.0.1:8000/api", True),
        ("http://10.1.2.3:8000/api", True),
        ("http://[::1]:8000/api", True),
    ],
)
def test_patient_profile_requires_a_local_reranker(url: str, ok: bool) -> None:
    kwargs = {
        "GF_PRIVACY_PROFILE": "patient",
        "GF_SEARCH_RERANK": "systemone",
        "GF_SEARCH_RERANK_URL": url,
    }
    if ok:
        _settings(**kwargs)
    else:
        with pytest.raises(ValidationError, match="reranker"):
            _settings(**kwargs)


def test_patient_profile_with_rerank_off_starts() -> None:
    _settings(GF_PRIVACY_PROFILE="patient")


def test_core_entry_still_requires_url_env(tmp_path: Path) -> None:
    servers = tmp_path / "servers.yaml"
    servers.write_text("servers:\n  - {name: gnomad, namespace: gnomad}\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="url_env"):
        load_registry(servers, {})


def test_core_instance_refuses_community_entries(tmp_path: Path) -> None:
    [backend] = _load(tmp_path, [_entry()])
    with pytest.raises(ConfigurationError, match="non-community instance"):
        build_server(_settings(), [*_core(), backend])


def test_serving_registry_adds_community_entries_only_when_configured(tmp_path: Path) -> None:
    servers = tmp_path / "servers.yaml"
    servers.write_text(
        "servers:\n  - {name: gnomad, namespace: gnomad, url_env: GF_GNOMAD_URL}\n",
        encoding="utf-8",
    )
    _load(tmp_path, [_entry()])  # writes community.yaml + tombstones.yaml
    core_only = load_serving_registry(_settings(), servers, {})
    assert [b.trust for b in core_only] == ["core"]
    community = _settings(
        GF_COMMUNITY_SERVERS_FILE=str(tmp_path / "community.yaml"),
        GF_TOMBSTONES_FILE=str(tmp_path / "tombstones.yaml"),
        GF_ALLOW_COMMUNITY_BACKENDS=True,
        GF_DRIFT_MODE="enforce",
        GF_QUARANTINE_FILE=str(tmp_path / "quarantine.json"),
    )
    assert [b.trust for b in load_serving_registry(community, servers, {})] == ["core", "community"]
