"""Pre-open path validation for the durable refresh SQLite database."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafeRefreshPathError(RuntimeError):
    """The configured database path cannot be opened without following unsafe links."""


def _directory_components(path: Path) -> list[Path]:
    current = Path(path.anchor)
    components: list[Path] = []
    for part in path.parts[1:]:
        current /= part
        components.append(current)
    return components


def _prepare_directory_chain(parent: Path) -> None:
    for component in _directory_components(parent):
        try:
            details = component.lstat()
        except FileNotFoundError:
            try:
                component.mkdir(mode=0o700)
            except FileExistsError:
                details = component.lstat()
            else:
                details = component.lstat()
        if stat.S_ISLNK(details.st_mode):
            raise UnsafeRefreshPathError("configured refresh ledger directory contains a symlink")
        if not stat.S_ISDIR(details.st_mode):
            raise UnsafeRefreshPathError("configured refresh ledger parent must be a directory")

    parent_details = parent.lstat()
    unsafe_writes = parent_details.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    if unsafe_writes and not (parent_details.st_mode & stat.S_ISVTX):
        raise UnsafeRefreshPathError(
            "configured refresh ledger parent must not be group/world writable"
        )


def _open_parent(parent: Path) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(parent, flags)
    except OSError as exc:
        raise UnsafeRefreshPathError(
            "configured refresh ledger parent cannot be opened without following links"
        ) from exc


def _validate_open_file(
    descriptor: int,
    *,
    label: str,
    require_owner_access: bool,
) -> None:
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise UnsafeRefreshPathError(f"configured refresh ledger {label} must be a regular file")
    owner_access = stat.S_IRUSR | stat.S_IWUSR
    if require_owner_access and details.st_mode & owner_access != owner_access:
        raise UnsafeRefreshPathError(
            f"configured refresh ledger {label} must be readable and writable by its owner"
        )


def _prepare_main_file(parent_fd: int, name: str) -> bool:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, os.O_RDWR | nofollow | cloexec, dir_fd=parent_fd)
    except FileNotFoundError:
        descriptor = os.open(
            name,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | nofollow | cloexec,
            0o600,
            dir_fd=parent_fd,
        )
        created = True
    except PermissionError as exc:
        raise UnsafeRefreshPathError(
            "configured refresh ledger file must be readable and writable by its owner"
        ) from exc
    except OSError as exc:
        raise UnsafeRefreshPathError("configured refresh ledger must be a regular file") from exc
    else:
        created = False

    try:
        _validate_open_file(descriptor, label="file", require_owner_access=not created)
        if not created and os.fstat(descriptor).st_size == 0:
            raise UnsafeRefreshPathError("configured refresh ledger is empty")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    return created


def _validate_sidecar(parent_fd: int, name: str) -> None:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise UnsafeRefreshPathError(
            "configured refresh ledger sidecar must be a regular file"
        ) from exc
    try:
        _validate_open_file(descriptor, label="sidecar", require_owner_access=True)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def prepare_refresh_sqlite_path(path: str | Path) -> tuple[Path, bool]:
    """Validate/create a SQLite path before SQLite can resolve it or its sidecars."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    _prepare_directory_chain(absolute.parent)
    parent_fd = _open_parent(absolute.parent)
    try:
        created = _prepare_main_file(parent_fd, absolute.name)
        _validate_sidecar(parent_fd, f"{absolute.name}-wal")
        _validate_sidecar(parent_fd, f"{absolute.name}-shm")
    finally:
        os.close(parent_fd)
    return absolute, created
