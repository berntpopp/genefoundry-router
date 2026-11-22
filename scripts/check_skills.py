#!/usr/bin/env python
"""Lint the research workflow skills under ``skills/`` (#336).

Per skill: agentskills.io frontmatter (``name`` = folder, ``a-z0-9-``, <= 64 chars;
``description`` <= 1024 chars starting "Use when"), SKILL.md < 20000 bytes, the shared
rules block byte-identical to the canonical copy in ``skills/README.md``, and every
backticked ``<namespace>_<tool>`` in any of the skill's Markdown files present in the
catalog snapshot. Only names whose prefix is a ``servers.yaml`` namespace are checked,
so ``call_tool``/``search_tools`` are ignored. Exits non-zero on any error.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "tests" / "discoverability" / "catalog.json"  # refreshed by snapshot-catalog
MAX_BYTES = 20_000
NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
TOOL_RE = re.compile(r'(?:`|name=")([a-z0-9]+)_([a-z0-9_]+)[`("]')  # `tool` or call_tool(name="tool")
LINK_RE = re.compile(r"`((?:references|scripts|assets)/[^`\s]+\.md)`|\]\(([^)#\s]+\.md)\)")
CALLS_RE = re.compile(r"<!-- tool-calls:start -->\n.*?<!-- tool-calls:end -->", re.S)
SIG_RE = re.compile(r"Signature: [a-z0-9_]+\(([^)]*)\)")
BLOCK_RE = re.compile(r"<!-- shared-rules:start -->\n(.*?)\n<!-- shared-rules:end -->", re.S)
# Thoroughness wording makes strong models over-call tools (Anthropic/OpenAI prompting guides).
THOROUGH_RE = re.compile(
    r"\b(if in doubt|check all|check every|always call|always check|be thorough|exhaustive"
    r"|every source|all sources)\b",
    re.I,
)


def canonical_block(readme: Path) -> str:
    match = BLOCK_RE.search(readme.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"{readme}: no shared-rules markers")
    return match.group(1)


def catalog_names(path: Path = CATALOG) -> set[str]:
    return {entry["name"] for entry in json.loads(path.read_text(encoding="utf-8"))}


def catalog_entries(path: Path = CATALOG) -> dict[str, dict[str, Any]]:
    return {entry["name"]: entry for entry in json.loads(path.read_text(encoding="utf-8"))}


def pinned_tools() -> set[str]:
    servers = yaml.safe_load((ROOT / "servers.yaml").read_text(encoding="utf-8"))["servers"]
    return {
        f"{s.get('namespace', s['name'])}_{leaf}"
        for s in servers
        if s.get("enabled", True)
        for leaf in s.get("entrypoints") or []
    }


def _arguments(entry: dict[str, Any]) -> str:
    """Argument names: required first when the description carries a signature."""
    skip = {"response_mode", "offset", "cursor", "request_id"}
    sig = SIG_RE.search(entry.get("description") or "")
    if not sig:
        return ", ".join(a for a in entry.get("params") or {} if a not in skip)
    parts = [a.strip() for a in sig.group(1).split(",") if a.strip()]
    req = [a for a in parts if not a.endswith("=")]
    opt = [a.rstrip("=") for a in parts if a.endswith("=") and a.rstrip("=") not in skip]
    return ", ".join(req) + (f"; optional: {', '.join(opt)}" if opt else "")


def calls_block(skill_dir: Path, catalog: dict[str, dict[str, Any]], pinned: set[str]) -> str:
    """The generated call table: every catalog tool the skill names, with its arguments."""
    text = " ".join(md.read_text(encoding="utf-8") for md in sorted(skill_dir.rglob("*.md")))
    tools = sorted({f"{ns}_{leaf}" for ns, leaf in TOOL_RE.findall(text)} & set(catalog))
    lines = [
        f"- `{t}`({_arguments(catalog[t])}) — {'direct' if t in pinned else 'via call_tool'}"
        for t in tools
    ]
    return "<!-- tool-calls:start -->\n" + "\n".join(lines) + "\n<!-- tool-calls:end -->"


def _namespaces() -> set[str]:
    servers = yaml.safe_load((ROOT / "servers.yaml").read_text(encoding="utf-8"))["servers"]
    return {s.get("namespace", s["name"]) for s in servers}


def _frontmatter(text: str) -> dict[str, object]:
    parts = text.split("---\n", 2)
    if not text.startswith("---\n") or len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else {}


def check_skills(
    skills_dir: Path,
    catalog_names: set[str],
    catalog: dict[str, dict[str, Any]] | None = None,
    pinned: set[str] | None = None,
    write: bool = False,
) -> list[str]:
    """Error strings for every skill under ``skills_dir`` (empty list = pass)."""
    block = canonical_block(skills_dir / "README.md")
    namespaces = _namespaces()
    errors: list[str] = []
    for folder_path in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        if not (folder_path / "SKILL.md").is_file():
            errors.append(f"{folder_path.name}: no SKILL.md (the server would skip it silently)")
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        folder = skill_md.parent.name
        text = skill_md.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        name, description = meta.get("name"), meta.get("description")
        if not (isinstance(name, str) and NAME_RE.fullmatch(name) and len(name) <= 64):
            errors.append(f"{folder}: name {name!r} must be a-z0-9-, <= 64 chars")
        elif name != folder:
            errors.append(f"{folder}: name {name!r} must equal the folder name")
        if not (
            isinstance(description, str)
            and description.startswith("Use when")
            and len(description) <= 1024
        ):
            errors.append(f"{folder}: description must start 'Use when' and be <= 1024 chars")
        size = len(text.encode("utf-8"))
        if size >= MAX_BYTES:
            errors.append(f"{folder}: SKILL.md is {size} bytes (limit < {MAX_BYTES})")
        if block not in text:
            errors.append(f"{folder}: shared rules block missing or altered (see skills/README.md)")
        if catalog is not None:
            fresh = calls_block(skill_md.parent, catalog, pinned or set())
            current = CALLS_RE.search(text)
            if current is None:
                errors.append(f"{folder}: no tool-calls block (run check_skills.py --write)")
            elif current.group(0) != fresh:
                if write:
                    skill_md.write_text(text.replace(current.group(0), fresh), encoding="utf-8")
                else:
                    errors.append(
                        f"{folder}: tool-calls block is stale (run check_skills.py --write)"
                    )
        for md in sorted(skill_md.parent.rglob("*.md")):
            for ns, leaf in TOOL_RE.findall(md.read_text(encoding="utf-8")):
                tool = f"{ns}_{leaf}"
                if ns in namespaces and tool not in catalog_names:
                    where = md.relative_to(skills_dir).as_posix()
                    errors.append(f"{folder}: unknown tool `{tool}` in {where}")
            for pair in LINK_RE.findall(md.read_text(encoding="utf-8")):
                link = pair[0] or pair[1]
                if not any((base / link).is_file() for base in (skill_md.parent, md.parent)):
                    where = md.relative_to(skills_dir).as_posix()
                    errors.append(f"{folder}: broken link `{link}` in {where}")
    return errors


def thoroughness_warnings(skills_dir: Path) -> list[str]:
    """Warnings for over-eager wording in SKILL.md, outside the shared and generated blocks."""
    block = canonical_block(skills_dir / "README.md")
    warnings: list[str] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8").replace(block, "\n" * block.count("\n"))
        for region in (BLOCK_RE, CALLS_RE):  # blank out, keeping newlines so line numbers hold
            text = region.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        for number, line in enumerate(text.splitlines(), 1):
            for match in THOROUGH_RE.finditer(line):
                warnings.append(
                    f"warning: {skill_md.parent.name}:{number}: thoroughness wording "
                    f"{match.group(0)!r} invites tool over-use"
                )
    return warnings


def main() -> int:
    entries = catalog_entries()
    write = "--write" in sys.argv[1:]
    errors = check_skills(ROOT / "skills", set(entries), entries, pinned_tools(), write)
    for message in thoroughness_warnings(ROOT / "skills") + errors:
        print(message, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
