"""Fail-closed validation of the exact source authorized for publication."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

_STABLE_COMPONENT = r"(?:0|[1-9][0-9]{0,63})"
_STABLE_TAG_RE = re.compile(
    rf"^v({_STABLE_COMPONENT})\.({_STABLE_COMPONENT})\.({_STABLE_COMPONENT})$"
)
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_RELEASE_PATHS = (
    "container-release.json",
    ".github/workflows/container-release.yml",
)
_REMOTE = "origin"
_PROTECTED_MAIN_REF = "refs/heads/main"
RELEASE_COMMAND_TIMEOUT_SECONDS = 60


class SourceReleaseError(ValueError):
    """A source identity cannot safely authorize a container publication."""


@dataclass(frozen=True)
class CommandResult:
    """Captured result of one read-only command invocation."""

    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Narrow argument-array command interface used by source validation."""

    def __call__(self, args: Sequence[str]) -> CommandResult: ...


@dataclass(frozen=True)
class ReleaseTag:
    """A syntactically exact stable application release tag."""

    tag: str
    version: str
    parts: tuple[int, int, int]


@dataclass(frozen=True)
class SourceRelease:
    """Validated immutable source identity for the release state machine."""

    tag: str
    version: str
    revision: str
    previous_stable_tag: str | None


@dataclass(frozen=True)
class _LocalTagIdentity:
    direct_revision: str
    commit_revision: str


def run_command(args: Sequence[str]) -> CommandResult:
    """Run one bounded, noninteractive command without a shell."""
    environment = os.environ.copy()
    environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"})
    try:
        completed = subprocess.run(  # noqa: S603
            list(args),
            check=False,
            capture_output=True,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=RELEASE_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        executable = args[0] if args else "command"
        raise SourceReleaseError(
            f"unable to execute {executable} within {RELEASE_COMMAND_TIMEOUT_SECONDS} seconds"
        ) from exc
    except OSError as exc:
        executable = args[0] if args else "command"
        raise SourceReleaseError(f"unable to execute {executable}") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def parse_release_tag(tag: str) -> ReleaseTag:
    """Parse only canonical ``vX.Y.Z`` tags without prerelease or local data."""
    match = _STABLE_TAG_RE.fullmatch(tag)
    if match is None:
        raise SourceReleaseError("release tag must be canonical stable SemVer in the form vX.Y.Z")
    try:
        parts = tuple(int(part) for part in match.groups())
    except ValueError as exc:
        raise SourceReleaseError("release tag contains an invalid numeric component") from exc
    return ReleaseTag(tag=tag, version=tag[1:], parts=(parts[0], parts[1], parts[2]))


def resolve_event_tag(event_name: str, event_ref: str) -> ReleaseTag:
    """Require the exact stable tag-push event that authorizes publication."""
    prefix = "refs/tags/"
    if event_name != "push" or not event_ref.startswith(prefix):
        raise SourceReleaseError("container publication requires an exact stable tag push")
    return parse_release_tag(event_ref.removeprefix(prefix))


def ensure_version_increases(current: ReleaseTag, previous: ReleaseTag) -> None:
    """Reject re-release, downgrade, and non-increasing stable versions."""
    if current.parts <= previous.parts:
        raise SourceReleaseError(
            f"release {current.tag} must be greater than previous stable tag {previous.tag}"
        )


def _require_success(result: CommandResult, description: str) -> str:
    if result.returncode != 0:
        raise SourceReleaseError(f"unable to {description}")
    return result.stdout.strip()


def _require_revision(value: str, description: str) -> str:
    if _REVISION_RE.fullmatch(value) is None:
        raise SourceReleaseError(f"{description} must be a full lowercase 40-character Git SHA")
    return value


def _resolve_local_tag(tag: ReleaseTag, runner: CommandRunner) -> _LocalTagIdentity:
    tag_ref = f"refs/tags/{tag.tag}"
    direct_output = _require_success(
        runner(("git", "rev-parse", tag_ref)),
        f"resolve local direct tag identity {tag.tag}",
    )
    commit_output = _require_success(
        runner(("git", "rev-parse", f"{tag_ref}^{{commit}}")),
        f"resolve local release tag {tag.tag}",
    )
    return _LocalTagIdentity(
        direct_revision=_require_revision(direct_output, "local direct tag revision"),
        commit_revision=_require_revision(commit_output, "resolved source SHA"),
    )


def _resolve_remote_tag(tag: ReleaseTag, local: _LocalTagIdentity, runner: CommandRunner) -> str:
    tag_ref = f"refs/tags/{tag.tag}"
    peeled_ref = f"{tag_ref}^{{}}"
    result = runner(("git", "ls-remote", "--tags", _REMOTE, tag_ref, peeled_ref))
    _require_success(result, f"look up remote tag {tag.tag}")
    output = result.stdout
    if output and not output.endswith("\n"):
        raise SourceReleaseError(f"remote tag {tag.tag} returned malformed identity data")
    revisions_by_ref: dict[str, list[str]] = {tag_ref: [], peeled_ref: []}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or fields[1] not in {tag_ref, peeled_ref}:
            raise SourceReleaseError(f"remote tag {tag.tag} returned malformed identity data")
        revision = _require_revision(fields[0], f"remote tag {tag.tag} revision")
        revisions_by_ref[fields[1]].append(revision)
    direct_rows = revisions_by_ref[tag_ref]
    peeled_rows = revisions_by_ref[peeled_ref]
    if len(direct_rows) != 1 or len(peeled_rows) > 1:
        raise SourceReleaseError(f"remote tag {tag.tag} returned malformed identity data")

    remote_direct = direct_rows[0]
    is_lightweight = local.direct_revision == local.commit_revision
    if is_lightweight:
        if peeled_rows:
            raise SourceReleaseError(
                f"remote lightweight tag {tag.tag} unexpectedly has a peeled identity"
            )
        if remote_direct != local.direct_revision:
            raise SourceReleaseError(f"remote tag {tag.tag} moved or changed identity")
    else:
        if len(peeled_rows) != 1:
            raise SourceReleaseError(f"remote tag {tag.tag} lacks its annotated peeled identity")
        if remote_direct != local.direct_revision or peeled_rows[0] != local.commit_revision:
            raise SourceReleaseError(f"remote tag {tag.tag} moved or changed identity")
    return remote_direct


def _read_package_version(runner: CommandRunner) -> str:
    version = _require_success(
        runner(("uv", "version", "--short")), "read the package version with uv"
    )
    if re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version) is None:
        raise SourceReleaseError("package version must be canonical stable SemVer")
    return version


