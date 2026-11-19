"""The capture scripts must record what production serves, in the wire format."""

import asyncio
import json

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from genefoundry_router.registry import BackendDef
from scripts import snapshot_catalog
from scripts.snapshot_fleet import _capture_backend


def test_fleet_capture_keeps_wire_camelcase_annotations() -> None:
    backend = FastMCP("one")

    @backend.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def ping() -> str:
        return "pong"

    captured = asyncio.run(_capture_backend(backend))  # type: ignore[arg-type]
    assert captured is not None
    annotations = captured[0].tools[0].annotations
    assert annotations is not None
    assert annotations["readOnlyHint"] is True
    assert "read_only_hint" not in annotations


def test_catalog_capture_includes_registry_tag_injection(monkeypatch, tmp_path) -> None:
    router = FastMCP("router")

    @router.tool(name="one_ping")
    def ping() -> str:
        return "pong"

    registry = [BackendDef(name="one-link", namespace="one", url_env="GF_ONE_URL", tags=["x"])]
    monkeypatch.setattr(snapshot_catalog, "load_registry", lambda *_: registry)
    monkeypatch.setattr(snapshot_catalog, "build_server", lambda *_, **__: router)
    out = tmp_path / "catalog.json"

    asyncio.run(snapshot_catalog._run("servers.yaml", out))

    (entry,) = json.loads(out.read_text(encoding="utf-8"))
    assert entry["name"] == "one_ping"
    assert entry["tags"] == ["x"]
