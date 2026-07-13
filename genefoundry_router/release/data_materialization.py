"""Download, verify, materialize, and roll back immutable data artifacts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import urljoin, urlsplit

import httpx

from genefoundry_router.release.data import (
    CompatibilityRange,
    DataRequirement,
    DataVerificationError,
    DownloadPolicy,
)


def _validate_download_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlsplit(url)
    normalized_hosts = tuple(host.lower().rstrip(".") for host in allowed_hosts)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or parsed.hostname.lower().rstrip(".") not in normalized_hosts
        or parsed.port not in {None, 443}
    ):
        raise DataVerificationError("download URL is outside the HTTPS host allowlist")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def download_artifact(
    url: str,
    destination: Path,
    requirement: DataRequirement,
    *,
    allowed_hosts: tuple[str, ...],
    client: httpx.Client | None = None,
    policy: DownloadPolicy | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Path:
    if not allowed_hosts or any(not host or "/" in host or "*" in host for host in allowed_hosts):
        raise DataVerificationError("download requires an exact host allowlist")
    if requirement.max_compressed_size is None:
        raise DataVerificationError("compressed size ceiling is unavailable")
    policy = policy or DownloadPolicy()
    destination.parent.mkdir(parents=True, exist_ok=True)
    owned_client = client is None
    active_client = client or httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(policy.stall_timeout_seconds, connect=policy.connect_timeout_seconds),
    )
    temporary_path: Path | None = None
    try:
        current_url = url
        response: httpx.Response | None = None
        for redirect_count in range(policy.max_redirects + 1):
            _validate_download_url(current_url, allowed_hosts)
            request = active_client.build_request("GET", current_url)
            response = active_client.send(request, stream=True)
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            response.close()
            if not location or redirect_count >= policy.max_redirects:
                raise DataVerificationError("download exceeded redirect limit")
            current_url = urljoin(current_url, location)
            _validate_download_url(current_url, allowed_hosts)
        assert response is not None
        with closing(response):
            response.raise_for_status()
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    length = int(declared_length)
                except ValueError as exc:
                    raise DataVerificationError("invalid download content length") from exc
                if length > requirement.max_compressed_size:
                    raise DataVerificationError("download exceeds compressed size ceiling")
            descriptor, raw_path = tempfile.mkstemp(
                dir=destination.parent, prefix=f".{destination.name}.", suffix=".partial"
            )
            temporary_path = Path(raw_path)
            os.fchmod(descriptor, 0o600)
            started = monotonic()
            last_progress = started
            total = 0
            with os.fdopen(descriptor, "wb") as output:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    now = monotonic()
                    if now - last_progress > policy.stall_timeout_seconds:
                        raise DataVerificationError("download stream stalled")
                    total += len(chunk)
                    if total > requirement.max_compressed_size:
                        raise DataVerificationError("download exceeds compressed size ceiling")
                    elapsed = now - started
                    if elapsed >= 1 and total / elapsed < policy.minimum_bytes_per_second:
                        raise DataVerificationError("download stream is below minimum throughput")
                    output.write(chunk)
                    last_progress = now
                output.flush()
                os.fsync(output.fileno())
        verify_compressed_artifact(temporary_path, requirement)
        os.replace(temporary_path, destination)
        temporary_path = None
        _fsync_directory(destination.parent)
        return destination
    except httpx.TimeoutException as exc:
        raise DataVerificationError("download stream stalled") from exc
    except httpx.HTTPError as exc:
        raise DataVerificationError("artifact download failed") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if owned_client:
            active_client.close()


def verify_compressed_artifact(path: Path, requirement: DataRequirement) -> None:
    if requirement.sha256 is None or requirement.compressed_size is None:
        raise DataVerificationError("artifact identity is unavailable")
    if requirement.max_compressed_size is None:
        raise DataVerificationError("compressed size ceiling is unavailable")
    try:
        info = path.lstat()
    except OSError as exc:
        raise DataVerificationError("artifact is not a regular file") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DataVerificationError("artifact is not a regular file")
    if info.st_size > requirement.max_compressed_size:
        raise DataVerificationError("artifact exceeds compressed size ceiling")
    if info.st_size != requirement.compressed_size:
        raise DataVerificationError("artifact compressed size does not match reviewed identity")
    digest = hashlib.sha256()
    total = 0
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > requirement.max_compressed_size:
                    raise DataVerificationError("artifact exceeds compressed size ceiling")
                digest.update(chunk)
    finally:
        os.close(descriptor)
    if digest.hexdigest() != requirement.sha256:
        raise DataVerificationError("artifact digest does not match reviewed identity")


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise DataVerificationError("archive member path escapes the materialization root")
    return path


def expanded_tree_identity(root: Path) -> tuple[str, int, int]:
    """Hash the sorted ``path\0mode\0size\0sha256`` regular-file listing."""

    entries: list[tuple[str, int, int, str]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DataVerificationError("expanded tree contains a link or special file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        relative = path.relative_to(root).as_posix()
        entries.append((relative, stat.S_IMODE(info.st_mode), info.st_size, digest.hexdigest()))
        total += info.st_size
    identity = hashlib.sha256()
    for name, mode, size, file_digest in entries:
        identity.update(f"{name}\0{mode:04o}\0{size}\0{file_digest}\n".encode())
    return identity.hexdigest(), total, len(entries)


@contextmanager
def _tar_stream(path: Path):  # type: ignore[no-untyped-def]
    with path.open("rb") as probe:
        zstd = probe.read(4) == b"\x28\xb5\x2f\xfd"
    if not zstd:
        with tarfile.open(path, "r|*") as archive:
            yield archive
        return
    zstd_binary = shutil.which("zstd")
    if zstd_binary is None:
        raise DataVerificationError("zstd decompressor is unavailable")
    process = subprocess.Popen(  # noqa: S603
        [zstd_binary, "-q", "-dc", "--", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            yield archive
    except BaseException:
        process.kill()
        raise
    finally:
        process.stdout.close()
        try:
            if process.wait(timeout=30) != 0:
                raise DataVerificationError("zstd decompression failed")
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise DataVerificationError("zstd decompression stalled") from exc


def _regular_mode(mode: int) -> int:
    return mode & 0o444 or 0o400


def _directory_mode(mode: int) -> int:
    return mode & 0o555 or 0o500


def _extract_archive(archive_path: Path, destination: Path, requirement: DataRequirement) -> None:
    assert requirement.max_members is not None
    assert requirement.max_expanded_size is not None
    count = 0
    expanded = 0
    seen: set[str] = set()
    try:
        with _tar_stream(archive_path) as archive:
            for member in archive:
                count += 1
                if count > requirement.max_members:
                    raise DataVerificationError("archive exceeds member ceiling")
                relative = _member_path(member.name)
                if member.name in seen:
                    raise DataVerificationError("archive contains a duplicate member path")
                seen.add(member.name)
                if member.mode & 0o6000:
                    raise DataVerificationError("archive member has a set-id mode")
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=_directory_mode(member.mode))
                    os.chmod(target, _directory_mode(member.mode))
                    continue
                if member.issym() or member.islnk():
                    raise DataVerificationError("archive contains a link")
                if not member.isreg():
                    raise DataVerificationError("archive contains a special file")
                source = archive.extractfile(member)
                if source is None:
                    raise DataVerificationError("archive regular member is unreadable")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        expanded += len(chunk)
                        if expanded > requirement.max_expanded_size:
                            raise DataVerificationError("archive exceeds expanded size ceiling")
                        output.write(chunk)
                    os.fchmod(output.fileno(), _regular_mode(member.mode))
                    output.flush()
                    os.fsync(output.fileno())
    except (tarfile.TarError, OSError, subprocess.SubprocessError) as exc:
        raise DataVerificationError("artifact is not a valid bounded archive") from exc
    directories = sorted(
        (path for path in destination.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in (*directories, destination):
        _fsync_directory(directory)
    if count != requirement.member_count:
        raise DataVerificationError("archive member count does not match reviewed identity")
    if expanded != requirement.expanded_size:
        raise DataVerificationError("expanded size does not match reviewed identity")


@contextmanager
def _exclusive_lock(data_root: Path):  # type: ignore[no-untyped-def]
    descriptor = os.open(
        data_root / ".materialize.lock",
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DataVerificationError("materialization lock is not a private regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _select(data_root: Path, target: Path) -> None:
    temporary = data_root / f".current-{os.getpid()}-{time.time_ns()}"
    try:
        temporary.symlink_to(target.name)
        os.replace(temporary, data_root / "current")
        _fsync_directory(data_root)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_private_data_root(data_root: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = data_root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        raise DataVerificationError("data root must be a private directory owned by this user")


def _identity_path(data_root: Path, digest_hex: str) -> Path:
    return data_root / f"{digest_hex}.identity.json"


def _identity_payload(
    requirement: DataRequirement, observed: tuple[str, int, int]
) -> dict[str, str | int]:
    assert requirement.sha256 is not None
    assert requirement.release_tag is not None
    assert requirement.schema_version is not None
    assert requirement.schema_minimum is not None
    assert requirement.schema_maximum is not None
    assert requirement.previous_known_good_digest is not None
    return {
        "schema_version": 1,
        "artifact_sha256": requirement.sha256,
        "expanded_tree_sha256": observed[0],
        "expanded_size": observed[1],
        "member_count": observed[2],
        "data_schema_version": requirement.schema_version,
        "schema_minimum": requirement.schema_minimum,
        "schema_maximum": requirement.schema_maximum,
        "release_tag": requirement.release_tag,
        "previous_known_good_digest": requirement.previous_known_good_digest,
    }


def _write_identity(
    data_root: Path, requirement: DataRequirement, observed: tuple[str, int, int]
) -> None:
    assert requirement.sha256 is not None
    payload = json.dumps(_identity_payload(requirement, observed), sort_keys=True).encode()
    descriptor, raw_path = tempfile.mkstemp(
        dir=data_root, prefix=f".{requirement.sha256}.", suffix=".identity"
    )
    temporary = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _identity_path(data_root, requirement.sha256))
        _fsync_directory(data_root)
    finally:
        temporary.unlink(missing_ok=True)


def _read_identity(data_root: Path, digest_hex: str) -> dict[str, object]:
    path = _identity_path(data_root, digest_hex)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64 * 1024:
            raise DataVerificationError("retained data identity is invalid")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise DataVerificationError("retained data identity is invalid")
        return cast("dict[str, object]", loaded)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataVerificationError("retained data identity is unavailable") from exc


def _verify_retained_identity(
    data_root: Path,
    digest: str,
    schema_minimum: str,
    schema_maximum: str,
) -> Path:
    digest_hex = digest.removeprefix("sha256:")
    target = data_root / digest_hex
    if target.is_symlink() or not target.is_dir():
        raise DataVerificationError("rollback target is not retained")
    identity = _read_identity(data_root, digest_hex)
    observed = expanded_tree_identity(target)
    if (
        identity.get("artifact_sha256") != digest_hex
        or identity.get("expanded_tree_sha256") != observed[0]
        or identity.get("expanded_size") != observed[1]
        or identity.get("member_count") != observed[2]
    ):
        raise DataVerificationError("retained data identity does not match the selected tree")
    data_schema = identity.get("data_schema_version")
    if not isinstance(data_schema, str) or not CompatibilityRange(
        minimum=schema_minimum, maximum=schema_maximum
    ).contains(data_schema):
        raise DataVerificationError("retained data schema is incompatible")
    return target


def _verify_requirement_identity(data_root: Path, requirement: DataRequirement) -> Path:
    assert requirement.sha256 is not None
    assert requirement.schema_minimum is not None
    assert requirement.schema_maximum is not None
    target = _verify_retained_identity(
        data_root,
        f"sha256:{requirement.sha256}",
        requirement.schema_minimum,
        requirement.schema_maximum,
    )
    identity = _read_identity(data_root, requirement.sha256)
    expected = _identity_payload(requirement, expanded_tree_identity(target))
    if any(identity.get(key) != value for key, value in expected.items()):
        raise DataVerificationError("retained data identity does not match reviewed evidence")
    return target


def _require_previous_known_good(data_root: Path, requirement: DataRequirement) -> None:
    assert requirement.sha256 is not None
    assert requirement.previous_known_good_digest is not None
    assert requirement.schema_minimum is not None
    assert requirement.schema_maximum is not None
    if requirement.previous_known_good_digest == f"sha256:{requirement.sha256}":
        return
    try:
        _verify_retained_identity(
            data_root,
            requirement.previous_known_good_digest,
            requirement.schema_minimum,
            requirement.schema_maximum,
        )
    except DataVerificationError as exc:
        raise DataVerificationError("previous-known-good target is not retained") from exc


def materialize_data(
    artifact: Path,
    requirement: DataRequirement,
    data_root: Path,
    *,
    schema_probe: Callable[[Path], str],
) -> Path:
    verify_compressed_artifact(artifact, requirement)
    assert requirement.sha256 is not None
    _ensure_private_data_root(data_root)
    target = data_root / requirement.sha256
    with _exclusive_lock(data_root):
        if target.exists():
            _verify_requirement_identity(data_root, requirement)
        else:
            staging = Path(tempfile.mkdtemp(dir=data_root, prefix=".materialize-"))
            try:
                _extract_archive(artifact, staging, requirement)
                observed = expanded_tree_identity(staging)
                if observed[0] != requirement.expanded_tree_sha256:
                    raise DataVerificationError(
                        "expanded tree digest does not match reviewed identity"
                    )
                actual_schema = schema_probe(staging)
                if actual_schema != requirement.schema_version:
                    raise DataVerificationError("schema probe does not match reviewed schema")
                _fsync_directory(staging)
                os.replace(staging, target)
                _fsync_directory(data_root)
                _write_identity(data_root, requirement, observed)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        _verify_requirement_identity(data_root, requirement)
        if schema_probe(target) != requirement.schema_version:
            raise DataVerificationError("schema probe does not match reviewed schema")
        _require_previous_known_good(data_root, requirement)
        _select(data_root, target)
        return target


def rollback_data(data_root: Path, digest: str, schema_minimum: str, schema_maximum: str) -> Path:
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise DataVerificationError("rollback digest is invalid")
    with _exclusive_lock(data_root):
        target = _verify_retained_identity(data_root, digest, schema_minimum, schema_maximum)
        _select(data_root, target)
    return target


def probe_schema_file(root: Path, schema_file: str) -> str:
    relative = _member_path(schema_file)
    path = root.joinpath(*relative.parts)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise DataVerificationError("schema file is not a bounded regular file")
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataVerificationError("schema file is not valid JSON") from exc
    if not isinstance(document, dict):
        raise DataVerificationError("schema file does not declare schema_version")
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, str):
        raise DataVerificationError("schema file does not declare schema_version")
    return schema_version
