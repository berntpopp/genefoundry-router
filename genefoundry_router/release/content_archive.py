"""Bounded streaming inspection of untrusted OCI layer tar archives."""

from __future__ import annotations

import gzip
import hashlib
import math
import tarfile
import unicodedata
import zlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, Protocol, cast

from genefoundry_router.release.content_detection import (
    PrivatePemScanner,
    classified_media,
    denied_by_name,
    forbidden_payload_prefix,
    looks_compressed,
    never_allow_by_name,
)
from genefoundry_router.release.content_diagnostics import FindingBuffer
from genefoundry_router.release.content_secrets import secret_shaped
from genefoundry_router.release.content_tar import TarStructure


class _Policy(Protocol):
    @property
    def max_entries(self) -> int: ...

    @property
    def max_file_bytes(self) -> int: ...

    @property
    def max_total_bytes(self) -> int: ...

    @property
    def max_blob_bytes(self) -> int: ...

    @property
    def max_uncompressed_layer_bytes(self) -> int: ...

    @property
    def magic_scan_bytes(self) -> int: ...

    @property
    def max_path_bytes(self) -> int: ...

    @property
    def max_diagnostic_bytes(self) -> int: ...

    @property
    def max_allowlist_file_bytes(self) -> int: ...

    @property
    def max_allowlist_total_bytes(self) -> int: ...

    @property
    def max_diagnostics(self) -> int: ...

    @property
    def allowlist_media_types(self) -> tuple[tuple[str, str], ...]: ...


@dataclass(frozen=True)
class LayerResult:
    denied: tuple[str, ...]
    allowlisted: tuple[str, ...]
    entries: int
    total_bytes: int
    allowlisted_entries: int
    allowlisted_bytes: int
    diagnostics_truncated: bool


def _error(message: str) -> Exception:
    # Delayed import avoids a content/content_archive import cycle.
    from genefoundry_router.release.content import ContentPolicyError

    return ContentPolicyError(message)


def _utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8", "strict"))
    except UnicodeEncodeError as exc:
        raise _error("archive path is not valid UTF-8") from exc


def _safe_archive_path(raw: str, max_bytes: int) -> str:
    if _utf8_size(raw) > max_bytes:
        raise _error("archive path byte limit exceeded")
    if not raw or raw.startswith("/") or "\\" in raw or "//" in raw:
        raise _error("unsafe archive path")
    if unicodedata.normalize("NFC", raw) != raw or any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in raw
    ):
        raise _error("archive path contains prohibited characters")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise _error("unsafe archive path")
    normalized = str(PurePosixPath(*parts))
    if normalized != raw:
        raise _error("unsafe archive path")
    return normalized


def _safe_symlink(path: str, target: str, max_bytes: int) -> None:
    if (
        not target
        or "\\" in target
        or _utf8_size(target) > max_bytes
        or unicodedata.normalize("NFC", target) != target
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in target)
        or secret_shaped(target, semantic_words=False)
    ):
        raise _error("archive symlink target is unsafe")
    absolute = target.startswith("/")
    parts: list[str] = [] if absolute else list(PurePosixPath(path).parent.parts)
    for index, part in enumerate(target.split("/")):
        if not part and absolute and index == 0:
            continue
        if not part or part == ".":
            raise _error("archive symlink target is unsafe")
        if part == "..":
            if not parts:
                raise _error("archive symlink escapes root")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise _error("archive symlink target is unsafe")


def _validate_pax(member: tarfile.TarInfo, policy: _Policy) -> None:
    allowed = {"path", "size", "mtime", "linkpath"}
    for key, value in member.pax_headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise _error("archive metadata is malformed")
        if _utf8_size(key) > 32:
            raise _error("archive metadata is prohibited")
        if key not in allowed:
            raise _error("archive metadata channel is prohibited")
        if key == "path":
            if _safe_archive_path(value, policy.max_path_bytes) != member.name:
                raise _error("archive metadata path binding is invalid")
            if secret_shaped(value, semantic_words=False):
                raise _error("archive metadata is prohibited")
        elif key == "linkpath":
            if not member.issym() or value != member.linkname:
                raise _error("archive metadata link binding is invalid")
            _safe_symlink(member.name, value, policy.max_path_bytes)
        elif secret_shaped(value, semantic_words=False):
            raise _error("archive metadata is prohibited")
        elif _utf8_size(value) > 64:
            raise _error("archive metadata value limit exceeded")
        elif key == "size":
            if not value.isdecimal() or int(value) != member.size:
                raise _error("archive metadata size binding is invalid")
        else:
            try:
                if not Decimal(value).is_finite() or float(value) != member.mtime:
                    raise _error("archive metadata mtime binding is invalid")
            except (InvalidOperation, ValueError, OverflowError) as exc:
                raise _error("archive metadata mtime is invalid") from exc


