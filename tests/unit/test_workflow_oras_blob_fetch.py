"""`oras blob fetch` takes exactly ONE `<name>@<digest>` reference and needs `--output -`.

A second positional argument fails at runtime ("requires exactly 1 argument but got 2"),
and only on the partial-publish recovery path, so CI never exercises it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


def _blob_fetch_calls() -> list[tuple[str, str]]:
    calls = []
    for path in WORKFLOWS:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.search(r"oras\"? blob fetch\b([^)\n]*)", line)
            if m:
                calls.append((path.name, m.group(1)))
    return calls


def test_blob_fetch_calls_exist() -> None:
    assert _blob_fetch_calls(), "guard went blind: no `oras blob fetch` call found"


def test_every_blob_fetch_has_one_digest_reference_and_stdout_output() -> None:
    for name, args in _blob_fetch_calls():
        positional = [a for a in args.split() if not a.startswith("-")]
        assert "--output -" in args, f"{name}: missing `--output -`: {args}"
        assert len(positional) == 1, f"{name}: expected one reference: {args}"
        assert re.fullmatch(r'"[^"]*@\$\w*digest"', positional[0]), f"{name}: {args}"
