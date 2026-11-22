"""Workflow skills as ``search_tools`` hits (#336).

Each ``skills/<name>/SKILL.md`` becomes a synthetic BM25 document named ``skill_<name>``
(text = name + description). It is ranked with the tools but serializes as a pointer to
the ``skill://`` resource, never in the listing or the call catalog. ``call_tool`` with its
name (or ``read_resource`` with its URI) returns the same SKILL.md text, for clients
that cannot read resources (#368), plus a footer naming its supporting files, which
``read_resource`` opens the same way (#370).
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
# Fixed text for a missed skill read (#372): read_resource EXISTS, so "tool is not
# available" sends models hunting for another tool. Never echoes the caller's URI/name.
SKILL_MISS_MESSAGE = (
    "No skill file at that URI; only skill:// files listed in a skill's router note "
    "can be read via call_tool."
)


def is_skill_name(name: object) -> bool:
    """True for a ``call_tool`` target that asks for a skill file (#372)."""
    return isinstance(name, str) and (name in READ_ALIASES or name.startswith(SKILL_PREFIX))


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
            meta={"files": [f for f in s.files if f != "SKILL.md"]},  # supporting files (#370)
        )
        for s in skills
    ]


def skill_uri(name: str) -> str:
    """The ``skill://`` resource behind the ``skill_<name>`` search document."""
    return f"skill://{name.removeprefix(SKILL_PREFIX)}/SKILL.md"


def skill_file_uris(tool: Tool) -> list[str]:
    """Every file the provider serves for this skill: SKILL.md first, then its supporting
    files. The only URIs ``call_tool`` reads (#370)."""
    base = skill_uri(tool.name).removesuffix("SKILL.md")
    return [base + f for f in ["SKILL.md", *(tool.meta or {}).get("files", [])]]


def skill_footer(tool: Tool) -> str:
    """Router note appended to a SKILL.md read through ``call_tool``: how to open each
    supporting file; empty when the skill has none (#370)."""
    calls = [
        f'- call_tool(name="read_resource", arguments={{"uri": "{uri}"}})'
        for uri in skill_file_uris(tool)[1:]
    ]
    if not calls:
        return ""
    return (
        "\n\n---\nRouter note: this skill's supporting files (open one when the steps above say to read it):\n"
        + "\n".join(calls)
        + "\n"
    )


def skill_call_uri(name: str, arguments: Any) -> str | None:
    """The ``skill://`` URI a ``call_tool`` target asks for: ``skill_<name>``, or a read
    alias with a ``skill://`` ``uri``; ``None`` for anything else. Callers still check the
    URI against :func:`skill_file_uris`."""
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
    if isinstance(uri, str) and uri.startswith("skill://"):
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
