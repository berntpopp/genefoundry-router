"""Real-Git regressions for protected source-release validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from genefoundry_router.release.source import (
    CommandResult,
    SourceReleaseError,
    run_command,
    validate_source_release,
)


def _run(args: tuple[str, ...]) -> CommandResult:
    result = run_command(args)
    assert result.returncode == 0, result.stderr
    return result


def _commit(repository: Path, message: str) -> str:
    _run(("git", "-C", str(repository), "add", "."))
    _run(
        (
            "git",
            "-C",
            str(repository),
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.test",
            "commit",
            "--quiet",
            "-m",
            message,
        )
    )
    return _run(("git", "-C", str(repository), "rev-parse", "HEAD^{commit}")).stdout.strip()


def _write_release_tree(repository: Path, version: str) -> None:
    workflow = repository / ".github" / "workflows" / "container-release.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("name: release\n", encoding="utf-8")
    (repository / "container-release.json").write_text("{}\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text(
        f'[project]\nname = "release-test"\nversion = "{version}"\n', encoding="utf-8"
    )
    (repository / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{version}] - 2026-07-13\n", encoding="utf-8"
    )


def _create_origin_with_newer_remote_tag(tmp_path: Path) -> tuple[Path, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(("git", "init", "--bare", "--quiet", str(origin)))
    _run(("git", "init", "--quiet", str(seed)))
    _run(("git", "-C", str(seed), "remote", "add", "origin", str(origin)))

    _write_release_tree(seed, "1.2.3")
    release_revision = _commit(seed, "release 1.2.3")
    _run(("git", "-C", str(seed), "update-ref", "refs/tags/v1.2.3", release_revision))

    _write_release_tree(seed, "1.2.4")
    newer_revision = _commit(seed, "release 1.2.4")
    _run(("git", "-C", str(seed), "update-ref", "refs/tags/v1.2.4", newer_revision))
    _run(
        (
            "git",
            "-C",
            str(seed),
            "push",
            "--quiet",
            "origin",
            "HEAD:refs/heads/main",
            "refs/tags/v1.2.3",
            "refs/tags/v1.2.4",
        )
    )
    return origin, release_revision


def _create_single_release_origin(tmp_path: Path, *, annotated: bool) -> tuple[Path, Path, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(("git", "init", "--bare", "--quiet", str(origin)))
    _run(("git", "init", "--quiet", str(seed)))
    _run(("git", "-C", str(seed), "remote", "add", "origin", str(origin)))
    (seed / "README.md").write_text("bootstrap\n", encoding="utf-8")
    _commit(seed, "bootstrap")
    _write_release_tree(seed, "1.2.3")
    revision = _commit(seed, "release 1.2.3")
    if annotated:
        _run(
            (
                "git",
                "-C",
                str(seed),
                "-c",
                "tag.gpgSign=false",
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release@example.test",
                "tag",
                "--annotate",
                "--message",
                "release 1.2.3",
                "v1.2.3",
                revision,
            )
        )
    else:
        _run(("git", "-C", str(seed), "update-ref", "refs/tags/v1.2.3", revision))
    _run(
        (
            "git",
            "-C",
            str(seed),
            "push",
            "--quiet",
            "origin",
            "HEAD:refs/heads/main",
            "refs/tags/v1.2.3",
        )
    )
    _run(("git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"))
    return origin, seed, revision


def _clone_release(origin: Path, checkout: Path) -> None:
    _run(("git", "clone", "--quiet", str(origin), str(checkout)))
    _run(("git", "-C", str(checkout), "checkout", "--quiet", "v1.2.3"))


def _validate_checkout(checkout: Path, revision: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(checkout)
    return validate_source_release(
        event_name="push",
        event_ref="refs/tags/v1.2.3",
        event_sha=revision,
        changelog_text=(checkout / "CHANGELOG.md").read_text(encoding="utf-8"),
    )


def test_real_git_ls_tree_reports_absent_exact_path_as_successful_empty_output(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _run(("git", "init", "--quiet", str(repository)))
    (repository / "present.txt").write_text("present\n", encoding="utf-8")
    revision = _commit(repository, "initial")
    _run(("git", "-C", str(repository), "update-ref", "refs/tags/v1.2.3", revision))

    result = run_command(
        (
            "git",
            "-C",
            str(repository),
            "ls-tree",
            "refs/tags/v1.2.3",
            "--",
            "container-release.json",
        )
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_shallow_tag_checkout_uses_complete_remote_tags_for_monotonic_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, release_revision = _create_origin_with_newer_remote_tag(tmp_path)
    checkout = tmp_path / "checkout"
    _run(
        (
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--no-tags",
            "--branch",
            "v1.2.3",
            f"file://{origin}",
            str(checkout),
        )
    )
    local_tags = _run(("git", "-C", str(checkout), "tag", "--list")).stdout.splitlines()
    assert "v1.2.4" not in local_tags

    monkeypatch.chdir(checkout)
    with pytest.raises(SourceReleaseError, match=r"greater than previous stable tag v1\.2\.4"):
        validate_source_release(
            event_name="push",
            event_ref="refs/tags/v1.2.3",
            event_sha=release_revision,
            changelog_text=(checkout / "CHANGELOG.md").read_text(encoding="utf-8"),
        )


@pytest.mark.parametrize("annotated", [False, True])
def test_real_lightweight_and_annotated_tag_identities_validate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, annotated: bool
) -> None:
    origin, _seed, revision = _create_single_release_origin(tmp_path, annotated=annotated)
    checkout = tmp_path / "checkout"
    _clone_release(origin, checkout)

    source = _validate_checkout(checkout, revision, monkeypatch)

    assert source.revision == revision


def test_reannotated_remote_tag_is_rejected_even_when_commit_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, seed, revision = _create_single_release_origin(tmp_path, annotated=True)
    checkout = tmp_path / "checkout"
    _clone_release(origin, checkout)
    _run(("git", "-C", str(seed), "tag", "--delete", "v1.2.3"))
    _run(
        (
            "git",
            "-C",
            str(seed),
            "-c",
            "tag.gpgSign=false",
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=release@example.test",
            "tag",
            "--annotate",
            "--message",
            "replacement annotation",
            "v1.2.3",
            revision,
        )
    )
    _run(
        (
            "git",
            "-C",
            str(seed),
            "push",
            "--quiet",
            "--force",
            "origin",
            "refs/tags/v1.2.3",
        )
    )

    with pytest.raises(SourceReleaseError, match="remote tag"):
        _validate_checkout(checkout, revision, monkeypatch)


def test_shallow_tag_checkout_fails_when_remote_main_commit_is_absent_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, seed, revision = _create_single_release_origin(tmp_path, annotated=False)
    (seed / "after-release.txt").write_text("main advanced\n", encoding="utf-8")
    _commit(seed, "advance main")
    _run(("git", "-C", str(seed), "push", "--quiet", "origin", "HEAD:refs/heads/main"))
    checkout = tmp_path / "checkout"
    _run(
        (
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--no-tags",
            "--branch",
            "v1.2.3",
            f"file://{origin}",
            str(checkout),
        )
    )

    with pytest.raises(SourceReleaseError, match="available locally"):
        _validate_checkout(checkout, revision, monkeypatch)


def test_stale_local_origin_main_does_not_influence_remote_main_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, _seed, revision = _create_single_release_origin(tmp_path, annotated=False)
    checkout = tmp_path / "checkout"
    _clone_release(origin, checkout)
    stale_revision = _run(("git", "-C", str(checkout), "rev-parse", f"{revision}^")).stdout.strip()
    _run(
        (
            "git",
            "-C",
            str(checkout),
            "update-ref",
            "refs/remotes/origin/main",
            stale_revision,
        )
    )

    source = _validate_checkout(checkout, revision, monkeypatch)

    assert source.revision == revision
