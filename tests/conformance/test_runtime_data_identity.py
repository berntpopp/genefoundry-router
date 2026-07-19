from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from docs.conformance import runtime_data_identity as runtime_identity
from docs.conformance.runtime_data_identity import (
    RuntimeDataIdentityError,
    build_identity_manifest,
    canonical_json_bytes,
    verify_runtime_identity,
)
from genefoundry_router.release.models import DataReleaseTag

MANIFEST_NAME = "data-identity-manifest.json"
RELEASE_TAG = "data-clingen-2026-07-16"


def _write_manifest(root: Path, manifest: dict[str, object]) -> None:
    (root / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")


def _valid_root(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    first = tmp_path / "a.sqlite"
    second = tmp_path / "nested" / "b.json"
    second.parent.mkdir()
    first.write_bytes(b"reference-snapshot")
    second.write_text('{"gene":"SCN5A"}\n', encoding="utf-8")
    manifest = build_identity_manifest(tmp_path, RELEASE_TAG, [second, first])
    _write_manifest(tmp_path, manifest)
    return tmp_path, manifest


def _first_input(manifest: dict[str, object]) -> dict[str, object]:
    inputs = manifest["inputs"]
    assert isinstance(inputs, list)
    item = inputs[0]
    assert isinstance(item, dict)
    return item


def test_canonical_json_bytes_has_the_exact_v1_serialization() -> None:
    value = {"z": "München", "a": {"finite": 1.25}, "inputs": []}

    assert canonical_json_bytes(value) == (
        b'{"a":{"finite":1.25},"inputs":[],"z":"M\xc3\xbcnchen"}'
    )


def test_canonical_json_bytes_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_json_bytes({"value": float("nan")})


def test_runtime_identity_is_the_digest_of_a_canonical_manifest(tmp_path: Path) -> None:
    root, manifest = _valid_root(tmp_path)

    identity = verify_runtime_identity(root)

    assert identity == {
        "release_tag": RELEASE_TAG,
        "digest": f"sha256:{hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()}",
    }


def test_build_manifest_uses_sorted_posix_paths_and_exact_input_metadata(
    tmp_path: Path,
) -> None:
    root, manifest = _valid_root(tmp_path)

    assert manifest == {
        "schema_version": 1,
        "release_tag": RELEASE_TAG,
        "inputs": [
            {
                "path": "a.sqlite",
                "size_bytes": len(b"reference-snapshot"),
                "sha256": hashlib.sha256(b"reference-snapshot").hexdigest(),
            },
            {
                "path": "nested/b.json",
                "size_bytes": len(b'{"gene":"SCN5A"}\n'),
                "sha256": hashlib.sha256(b'{"gene":"SCN5A"}\n').hexdigest(),
            },
        ],
    }
    assert verify_runtime_identity(root)["digest"].startswith("sha256:")


@pytest.mark.parametrize(
    ("release_tag", "match"),
    [
        ("latest", "immutable"),
        ("with/slash", "release_tag"),
        ("", "release_tag"),
    ],
)
def test_build_manifest_rejects_invalid_release_tags(
    tmp_path: Path, release_tag: str, match: str
) -> None:
    data = tmp_path / "clingen.sqlite"
    data.write_bytes(b"snapshot")

    with pytest.raises(RuntimeDataIdentityError, match=match):
        build_identity_manifest(tmp_path, release_tag, [data])


@pytest.mark.parametrize(
    ("release_tag", "accepted"),
    [
        (RELEASE_TAG, True),
        ("Data-Clingen_2026.07-16", True),
        ("A", True),
        ("a" * 128, True),
        ("LATEST", False),
        ("main", False),
        ("with/slash", False),
        ("a" * 129, False),
    ],
)
def test_release_tag_validation_matches_the_router_canonical_model(
    tmp_path: Path, release_tag: str, accepted: bool
) -> None:
    adapter = TypeAdapter(DataReleaseTag)
    data = tmp_path / "clingen.sqlite"
    data.write_bytes(b"snapshot")

    if accepted:
        assert adapter.validate_python(release_tag) == release_tag
        manifest = build_identity_manifest(tmp_path, release_tag, [data])
        assert manifest["release_tag"] == release_tag
        _write_manifest(tmp_path, manifest)
        assert verify_runtime_identity(tmp_path)["release_tag"] == release_tag
    else:
        with pytest.raises(ValidationError):
            adapter.validate_python(release_tag)
        with pytest.raises(RuntimeDataIdentityError):
            build_identity_manifest(tmp_path, release_tag, [data])


@pytest.mark.parametrize(
    "bad_tag",
    [None, 1, True, [RELEASE_TAG], "latest", "LATEST"],
)
def test_verify_rejects_invalid_release_tags(tmp_path: Path, bad_tag: object) -> None:
    root, manifest = _valid_root(tmp_path)
    manifest["release_tag"] = bad_tag
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match="release_tag"):
        verify_runtime_identity(root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.update({"unexpected": True}),
        lambda manifest: manifest.pop("inputs"),
        lambda manifest: manifest.update({"schema_version": 2}),
        lambda manifest: manifest.update({"schema_version": True}),
        lambda manifest: manifest.update({"inputs": {}}),
    ],
    ids=["extra-key", "missing-key", "schema-version", "boolean-version", "inputs-type"],
)
def test_verify_rejects_invalid_top_level_shape(tmp_path: Path, mutation: Any) -> None:
    root, manifest = _valid_root(tmp_path)
    mutation(manifest)
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError):
        verify_runtime_identity(root)


