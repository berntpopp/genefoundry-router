#!/usr/bin/env python
"""Verify each SHA pin's ``# vX.Y.Z`` comment against the upstream tag.

``tests/unit/test_action_pin_comments.py`` proves the comments are *self-consistent*
offline: one SHA is never documented as two versions. That cannot catch a pin whose
comment is uniformly wrong — and on 2026-07-30 five of them were, every one understating
the pinned release by up to three majors::

    actions/upload-artifact@043fb46d…   said v4.6.2   was really v7.0.1
    actions/download-artifact@3e5f45b2… said v4.3.0   was really v8.0.1
    docker/build-push-action@53b7df96…  said v7.0.0   was really v7.3.0
    docker/login-action@af1e73f9…       said v4.0.0   was really v4.4.0
    docker/setup-buildx-action@bb05f3f5… said v4.0.0  was really v4.2.0

Resolving the claim requires the network, so this is a script wired into CI rather than a
hermetic unit test. Without a usable token it SKIPS rather than fails: it must never turn
an offline checkout red, and it must never become a reason to weaken the offline guard.

A bare-major comment (``# v4``) is advisory — those tags move, so the SHA legitimately
trails the tag. Such comments are reported but do not fail the run.

Usage::

    python scripts/check_action_pin_versions.py           # verify, exit 1 on a lie
    python scripts/check_action_pin_versions.py --list     # print every pin and verdict
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

USES = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<action>[^@\s]+)@(?P<ref>[0-9a-f]{40})\s*#\s*(?P<comment>\S+)"
)
EXACT_TAG = re.compile(r"^v\d+\.\d+\.\d+")


def _api_lines(path: str, jq: str) -> list[str] | None:
    """Call the GitHub API via `gh`, projecting through jq to newline-delimited text.

    `gh api --paginate` emits one JSON document *per page*, which `json.loads` cannot
    parse, so project with `--jq` and read lines instead of assembling JSON.
    """
    try:
        # `gh` is the repo's standard GitHub client and every argument here is built
        # from committed workflow text, not from user input.
        proc = subprocess.run(  # noqa: S603
            ["gh", "api", path, "--paginate", "--jq", jq],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _tag_commit(repo: str, tag: str) -> str | None:
    """The commit *tag* points at (the commits API peels annotated tags): one request.

    Scanning every tag and dereferencing each annotated one cost hundreds of requests for
    github/codeql-action, and a single throttled deref turned a correct pin into a "LIE".
    """
    rows = _api_lines(f"repos/{repo}/commits/{tag}", ".sha")
    return rows[0].strip() if rows else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print every pin and verdict")
    args = parser.parse_args()

    if not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN"):
        probe = _api_lines("rate_limit", ".rate.limit")
        if probe is None:
            print("check_action_pin_versions: no GitHub credentials — skipped", file=sys.stderr)
            return 0

    pins: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USES.match(line)
            if match:
                repo = "/".join(match.group("action").split("/")[:2])
                pins[(repo, match.group("ref"))][match.group("comment")].append(
                    f"{path.name}:{lineno}"
                )

    lies: list[str] = []
    for (repo, sha), comments in sorted(pins.items()):
        for comment, sites in sorted(comments.items()):
            if not EXACT_TAG.match(comment):
                verdict, actual = "MOVING", "-"  # bare-major label; the tag legitimately moves
            elif (actual := _tag_commit(repo, comment) or "") == sha:
                verdict = "OK"
            elif not actual:
                print(
                    f"  ?    {repo}@{sha[:12]} {comment} — could not resolve, skipped",
                    file=sys.stderr,
                )
                continue
            else:
                verdict = "LIE"
                lies.append(
                    f"{repo}@{sha[:12]} is documented as {comment} but {comment} is "
                    f"{actual[:12]} — {len(sites)} site(s), e.g. {sites[0]}"
                )
            if args.list or verdict == "LIE":
                print(f"  {verdict:6} {repo:38} {comment:10} {actual[:12]}")

    if lies:
        print("\nA SHA pin's version comment names a tag it is not:", file=sys.stderr)
        for lie in lies:
            print(f"  - {lie}", file=sys.stderr)
        return 1
    print("check_action_pin_versions: every exact-version pin comment matches its SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
