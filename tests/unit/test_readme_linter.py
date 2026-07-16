"""Regression tests for the vendored README-standard linter."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

from scripts import check_readme


def test_repo_slug_uses_origin_when_checkout_directory_is_a_worktree_name(
    tmp_path: Path, monkeypatch
) -> None:
    checkout = tmp_path / "temporary-release-worktree"
    git = which("git")
    assert git is not None
    subprocess.run([git, "init", "-q", str(checkout)], check=True)  # noqa: S603
    subprocess.run(  # noqa: S603 -- fixed git argv in a temporary test repository
        [
            git,
            "-C",
            str(checkout),
            "remote",
            "add",
            "origin",
            "git@github.com:berntpopp/clingen-link.git",
        ],
        check=True,
    )
    monkeypatch.setattr(check_readme, "ROOT", checkout)

    assert check_readme.repo_slug() == "clingen-link"