@pytest.mark.parametrize(
    "replacement",
    [None, [], "manifest", 1, True],
)
def test_verify_rejects_non_object_manifest(tmp_path: Path, replacement: object) -> None:
    data = tmp_path / "clingen.sqlite"
    data.write_bytes(b"snapshot")
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(replacement), encoding="utf-8")

    with pytest.raises(RuntimeDataIdentityError, match="object"):
        verify_runtime_identity(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.update({"unexpected": True}),
        lambda item: item.pop("sha256"),
        lambda item: item.update({"path": 1}),
        lambda item: item.update({"size_bytes": "1"}),
        lambda item: item.update({"size_bytes": True}),
        lambda item: item.update({"size_bytes": -1}),
        lambda item: item.update({"sha256": 1}),
        lambda item: item.update({"sha256": "A" * 64}),
        lambda item: item.update({"sha256": "a" * 63}),
    ],
    ids=[
        "extra-key",
        "missing-key",
        "path-type",
        "size-type",
        "boolean-size",
        "negative-size",
        "digest-type",
        "uppercase-digest",
        "short-digest",
    ],
)
def test_verify_rejects_invalid_input_shape(tmp_path: Path, mutation: Any) -> None:
    root, manifest = _valid_root(tmp_path)
    mutation(_first_input(manifest))
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError):
        verify_runtime_identity(root)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.sqlite",
        "/absolute.sqlite",
        "./a.sqlite",
        "nested/../a.sqlite",
        "nested//b.json",
        ".",
        MANIFEST_NAME,
    ],
)
def test_verify_rejects_unsafe_or_aliased_manifest_paths(tmp_path: Path, bad_path: str) -> None:
    root, manifest = _valid_root(tmp_path)
    _first_input(manifest)["path"] = bad_path
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match=r"path|manifest"):
        verify_runtime_identity(root)


def test_verify_rejects_unsorted_inputs(tmp_path: Path) -> None:
    root, manifest = _valid_root(tmp_path)
    inputs = manifest["inputs"]
    assert isinstance(inputs, list)
    inputs.reverse()
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match="sorted"):
        verify_runtime_identity(root)


def test_verify_rejects_duplicate_paths(tmp_path: Path) -> None:
    root, manifest = _valid_root(tmp_path)
    inputs = manifest["inputs"]
    assert isinstance(inputs, list)
    inputs.insert(0, dict(_first_input(manifest)))
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match="duplicate"):
        verify_runtime_identity(root)


def test_runtime_identity_rejects_corrupted_materialized_input(tmp_path: Path) -> None:
    root, _ = _valid_root(tmp_path)
    (root / "a.sqlite").write_bytes(b"corrupted-snapshot")

    with pytest.raises(RuntimeDataIdentityError, match="sha256"):
        verify_runtime_identity(root)


def test_verify_rejects_wrong_size_even_when_digest_matches(tmp_path: Path) -> None:
    root, manifest = _valid_root(tmp_path)
    _first_input(manifest)["size_bytes"] = 999
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match="size_bytes"):
        verify_runtime_identity(root)


def test_verify_rejects_unexpected_regular_file(tmp_path: Path) -> None:
    root, _ = _valid_root(tmp_path)
    (root / "extra.sqlite").write_bytes(b"unlisted")

    with pytest.raises(RuntimeDataIdentityError, match="unexpected regular file"):
        verify_runtime_identity(root)


def test_verify_fails_closed_when_a_subtree_cannot_be_inventoried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _valid_root(tmp_path)
    blocked = root / "blocked"
    blocked.mkdir()
    (blocked / "unlisted.sqlite").write_bytes(b"must-not-be-hidden")
    original_scandir = os.scandir

    def deny_blocked(path: Any) -> Any:
        if Path(path) == blocked:
            raise PermissionError("deterministic denied subtree")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", deny_blocked)

    with pytest.raises(RuntimeDataIdentityError, match=r"inventory|travers"):
        verify_runtime_identity(root)


