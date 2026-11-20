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

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "tests" / "discoverability" / "catalog.json"  # refreshed by snapshot-catalog
MAX_BYTES = 20_000
NAME_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
TOOL_RE = re.compile(r"`([a-z0-9]+)_([a-z0-9_]+)[`(]")
BLOCK_RE = re.compile(r"<!-- shared-rules:start -->\n(.*?)\n<!-- shared-rules:end -->", re.S)


def canonical_block(readme: Path) -> str:
    match = BLOCK_RE.search(readme.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit(f"{readme}: no shared-rules markers")
    return match.group(1)


def catalog_names(path: Path = CATALOG) -> set[str]:
    return {entry["name"] for entry in json.loads(path.read_text(encoding="utf-8"))}


def _namespaces() -> set[str]:
    servers = yaml.safe_load((ROOT / "servers.yaml").read_text(encoding="utf-8"))["servers"]
    return {s.get("namespace", s["name"]) for s in servers}


def _frontmatter(text: str) -> dict[str, object]:
    parts = text.split("---\n", 2)
    if not text.startswith("---\n") or len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else {}


def check_skills(skills_dir: Path, catalog_names: set[str]) -> list[str]:
    """Error strings for every skill under ``skills_dir`` (empty list = pass)."""
    block = canonical_block(skills_dir / "README.md")
    namespaces = _namespaces()
    errors: list[str] = []
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
        for md in sorted(skill_md.parent.rglob("*.md")):
            for ns, leaf in TOOL_RE.findall(md.read_text(encoding="utf-8")):
                tool = f"{ns}_{leaf}"
                if ns in namespaces and tool not in catalog_names:
                    where = md.relative_to(skills_dir).as_posix()
                    errors.append(f"{folder}: unknown tool `{tool}` in {where}")
    return errors


def main() -> int:
    errors = check_skills(ROOT / "skills", catalog_names())
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
