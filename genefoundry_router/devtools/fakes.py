"""Manifest models and FastMCP fakes for the offline fleet harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool, ToolResult
from pydantic import BaseModel, ConfigDict, Field


class _EchoTool(Tool):
    """A Tool subclass that echoes its call arguments back as structured data.

    The ``parameters`` field is set at construction time to the captured JSON
    Schema, so the BM25 index on the router side sees the same parameter
    names and descriptions as the real production tool.
    """

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return self.convert_result({"tool": self.name, "args": arguments})


def build_fake_tool(
    name: str,
    description: str,
    input_schema: dict[str, Any],
    tags: list[str] | None = None,
) -> Tool:
    """Build an echo tool that advertises ``input_schema`` verbatim.

    The captured JSON Schema is passed directly to ``_EchoTool`` as
    ``parameters`` so the gateway's BM25 index sees the same parameter
    names/descriptions as production (the search text is name + description +
    param names + param descriptions).  The echo body accepts any arguments
    dict and returns them, allowing call round-trip verification.

    Note: ``Tool.from_function`` rejects ``**kwargs`` signatures in fastmcp
    3.4.2 (``ParsedFunction.from_function`` raises ``ValueError``).  Direct
    subclass construction with ``parameters=input_schema`` is the correct
    mechanism for injecting an arbitrary schema.
    """
    return _EchoTool(
        name=name,
        description=description,
        parameters=input_schema,
        tags=set(tags or []),
    )


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(  # noqa: N815
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    outputSchema: dict[str, Any] | None = None  # noqa: N815
    annotations: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)


class BackendSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)


class SnapshotMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    captured_at: str
    source: str
    router_servers_file: str


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_meta: SnapshotMeta
    backends: dict[str, BackendSpec]


def make_backend_from_spec(namespace: str, spec: BackendSpec) -> FastMCP:
    """Build a FastMCP fake for one backend from its manifest spec.

    ``outputSchema`` and ``annotations`` are captured in ``ToolSpec`` for snapshot
    fidelity but are intentionally not forwarded to the fake (spec §6.1): they are
    not BM25-searched (§3.1) and no test asserts on them.
    """
    server = FastMCP(namespace)
    for tool in spec.tools:
        server.add_tool(build_fake_tool(tool.name, tool.description, tool.inputSchema, tool.tags))
    return server


def load_manifest(path: str | Path) -> Manifest:
    """Load and validate the committed fleet manifest."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Manifest.model_validate(data)
