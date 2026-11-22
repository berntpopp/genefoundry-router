"""Workflow skills as ``search_tools`` hits (#336).

Each ``skills/<name>/SKILL.md`` becomes a synthetic BM25 document named ``skill_<name>``
(text = name + description). It is ranked with the tools but serializes as a pointer to
the ``skill://`` resource, never in the listing or the call catalog. ``call_tool`` with its
name (or ``read_resource`` with its URI) returns the same SKILL.md text, for clients
that cannot read resources (#368).
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from fastmcp.skills import Skill
from fastmcp.tools.base import Tool

SKILL_PREFIX = "skill_"
# The names tools-only agents guess for reading a resource (#368); skill:// URIs only.
READ_ALIASES = frozenset({"read_resource", "resources/read"})


def skill_tools(skills_dir: Path) -> list[Tool]:
    """One synthetic search document per ``<skills_dir>/<name>/SKILL.md``."""
    skills = [
        Skill.from_directory(p.parent)
        for p in sorted(skills_dir.glob("*/SKILL.md"))
        if not p.parent.name.startswith(".")  # the provider skips dot-folders too
    ]
    return [
        Tool(
            name=f"{SKILL_PREFIX}{s.name}",
            description=s.description,
            parameters={"type": "object", "properties": {}},
        )
        for s in skills
    ]


def skill_uri(name: str) -> str:
    """The ``skill://`` resource behind the ``skill_<name>`` search document."""
    return f"skill://{name.removeprefix(SKILL_PREFIX)}/SKILL.md"


def skill_call_uri(name: str, arguments: Any) -> str | None:
    """The ``skill://`` URI a ``call_tool`` target asks for: ``skill_<name>``, or a read
    alias with a ``skill://<name>/SKILL.md`` ``uri``; ``None`` for anything else."""
    if name.startswith(SKILL_PREFIX):
        return skill_uri(name)
    if name not in READ_ALIASES:
        return None
    if isinstance(arguments, str):  # strict clients send arguments as a JSON string
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return None
    uri = arguments.get("uri") if isinstance(arguments, dict) else None
    if isinstance(uri, str) and uri.startswith("skill://") and uri.endswith("/SKILL.md"):
        return uri
    return None


def skill_hit(tool: Tool, strict: bool = False) -> dict[str, Any]:
    """The pointer a skill hit serializes to in every detail mode (no inputSchema); the
    ``call`` form matches the listed ``call_tool`` schema (strict: ``arguments`` a string)."""
    uri = skill_uri(tool.name)
    return {
        "name": tool.name,
        "skill": uri,
        "description": tool.description or "",
        "read": {"method": "resources/read", "uri": uri},
        "call": {
            "tool": "call_tool",
            "arguments": {"name": tool.name, "arguments": "{}" if strict else {}},
        },
    }


def serve(ranked: Sequence[Tool], served: int, skills: Collection[str]) -> list[Tool]:
    """The top ``served`` tools plus the best skill ranked within the top ``served`` (#368):
    a skill hit rides along instead of taking a tool's slot."""
    best = next((t.name for t in ranked[:served] if t.name in skills), None)
    keep = {best, *[t.name for t in ranked if t.name not in skills][:served]}
    return [t for t in ranked if t.name in keep]
