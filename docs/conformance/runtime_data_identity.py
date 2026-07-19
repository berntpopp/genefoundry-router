#!/usr/bin/env python3
"""Build and verify the canonical GeneFoundry runtime data identity v1."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import cast

MANIFEST_NAME = "data-identity-manifest.json"

_MANIFEST_KEYS = frozenset({"schema_version", "release_tag", "inputs"})
_INPUT_KEYS = frozenset({"path", "size_bytes", "sha256"})
_RELEASE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MUTABLE_RELEASE_TAGS = frozenset({"latest", "main", "master", "head", "stable", "current"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class RuntimeDataIdentityError(ValueError):
    """A materialized data root cannot prove its runtime identity."""


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a value as canonical UTF-8 JSON for identity hashing."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _resolved_root(root: Path) -> Path:
    if root.is_symlink():
        raise RuntimeDataIdentityError(f"materialized data root is a symlink: {root}")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeDataIdentityError(f"materialized data root does not exist: {root}") from exc
    if not resolved.is_dir():
        raise RuntimeDataIdentityError(f"materialized data root is not a directory: {root}")
    return resolved


def _validated_release_tag(value: object) -> str:
    if type(value) is not str or not _RELEASE_TAG.fullmatch(value):
        raise RuntimeDataIdentityError(
            "release_tag must use only letters, digits, dots, underscores, and hyphens "
            "and contain at most 128 characters"
        )
    release_tag = value
    if release_tag.lower() in _MUTABLE_RELEASE_TAGS:
        raise RuntimeDataIdentityError("release_tag must be immutable")
    return release_tag


def _canonical_relative_path(value: object) -> PurePosixPath:
    if type(value) is not str or not value:
        raise RuntimeDataIdentityError("input path must be a non-empty POSIX string")
    stored = value
    path = PurePosixPath(stored)
    if (
        path.is_absolute()
        or path.as_posix() != stored
        or path == PurePosixPath(".")
        or ".." in path.parts
    ):
        raise RuntimeDataIdentityError(
            f"input path is not a canonical relative POSIX path: {stored}"
        )
    if path == PurePosixPath(MANIFEST_NAME):
        raise RuntimeDataIdentityError("the identity manifest cannot be an authoritative input")
    return path


def _regular_file(root: Path, relative: PurePosixPath) -> Path:
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink():
        raise RuntimeDataIdentityError(f"input path is a symlink alias: {relative.as_posix()}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeDataIdentityError(f"input file is missing: {relative.as_posix()}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeDataIdentityError(
            f"input resolves outside the materialized data root: {relative.as_posix()}"
        ) from exc
    if resolved != candidate:
        raise RuntimeDataIdentityError(
            f"input path uses a symlink or filesystem alias: {relative.as_posix()}"
        )
    if not resolved.is_file():
        raise RuntimeDataIdentityError(f"input is not a regular file: {relative.as_posix()}")
    return resolved


def _build_file_entry(path: Path, relative: PurePosixPath) -> dict[str, object]:
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size_bytes += len(chunk)
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeDataIdentityError(f"input file is unreadable: {relative.as_posix()}") from exc
    return {
        "path": relative.as_posix(),
        "size_bytes": size_bytes,
        "sha256": digest.hexdigest(),
    }


def _build_relative_path(root: Path, file: Path) -> PurePosixPath:
    candidate = root / file if not file.is_absolute() else file
    try:
        lexical_relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeDataIdentityError(f"input file must be beneath the data root: {file}") from exc
    relative = _canonical_relative_path(lexical_relative.as_posix())
    resolved = _regular_file(root, relative)
    if resolved != candidate:
        raise RuntimeDataIdentityError(f"input path uses a filesystem alias: {file}")
    return relative


def build_identity_manifest(
    root: Path, release_tag: str, files: Sequence[Path]
) -> dict[str, object]:
    """Create the exact v1 manifest from regular files beneath one materialized root."""
    resolved_root = _resolved_root(root)
    valid_release_tag = _validated_release_tag(release_tag)
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for file in files:
        relative = _build_relative_path(resolved_root, file)
        stored = relative.as_posix()
        if stored in seen:
            raise RuntimeDataIdentityError(f"duplicate input path: {stored}")
        seen.add(stored)
        entries.append(_build_file_entry(_regular_file(resolved_root, relative), relative))
    entries.sort(key=lambda entry: cast(str, entry["path"]))
    return {
        "schema_version": 1,
        "release_tag": valid_release_tag,
        "inputs": entries,
    }


def _require_exact_keys(value: dict[object, object], expected: frozenset[str], label: str) -> None:
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(str(key) for key in keys - expected)
        raise RuntimeDataIdentityError(
            f"{label} has invalid keys (missing={missing}, unexpected={extra})"
        )


def _validated_input(value: object, index: int) -> tuple[PurePosixPath, int, str]:
    if type(value) is not dict:
        raise RuntimeDataIdentityError(f"inputs[{index}] must be an object")
    item = cast(dict[object, object], value)
    _require_exact_keys(item, _INPUT_KEYS, f"inputs[{index}]")
    relative = _canonical_relative_path(item["path"])
    size = item["size_bytes"]
    if type(size) is not int or size < 0:
        raise RuntimeDataIdentityError(f"inputs[{index}].size_bytes must be a non-negative integer")
    digest = item["sha256"]
    if type(digest) is not str or not _SHA256_HEX.fullmatch(digest):
        raise RuntimeDataIdentityError(
            f"inputs[{index}].sha256 must be exactly 64 lowercase hexadecimal characters"
        )
    return relative, size, digest


def _load_manifest(root: Path) -> dict[str, object]:
    path = root / MANIFEST_NAME
    if path.is_symlink():
        raise RuntimeDataIdentityError("identity manifest must not be a symlink")
    if not path.is_file():
        raise RuntimeDataIdentityError("identity manifest is missing or is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeDataIdentityError("identity manifest is unreadable or invalid JSON") from exc
    if type(value) is not dict:
        raise RuntimeDataIdentityError("identity manifest must be an object")
    manifest = cast(dict[object, object], value)
    _require_exact_keys(manifest, _MANIFEST_KEYS, "identity manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise RuntimeDataIdentityError("identity manifest schema_version must be integer 1")
    if type(manifest["inputs"]) is not list:
        raise RuntimeDataIdentityError("identity manifest inputs must be an array")
    return cast(dict[str, object], manifest)


def _discover_regular_files(root: Path) -> set[str]:
    discovered: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        relative_directory = directory.relative_to(root).as_posix()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            location = "." if relative_directory == "." else relative_directory
            raise RuntimeDataIdentityError(
                f"materialized data root inventory failed at: {location}"
            ) from exc

        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise RuntimeDataIdentityError(
                    f"materialized data root inventory failed at: {relative}"
                ) from exc
            if stat.S_ISLNK(mode):
                raise RuntimeDataIdentityError(f"unexpected symlink in data root: {relative}")
            if relative == MANIFEST_NAME:
                if not stat.S_ISREG(mode):
                    raise RuntimeDataIdentityError(
                        "identity manifest is missing or is not a regular file"
                    )
                continue
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                discovered.add(relative)
    return discovered


def _verify_file_entries(root: Path, validated: Sequence[tuple[PurePosixPath, int, str]]) -> None:
    for relative, expected_size, expected_digest in validated:
        path = _regular_file(root, relative)
        actual = _build_file_entry(path, relative)
        if actual["size_bytes"] != expected_size:
            raise RuntimeDataIdentityError(f"size_bytes mismatch for input: {relative.as_posix()}")
        if actual["sha256"] != expected_digest:
            raise RuntimeDataIdentityError(f"sha256 mismatch for input: {relative.as_posix()}")


def verify_runtime_identity(root: Path) -> dict[str, str]:
    """Rehash every authoritative runtime file and return release_tag/digest on success.

    Two content passes bracket the complete directory inventory to detect concurrent input
    mutation on a best-effort basis. Portable filesystems do not provide an atomic directory and
    file-content snapshot, so mutation after the final pass remains outside this v1 guarantee.
    """
    resolved_root = _resolved_root(root)
    manifest = _load_manifest(resolved_root)
    release_tag = _validated_release_tag(manifest["release_tag"])
    inputs = cast(list[object], manifest["inputs"])

    validated: list[tuple[PurePosixPath, int, str]] = []
    stored_paths: list[str] = []
    for index, value in enumerate(inputs):
        entry = _validated_input(value, index)
        stored = entry[0].as_posix()
        if stored in stored_paths:
            raise RuntimeDataIdentityError(f"duplicate input path: {stored}")
        stored_paths.append(stored)
        validated.append(entry)
    if stored_paths != sorted(stored_paths):
        raise RuntimeDataIdentityError("identity manifest inputs must be lexically sorted by path")

    _verify_file_entries(resolved_root, validated)

    inventory = set(stored_paths)
    discovered = _discover_regular_files(resolved_root)
    missing = sorted(inventory - discovered)
    if missing:
        raise RuntimeDataIdentityError(f"missing regular file from data root: {missing[0]}")
    extra = sorted(discovered - inventory)
    if extra:
        raise RuntimeDataIdentityError(f"unexpected regular file in data root: {extra[0]}")

    _verify_file_entries(resolved_root, validated)
    final_manifest = _load_manifest(resolved_root)
    if canonical_json_bytes(final_manifest) != canonical_json_bytes(manifest):
        raise RuntimeDataIdentityError("identity manifest changed during verification")

    digest = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    return {"release_tag": release_tag, "digest": f"sha256:{digest}"}
