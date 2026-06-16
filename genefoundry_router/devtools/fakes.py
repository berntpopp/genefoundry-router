"""Manifest models and FastMCP fakes for the offline fleet harness."""

from __future__ import annotations

from typing import Any

from fastmcp.tools import Tool, ToolResult


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
