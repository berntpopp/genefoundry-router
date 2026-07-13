"""Versioned, bounded policy model for public image inspection."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

_POLICY_PATH = Path(__file__).parent.parent / "data" / "image-content-policy-v1.json"
_APPROVED_EXTENSIONS = (
    ".css",
    ".csv",
    ".html",
    ".json",
    ".md",
    ".py",
    ".sql",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
)
_APPROVED_IGNORES = (
    ".git/",
    ".env",
    ".env.*",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".coverage",
    "htmlcov/",
    "venv/",
    "data/",
    "datasets/",
    "corpus/",
    "output/",
    "outputs/",
)


class ContentPolicyError(ValueError):
    """Raised when image evidence or content policy is invalid."""


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContentPolicyError("duplicate JSON key")
        result[key] = value
    return result


def json_bytes(path: Path, *, limit: int = 16 * 1024 * 1024) -> tuple[object, bytes]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ContentPolicyError(f"missing regular file: {path.name}")
            if before.st_nlink != 1:
                raise ContentPolicyError("JSON evidence hardlink is prohibited")
            if before.st_size > limit:
                raise ContentPolicyError(f"JSON document exceeds {limit} bytes")
            raw = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
        if len(raw) > limit:
            raise ContentPolicyError(f"JSON document exceeds {limit} bytes")
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ContentPolicyError("JSON evidence changed while being read")
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except ContentPolicyError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentPolicyError(f"invalid JSON document: {path.name}") from exc
    return value, raw


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ContentPolicy:
    _NUMERIC_FIELDS: ClassVar[tuple[str, ...]] = (
        "max_layers",
        "max_entries",
        "max_file_bytes",
        "max_total_bytes",
        "max_blob_bytes",
        "max_uncompressed_layer_bytes",
        "max_uncompressed_image_bytes",
        "magic_scan_bytes",
        "max_path_bytes",
        "max_diagnostic_bytes",
        "max_allowlist_entries",
        "max_allowlist_file_bytes",
        "max_allowlist_total_bytes",
        "max_diagnostics",
    )
    _HARD_MAXIMA: ClassVar[dict[str, int]] = {
        "max_layers": 256,
        "max_entries": 200_000,
        "max_file_bytes": 64 * 1024 * 1024,
        "max_total_bytes": 1024 * 1024 * 1024,
        "max_blob_bytes": 1024 * 1024 * 1024,
        "max_uncompressed_layer_bytes": 1024 * 1024 * 1024,
        "max_uncompressed_image_bytes": 2 * 1024 * 1024 * 1024,
        "magic_scan_bytes": 64 * 1024,
        "max_path_bytes": 4096,
        "max_diagnostic_bytes": 64 * 1024,
        "max_allowlist_entries": 128,
        "max_allowlist_file_bytes": 4 * 1024 * 1024,
        "max_allowlist_total_bytes": 16 * 1024 * 1024,
        "max_diagnostics": 256,
    }
    version: int
    max_layers: int
    max_entries: int
    max_file_bytes: int
    max_total_bytes: int
    max_blob_bytes: int
    max_uncompressed_layer_bytes: int
    max_uncompressed_image_bytes: int
    magic_scan_bytes: int
    max_path_bytes: int
    max_diagnostic_bytes: int
    max_allowlist_entries: int
    max_allowlist_file_bytes: int
    max_allowlist_total_bytes: int
    max_diagnostics: int
    allowlist_media_types: tuple[tuple[str, str], ...]
    layer_media_types: tuple[str, ...]
    required_dockerignore: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ContentPolicyError("unsupported content policy version")
        if any(
            type(getattr(self, name)) is not int
            or getattr(self, name) <= 0
            or getattr(self, name) > maximum
            for name, maximum in self._HARD_MAXIMA.items()
        ):
            raise ContentPolicyError("content policy limit is invalid")
        if not (
            self.max_file_bytes <= self.max_total_bytes
            and self.magic_scan_bytes <= self.max_file_bytes
            and self.max_uncompressed_layer_bytes <= self.max_uncompressed_image_bytes
            and self.max_diagnostic_bytes >= 4096
            and self.max_allowlist_entries <= self.max_entries
            and self.max_allowlist_file_bytes <= self.max_file_bytes
            and self.max_allowlist_total_bytes <= self.max_total_bytes
        ):
            raise ContentPolicyError("content policy limits are inconsistent")
        media = dict(self.allowlist_media_types)
        if (
            len(media) != len(self.allowlist_media_types)
            or tuple(sorted(media)) != _APPROVED_EXTENSIONS
            or any(
                not extension.startswith(".")
                or extension != extension.lower()
                or value != "text/plain; charset=utf-8"
                for extension, value in media.items()
            )
        ):
            raise ContentPolicyError("content policy allowlist media contract is invalid")
        if (
            self.layer_media_types
            != (
                "application/vnd.oci.image.layer.v1.tar",
                "application/vnd.oci.image.layer.v1.tar+gzip",
            )
            or not self.required_dockerignore
            or self.required_dockerignore != _APPROVED_IGNORES
        ):
            raise ContentPolicyError("content policy lists are invalid")

    @property
    def allowed_allowlist_extensions(self) -> tuple[str, ...]:
        return tuple(extension for extension, _ in self.allowlist_media_types)

    @property
    def digest(self) -> str:
        return canonical_digest(self._payload())

    def _payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            **{name: getattr(self, name) for name in self._NUMERIC_FIELDS},
            "allowlist_media_types": dict(self.allowlist_media_types),
            "layer_media_types": list(self.layer_media_types),
            "required_dockerignore": list(self.required_dockerignore),
        }

    @classmethod
    def default(cls) -> ContentPolicy:
        return cls.from_path(_POLICY_PATH)

    @classmethod
    def from_path(cls, path: Path) -> ContentPolicy:
        value, _ = json_bytes(path)
        if not isinstance(value, dict):
            raise ContentPolicyError("content policy must be a JSON object")
        expected = {
            "version",
            *cls._NUMERIC_FIELDS,
            "allowlist_media_types",
            "layer_media_types",
            "required_dockerignore",
        }
        if set(value) != expected:
            raise ContentPolicyError("content policy keys are invalid")
        if type(value["version"]) is not int or any(
            type(value[name]) is not int for name in cls._NUMERIC_FIELDS
        ):
            raise ContentPolicyError("content policy types are invalid")
        media, layers, ignores = (
            value["allowlist_media_types"],
            value["layer_media_types"],
            value["required_dockerignore"],
        )
        if (
            not isinstance(media, dict)
            or any(
                not isinstance(key, str) or not isinstance(item, str) for key, item in media.items()
            )
            or not isinstance(layers, list)
            or any(not isinstance(item, str) for item in layers)
            or not isinstance(ignores, list)
            or any(not isinstance(item, str) for item in ignores)
        ):
            raise ContentPolicyError("content policy types are invalid")
        try:
            return cls(
                **{name: value[name] for name in ("version", *cls._NUMERIC_FIELDS)},
                allowlist_media_types=tuple(sorted(media.items())),
                layer_media_types=tuple(layers),
                required_dockerignore=tuple(ignores),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContentPolicyError("invalid content policy") from exc

    def with_limits(self, **limits: int) -> ContentPolicy:
        permitted = {
            "max_layers",
            "max_entries",
            "max_file_bytes",
            "max_total_bytes",
            "max_uncompressed_image_bytes",
        }
        if not limits.keys() <= permitted or any(
            type(value) is not int or value <= 0 for value in limits.values()
        ):
            raise ContentPolicyError("invalid policy limit override")
        if any(value > getattr(self, name) for name, value in limits.items()):
            raise ContentPolicyError("policy limits may tighten but never relax")
        entries = limits.get("max_entries", self.max_entries)
        total_bytes = limits.get("max_total_bytes", self.max_total_bytes)
        file_bytes = min(limits.get("max_file_bytes", self.max_file_bytes), total_bytes)
        image_bytes = limits.get("max_uncompressed_image_bytes", self.max_uncompressed_image_bytes)
        return dataclasses.replace(
            self,
            max_layers=limits.get("max_layers", self.max_layers),
            max_entries=entries,
            max_file_bytes=file_bytes,
            max_total_bytes=total_bytes,
            max_uncompressed_layer_bytes=min(self.max_uncompressed_layer_bytes, image_bytes),
            max_uncompressed_image_bytes=image_bytes,
            magic_scan_bytes=min(self.magic_scan_bytes, file_bytes),
            max_allowlist_entries=min(self.max_allowlist_entries, entries),
            max_allowlist_file_bytes=min(self.max_allowlist_file_bytes, file_bytes),
            max_allowlist_total_bytes=min(self.max_allowlist_total_bytes, total_bytes),
        )
