#!/usr/bin/env python
"""Enforce a per-file line budget across the package.

Hard cap: 600 lines per Python module in ``genefoundry_router/``. Files listed in
``.loc-allowlist`` (``path<TAB>ceiling``) are grandfathered at their recorded
ceiling. Tests are exempt. Exits non-zero on any violation.
"""

from __future__ import annotations

import sys
from pathlib import Path

HARD_CAP = 600
ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["genefoundry_router"]
ALLOWLIST = ROOT / ".loc-allowlist"


def load_allowlist() -> dict[str, int]:
    ceilings: dict[str, int] = {}
    if not ALLOWLIST.exists():
        return ceilings
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            ceilings[parts[0]] = int(parts[1])
    return ceilings


def iter_py_files() -> list[Path]:
    files: list[Path] = []
    for target in TARGETS:
        base = ROOT / target
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def main() -> int:
    ceilings = load_allowlist()
    violations: list[str] = []
    for path in iter_py_files():
        rel = path.relative_to(ROOT).as_posix()
        count = len(path.read_text(encoding="utf-8").splitlines())
        ceiling = ceilings.get(rel, HARD_CAP)
        if count > ceiling:
            violations.append(f"{rel}: {count} lines > {ceiling}")
    if violations:
        print("Line budget violations:")
        for v in violations:
            print(f"  {v}")
        return 1
    print("Line budget OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
