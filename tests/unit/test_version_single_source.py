"""Guard: pyproject -> installed metadata -> __version__ -> serverInfo are one value.

Enforces the fleet Versioning Standard v1 (docs/VERSIONING-STANDARD-v1.md):
the version lives ONLY in pyproject.toml [project].version; everything else
derives from installed distribution metadata. A drift fails CI.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

from genefoundry_router import __version__
from genefoundry_router.config import RouterSettings
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server

DIST = "genefoundry-router"


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def test_pyproject_is_the_single_source() -> None:
    assert version(DIST) == _pyproject_version()


def test_dunder_version_is_metadata_derived() -> None:
    assert __version__ == version(DIST)


def test_mcp_server_info_version_matches_package() -> None:
    settings = RouterSettings(_env_file=None, GF_AUTH_MODE="none")
    server = build_server(
        settings,
        [BackendDef(name="hgnc", url_env="X", namespace="hgnc", enabled=False)],
        enable_search=False,
    )
    assert server.version == __version__
