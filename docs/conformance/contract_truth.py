#!/usr/bin/env python3
"""Lint Markdown claims against a live MCP tool catalog."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

Rule = Literal[
    "unknown-argument",
    "universal-response-claim",
    "historical-record-marker",
]


@dataclass(frozen=True, order=True)
class Finding:
    """One actionable documentation contract violation."""

    path: Path
    line: int
    rule: Rule
    message: str


_INTERNAL_ROOTS = frozenset({"specs", "plans", "superpowers", "reviews"})
_HISTORICAL_ROOTS = ("specs", "plans", "superpowers")
_DATED_MARKDOWN = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
_HISTORICAL_MARKER = re.compile(r"^> Historical record(?:[ \t]*|[ \t]*—[ \t]+\S.*)$")
_CALL = re.compile(
    r"(?<![.\w])(?P<callee>[A-Za-z_][A-Za-z0-9_-]*)"
    r"\((?P<arguments>[^()\n]*)\)"
)
_KEYWORD = re.compile(r"(?:^|,)\s*(?P<name>[A-Za-z_]\w*)\s*=")
_CLAUSE = re.compile(r"[^.!?;]+(?:[.!?;]+|$)")
_EVERY_RESPONSE = re.compile(
    r"\bevery\s+(?:mcp\s+)?(?:tool\s+)?(?:response|envelope)\b"
    r"[^.!?;]*?\b(?:include|includes|contain|contains|return|returns|has|have)\b",
    re.IGNORECASE,
)
_ALL_TOOLS = re.compile(
    r"\ball\s+(?:mcp\s+)?tools\b"
    r"(?=[^.!?;]*\b(?:return|returns|respond|responds|response|envelope)\b)"
    r"[^.!?;]*?\b(?:return|returns|respond|responds|include|includes|contain|contains|"
    r"produce|produces|use|uses|have|has)\b",
    re.IGNORECASE,
)
_EXPLICIT_EXCEPTION = re.compile(r"\bexcept[ \t]+(?:`[^`\n]+`|[A-Za-z0-9_][\w.-]*)", re.IGNORECASE)
_UNIVERSAL_SUBJECT = re.compile(
    r"\b(?:all\s+(?:mcp\s+)?tools|"
    r"every\s+(?:mcp\s+)?(?:tool\s+)?(?:response|envelope))\b",
    re.IGNORECASE,
)
_CONTRAST = re.compile(r"\b(?:while|whereas|but)\b", re.IGNORECASE)
_CANONICAL_DISCLAIMER = (
    "Research use only. Not clinical decision support. Do not use for diagnosis, "
    "treatment, triage, or patient management."
)
_ALLOWLISTED_PROSE = frozenset({_CANONICAL_DISCLAIMER})


def active_markdown_files(root: Path) -> list[Path]:
    """Return active Markdown files in deterministic order."""
    files = [path for name in ("README.md", "CHANGELOG.md") if (path := root / name).is_file()]
    docs = root / "docs"
    if docs.is_dir():
        files.extend(
            path
            for path in docs.glob("**/*.md")
            if path.is_file()
            and _relative_parts(path, docs)
            and _relative_parts(path, docs)[0] not in _INTERNAL_ROOTS
        )
    return sorted(files)


def historical_markdown_files(root: Path) -> list[Path]:
    """Return dated Markdown records below the historical documentation roots."""
    docs = root / "docs"
    files: list[Path] = []
    for directory_name in _HISTORICAL_ROOTS:
        directory = docs / directory_name
        if directory.is_dir():
            files.extend(
                path
                for path in directory.glob("**/*.md")
                if path.is_file() and _DATED_MARKDOWN.fullmatch(path.name)
            )
    return sorted(files)


def lint_repository(
    root: Path,
    catalog: Mapping[str, Mapping[str, object]],
) -> list[Finding]:
    """Lint historical fences and active documentation against ``catalog``."""
    findings: list[Finding] = []

    for path in historical_markdown_files(root):
        finding = _lint_historical_record(root, path)
        if finding is not None:
            findings.append(finding)

    for path in active_markdown_files(root):
        text = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(root)
        findings.extend(_lint_calls(relative_path, text, catalog))
        findings.extend(_lint_universal_claims(relative_path, text))

    return sorted(
        findings,
        key=lambda finding: (
            finding.path.as_posix(),
            finding.line,
            finding.rule,
            finding.message,
        ),
    )


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    return path.relative_to(root).parts


def _lint_historical_record(root: Path, path: Path) -> Finding | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    index = _first_historical_prose_line(lines)
    candidate = lines[index].strip() if index < len(lines) else ""
    if _HISTORICAL_MARKER.fullmatch(candidate):
        return None
    return Finding(
        path=path.relative_to(root),
        line=index + 1,
        rule="historical-record-marker",
        message=(
            "first prose block must begin with '> Historical record' or '> Historical record — …'"
        ),
    )


def _first_historical_prose_line(lines: list[str]) -> int:
    index = 0
    if lines and lines[0].strip() == "---":
        index = 1
        while index < len(lines) and lines[index].strip() != "---":
            index += 1
        if index < len(lines):
            index += 1

    index = _skip_blank_lines(lines, index)
    if index < len(lines) and re.match(r"^#(?:\s|$)", lines[index].strip()):
        index += 1

    while True:
        index = _skip_blank_lines(lines, index)
        if index >= len(lines) or not _is_metadata_line(lines[index]):
            return index
        index += 1


def _skip_blank_lines(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _is_metadata_line(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r"^(?:[-*]\s+)?\*\*(?:Date|Status):\*\*", stripped))


def _lint_calls(
    path: Path,
    text: str,
    catalog: Mapping[str, Mapping[str, object]],
) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for call in _CALL.finditer(line):
            callee = call.group("callee")
            if callee not in catalog:
                continue
            allowed = _allowed_keywords(catalog[callee])
            for keyword in _KEYWORD.finditer(call.group("arguments")):
                name = keyword.group("name")
                if name not in allowed:
                    findings.append(
                        Finding(
                            path=path,
                            line=line_number,
                            rule="unknown-argument",
                            message=(
                                f"{callee}.{name} is absent from the live inputSchema.properties"
                            ),
                        )
                    )
    return findings


def _allowed_keywords(tool: Mapping[str, object]) -> frozenset[str]:
    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, Mapping):
        return frozenset()
    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return frozenset()
    return frozenset(key for key in properties if isinstance(key, str))


def _lint_universal_claims(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for block_start, block in _prose_blocks(text):
        if _is_allowlisted_prose_block(block):
            continue
        for clause_match in _CLAUSE.finditer(block):
            clause = clause_match.group()
            claims = [*_EVERY_RESPONSE.finditer(clause), *_ALL_TOOLS.finditer(clause)]
            for claim in sorted(claims, key=lambda match: (match.start(), match.end())):
                if _claim_is_qualified(clause, claim):
                    continue
                claim_start = block_start + clause_match.start() + claim.start()
                finding = Finding(
                    path=path,
                    line=text.count("\n", 0, claim_start) + 1,
                    rule="universal-response-claim",
                    message="unqualified universal MCP response or envelope claim",
                )
                if finding not in findings:
                    findings.append(finding)
    return findings


def _prose_blocks(text: str) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    start = 0
    for separator in re.finditer(r"\n[ \t]*\n", text):
        block = text[start : separator.start()]
        if block.strip():
            blocks.append((start, block))
        start = separator.end()
    final_block = text[start:]
    if final_block.strip():
        blocks.append((start, final_block))
    return blocks


def _is_allowlisted_prose_block(text: str) -> bool:
    return _normalized_prose(text) in _ALLOWLISTED_PROSE


def _normalized_prose(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*>\s?", "", line)
        if line.strip() == "[!IMPORTANT]":
            continue
        lines.append(line.strip())
    return " ".join(" ".join(lines).split())


def _claim_is_qualified(clause: str, claim: re.Match[str]) -> bool:
    before = clause[: claim.start()]
    if re.search(r"\bnot\s*$", before, re.IGNORECASE):
        return True
    if _EXPLICIT_EXCEPTION.search(claim.group()):
        return True
    postfix = clause[claim.end() :]
    exception = _EXPLICIT_EXCEPTION.search(postfix)
    if exception is None:
        return False
    intervening = postfix[: exception.start()]
    return not (_CONTRAST.search(intervening) or _UNIVERSAL_SUBJECT.search(intervening))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the contract-truth linter from a JSON live-catalog capture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error("--root must be an existing directory")
    with args.catalog.open(encoding="utf-8") as catalog_file:
        raw_catalog = json.load(catalog_file)
    if not isinstance(raw_catalog, Mapping):
        parser.error("--catalog must contain a JSON object")
    for tool_name, tool in raw_catalog.items():
        if not isinstance(tool, Mapping):
            parser.error(f"catalog entry {tool_name} must be a JSON object")
        input_schema = tool.get("inputSchema")
        if not isinstance(input_schema, Mapping):
            parser.error(f"catalog entry {tool_name} must have an object inputSchema")
        if not isinstance(input_schema.get("properties"), Mapping):
            parser.error(f"catalog entry {tool_name} inputSchema must have object properties")
    catalog = cast(Mapping[str, Mapping[str, object]], raw_catalog)

    findings = lint_repository(args.root, catalog)
    for finding in findings:
        print(f"{finding.path.as_posix()}:{finding.line}: {finding.rule}: {finding.message}")
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
