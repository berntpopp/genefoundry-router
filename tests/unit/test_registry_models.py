import pytest
from pydantic import ValidationError

from genefoundry_router.registry import BackendDef, TransformConfig


def test_backenddef_minimal_defaults():
    b = BackendDef(name="gnomad", url_env="GF_GNOMAD_URL", namespace="gnomad")
    assert b.enabled is True
    assert b.cache_ttl == 300
    assert b.tags == []
    assert b.transform is None
    assert b.url is None


def test_namespace_must_be_lowercase_token():
    with pytest.raises(ValidationError):
        BackendDef(name="x", url_env="GF_X_URL", namespace="Bad-Name")


def test_transform_config_parses_nested():
    b = BackendDef(
        name="pubtator",
        url_env="GF_PUBTATOR_URL",
        namespace="pubtator",
        transform={"strip_prefix": "pubtator_"},
    )
    assert isinstance(b.transform, TransformConfig)
    assert b.transform.strip_prefix == "pubtator_"
    assert b.transform.rename == {}
    assert b.transform.arg_rename == {}


def test_transport_defaults_to_http_and_accepts_it():
    # R1.1: servers.yaml sets defaults.transport: http -> BackendDef must accept it.
    b = BackendDef(name="gnomad", url_env="GF_GNOMAD_URL", namespace="gnomad", transport="http")
    assert b.transport == "http"
    assert BackendDef(name="g", url_env="X", namespace="g").transport == "http"


def test_non_http_transport_rejected():
    with pytest.raises(ValidationError):
        BackendDef(name="x", url_env="GF_X_URL", namespace="x", transport="sse")
