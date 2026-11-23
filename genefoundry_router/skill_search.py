"""Workflow skills as ``search_tools`` hits (#336).

Each ``skills/<name>/SKILL.md`` becomes a synthetic BM25 document named ``skill_<name>``
(text = name + description). It is ranked with the tools but serializes as a pointer to
the ``skill://`` resource, never in the listing or the call catalog. ``call_tool`` with its
name (or ``read_resource`` with its URI) returns the same SKILL.md text, for clients
that cannot read resources (#368), plus a footer naming its supporting files, which
``read_resource`` opens the same way (#370). The listed ``skill`` tool returns the same text
by skill name (#384).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Collection, Sequence
from pathlib import Path
from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
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


SKILL_TOOL = "skill"  # router meta-tool, like search_tools/call_tool (#384)
_SKILL_COMMAND_HELP = 'The skill name (no arguments). E.g., "germline-variant-classification"'
# Adapted from inspect_ai 0.3.260 tool/_tools/_skill/tool.py, MIT (c) 2024 UK AI Security Institute.
# inspect_ai derives it from the Claude Code and Codex CLI skills prompts. Benchmarked verbatim (#384).
_SKILL_TOOL_HEADER = """Invoke a skill to get specialized instructions for a task.

<skills_instructions>
Skills provide specialized capabilities and domain knowledge. Each skill contains instructions, and may include references.

When to use:
- Before starting a task, check if any skill in <available_skills> matches the request
- Use the description field to determine relevance

How to invoke:
- Call this tool with the skill name only
- Example: command: "germline-variant-classification"
- Example: command: "literature-evidence-review"

After invoking:
- Follow the instructions returned by the skill
- If the skill lists reference files, load only the specific files needed, with the read_resource calls it gives — don't bulk-load

Multiple skills:
- If multiple skills apply, choose the minimal set and invoke them in sequence
- State which skills you're using and why

Important:
- When a skill is relevant, invoke this tool IMMEDIATELY as your first action — NEVER just mention a skill without actually calling this tool
- Invoke the skill tool BEFORE generating any other response about the task
- Only invoke skills listed in <available_skills>
- Do not invoke a skill that is already running
- If a skill can't be applied, state the issue and proceed with the best alternative
</skills_instructions>

"""
_WEAK_TAIL = frozenset({"a", "an", "the", "as", "or", "is", "be", "must", "to", "of", "and"})


def skill_tool_description(skills: Sequence[Tool]) -> str:
    """The ``skill`` tool's description: fixed header + ``<available_skills>`` (#384)."""
    items = "".join(
        f"<skill>\n<name>{s.name.removeprefix(SKILL_PREFIX)}</name>\n"
        f"<description>{s.description}</description></skill>\n"
        for s in sorted(skills, key=lambda s: s.name)
    )
    return f"{_SKILL_TOOL_HEADER}<available_skills>\n{items}</available_skills>\n"


def make_skill_tool(skills: Sequence[Tool], read: Callable[[str, Context], Awaitable[Any]]) -> Tool:
    """The listed ``skill`` tool: ``read`` is ``call_tool``'s skill read, so both return
    the same SKILL.md + router footer (#384)."""
    names = sorted(s.name.removeprefix(SKILL_PREFIX) for s in skills)
    miss = {  # constants only: never echoes the caller's command
        "success": False,
        "error_code": "not_found",
        "message": "No skill by that name.",
        "retryable": False,
        "suggestions": [f"Call skill(command=...) with one of: {', '.join(names)}."],
    }

    async def skill(
        command: Annotated[str, _SKILL_COMMAND_HELP],
        ctx: Context = None,  # type: ignore[assignment]
    ) -> Any:
        if command not in names:
            raise ToolError(json.dumps(miss, separators=(",", ":"), sort_keys=True))
        return await read(skill_uri(command), ctx)

    return Tool.from_function(fn=skill, name=SKILL_TOOL, description=skill_tool_description(skills))


def skill_trigger(tool: Tool) -> str:
    """A few words from the SKILL.md description saying when to load it (#376)."""
    text = re.sub(r"^Use when (asked )?(to |whether )?", "", tool.description or "")
    text = re.sub(r" \([^)]*\)", "", text)
    words = re.split(r", e\.g\.| — |; ", text)[0].split()[:5]
    cut = next((i for i, w in enumerate(words) if i >= 3 and w.endswith(",")), None)
    words = words[: cut + 1] if cut is not None else words
    while words and words[-1].rstrip(",") in _WEAK_TAIL:
        words.pop()
    return " ".join(words).rstrip(",")


def skills_line(skills: Sequence[Tool]) -> str:
    """The instructions sentence naming every served skill; empty when none (#376)."""
    if not skills:
        return ""
    named = ", ".join(f"{s.name.removeprefix(SKILL_PREFIX)} ({skill_trigger(s)})" for s in skills)
    return (
        " WORKFLOW SKILLS — before a multi-step job, load the matching one with "
        f'`skill(command="<name>")`: {named}.'
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