def _require_checkout_head(revision: str, runner: CommandRunner) -> None:
    output = _require_success(
        runner(("git", "rev-parse", "HEAD^{commit}")), "resolve checkout HEAD"
    )
    head_revision = _require_revision(output, "checkout HEAD revision")
    if head_revision != revision:
        raise SourceReleaseError("checkout HEAD does not match the resolved release commit")


def _previous_stable_tag(
    current: ReleaseTag, remote_current_revision: str, runner: CommandRunner
) -> ReleaseTag | None:
    result = runner(("git", "ls-remote", "--tags", "--refs", _REMOTE, "refs/tags/v*"))
    _require_success(result, "list remote stable tags")
    output = result.stdout
    if output and not output.endswith("\n"):
        raise SourceReleaseError("remote stable tag inventory returned malformed identity data")
    stable_tags: list[ReleaseTag] = []
    revisions_by_ref: dict[str, str] = {}
    current_ref = f"refs/tags/{current.tag}"
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or not fields[1].startswith("refs/tags/"):
            raise SourceReleaseError("remote stable tag inventory returned malformed identity data")
        revision = fields[0]
        if _REVISION_RE.fullmatch(revision) is None or fields[1] in revisions_by_ref:
            raise SourceReleaseError("remote stable tag inventory returned malformed identity data")
        revisions_by_ref[fields[1]] = revision
        value = fields[1].removeprefix("refs/tags/")
        try:
            candidate = parse_release_tag(value)
        except SourceReleaseError:
            continue
        if candidate.tag != current.tag:
            stable_tags.append(candidate)
    if revisions_by_ref.get(current_ref) != remote_current_revision:
        raise SourceReleaseError(
            "remote stable tag inventory is missing or inconsistent for the current release tag"
        )
    if not stable_tags:
        return None
    previous = max(stable_tags, key=lambda candidate: candidate.parts)
    ensure_version_increases(current, previous)
    return previous


