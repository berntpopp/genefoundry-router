"""Every workflow's setup-python must match `.python-version` (the fleet's single Python).

The reusable `_container-*.yml` workflows `uv sync` both this repo's tools and the caller
repo with a pinned uv that does not download interpreters, so a drifted `python-version`
fails every backend that declares `requires-python = ">=<.python-version>"`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


def _setup_python_versions() -> list[tuple[str, str]]:
    found = []
    for path in WORKFLOWS:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("actions/setup-python@"):
                    found.append((path.name, str((step.get("with") or {}).get("python-version"))))
    return found


def test_setup_python_matches_python_version_file() -> None:
    expected = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    found = _setup_python_versions()
    names = {name for name, _ in found}
    assert {"_container-ci.yml", "_container-release.yml", "ci.yml"} <= names
    assert [f for f in found if f[1] != expected] == []
