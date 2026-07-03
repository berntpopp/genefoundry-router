"""The expected serverInfo.name for a backend defaults to ``<namespace>-link`` but can be
overridden when the router aliases a backend to a shorter namespace than its ratified name.

Case in point: the router calls the ``spliceailookup-link`` backend ``spliceai`` (a shorter
tool-prefix), but the backend's own conformance CI ratifies ``serverInfo.name`` =
``spliceailookup-link``. fleet-probe must assert the backend's REAL name, not the alias.
"""

from __future__ import annotations

from genefoundry_router.registry import BackendDef, expected_server_name


def test_expected_server_name_defaults_to_namespace_link():
    backend = BackendDef(name="gnomad", url_env="X", namespace="gnomad")
    assert expected_server_name(backend) == "gnomad-link"


def test_expected_server_name_uses_explicit_override():
    backend = BackendDef(
        name="spliceai", url_env="X", namespace="spliceai", server_name="spliceailookup-link"
    )
    assert expected_server_name(backend) == "spliceailookup-link"