def _require_changelog_entry(version: str, changelog_text: str) -> None:
    heading = re.compile(rf"^## \[{re.escape(version)}\](?: - .+)?$", re.MULTILINE)
    if heading.search(changelog_text) is None:
        raise SourceReleaseError(f"changelog lacks an explicit [{version}] release entry")


def _require_protected_main_ancestor(revision: str, runner: CommandRunner) -> None:
    lookup = runner(("git", "ls-remote", "--heads", _REMOTE, _PROTECTED_MAIN_REF))
    _require_success(lookup, "look up remote protected main")
    output = lookup.stdout
    if output and not output.endswith("\n"):
        raise SourceReleaseError("remote protected main returned malformed identity data")
    rows = output.splitlines()
    if len(rows) != 1:
        raise SourceReleaseError("remote protected main returned malformed identity data")
    fields = rows[0].split("\t")
    if len(fields) != 2 or fields[1] != _PROTECTED_MAIN_REF:
        raise SourceReleaseError("remote protected main returned malformed identity data")
    main_revision = _require_revision(fields[0], "remote protected main revision")
    availability = runner(("git", "cat-file", "-e", f"{main_revision}^{{commit}}"))
    if availability.returncode != 0:
        raise SourceReleaseError("remote protected main commit is not available locally")

    result = runner(("git", "merge-base", "--is-ancestor", revision, main_revision))
    if result.returncode == 1:
        raise SourceReleaseError(
            f"release revision {revision} is not reachable from protected main"
        )
    _require_success(result, "verify protected main ancestry")


def _require_release_files(tag: ReleaseTag, revision: str, runner: CommandRunner) -> None:
    for path in _REQUIRED_RELEASE_PATHS:
        result = runner(("git", "ls-tree", revision, "--", path))
        _require_success(result, f"inspect required release file {path} in tag {tag.tag}")
        if result.stdout == "":
            raise SourceReleaseError(
                f"tag {tag.tag} lacks required post-adoption release file {path}"
            )
        expected_entry = re.compile(rf"(?:100644|100755) blob [0-9a-f]{{40}}\t{re.escape(path)}\n")
        if expected_entry.fullmatch(result.stdout) is None:
            raise SourceReleaseError(
                f"tag {tag.tag} returned malformed tree identity for required release file {path}"
            )


def validate_source_release(
    *,
    event_name: str,
    event_ref: str,
    event_sha: str,
    changelog_text: str,
    runner: CommandRunner = run_command,
) -> SourceRelease:
    """Validate a tag checkout and changelog text read from that exact checkout."""
    tag = resolve_event_tag(event_name, event_ref)
    expected_revision = _require_revision(event_sha, "event source SHA")

    local_tag = _resolve_local_tag(tag, runner)
    resolved_revision = local_tag.commit_revision
    if resolved_revision != expected_revision:
        raise SourceReleaseError(
            f"local tag {tag.tag} does not resolve to the expected event source SHA"
        )

    remote_direct_revision = _resolve_remote_tag(tag, local_tag, runner)
    _require_checkout_head(resolved_revision, runner)

    package_version = _read_package_version(runner)
    if package_version != tag.version:
        raise SourceReleaseError(
            f"tag version {tag.version} does not match package version {package_version}"
        )
    _require_changelog_entry(tag.version, changelog_text)

    previous = _previous_stable_tag(tag, remote_direct_revision, runner)
    _require_protected_main_ancestor(resolved_revision, runner)
    _require_release_files(tag, resolved_revision, runner)
    return SourceRelease(
        tag=tag.tag,
        version=tag.version,
        revision=resolved_revision,
        previous_stable_tag=previous.tag if previous is not None else None,
    )