def test_verify_rejects_input_mutated_during_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _valid_root(tmp_path)
    original_discover = runtime_identity._discover_regular_files

    def discover_then_mutate(data_root: Path) -> set[str]:
        discovered = original_discover(data_root)
        (data_root / "a.sqlite").write_bytes(b"corrupted-snapshot")
        return discovered

    monkeypatch.setattr(runtime_identity, "_discover_regular_files", discover_then_mutate)

    with pytest.raises(RuntimeDataIdentityError, match="sha256"):
        verify_runtime_identity(root)


def test_verify_rejects_missing_input(tmp_path: Path) -> None:
    root, _ = _valid_root(tmp_path)
    (root / "a.sqlite").unlink()

    with pytest.raises(RuntimeDataIdentityError, match="missing"):
        verify_runtime_identity(root)


def test_verify_rejects_listed_directory(tmp_path: Path) -> None:
    root, manifest = _valid_root(tmp_path)
    (root / "a.sqlite").unlink()
    (root / "a.sqlite").mkdir()
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match="regular file"):
        verify_runtime_identity(root)


def test_verify_rejects_symlink_input(tmp_path: Path) -> None:
    root, manifest = _valid_root(tmp_path)
    target = root / "target.sqlite"
    target.write_bytes((root / "a.sqlite").read_bytes())
    (root / "a.sqlite").unlink()
    (root / "a.sqlite").symlink_to(target)
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match=r"symlink|alias"):
        verify_runtime_identity(root)


def test_verify_rejects_parent_directory_symlink_alias(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.json").write_text('{"gene":"SCN5A"}\n', encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "nested").symlink_to(outside, target_is_directory=True)
    content = (outside / "b.json").read_bytes()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "release_tag": RELEASE_TAG,
        "inputs": [
            {
                "path": "nested/b.json",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    _write_manifest(root, manifest)

    with pytest.raises(RuntimeDataIdentityError, match=r"outside|symlink|alias"):
        verify_runtime_identity(root)


def test_build_rejects_file_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"snapshot")

    with pytest.raises(RuntimeDataIdentityError, match=r"beneath|outside"):
        build_identity_manifest(root, RELEASE_TAG, [outside])


def test_build_rejects_path_alias(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    data = root / "data.sqlite"
    data.write_bytes(b"snapshot")
    alias = root / "alias.sqlite"
    alias.symlink_to(data)

    with pytest.raises(RuntimeDataIdentityError, match=r"symlink|alias"):
        build_identity_manifest(root, RELEASE_TAG, [alias])


def test_build_rejects_parent_directory_symlink_alias(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    data = outside / "data.sqlite"
    data.write_bytes(b"snapshot")
    alias = root / "alias"
    alias.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeDataIdentityError, match=r"outside|symlink|alias"):
        build_identity_manifest(root, RELEASE_TAG, [alias / "data.sqlite"])


def test_build_rejects_duplicate_files(tmp_path: Path) -> None:
    data = tmp_path / "data.sqlite"
    data.write_bytes(b"snapshot")

    with pytest.raises(RuntimeDataIdentityError, match="duplicate"):
        build_identity_manifest(tmp_path, RELEASE_TAG, [data, data])


def test_build_rejects_manifest_as_authoritative_input(tmp_path: Path) -> None:
    manifest = tmp_path / MANIFEST_NAME
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeDataIdentityError, match="manifest"):
        build_identity_manifest(tmp_path, RELEASE_TAG, [manifest])


@pytest.mark.parametrize("root_kind", ["missing", "file", "symlink"])
def test_build_and_verify_reject_invalid_roots(tmp_path: Path, root_kind: str) -> None:
    root = tmp_path / "root"
    if root_kind == "file":
        root.write_bytes(b"not-a-directory")
    elif root_kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        root.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeDataIdentityError, match="root"):
        build_identity_manifest(root, RELEASE_TAG, [])
    with pytest.raises(RuntimeDataIdentityError, match="root"):
        verify_runtime_identity(root)


@pytest.mark.parametrize("manifest_kind", ["missing", "directory", "symlink", "invalid-json"])
def test_verify_rejects_invalid_manifest_file(tmp_path: Path, manifest_kind: str) -> None:
    manifest_path = tmp_path / MANIFEST_NAME
    if manifest_kind == "directory":
        manifest_path.mkdir()
    elif manifest_kind == "symlink":
        target = tmp_path / "manifest-target.json"
        target.write_text("{}", encoding="utf-8")
        manifest_path.symlink_to(target)
    elif manifest_kind == "invalid-json":
        manifest_path.write_text("{", encoding="utf-8")

    with pytest.raises(RuntimeDataIdentityError, match="manifest"):
        verify_runtime_identity(tmp_path)