def _validate_header_metadata(member: tarfile.TarInfo) -> None:
    for value in (member.uname, member.gname):
        if (
            not isinstance(value, str)
            or _utf8_size(value) > 32
            or secret_shaped(value, semantic_words=False)
        ):
            raise _error("archive metadata owner is prohibited")
    if (
        type(member.uid) is not int
        or type(member.gid) is not int
        or not 0 <= member.uid <= 2**31 - 1
        or not 0 <= member.gid <= 2**31 - 1
        or type(member.mode) is not int
        or not 0 <= member.mode <= 0o7777
        or not isinstance(member.mtime, (int, float))
        or isinstance(member.mtime, bool)
        or not math.isfinite(member.mtime)
    ):
        raise _error("archive numeric metadata is invalid")


def inspect_layer(
    path: Path,
    policy: _Policy,
    allowlist: frozenset[str],
    compression: str | None = None,
) -> LayerResult:
    """Stream a tar layer once, retaining only bounded diagnostics."""
    denied = FindingBuffer(policy.max_diagnostics, policy.max_diagnostic_bytes)
    allowed_seen = FindingBuffer(policy.max_diagnostics, policy.max_diagnostic_bytes)
    seen: set[str] = set()
    entries = 0
    total = 0
    allowlisted_total = 0
    allowlisted_count = 0
    allowlist_media = dict(policy.allowlist_media_types)
    try:
        if path.is_symlink() or not path.is_file():
            raise _error("layer blob must be a regular file")
        if path.stat().st_size > policy.max_blob_bytes:
            raise _error("compressed layer blob byte limit exceeded")
        mode: Literal["r|*", "r|", "r|gz"]
        if compression is None:
            mode = "r|*"
        elif compression == "plain":
            mode = "r|"
        elif compression == "gzip":
            mode = "r|gz"
        else:
            raise _error("unsupported layer compression")
        with tarfile.open(path, mode=mode) as archive:
            for member in archive:
                entries += 1
                if entries > policy.max_entries:
                    raise _error("archive entry limit exceeded")
                _validate_header_metadata(member)
                _validate_pax(member, policy)
                is_root = member.name == "."
                if is_root and not member.isdir():
                    raise _error("unsafe archive path")
                normalized = (
                    "." if is_root else _safe_archive_path(member.name, policy.max_path_bytes)
                )
                if secret_shaped(normalized, semantic_words=False):
                    raise _error("archive metadata contains prohibited material")
                identity = hashlib.sha256(normalized.encode()).hexdigest()
                if identity in seen:
                    raise _error(f"duplicate archive entry: {normalized}")
                seen.add(identity)
                if member.issym():
                    _safe_symlink(normalized, member.linkname, policy.max_path_bytes)
                    continue
                if member.islnk():
                    raise _error("archive hardlinks are prohibited")
                if member.ischr() or member.isblk() or member.isfifo():
                    raise _error(f"special archive entry is prohibited: {normalized}")
                if member.mode & 0o6000:
                    raise _error(f"set-id archive entry is prohibited: {normalized}")
                if is_root:
                    continue
                if member.isdir():
                    continue
                if not member.isfile():
                    raise _error(f"unsupported archive entry type: {normalized}")
                if member.size < 0 or member.size > policy.max_total_bytes - total:
                    raise _error("archive aggregate byte limit exceeded")
                total += member.size
                is_allowed = normalized in allowlist
                if is_allowed and member.size > policy.max_allowlist_file_bytes:
                    raise _error("allowlisted file byte limit exceeded")
                if is_allowed:
                    allowlisted_count += 1
                    allowlisted_total += member.size
                    if allowlisted_total > policy.max_allowlist_total_bytes:
                        raise _error("allowlist aggregate byte limit exceeded")
                stream = archive.extractfile(member)
                if stream is None:
                    raise _error(f"unable to read regular archive entry: {normalized}")
                prefix = b""
                allowlisted_payload = bytearray()
                pem_scanner = PrivatePemScanner()
                consumed = 0
                while consumed < member.size:
                    chunk = stream.read(min(64 * 1024, member.size - consumed))
                    if not chunk:
                        raise _error("truncated tar entry")
                    if len(prefix) < policy.magic_scan_bytes:
                        prefix += chunk[: policy.magic_scan_bytes - len(prefix)]
                    if is_allowed:
                        allowlisted_payload.extend(chunk)
                    pem_scanner.feed(chunk)
                    consumed += len(chunk)
                private_material = pem_scanner.finish()
                name_violation = denied_by_name(normalized)
                expected_media = allowlist_media.get(PurePosixPath(normalized).suffix.lower())
                hard_violation = (
                    never_allow_by_name(normalized)
                    or forbidden_payload_prefix(prefix, policy.magic_scan_bytes)
                    or private_material
                    or (
                        is_allowed
                        and classified_media(bytes(allowlisted_payload)) != expected_media
                    )
                    or member.size > policy.max_file_bytes
                )
                whiteout = PurePosixPath(normalized).name.startswith(".wh.")
                if whiteout:
                    whiteout_name = PurePosixPath(normalized).name
                    malformed = whiteout_name == ".wh." or (
                        whiteout_name.startswith(".wh..wh.") and whiteout_name != ".wh..wh..opq"
                    )
                    if member.size != 0 or malformed:
                        raise _error(f"malformed whiteout entry: {normalized}")
                if is_allowed and not hard_violation and not whiteout:
                    allowed_seen.add(normalized)
                elif hard_violation or (name_violation and not whiteout):
                    denied.add(normalized)
    except Exception as exc:
        from genefoundry_router.release.content import ContentPolicyError

        if isinstance(exc, ContentPolicyError):
            raise
        if isinstance(exc, (tarfile.TarError, OSError, EOFError)):
            raise _error("invalid or malformed tar layer") from exc
        raise
    return LayerResult(
        denied.values,
        allowed_seen.values,
        entries,
        total,
        allowlisted_count,
        allowlisted_total,
        denied.truncated or allowed_seen.truncated,
    )


