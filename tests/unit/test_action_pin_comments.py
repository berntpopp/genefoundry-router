"""Regression guard: a SHA pin's ``# vX.Y.Z`` comment must not lie.

Actions are SHA-pinned for supply-chain integrity, but the SHA is opaque to a human
reviewer — the trailing ``# vX.Y.Z`` comment is the *only* readable half of the pin, and
it is what a reviewer, an auditor, and Dependabot's changelog all read. Nothing verified
it: ``make lint-actions`` runs ``actionlint``, which checks workflow *syntax* and never
looks at the comment. So the comment could drift from the SHA and CI stayed green.

2026-07-30: it did. ``actions/setup-python@5fda3b95…`` is tag **v7.0.0**, but five uses
across the two *fleet-wide reusable* workflows (``_container-ci.yml``,
``_container-release.yml``) were commented ``# v6.0.0`` while the same SHA was correctly
commented ``# v7.0.0`` in ``ci.yml``, ``drift.yml`` and ``fleet-probe.yml``. Every one of
the 21 ``-link`` backends consumes those two reusable workflows, so the misleading
comment was the fleet's audit surface.

This guard needs no network: a single SHA cannot be two different versions, so requiring
*internal consistency* — one SHA, one version comment — catches exactly this defect
offline and deterministically.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# `uses: owner/repo[/sub/path]@ref  # comment`
_USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<action>[^@\s]+)@(?P<ref>\S+)(?:\s*#\s*(?P<comment>.+?))?\s*$"
)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def _iter_uses() -> list[tuple[Path, int, str, str, str | None]]:
    files = sorted([*_WORKFLOWS.glob("*.yml"), *_WORKFLOWS.glob("*.yaml")])
    assert files, f"no workflow files found under {_WORKFLOWS}"
    found: list[tuple[Path, int, str, str, str | None]] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = _USES.match(line)
            if match:
                found.append(
                    (
                        path,
                        lineno,
                        match.group("action"),
                        match.group("ref"),
                        match.group("comment"),
                    )
                )
    return found


def test_external_actions_are_sha_pinned_with_a_version_comment() -> None:
    """Every third-party ``uses:`` is a 40-hex SHA and carries a readable version."""
    problems: list[str] = []
    for path, lineno, action, ref, comment in _iter_uses():
        if action.startswith("./"):  # local reusable workflow — no SHA to pin
            continue
        if not _SHA.match(ref):
            problems.append(f"{path.name}:{lineno} {action}@{ref} is not SHA-pinned")
        elif not comment:
            problems.append(f"{path.name}:{lineno} {action}@{ref[:12]} has no version comment")
    assert not problems, "unpinned or undocumented actions:\n" + "\n".join(problems)


def test_one_sha_maps_to_exactly_one_version_comment() -> None:
    """The same SHA must never be documented as two different versions.

    Keyed on the action's *base* repository so that sub-path actions of one release
    (``github/codeql-action/init`` and ``…/analyze``) are compared against each other.
    """
    seen: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for path, lineno, action, ref, comment in _iter_uses():
        if action.startswith("./") or not _SHA.match(ref) or not comment:
            continue
        base = "/".join(action.split("/")[:2])
        seen[(base, ref)][comment].append(f"{path.name}:{lineno}")

    conflicts: list[str] = []
    for (base, ref), comments in sorted(seen.items()):
        if len(comments) > 1:
            detail = "; ".join(
                f"{comment!r} at {', '.join(sites)}" for comment, sites in sorted(comments.items())
            )
            conflicts.append(
                f"{base}@{ref[:12]} is documented as {len(comments)} versions: {detail}"
            )

    assert not conflicts, (
        "a SHA pin's version comment contradicts itself — one of these is wrong:\n"
        + "\n".join(conflicts)
    )


def test_codeql_steps_use_one_release_per_workflow() -> None:
    """CodeQL init/analyze must move atomically to one reviewed release."""
    by_workflow: dict[Path, set[tuple[str, str | None]]] = defaultdict(set)
    for path, _lineno, action, ref, comment in _iter_uses():
        if action.startswith("github/codeql-action/"):
            by_workflow[path].add((ref, comment))

    conflicts = {path.name: sorted(pins) for path, pins in by_workflow.items() if len(pins) != 1}
    assert not conflicts, f"CodeQL steps use mixed releases: {conflicts}"
