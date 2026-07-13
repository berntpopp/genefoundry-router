"""Stable, bounded snapshots of untrusted OCI descriptor blobs."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from types import TracebackType

from genefoundry_router.release.content_policy import ContentPolicyError

_READ_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_FLAGS = _READ_FLAGS | getattr(os, "O_DIRECTORY", 0)


def _open_blob(layout: Path, digest: str) -> int:
    descriptors: list[int] = []
    try:
        layout_fd = os.open(layout, _DIRECTORY_FLAGS)
        descriptors.append(layout_fd)
        blobs_fd = os.open("blobs", _DIRECTORY_FLAGS, dir_fd=layout_fd)
        descriptors.append(blobs_fd)
        sha256_fd = os.open("sha256", _DIRECTORY_FLAGS, dir_fd=blobs_fd)
        descriptors.append(sha256_fd)
        return os.open(digest, _READ_FLAGS, dir_fd=sha256_fd)
    except OSError as exc:
        raise ContentPolicyError("unable to open OCI blob safely") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class BlobSnapshotStore:
    """Copy each digest-addressed source once into private immutable evidence."""

    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._root: Path | None = None
        self._cache: dict[str, tuple[Path, int]] = {}

    def __enter__(self) -> BlobSnapshotStore:
        self._temporary = tempfile.TemporaryDirectory(prefix="genefoundry-oci-evidence-")
        self._root = Path(self._temporary.name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        assert self._temporary is not None
        self._temporary.cleanup()

    def snapshot(
        self,
        layout: Path,
        digest: str,
        expected_size: int,
        max_size: int,
    ) -> Path:
        if expected_size > max_size:
            raise ContentPolicyError("OCI blob byte limit exceeded")
        cached = self._cache.get(digest)
        if cached is not None:
            if cached[1] != expected_size:
                raise ContentPolicyError("OCI blob size mismatch")
            return cached[0]
        assert self._root is not None
        blob_root = layout / "blobs"
        algorithm_root = blob_root / "sha256"
        source_path = algorithm_root / digest
        if blob_root.is_symlink() or algorithm_root.is_symlink():
            raise ContentPolicyError("OCI blob directory may not be symlinked")
        if source_path.is_symlink():
            raise ContentPolicyError("OCI blob may not be symlinked")
        if not source_path.exists():
            raise ContentPolicyError("missing OCI blob")
        source_fd = _open_blob(layout, digest)
        target = self._root / digest
        hasher = hashlib.sha256()
        copied = 0
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise ContentPolicyError("missing regular OCI blob")
            if source_stat.st_nlink != 1:
                raise ContentPolicyError("OCI evidence hardlink is prohibited")
            if source_stat.st_size != expected_size:
                raise ContentPolicyError("OCI blob size mismatch")
            with os.fdopen(source_fd, "rb", closefd=False) as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > expected_size or copied > max_size:
                        raise ContentPolicyError("OCI blob byte limit exceeded")
                    hasher.update(chunk)
                    output.write(chunk)
        except OSError as exc:
            raise ContentPolicyError("unable to snapshot OCI blob") from exc
        finally:
            os.close(source_fd)
        if copied != expected_size:
            raise ContentPolicyError("OCI blob size mismatch")
        if hasher.hexdigest() != digest:
            raise ContentPolicyError("OCI blob digest mismatch")
        target.chmod(0o400)
        self._cache[digest] = (target, copied)
        return target