def uncompressed_layer_digest(
    path: Path, compression: str, limit: int, metadata_limit: int = 8192
) -> str:
    """Hash the complete uncompressed layer stream under a hard byte ceiling."""
    if path.is_symlink() or not path.is_file():
        raise _error("layer blob must be a regular file")
    try:
        raw = path.open("rb")
        prefix = raw.read(6)
        raw.seek(0)
        if compression == "plain":
            if looks_compressed(prefix):
                raise _error("plain OCI layer has compressed encoding")
            stream: BinaryIO = raw
        elif compression == "gzip":
            if not prefix.startswith(b"\x1f\x8b"):
                raise _error("gzip OCI layer has plain encoding")
            stream = cast(BinaryIO, gzip.GzipFile(fileobj=raw))
        else:
            raise _error("unsupported layer compression")
        hasher = hashlib.sha256()
        structure = TarStructure(metadata_limit)
        total = 0
        with raw, stream:
            while chunk := stream.read(64 * 1024):
                total += len(chunk)
                if total > limit:
                    raise _error("uncompressed layer byte limit exceeded")
                hasher.update(chunk)
                structure.feed(chunk)
        structure.finish()
    except Exception as exc:
        from genefoundry_router.release.content_policy import ContentPolicyError

        if isinstance(exc, ContentPolicyError):
            raise
        if isinstance(exc, (OSError, EOFError, gzip.BadGzipFile, zlib.error)):
            raise _error("invalid compressed layer stream") from exc
        raise
    return f"sha256:{hasher.hexdigest()}"
