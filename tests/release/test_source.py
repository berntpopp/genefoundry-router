"""Tests for exact, protected source-release identity validation."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from genefoundry_router.release.source import (
    RELEASE_COMMAND_TIMEOUT_SECONDS,
    CommandResult,
    SourceReleaseError,
    ensure_version_increases,
    parse_release_tag,
    resolve_event_tag,
    run_command,
    validate_source_release,
)

TAG = "v1.2.3"
VERSION = "1.2.3"
SOURCE_SHA = "a" * 40
REMOTE_SHA = "b" * 40
TREE_ENTRY_SHA = "c" * 40
MAIN_SHA = "f" * 40


@dataclass
class RecordingRunner:
    """Return exact synthetic command results and retain argument arrays."""

    results: dict[tuple[str, ...], CommandResult]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, args: Sequence[str]) -> CommandResult:
        command = tuple(args)
        self.calls.append(command)
        try:
            return self.results[command]
        except KeyError as exc:
            raise AssertionError(f"unexpected command: {command!r}") from exc


def command_results(
    *,
    tag: str = TAG,
    source_sha: str = SOURCE_SHA,
    local_tag_sha: str | None = None,
    remote_sha: str | None = None,
    remote_peeled_sha: str | None = None,
    remote_exact_output: str | None = None,
    package_version: str = VERSION,
    head_sha: str | None = None,
    stable_tags: str = "v1.2.2\nv1.2.3\n",
    remote_main_output: str | None = None,
    main_object_returncode: int = 0,
    ancestor_returncode: int = 0,
    missing_tree_path: str | None = None,
) -> dict[tuple[str, ...], CommandResult]:
    local_tag_sha = local_tag_sha or source_sha
    head_sha = head_sha or source_sha
    remote_sha = remote_sha or local_tag_sha
    tag_ref = f"refs/tags/{tag}"
    if remote_exact_output is None:
        remote_exact_output = f"{remote_sha}\t{tag_ref}\n"
        if remote_peeled_sha is not None:
            remote_exact_output += f"{remote_peeled_sha}\t{tag_ref}^{{}}\n"
    if remote_main_output is None:
        remote_main_output = f"{MAIN_SHA}\trefs/heads/main\n"
    remote_stable_tags = "".join(
        f"{remote_sha if value == tag else TREE_ENTRY_SHA}\trefs/tags/{value}\n"
        for value in stable_tags.splitlines()
    )
    results = {
        ("git", "rev-parse", tag_ref): CommandResult(0, f"{local_tag_sha}\n", ""),
        ("git", "rev-parse", f"{tag_ref}^{{commit}}"): CommandResult(0, f"{source_sha}\n", ""),
        (
            "git",
            "ls-remote",
            "--tags",
            "origin",
            tag_ref,
            f"{tag_ref}^{{}}",
        ): CommandResult(0, remote_exact_output, ""),
        ("git", "rev-parse", "HEAD^{commit}"): CommandResult(0, f"{head_sha}\n", ""),
        ("uv", "version", "--short"): CommandResult(0, f"{package_version}\n", ""),
        (
            "git",
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
            "refs/tags/v*",
        ): CommandResult(0, remote_stable_tags, ""),
        (
            "git",
            "ls-remote",
            "--heads",
            "origin",
            "refs/heads/main",
        ): CommandResult(0, remote_main_output, ""),
        ("git", "cat-file", "-e", f"{MAIN_SHA}^{{commit}}"): CommandResult(
            main_object_returncode, "", ""
        ),
        (
            "git",
            "merge-base",
            "--is-ancestor",
            source_sha,
            MAIN_SHA,
        ): CommandResult(ancestor_returncode, "", ""),
        ("git", "ls-tree", source_sha, "--", "container-release.json"): CommandResult(
            0,
            ""
            if missing_tree_path == "container-release.json"
            else f"100644 blob {TREE_ENTRY_SHA}\tcontainer-release.json\n",
            "",
        ),
        (
            "git",
            "ls-tree",
            source_sha,
            "--",
            ".github/workflows/container-release.yml",
        ): CommandResult(
            0,
            ""
            if missing_tree_path == ".github/workflows/container-release.yml"
            else (f"100644 blob {TREE_ENTRY_SHA}\t.github/workflows/container-release.yml\n"),
            "",
        ),
    }
    return results


def validate(runner: RecordingRunner, *, event_sha: str = SOURCE_SHA):
    return validate_source_release(
        event_name="push",
        event_ref=f"refs/tags/{TAG}",
        event_sha=event_sha,
        changelog_text="# Changelog\n\n## [1.2.3] - 2026-07-13\n\n- Release.\n",
        runner=runner,
    )


@pytest.mark.parametrize("tag", ["v1", "v1.2", "1.2.3", "v1.2.3-rc.1", "v1.2.3+local"])
def test_parse_release_tag_rejects_non_stable_semver(tag: str) -> None:
    with pytest.raises(SourceReleaseError):
        parse_release_tag(tag)


@pytest.mark.parametrize("tag", ["v01.2.3", "v1.02.3", "v1.2.03", "v1.2.3\nmain"])
def test_parse_release_tag_rejects_ambiguous_or_unsafe_text(tag: str) -> None:
    with pytest.raises(SourceReleaseError):
        parse_release_tag(tag)


def test_parse_release_tag_returns_stable_version() -> None:
    release_tag = parse_release_tag("v12.34.56")

    assert release_tag.tag == "v12.34.56"
    assert release_tag.version == "12.34.56"
    assert release_tag.parts == (12, 34, 56)


def test_parse_release_tag_wraps_pathological_integer_conversion() -> None:
    pathological_tag = f"v{'9' * 5000}.1.1"

    with pytest.raises(SourceReleaseError):
        parse_release_tag(pathological_tag)


@pytest.mark.parametrize(
    ("event_name", "event_ref"),
    [
        ("workflow_dispatch", "refs/heads/main"),
        ("push", "refs/heads/main"),
        ("pull_request", f"refs/tags/{TAG}"),
    ],
)
def test_release_source_requires_tag_push(event_name: str, event_ref: str) -> None:
    with pytest.raises(SourceReleaseError, match="tag push"):
        resolve_event_tag(event_name, event_ref)


def test_validation_rejects_tag_package_version_mismatch() -> None:
    runner = RecordingRunner(command_results(package_version="1.2.4"))

    with pytest.raises(SourceReleaseError, match="package version"):
        validate(runner)


def test_validation_requires_explicit_changelog_release_entry() -> None:
    runner = RecordingRunner(command_results())

    with pytest.raises(SourceReleaseError, match="changelog"):
        validate_source_release(
            event_name="push",
            event_ref=f"refs/tags/{TAG}",
            event_sha=SOURCE_SHA,
            changelog_text="# Changelog\n\nThe 1.2.3 release exists.\n",
            runner=runner,
        )


def test_validation_rejects_tag_outside_protected_main_history() -> None:
    runner = RecordingRunner(command_results(ancestor_returncode=1))

    with pytest.raises(SourceReleaseError, match="protected main"):
        validate(runner)


@pytest.mark.parametrize(
    "remote_main_output",
    [
        "",
        f"{MAIN_SHA}\trefs/heads/main\n{MAIN_SHA}\trefs/heads/main\n",
        f"{MAIN_SHA}\trefs/heads/main\n{REMOTE_SHA}\trefs/heads/main\n",
        f"{MAIN_SHA}\trefs/heads/main\n\n",
        "malformed main row\n",
        f"{MAIN_SHA}\trefs/heads/develop\n",
    ],
)
def test_remote_protected_main_identity_must_be_exact(remote_main_output: str) -> None:
    runner = RecordingRunner(command_results(remote_main_output=remote_main_output))

    with pytest.raises(SourceReleaseError, match="remote protected main"):
        validate(runner)


def test_remote_protected_main_commit_must_be_available_locally() -> None:
    runner = RecordingRunner(command_results(main_object_returncode=128))

    with pytest.raises(SourceReleaseError, match="available locally"):
        validate(runner)


def test_validation_rejects_remote_tag_movement() -> None:
    runner = RecordingRunner(command_results(remote_sha=REMOTE_SHA))

    with pytest.raises(SourceReleaseError, match="remote tag"):
        validate(runner)


@pytest.mark.parametrize(
    "remote_output",
    [
        f"{SOURCE_SHA}\trefs/tags/{TAG}\n{SOURCE_SHA}\trefs/tags/{TAG}\n",
        f"{SOURCE_SHA}\trefs/tags/{TAG}\n{REMOTE_SHA}\trefs/tags/{TAG}\n",
        (
            f"{SOURCE_SHA}\trefs/tags/{TAG}\n"
            f"{SOURCE_SHA}\trefs/tags/{TAG}^{{}}\n"
            f"{REMOTE_SHA}\trefs/tags/{TAG}^{{}}\n"
        ),
        f"{SOURCE_SHA}\trefs/tags/{TAG}\n\n",
        f"{SOURCE_SHA}\trefs/tags/{TAG}^{{}}\n",
        "malformed remote row\n",
    ],
)
def test_remote_exact_tag_identity_rejects_duplicate_peeled_only_or_malformed_rows(
    remote_output: str,
) -> None:
    runner = RecordingRunner(command_results(remote_exact_output=remote_output))

    with pytest.raises(SourceReleaseError, match="remote tag"):
        validate(runner)


def test_lightweight_tag_rejects_unexpected_remote_peeled_row() -> None:
    runner = RecordingRunner(command_results(remote_peeled_sha=SOURCE_SHA))

    with pytest.raises(SourceReleaseError, match="lightweight"):
        validate(runner)


def test_annotated_tag_requires_matching_remote_object_and_peeled_commit() -> None:
    tag_object_sha = "d" * 40
    runner = RecordingRunner(
        command_results(
            local_tag_sha=tag_object_sha,
            remote_sha=tag_object_sha,
            remote_peeled_sha=SOURCE_SHA,
        )
    )

    source = validate(runner)

    assert source.revision == SOURCE_SHA


@pytest.mark.parametrize(
    ("remote_tag_sha", "remote_peeled_sha"),
    [("e" * 40, SOURCE_SHA), ("d" * 40, None), ("d" * 40, REMOTE_SHA)],
)
def test_annotated_tag_rejects_reannotation_or_incomplete_remote_identity(
    remote_tag_sha: str, remote_peeled_sha: str | None
) -> None:
    runner = RecordingRunner(
        command_results(
            local_tag_sha="d" * 40,
            remote_sha=remote_tag_sha,
            remote_peeled_sha=remote_peeled_sha,
        )
    )

    with pytest.raises(SourceReleaseError, match="remote tag"):
        validate(runner)


def test_validation_rejects_local_tag_mismatch_with_event_source_sha() -> None:
    runner = RecordingRunner(command_results())

    with pytest.raises(SourceReleaseError, match="event source SHA"):
        validate(runner, event_sha=REMOTE_SHA)


@pytest.mark.parametrize(
    ("current", "previous"),
    [("v1.2.3", "v1.2.3"), ("v1.2.3", "v1.2.4"), ("v1.2.3", "v2.0.0")],
)
def test_version_must_increase_over_previous_stable_tag(current: str, previous: str) -> None:
    with pytest.raises(SourceReleaseError, match="greater than previous stable"):
        ensure_version_increases(parse_release_tag(current), parse_release_tag(previous))


def test_validation_rejects_downgrade_against_repository_stable_tags() -> None:
    runner = RecordingRunner(command_results(stable_tags="v1.2.3\nv1.2.4\nnot-a-release\n"))

    with pytest.raises(SourceReleaseError, match="greater than previous stable"):
        validate(runner)


@pytest.mark.parametrize(
    "remote_rows",
    [
        f"{SOURCE_SHA}\trefs/tags/v1.2.2\n",
        f"{SOURCE_SHA}\trefs/tags/v1.2.3\n{SOURCE_SHA}\trefs/tags/v1.2.3\n",
        f"{SOURCE_SHA}\trefs/tags/v1.2.3\n{REMOTE_SHA}\trefs/tags/v1.2.3\n",
        f"{SOURCE_SHA}\trefs/tags/v1.2.3\n\n",
        "malformed remote tag row\n",
    ],
)
def test_remote_stable_tag_inventory_must_be_complete_and_well_formed(
    remote_rows: str,
) -> None:
    results = command_results()
    command = ("git", "ls-remote", "--tags", "--refs", "origin", "refs/tags/v*")
    results[command] = CommandResult(0, remote_rows, "")
    runner = RecordingRunner(results)

    with pytest.raises(SourceReleaseError, match="remote stable tag inventory"):
        validate(runner)


@pytest.mark.parametrize(
    "missing_path",
    ["container-release.json", ".github/workflows/container-release.yml"],
)
def test_validation_rejects_pre_adoption_tag_without_release_files(
    missing_path: str,
) -> None:
    runner = RecordingRunner(command_results(missing_tree_path=missing_path))

    with pytest.raises(SourceReleaseError, match=missing_path):
        validate(runner)


@pytest.mark.parametrize(
    "path",
    ["container-release.json", ".github/workflows/container-release.yml"],
)
@pytest.mark.parametrize(
    "malformed_output",
    [
        "malformed output\n",
        f"100644 blob {TREE_ENTRY_SHA}\twrong-path\n",
        f"040000 tree {TREE_ENTRY_SHA}\t{{path}}\n",
        f"120000 blob {TREE_ENTRY_SHA}\t{{path}}\n",
        (f"100644 blob {TREE_ENTRY_SHA}\t{{path}}\n100644 blob {'d' * 40}\t{{path}}\n"),
    ],
)
def test_release_file_rejects_malformed_tree_identity(path: str, malformed_output: str) -> None:
    results = command_results()
    command = ("git", "ls-tree", SOURCE_SHA, "--", path)
    results[command] = CommandResult(0, malformed_output.format(path=path), "")
    runner = RecordingRunner(results)

    with pytest.raises(SourceReleaseError) as error:
        validate(runner)

    assert str(error.value) == (
        f"tag {TAG} returned malformed tree identity for required release file {path}"
    )


def test_invalid_tag_never_reaches_command_runner() -> None:
    runner = RecordingRunner({})

    with pytest.raises(SourceReleaseError):
        validate_source_release(
            event_name="push",
            event_ref="refs/tags/v1.2.3;git push attacker main",
            event_sha=SOURCE_SHA,
            changelog_text="",
            runner=runner,
        )

    assert runner.calls == []


def test_checkout_head_must_match_resolved_release_commit() -> None:
    runner = RecordingRunner(command_results(head_sha=REMOTE_SHA))

    with pytest.raises(SourceReleaseError, match="checkout HEAD"):
        validate(runner)


def test_release_file_queries_remain_bound_to_resolved_commit_sha() -> None:
    class TagMovingRunner(RecordingRunner):
        def __call__(self, args: Sequence[str]) -> CommandResult:
            result = super().__call__(args)
            if tuple(args)[1:3] == ("ls-remote", "--tags"):
                for path in ("container-release.json", ".github/workflows/container-release.yml"):
                    moved_query = ("git", "ls-tree", f"refs/tags/{TAG}", "--", path)
                    self.results[moved_query] = CommandResult(0, "", "")
            return result

    runner = TagMovingRunner(command_results())

    validate(runner)

    tree_calls = [call for call in runner.calls if call[:2] == ("git", "ls-tree")]
    assert tree_calls == [
        ("git", "ls-tree", SOURCE_SHA, "--", "container-release.json"),
        (
            "git",
            "ls-tree",
            SOURCE_SHA,
            "--",
            ".github/workflows/container-release.yml",
        ),
    ]


@pytest.mark.parametrize("raw_detail", ["No such file or directory", "host-specific failure"])
def test_default_runner_reports_deterministic_command_launch_failure(
    monkeypatch: pytest.MonkeyPatch, raw_detail: str
) -> None:
    def fail_to_launch(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError(2, raw_detail, "git")

    monkeypatch.setattr("genefoundry_router.release.source.subprocess.run", fail_to_launch)

    with pytest.raises(SourceReleaseError) as error:
        run_command(("git", "status", "--short"))

    assert str(error.value) == "unable to execute git"


def test_default_runner_is_bounded_and_noninteractive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def complete(args: object, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setenv("PRESERVED_RELEASE_TEST_VALUE", "present")
    monkeypatch.setattr("genefoundry_router.release.source.subprocess.run", complete)

    result = run_command(("git", "status", "--short"))

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert result.stdout == "ok\n"
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["timeout"] == RELEASE_COMMAND_TIMEOUT_SECONDS
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GCM_INTERACTIVE"] == "Never"
    assert environment["PRESERVED_RELEASE_TEST_VALUE"] == "present"


def test_default_runner_reports_deterministic_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(("git", "status"), 999, output="host detail")

    monkeypatch.setattr("genefoundry_router.release.source.subprocess.run", time_out)

    with pytest.raises(SourceReleaseError) as error:
        run_command(("git", "status", "--short"))

    assert str(error.value) == (
        f"unable to execute git within {RELEASE_COMMAND_TIMEOUT_SECONDS} seconds"
    )


@pytest.mark.parametrize("raw_stderr", ["fatal: localized detail", "different host detail"])
def test_command_failure_reports_deterministic_operation_message(raw_stderr: str) -> None:
    results = command_results()
    command = ("git", "rev-parse", f"refs/tags/{TAG}^{{commit}}")
    results[command] = CommandResult(128, "", raw_stderr)
    runner = RecordingRunner(results)

    with pytest.raises(SourceReleaseError) as error:
        validate(runner)

    assert str(error.value) == f"unable to resolve local release tag {TAG}"


@pytest.mark.parametrize(
    "path",
    ["container-release.json", ".github/workflows/container-release.yml"],
)
def test_release_file_git_failure_is_not_mislabeled_as_missing(path: str) -> None:
    results = command_results()
    command = ("git", "ls-tree", SOURCE_SHA, "--", path)
    results[command] = CommandResult(128, "", "fatal: object database unavailable")
    runner = RecordingRunner(results)

    with pytest.raises(SourceReleaseError) as error:
        validate(runner)

    assert str(error.value) == f"unable to inspect required release file {path} in tag {TAG}"


def test_validation_returns_exact_source_identity_and_uses_argument_arrays() -> None:
    runner = RecordingRunner(command_results())

    source = validate(runner)

    assert source.tag == TAG
    assert source.version == VERSION
    assert source.revision == SOURCE_SHA
    assert source.previous_stable_tag == "v1.2.2"
    assert runner.calls == [
        ("git", "rev-parse", f"refs/tags/{TAG}"),
        ("git", "rev-parse", f"refs/tags/{TAG}^{{commit}}"),
        (
            "git",
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{TAG}",
            f"refs/tags/{TAG}^{{}}",
        ),
        ("git", "rev-parse", "HEAD^{commit}"),
        ("uv", "version", "--short"),
        ("git", "ls-remote", "--tags", "--refs", "origin", "refs/tags/v*"),
        ("git", "ls-remote", "--heads", "origin", "refs/heads/main"),
        ("git", "cat-file", "-e", f"{MAIN_SHA}^{{commit}}"),
        (
            "git",
            "merge-base",
            "--is-ancestor",
            SOURCE_SHA,
            MAIN_SHA,
        ),
        ("git", "ls-tree", SOURCE_SHA, "--", "container-release.json"),
        (
            "git",
            "ls-tree",
            SOURCE_SHA,
            "--",
            ".github/workflows/container-release.yml",
        ),
    ]
