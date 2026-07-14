#!/usr/bin/env python
"""Enforce the GeneFoundry README Standard v1.

One linter, copied verbatim into every fleet repo. It checks the things a reader
notices and a maintainer forgets: length, section order, the badge row, the
research-use callout, link integrity, and hand-typed facts that rot.

See ``docs/README-STANDARD-v1.md`` (in genefoundry-router) for the rationale.

Repo class is inferred, not configured: a backend ships
``.github/workflows/conformance.yml``; the router does not (its slot-3 badge is
``security.yml``). Content inside ``<!-- BEGIN GENERATED: x -->`` /
``<!-- END GENERATED: x -->`` markers is exempt from the hand-typed-fact rule,
because a test owns it.

Exits non-zero on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

LINE_CEILING = 200

REQUIRED_SECTIONS = [
    "Why",
    "Quick start",
    "Tools",
    "Data & provenance",
    "Documentation",
    "Contributing",
    "License",
]

RUO_CALLOUT = (
    "> [!IMPORTANT]\n"
    "> Research use only. Not clinical decision support. Do not use for diagnosis,\n"
    "> treatment, triage, or patient management."
)

# Derived facts that drift silently. Enumerations are fine; aggregates are not.
FORBIDDEN_FACTS = [
    (re.compile(r"\b\d+\s+tools?\b", re.I), "hand-typed tool count"),
    (re.compile(r"\b\d+\s+(?:tests?|passing)\b", re.I), "hand-typed test count"),
    (re.compile(r"\bcoverage[:\s]+\d+\s*%", re.I), "hand-typed coverage"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*/\s*10\b"), "self-awarded score"),
]

GENERATED_BLOCK = re.compile(
    r"<!--\s*BEGIN GENERATED:.*?-->.*?<!--\s*END GENERATED:.*?-->",
    re.S,
)


def repo_slug() -> str:
    return ROOT.name


def is_router() -> bool:
    return not (ROOT / ".github/workflows/conformance.yml").exists()


def expected_badges(slug: str) -> list[tuple[str, str]]:
    """(label, substring that must appear in the badge line), in order."""
    gate = "security.yml" if is_router() else "conformance.yml"
    gate_label = "Security" if is_router() else "Conformance"
    return [
        ("Python 3.12+", "img.shields.io/badge/python-3.12"),
        ("CI", f"/{slug}/actions/workflows/ci.yml/badge.svg"),
        (gate_label, f"/{slug}/actions/workflows/{gate}/badge.svg"),
        ("License: MIT", "img.shields.io/badge/license-MIT"),
    ]


def check_length(lines: list[str], errors: list[str]) -> None:
    if len(lines) > LINE_CEILING:
        errors.append(
            f"README is {len(lines)} lines; ceiling is {LINE_CEILING}. "
            f"Move content to docs/ (see README-STANDARD-v1 relocation table)."
        )


def check_title(lines: list[str], errors: list[str]) -> None:
    h1s = [ln for ln in lines if ln.startswith("# ")]
    if len(h1s) != 1:
        errors.append(f"expected exactly one H1, found {len(h1s)}")
        return
    if not lines or not lines[0].startswith("# "):
        errors.append("the H1 must be the first line")


def check_badges(text: str, lines: list[str], errors: list[str]) -> None:
    badge_lines = [ln for ln in lines if "badge.svg" in ln or "img.shields.io" in ln]
    want = expected_badges(repo_slug())
    if len(badge_lines) != len(want):
        errors.append(
            f"expected exactly {len(want)} badges, found {len(badge_lines)}. "
            f"Canonical row: {', '.join(label for label, _ in want)}."
        )
        return
    for i, ((label, needle), got) in enumerate(zip(want, badge_lines), start=1):
        if needle not in got:
            errors.append(
                f"badge {i} should be {label!r} (expected {needle!r} in the URL); got: {got.strip()[:80]}"
            )
    if "?branch=" in text:
        errors.append("badge URLs must not pin ?branch= — they default to the default branch")


def check_sections(lines: list[str], errors: list[str]) -> None:
    found = [ln[3:].strip() for ln in lines if ln.startswith("## ")]
    if found != REQUIRED_SECTIONS:
        errors.append(
            "H2 sections must be exactly, in order:\n"
            f"  expected: {REQUIRED_SECTIONS}\n"
            f"  found:    {found}"
        )


def check_callout(text: str, errors: list[str]) -> None:
    if RUO_CALLOUT not in text:
        errors.append(
            "missing or reworded research-use callout. It must appear verbatim:\n"
            + "\n".join(f"    {ln}" for ln in RUO_CALLOUT.splitlines())
        )


def check_links(text: str, errors: list[str]) -> None:
    for match in re.finditer(r"\[[^\]]*\]\(([^)]+)\)", text):
        target = match.group(1).split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (ROOT / target).exists():
            errors.append(f"broken relative link: {target!r} does not exist")


def check_facts(text: str, errors: list[str]) -> None:
    scrubbed = GENERATED_BLOCK.sub("", text)
    for pattern, what in FORBIDDEN_FACTS:
        for m in pattern.finditer(scrubbed):
            errors.append(
                f"{what}: {m.group(0)!r} — a hand-typed derived fact rots. "
                f"Generate it inside a GENERATED block (a test must own it), or drop it."
            )


def main() -> int:
    if not README.exists():
        print("error: README.md not found", file=sys.stderr)
        return 1

    text = README.read_text(encoding="utf-8")
    lines = text.splitlines()
    errors: list[str] = []

    check_length(lines, errors)
    check_title(lines, errors)
    check_badges(text, lines, errors)
    check_sections(lines, errors)
    check_callout(text, errors)
    check_links(text, errors)
    check_facts(text, errors)

    if errors:
        klass = "router" if is_router() else "backend"
        print(
            f"README Standard v1 violations in {repo_slug()} ({klass}):\n",
            file=sys.stderr,
        )
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(
            "\nSee docs/README-STANDARD-v1.md in genefoundry-router.",
            file=sys.stderr,
        )
        return 1

    print(f"README Standard v1: OK ({len(lines)} lines, ceiling {LINE_CEILING})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
