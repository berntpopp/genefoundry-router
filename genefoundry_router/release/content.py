"""Fail-closed policy for public OCI image contents and Docker build contexts."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from genefoundry_router.release.content_archive import (
    inspect_layer,
    uncompressed_layer_digest,
)
from genefoundry_router.release.content_config import (
    add_annotation_findings,
    inspect_config,
)
from genefoundry_router.release.content_diagnostics import FindingBuffer
from genefoundry_router.release.content_policy import (
    ContentPolicy,
    ContentPolicyError,
)
from genefoundry_router.release.content_policy import (
    canonical_digest as _canonical_digest,
)
from genefoundry_router.release.content_policy import (
    json_bytes as _json_bytes,
)

_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
_SHA256 = re.compile(r"sha256:([0-9a-f]{64})\Z")


@dataclass(frozen=True)
class ContentReport:
    policy_digest: str
    allowlist_digest: str
    denied_paths: tuple[str, ...] = ()
    allowlisted_paths: tuple[str, ...] = ()
    denied_config: tuple[str, ...] = ()
    context_size: int = 0
    inspected_entries: int = 0
    inspected_bytes: int = 0
    diagnostics_truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "allowlist_digest": self.allowlist_digest,
            "allowlisted_paths": list(self.allowlisted_paths),
            "context_size": self.context_size,
            "denied_config": list(self.denied_config),
            "denied_paths": list(self.denied_paths),
            "diagnostics_truncated": self.diagnostics_truncated,
            "inspected_bytes": self.inspected_bytes,
            "inspected_entries": self.inspected_entries,
            "policy_digest": self.policy_digest,
        }


def _safe_path(raw: str, max_bytes: int, *, label: str = "allowlist") -> str:
    try:
        if len(raw.encode("utf-8", "strict")) > max_bytes:
            raise ContentPolicyError(f"{label} path byte limit exceeded")
    except UnicodeEncodeError as exc:
        raise ContentPolicyError(f"{label} path is not valid UTF-8") from exc
    if not raw or raw.startswith("/") or "\\" in raw or "//" in raw:
        raise ContentPolicyError(f"unsafe {label} path")
    if unicodedata.normalize("NFC", raw) != raw or any(
        unicodedata.category(character) in {"Cc", "Cf"} for character in raw
    ):
        raise ContentPolicyError(f"{label} path contains prohibited characters")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ContentPolicyError(f"unsafe {label} path")
    normalized = str(PurePosixPath(*parts))
    if normalized != raw:
        raise ContentPolicyError(f"unsafe {label} path")
    return normalized


def _prepare_allowlist(
    policy: ContentPolicy, allowlist: tuple[str, ...]
) -> tuple[frozenset[str], str]:
    if len(allowlist) > policy.max_allowlist_entries:
        raise ContentPolicyError("allowlist entry limit exceeded")
    normalized: list[str] = []
    for raw in allowlist:
        path = _safe_path(raw, policy.max_path_bytes)
        if PurePosixPath(path).suffix.lower() not in policy.allowed_allowlist_extensions:
            raise ContentPolicyError("allowlist path has an unreviewed extension")
        normalized.append(path)
    if len(set(normalized)) != len(normalized):
        raise ContentPolicyError("duplicate allowlist path")
    ordered = tuple(sorted(normalized))
    return frozenset(ordered), _canonical_digest(list(ordered))


def _bounded_report_diagnostics(
    policy: ContentPolicy,
    denied: tuple[str, ...] | set[str],
    allowlisted: tuple[str, ...] | set[str],
    config: tuple[str, ...] | set[str],
) -> tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], bool]:
    remaining_bytes = policy.max_diagnostic_bytes - 1024
    remaining_count = policy.max_diagnostics
    truncated = False
    bounded: list[tuple[str, ...]] = []
    for values in (denied, allowlisted, config):
        accepted: list[str] = []
        for value in sorted(set(values)):
            cost = len(json.dumps(value, ensure_ascii=True).encode("utf-8")) + 1
            if remaining_count <= 0 or cost > remaining_bytes:
                truncated = True
                continue
            accepted.append(value)
            remaining_count -= 1
            remaining_bytes -= cost
        bounded.append(tuple(accepted))
    return (bounded[0], bounded[1], bounded[2]), truncated


def inspect_rootfs(
    archive: Path, policy: ContentPolicy | None = None, allowlist: tuple[str, ...] = ()
) -> ContentReport:
    """Inspect one layer tar without extracting it."""
    selected = policy or ContentPolicy.default()
    allowed, allowlist_digest = _prepare_allowlist(selected, allowlist)
    with archive.open("rb") as handle:
        compression = "gzip" if handle.read(2) == b"\x1f\x8b" else "plain"
    uncompressed_layer_digest(
        archive,
        compression,
        selected.max_uncompressed_layer_bytes,
        selected.max_path_bytes + 1024,
    )
    layer = inspect_layer(archive, selected, allowed)
    (denied, allowlisted, _), truncated = _bounded_report_diagnostics(
        selected, layer.denied, layer.allowlisted, ()
    )
    return ContentReport(
        policy_digest=selected.digest,
        allowlist_digest=allowlist_digest,
        denied_paths=denied,
        allowlisted_paths=allowlisted,
        inspected_entries=layer.entries,
        inspected_bytes=layer.total_bytes,
        diagnostics_truncated=layer.diagnostics_truncated or truncated,
    )


def _descriptor_blob(
    layout: Path,
    descriptor: object,
    media_types: set[str],
    *,
    platform: bool = False,
    max_size: int,
) -> tuple[dict[str, Any], Path]:
    allowed_keys = {"mediaType", "digest", "size", "annotations"}
    if platform:
        allowed_keys.add("platform")
    if not isinstance(descriptor, dict) or set(descriptor) - allowed_keys:
        raise ContentPolicyError("malformed or ambiguous OCI descriptor")
    media = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if (
        media not in media_types
        or not isinstance(digest, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise ContentPolicyError("unsupported or malformed OCI descriptor")
    annotations = descriptor.get("annotations")
    if annotations is not None and (
        not isinstance(annotations, dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in annotations.items()
        )
    ):
        raise ContentPolicyError("malformed OCI descriptor annotations")
    match = _SHA256.fullmatch(digest)
    if match is None:
        raise ContentPolicyError("only sha256 OCI descriptors are supported")
    blob = layout / "blobs" / "sha256" / match.group(1)
    root = layout.resolve()
    if any(parent.is_symlink() for parent in (layout / "blobs", layout / "blobs" / "sha256")):
        raise ContentPolicyError("OCI blob directory may not be symlinked")
    if not blob.resolve().is_relative_to(root):
        raise ContentPolicyError("OCI blob escapes layout root")
    if blob.is_symlink() or not blob.is_file():
        raise ContentPolicyError("missing OCI blob")
    actual_size = blob.stat().st_size
    if actual_size != size:
        raise ContentPolicyError("OCI blob size mismatch")
    if actual_size > max_size:
        raise ContentPolicyError("OCI blob byte limit exceeded")
    hasher = hashlib.sha256()
    with blob.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != match.group(1):
        raise ContentPolicyError("OCI blob digest mismatch")
    return descriptor, blob


def _validate_layer_encoding(descriptor: dict[str, Any], blob: Path) -> None:
    try:
        with blob.open("rb") as handle:
            gzip_magic = handle.read(2) == b"\x1f\x8b"
    except OSError as exc:
        raise ContentPolicyError("unable to read OCI layer encoding") from exc
    if gzip_magic != descriptor["mediaType"].endswith("+gzip"):
        raise ContentPolicyError("OCI layer media type does not match blob encoding")


def inspect_oci_layout(
    layout: Path, policy: ContentPolicy | None = None, allowlist: tuple[str, ...] = ()
) -> ContentReport:
    """Verify and inspect one linux/amd64 OCI image layout."""
    selected = policy or ContentPolicy.default()
    absolute_layout = layout.absolute()
    if any(candidate.is_symlink() for candidate in (absolute_layout, *absolute_layout.parents)):
        raise ContentPolicyError("OCI layout ancestors may not be symlinked")
    allowed, allowlist_digest = _prepare_allowlist(selected, allowlist)
    layout_version, _ = _json_bytes(layout / "oci-layout")
    if layout_version != {"imageLayoutVersion": "1.0.0"}:
        raise ContentPolicyError("invalid OCI layout version")
    index, _ = _json_bytes(layout / "index.json")
    if (
        not isinstance(index, dict)
        or set(index) - {"schemaVersion", "mediaType", "manifests", "annotations"}
        or index.get("schemaVersion") != 2
        or index.get("mediaType", "application/vnd.oci.image.index.v1+json")
        != "application/vnd.oci.image.index.v1+json"
    ):
        raise ContentPolicyError("invalid OCI index")
    manifests = index.get("manifests")
    if not isinstance(manifests, list) or len(manifests) != 1:
        raise ContentPolicyError("OCI index must contain exactly one manifest")
    config_findings = FindingBuffer(selected.max_diagnostics, selected.max_diagnostic_bytes)
    add_annotation_findings(index.get("annotations"), "index", config_findings)
    manifest_desc, manifest_path = _descriptor_blob(
        layout, manifests[0], {_OCI_MANIFEST}, platform=True, max_size=16 * 1024 * 1024
    )
    add_annotation_findings(manifest_desc.get("annotations"), "index.manifests[0]", config_findings)
    if manifest_desc.get("platform") != {"architecture": "amd64", "os": "linux"}:
        raise ContentPolicyError("OCI manifest must target exactly linux/amd64")
    manifest, _ = _json_bytes(manifest_path)
    if (
        not isinstance(manifest, dict)
        or set(manifest) - {"schemaVersion", "mediaType", "config", "layers", "annotations"}
        or manifest.get("schemaVersion") != 2
        or manifest.get("mediaType") != _OCI_MANIFEST
    ):
        raise ContentPolicyError("invalid OCI manifest")
    add_annotation_findings(manifest.get("annotations"), "manifest", config_findings)
    config_desc, config_path = _descriptor_blob(
        layout, manifest.get("config"), {_OCI_CONFIG}, max_size=16 * 1024 * 1024
    )
    add_annotation_findings(config_desc.get("annotations"), "manifest.config", config_findings)
    del config_desc
    config, _ = _json_bytes(config_path)
    if (
        not isinstance(config, dict)
        or config.get("architecture") != "amd64"
        or config.get("os") != "linux"
    ):
        raise ContentPolicyError("image config platform does not match linux/amd64")
    config_values, config_truncated = inspect_config(
        config, selected.max_diagnostics, selected.max_diagnostic_bytes
    )
    config_findings.update(config_values)
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ContentPolicyError("OCI manifest must contain layers")
    rootfs = config.get("rootfs")
    if (
        not isinstance(rootfs, dict)
        or set(rootfs) != {"type", "diff_ids"}
        or rootfs.get("type") != "layers"
        or not isinstance(rootfs.get("diff_ids"), list)
        or len(rootfs["diff_ids"]) != len(layers)
        or any(
            not isinstance(item, str) or _SHA256.fullmatch(item) is None
            for item in rootfs["diff_ids"]
        )
    ):
        raise ContentPolicyError("image config rootfs diff_ids are invalid")
    denied = FindingBuffer(selected.max_diagnostics, selected.max_diagnostic_bytes)
    allowlisted = FindingBuffer(selected.max_diagnostics, selected.max_diagnostic_bytes)
    total_entries = 0
    total_bytes = 0
    allowlisted_entries = 0
    allowlisted_bytes = 0
    diagnostics_truncated = config_truncated
    for index, descriptor in enumerate(layers):
        layer_desc, layer_path = _descriptor_blob(
            layout,
            descriptor,
            set(selected.layer_media_types),
            max_size=selected.max_blob_bytes,
        )
        add_annotation_findings(layer_desc.get("annotations"), "manifest.layers", config_findings)
        _validate_layer_encoding(layer_desc, layer_path)
        compression = "gzip" if layer_desc["mediaType"].endswith("+gzip") else "plain"
        actual_diff_id = uncompressed_layer_digest(
            layer_path,
            compression,
            selected.max_uncompressed_layer_bytes,
            selected.max_path_bytes + 1024,
        )
        if actual_diff_id != rootfs["diff_ids"][index]:
            raise ContentPolicyError(f"layer {index} diff_id does not match config rootfs")
        layer = inspect_layer(layer_path, selected, allowed, compression)
        diagnostics_truncated |= layer.diagnostics_truncated
        total_entries += layer.entries
        total_bytes += layer.total_bytes
        allowlisted_entries += layer.allowlisted_entries
        allowlisted_bytes += layer.allowlisted_bytes
        if total_entries > selected.max_entries:
            raise ContentPolicyError("image entry limit exceeded")
        if total_bytes > selected.max_total_bytes:
            raise ContentPolicyError("image aggregate byte limit exceeded")
        if allowlisted_entries > selected.max_allowlist_entries:
            raise ContentPolicyError("image allowlist entry limit exceeded")
        if allowlisted_bytes > selected.max_allowlist_total_bytes:
            raise ContentPolicyError("image allowlist aggregate byte limit exceeded")
        denied.update(layer.denied)
        allowlisted.update(layer.allowlisted)
    (bounded_denied, bounded_allowed, bounded_config), bounded_truncated = (
        _bounded_report_diagnostics(
            selected, denied.values, allowlisted.values, config_findings.values
        )
    )
    return ContentReport(
        policy_digest=selected.digest,
        allowlist_digest=allowlist_digest,
        denied_paths=bounded_denied,
        allowlisted_paths=bounded_allowed,
        denied_config=bounded_config,
        inspected_entries=total_entries,
        inspected_bytes=total_bytes,
        diagnostics_truncated=(
            diagnostics_truncated
            or denied.truncated
            or allowlisted.truncated
            or config_findings.truncated
            or bounded_truncated
        ),
    )


def _ignored(relative: str, patterns: tuple[str, ...]) -> bool:
    ignored = False
    for raw in patterns:
        if not raw or raw.startswith("#"):
            continue
        negate = raw.startswith("!")
        pattern = raw[1:] if negate else raw
        directory = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        matched = relative == pattern or (directory and relative.startswith(pattern + "/"))
        if "/" not in pattern:
            matched = matched or any(
                fnmatch.fnmatchcase(part, pattern) for part in relative.split("/")
            )
        matched = matched or fnmatch.fnmatchcase(relative, pattern)
        if matched:
            ignored = not negate
    return ignored


def inspect_build_context(root: Path, policy: ContentPolicy | None = None) -> ContentReport:
    """Validate required Docker exclusions and safely measure the included context."""
    selected = policy or ContentPolicy.default()
    if root.is_symlink() or not root.is_dir():
        raise ContentPolicyError("build context root must be a regular directory")
    ignore_path = root / ".dockerignore"
    try:
        lines = tuple(line.strip() for line in ignore_path.read_text().splitlines())
    except (OSError, UnicodeDecodeError) as exc:
        raise ContentPolicyError("missing or invalid .dockerignore") from exc
    missing = sorted(set(selected.required_dockerignore) - set(lines))
    if missing:
        raise ContentPolicyError(".dockerignore missing required exclusions")
    approved_negations = {"!.env.example", "!.env.docker.example"}
    if any(line.startswith("!") and line not in approved_negations for line in lines):
        raise ContentPolicyError(".dockerignore may not re-include release content")
    for line in lines:
        pattern = line.removeprefix("!").rstrip("/")
        if pattern.startswith("/") or "\\" in pattern or ".." in pattern.split("/"):
            raise ContentPolicyError("unsafe .dockerignore pattern")
    total = 0
    entries = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name, reverse=True)
        except OSError as exc:
            raise ContentPolicyError("unable to inspect build context") from exc
        for child in children:
            relative = child.path.removeprefix(str(root) + os.sep).replace(os.sep, "/")
            _safe_path(relative, selected.max_path_bytes, label="context")
            if _ignored(relative, lines):
                continue
            if child.is_symlink():
                raise ContentPolicyError(f"build context symlink is prohibited: {relative}")
            if child.is_dir(follow_symlinks=False):
                stack.append(Path(child.path))
                continue
            if not child.is_file(follow_symlinks=False):
                raise ContentPolicyError(f"special build context entry is prohibited: {relative}")
            stat_result = child.stat(follow_symlinks=False)
            if stat_result.st_nlink != 1:
                raise ContentPolicyError(f"build context hardlink is prohibited: {relative}")
            size = stat_result.st_size
            if size > selected.max_file_bytes:
                raise ContentPolicyError(f"build context file exceeds size limit: {relative}")
            entries += 1
            total += size
            if entries > selected.max_entries or total > selected.max_total_bytes:
                raise ContentPolicyError("build context exceeds policy limits")
    return ContentReport(
        policy_digest=selected.digest,
        allowlist_digest=_canonical_digest([]),
        context_size=total,
        inspected_entries=entries,
        inspected_bytes=total,
    )
