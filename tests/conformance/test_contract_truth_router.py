from __future__ import annotations

import os
from pathlib import Path

import pytest

from docs.conformance.contract_truth import (
    active_markdown_files,
    historical_markdown_files,
    lint_repository,
)
from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import make_fake_backend
from genefoundry_router.registry import BackendDef
from genefoundry_router.server import build_server


@pytest.mark.asyncio
async def test_router_active_documentation_matches_its_live_tool_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GF_AUTH_MODE", "jwt")
    for key in tuple(os.environ):
        if key.startswith("GF_"):
            monkeypatch.delenv(key)

    settings = RouterSettings(_env_file=None)
    assert settings.GF_AUTH_MODE == "none"
    registry = [BackendDef(name="gnomad", url_env="X", namespace="gnomad")]
    server = build_server(
        settings,
        registry,
        proxy_targets={
            "gnomad": make_fake_backend("gnomad-link", ["get_variant_details", "search_genes"])
        },
    )
    # The transformed visible registry is the intentional client-facing gateway surface.
    tools = await server.list_tools()
    catalog = {tool.name: {"inputSchema": tool.parameters or {"properties": {}}} for tool in tools}
    root = Path(__file__).resolve().parents[2]

    assert (root / "pyproject.toml").is_file()
    assert (root / "README.md").is_file()
    assert active_markdown_files(root)
    assert historical_markdown_files(root)
    assert lint_repository(root, catalog) == []
