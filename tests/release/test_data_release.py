"""Immutable reference-data release contract and materialization tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from genefoundry_router.release.cli import app
from genefoundry_router.release.data import (
    DataReleaseManifest,
    DataRequirement,
    DataVerificationError,
    DownloadPolicy,
)
from genefoundry_router.release.data_materialization import (
    download_artifact,
    expanded_tree_identity,
    materialize_data,
    rollback_data,
    verify_compressed_artifact,
)
from genefoundry_router.release.vulnerabilities import ReleaseExitCode

SHA_A = "a" * 64
SHA_B = "b" * 64
runner = CliRunner()


def manifest_fixture(**updates: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "dataset": {
            "name": "ClinGen dosage sensitivity",
            "release": "data-clingen-2026-07-13",
            "source": {
                "identifier": "clingen-dosage-2026-07-13",
                "url": "https://data.example.test/clingen.tar.gz",
                "retrieved_at": "2026-07-13T12:00:00Z",
                "sha256": SHA_A,
            },
        },
        "transformation": {
            "repository": "berntpopp/clingen-link",
            "revision": "c" * 40,
        },
        "schema": {"actual": "2.1.0", "minimum": "2.0.0", "maximum": "2.9.9"},
        "record_counts": {"genes": 123, "regions": 45},
        "artifact": {
            "filename": "clingen.tar.gz",
            "sha256": SHA_A,
            "compressed_size": 1024,
            "max_compressed_size": 2048,
            "expanded_tree_sha256": SHA_B,
            "expanded_size": 4096,
            "max_expanded_size": 8192,
            "member_count": 2,
            "max_members": 10,
        },
        "license": {
            "name": "ClinGen terms",
            "url": "https://example.test/license",
            "redistribution_allowed": True,
            "reviewed_at": "2026-07-13T12:01:00Z",
            "reviewer": "GeneFoundry maintainer",
        },
        "previous_known_good_digest": f"sha256:{'d' * 64}",
        "application_compatibility": {"minimum": "3.0.0", "maximum": "3.9.9"},
        "disclaimer": "Research use only; not clinical decision support.",
    }
    manifest.update(updates)
    return manifest


def requirement_fixture(**updates: object) -> DataRequirement:
    values: dict[str, object] = {
        "mode": "external-reference",
        "release_tag": "data-clingen-2026-07-13",
        "sha256": SHA_A,
        "compressed_size": 4,
        "max_compressed_size": 8,
        "expanded_tree_sha256": SHA_B,
        "expanded_size": 4,
        "max_expanded_size": 8,
        "member_count": 1,
        "max_members": 2,
        "schema_version": "2.1.0",
        "schema_minimum": "2.0.0",
        "schema_maximum": "2.9.9",
        "previous_known_good_digest": f"sha256:{'d' * 64}",
        "reproducible_rollback": True,
    }
    values.update(updates)
    return DataRequirement.model_validate(values)


def test_manifest_requires_complete_identity_and_release_evidence() -> None:
    parsed = DataReleaseManifest.model_validate(manifest_fixture())

    assert parsed.dataset.source.identifier == "clingen-dosage-2026-07-13"
    assert parsed.transformation.revision == "c" * 40
    assert parsed.schema_identity.actual == "2.1.0"
    assert parsed.record_counts == {"genes": 123, "regions": 45}
    assert parsed.artifact.expanded_tree_sha256 == SHA_B
    assert parsed.previous_known_good_digest == f"sha256:{'d' * 64}"


@pytest.mark.parametrize(
    "field",
    [
        "dataset",
        "transformation",
        "schema",
        "record_counts",
        "artifact",
        "license",
        "previous_known_good_digest",
        "application_compatibility",
        "disclaimer",
    ],
)
def test_manifest_rejects_missing_required_evidence(field: str) -> None:
    document = manifest_fixture()
    del document[field]

    with pytest.raises(ValidationError):
        DataReleaseManifest.model_validate(document)


def test_manifest_rejects_schema_actual_outside_compatible_range() -> None:
    document = manifest_fixture(schema={"actual": "3.0.0", "minimum": "2.0.0", "maximum": "2.9.9"})

    with pytest.raises(ValidationError, match="schema"):
        DataReleaseManifest.model_validate(document)


def test_manifest_rejects_mutable_data_release_labels() -> None:
    document = manifest_fixture()
    dataset = document["dataset"]
    assert isinstance(dataset, dict)
    dataset["release"] = "latest"

    with pytest.raises(ValidationError, match="release"):
        DataReleaseManifest.model_validate(document)


def test_manifest_rejects_actual_values_above_security_ceilings() -> None:
    document = manifest_fixture()
    artifact = document["artifact"]
    assert isinstance(artifact, dict)
    artifact["expanded_size"] = 8193

    with pytest.raises(ValidationError, match="expanded"):
        DataReleaseManifest.model_validate(document)


def test_publication_rejects_negative_redistribution_decision() -> None:
    document = manifest_fixture()
    license_evidence = document["license"]
    assert isinstance(license_evidence, dict)
    license_evidence["redistribution_allowed"] = False
    manifest = DataReleaseManifest.model_validate(document)

    with pytest.raises(DataVerificationError, match="redistribution"):
        manifest.validate_publication()


def test_upstream_live_cannot_claim_reproducible_rollback() -> None:
    with pytest.raises(ValidationError, match="reproducible"):
        DataRequirement(mode="upstream-live", reproducible_rollback=True)


def test_upstream_live_cannot_claim_reviewed_immutable_artifact_identity() -> None:
    with pytest.raises(ValidationError, match="immutable artifact"):
        DataRequirement(mode="upstream-live", reproducible_rollback=False, sha256=SHA_A)


def test_external_reference_requires_complete_artifact_identity() -> None:
    with pytest.raises(ValidationError, match="artifact identity"):
        DataRequirement(mode="external-reference", reproducible_rollback=True)


def test_materialize_rejects_digest_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.zst"
    artifact.write_bytes(b"not the reviewed bundle")
    requirement = requirement_fixture(sha256="0" * 64, compressed_size=23, max_compressed_size=32)

    with pytest.raises(DataVerificationError, match="digest"):
        verify_compressed_artifact(artifact, requirement)


def test_verify_rejects_oversize_compressed_file_before_hashing(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.tar"
    artifact.write_bytes(b"oversize")
    requirement = requirement_fixture(
        sha256=hashlib.sha256(b"oversize").hexdigest(),
        compressed_size=7,
        max_compressed_size=7,
    )

    with pytest.raises(DataVerificationError, match="compressed size ceiling"):
        verify_compressed_artifact(artifact, requirement)


def test_verify_rejects_symlinked_artifact(tmp_path: Path) -> None:
    target = tmp_path / "bundle.tar"
    target.write_bytes(b"data")
    link = tmp_path / "link.tar"
    link.symlink_to(target)

    with pytest.raises(DataVerificationError, match="regular file"):
        verify_compressed_artifact(link, requirement_fixture())


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, follow_redirects=False)


def _require_bytes(content: bytes) -> DataRequirement:
    return requirement_fixture(
        sha256=hashlib.sha256(content).hexdigest(),
        compressed_size=len(content),
        max_compressed_size=max(len(content), 1),
    )


def test_download_rejects_redirect_outside_exact_host_allowlist(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            302, headers={"location": "https://evil.example/bundle.tar"}
        )
    )
    destination = tmp_path / "bundle.tar"

    with _client(transport) as client:
        with pytest.raises(DataVerificationError, match="allowlist"):
            download_artifact(
                "https://data.example.test/bundle.tar",
                destination,
                _require_bytes(b"data"),
                allowed_hosts=("data.example.test",),
                client=client,
            )

    assert not destination.exists()


def test_download_follows_bounded_redirect_on_allowed_host_atomically(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bundle.tar":
            return httpx.Response(302, headers={"location": "/immutable/bundle.tar"})
        return httpx.Response(200, content=b"data")

    destination = tmp_path / "bundle.tar"
    with _client(httpx.MockTransport(handler)) as client:
        downloaded = download_artifact(
            "https://data.example.test/bundle.tar",
            destination,
            _require_bytes(b"data"),
            allowed_hosts=("data.example.test",),
            client=client,
        )

    assert downloaded == destination
    assert destination.read_bytes() == b"data"


def test_download_rejects_oversize_body_and_preserves_existing_file(tmp_path: Path) -> None:
    destination = tmp_path / "bundle.tar"
    destination.write_bytes(b"reviewed-old")
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"oversize"))

    with _client(transport) as client:
        with pytest.raises(DataVerificationError, match="compressed size ceiling"):
            download_artifact(
                "https://data.example.test/bundle.tar",
                destination,
                requirement_fixture(compressed_size=4, max_compressed_size=4),
                allowed_hosts=("data.example.test",),
                client=client,
            )

    assert destination.read_bytes() == b"reviewed-old"


def _clock(values: list[float]) -> Callable[[], float]:
    iterator: Iterator[float] = iter(values)
    return lambda: next(iterator)


def test_download_rejects_stalled_stream(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"data"))

    with _client(transport) as client:
        with pytest.raises(DataVerificationError, match="stalled"):
            download_artifact(
                "https://data.example.test/bundle.tar",
                tmp_path / "bundle.tar",
                _require_bytes(b"data"),
                allowed_hosts=("data.example.test",),
                client=client,
                policy=DownloadPolicy(stall_timeout_seconds=1, minimum_bytes_per_second=1),
                monotonic=_clock([0.0, 2.0]),
            )


def test_download_rejects_stream_below_minimum_throughput(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"data"))

    with _client(transport) as client:
        with pytest.raises(DataVerificationError, match="throughput"):
            download_artifact(
                "https://data.example.test/bundle.tar",
                tmp_path / "bundle.tar",
                _require_bytes(b"data"),
                allowed_hosts=("data.example.test",),
                client=client,
                policy=DownloadPolicy(stall_timeout_seconds=5, minimum_bytes_per_second=10),
                monotonic=_clock([0.0, 2.0]),
            )


def _write_tar(path: Path, entries: list[tuple[str, bytes, int, bytes | None]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content, mode, kind in entries:
            member = tarfile.TarInfo(name)
            member.mode = mode
            if kind is None:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            else:
                member.type = kind
                member.linkname = content.decode() if content else "target"
                archive.addfile(member)


def _write_tree_tar(path: Path, root_name: str, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo(root_name)
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o555
        archive.addfile(directory)
        for name, content in files.items():
            member = tarfile.TarInfo(f"{root_name}/{name}")
            member.mode = 0o444
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def _tree_digest(files: dict[str, tuple[bytes, int]]) -> str:
    digest = hashlib.sha256()
    for name, (content, mode) in sorted(files.items()):
        file_digest = hashlib.sha256(content).hexdigest()
        digest.update(f"{name}\0{mode:04o}\0{len(content)}\0{file_digest}\n".encode())
    return digest.hexdigest()


def _materialized_mode(mode: int) -> int:
    return mode & 0o444 or 0o400


def _materialized_files(files: dict[str, tuple[bytes, int]]) -> dict[str, tuple[bytes, int]]:
    return {name: (content, _materialized_mode(mode)) for name, (content, mode) in files.items()}


def _archive_requirement(
    artifact: Path,
    files: dict[str, tuple[bytes, int]],
    *,
    member_count: int | None = None,
    **updates: object,
) -> DataRequirement:
    values: dict[str, object] = {
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "compressed_size": artifact.stat().st_size,
        "max_compressed_size": artifact.stat().st_size,
        "expanded_tree_sha256": _tree_digest(_materialized_files(files)),
        "expanded_size": sum(len(content) for content, _mode in files.values()),
        "max_expanded_size": sum(len(content) for content, _mode in files.values()),
        "member_count": member_count if member_count is not None else len(files),
        "max_members": member_count if member_count is not None else len(files),
    }
    values.setdefault("previous_known_good_digest", f"sha256:{values['sha256']}")
    values.update(updates)
    return requirement_fixture(**values)


def _manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_expanded_identity_uses_sorted_path_mode_size_and_digest(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "z.txt").write_bytes(b"z")
    (tmp_path / "nested" / "a.txt").write_bytes(b"alpha")
    (tmp_path / "z.txt").chmod(0o600)
    (tmp_path / "nested" / "a.txt").chmod(0o644)
    files = {"z.txt": (b"z", 0o600), "nested/a.txt": (b"alpha", 0o644)}

    assert expanded_tree_identity(tmp_path) == (_tree_digest(files), 6, 2)


def test_materialize_selects_verified_version_atomically(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.tar.gz"
    files = {"nested/data.sqlite": (b"sqlite fixture", 0o640)}
    _write_tar(artifact, [("nested/data.sqlite", b"sqlite fixture", 0o640, None)])
    requirement = _archive_requirement(artifact, files)
    data_root = tmp_path / "data"

    selected = materialize_data(
        artifact, requirement, data_root, schema_probe=lambda _root: "2.1.0"
    )

    assert selected == data_root / requirement.sha256
    assert (selected / "nested/data.sqlite").read_bytes() == b"sqlite fixture"
    assert (selected / "nested/data.sqlite").stat().st_mode & 0o777 == 0o440
    assert (data_root / "current").resolve() == selected.resolve()


def test_materialize_accepts_archive_with_directory_members(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.tar.gz"
    files = {"data/schema.json": (b'{"schema_version":"2.1.0"}', 0o444)}
    _write_tree_tar(artifact, "data", {"schema.json": b'{"schema_version":"2.1.0"}'})
    requirement = _archive_requirement(
        artifact,
        files,
        member_count=2,
    )

    selected = materialize_data(
        artifact,
        requirement,
        tmp_path / "data-root",
        schema_probe=lambda root: json.loads((root / "data/schema.json").read_text())[
            "schema_version"
        ],
    )

    assert (selected / "data/schema.json").stat().st_mode & 0o777 == 0o444


@pytest.mark.parametrize(
    ("name", "mode", "kind", "match"),
    [
        ("../escape", 0o644, None, "path"),
        ("link", 0o644, tarfile.SYMTYPE, "link"),
        ("fifo", 0o644, tarfile.FIFOTYPE, "special"),
        ("setuid", 0o4644, None, "set-id"),
    ],
)
def test_materialize_rejects_unsafe_archive_members(
    tmp_path: Path, name: str, mode: int, kind: bytes | None, match: str
) -> None:
    artifact = tmp_path / "unsafe.tar.gz"
    _write_tar(artifact, [(name, b"target" if kind else b"data", mode, kind)])
    requirement = _archive_requirement(artifact, {"placeholder": (b"data", 0o644)})

    with pytest.raises(DataVerificationError, match=match):
        materialize_data(
            artifact, requirement, tmp_path / "data", schema_probe=lambda _root: "2.1.0"
        )

    assert not (tmp_path / "escape").exists()


def test_materialize_rejects_expansion_bomb_before_writing_beyond_ceiling(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "bomb.tar.gz"
    _write_tar(artifact, [("data.bin", b"x" * 1024, 0o600, None)])
    requirement = _archive_requirement(
        artifact,
        {"data.bin": (b"x", 0o600)},
        expanded_size=1,
        max_expanded_size=8,
    )

    with pytest.raises(DataVerificationError, match="expanded size ceiling"):
        materialize_data(
            artifact, requirement, tmp_path / "data", schema_probe=lambda _root: "2.1.0"
        )


def test_interrupted_replacement_preserves_current_selection(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    first = tmp_path / "first.tar.gz"
    _write_tar(first, [("data.txt", b"first", 0o600, None)])
    first_requirement = _archive_requirement(first, {"data.txt": (b"first", 0o600)})
    first_selected = materialize_data(
        first, first_requirement, data_root, schema_probe=lambda _root: "2.1.0"
    )
    second = tmp_path / "second.tar.gz"
    _write_tar(second, [("data.txt", b"second", 0o600, None)])
    second_requirement = _archive_requirement(
        second,
        {"data.txt": (b"second", 0o600)},
        previous_known_good_digest=f"sha256:{first_requirement.sha256}",
    )

    with pytest.raises(RuntimeError, match="probe interrupted"):
        materialize_data(
            second,
            second_requirement,
            data_root,
            schema_probe=lambda _root: (_ for _ in ()).throw(RuntimeError("probe interrupted")),
        )

    assert (data_root / "current").resolve() == first_selected.resolve()
    assert not (data_root / second_requirement.sha256).exists()


def test_materialize_rejects_incompatible_schema_probe(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.tar.gz"
    _write_tar(artifact, [("data.txt", b"data", 0o600, None)])
    requirement = _archive_requirement(artifact, {"data.txt": (b"data", 0o600)})

    with pytest.raises(DataVerificationError, match="schema"):
        materialize_data(
            artifact, requirement, tmp_path / "data", schema_probe=lambda _root: "3.0.0"
        )


def test_materialize_rejects_missing_previous_known_good(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.tar.gz"
    _write_tar(artifact, [("data.txt", b"data", 0o600, None)])
    requirement = _archive_requirement(
        artifact,
        {"data.txt": (b"data", 0o600)},
        previous_known_good_digest=f"sha256:{'d' * 64}",
    )

    with pytest.raises(DataVerificationError, match="previous-known-good"):
        materialize_data(
            artifact, requirement, tmp_path / "data", schema_probe=lambda _root: "2.1.0"
        )


def test_materialize_rejects_group_writable_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(mode=0o770)
    data_root.chmod(0o770)
    artifact = tmp_path / "bundle.tar.gz"
    _write_tar(artifact, [("data.txt", b"data", 0o600, None)])
    requirement = _archive_requirement(artifact, {"data.txt": (b"data", 0o600)})

    with pytest.raises(DataVerificationError, match="data root"):
        materialize_data(artifact, requirement, data_root, schema_probe=lambda _root: "2.1.0")


def test_materialize_bootstrap_self_previous_known_good_only_when_empty(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    first = tmp_path / "first.tar.gz"
    _write_tar(first, [("data.txt", b"first", 0o600, None)])
    first_requirement = _archive_requirement(first, {"data.txt": (b"first", 0o600)})
    materialize_data(first, first_requirement, data_root, schema_probe=lambda _root: "2.1.0")

    second = tmp_path / "second.tar.gz"
    _write_tar(second, [("data.txt", b"second", 0o600, None)])
    second_requirement = _archive_requirement(second, {"data.txt": (b"second", 0o600)})

    with pytest.raises(DataVerificationError, match="previous-known-good"):
        materialize_data(second, second_requirement, data_root, schema_probe=lambda _root: "2.1.0")


def test_rollback_selects_retained_previous_known_good(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    paths: list[tuple[Path, DataRequirement]] = []
    previous_digest: str | None = None
    for label in (b"first", b"second"):
        artifact = tmp_path / f"{label.decode()}.tar.gz"
        _write_tar(artifact, [("data.txt", label, 0o600, None)])
        updates = (
            {"previous_known_good_digest": f"sha256:{previous_digest}"}
            if previous_digest is not None
            else {}
        )
        requirement = _archive_requirement(artifact, {"data.txt": (label, 0o600)}, **updates)
        selected = materialize_data(
            artifact, requirement, data_root, schema_probe=lambda _root: "2.1.0"
        )
        paths.append((selected, requirement))
        previous_digest = requirement.sha256

    rolled_back = rollback_data(data_root, f"sha256:{paths[0][1].sha256}", "2.0.0", "2.9.9")

    assert rolled_back == paths[0][0]
    assert (data_root / "current").resolve() == paths[0][0].resolve()
    assert paths[1][0].is_dir()


def test_rollback_rejects_mutated_retained_tree(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    artifact = tmp_path / "bundle.tar.gz"
    _write_tar(artifact, [("data.txt", b"data", 0o600, None)])
    requirement = _archive_requirement(artifact, {"data.txt": (b"data", 0o600)})
    selected = materialize_data(
        artifact, requirement, data_root, schema_probe=lambda _root: "2.1.0"
    )
    (selected / "data.txt").chmod(0o600)
    (selected / "data.txt").write_bytes(b"tampered")

    with pytest.raises(DataVerificationError, match="identity"):
        rollback_data(data_root, f"sha256:{requirement.sha256}", "2.0.0", "2.9.9")


def test_rollback_rejects_group_writable_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    artifact = tmp_path / "bundle.tar.gz"
    _write_tar(artifact, [("data.txt", b"data", 0o600, None)])
    requirement = _archive_requirement(artifact, {"data.txt": (b"data", 0o600)})
    materialize_data(artifact, requirement, data_root, schema_probe=lambda _root: "2.1.0")
    data_root.chmod(0o770)

    with pytest.raises(DataVerificationError, match="data root"):
        rollback_data(data_root, f"sha256:{requirement.sha256}", "2.0.0", "2.9.9")


def test_rollback_rejects_public_identity_sidecar(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    artifact = tmp_path / "bundle.tar.gz"
    _write_tar(artifact, [("data.txt", b"data", 0o600, None)])
    requirement = _archive_requirement(artifact, {"data.txt": (b"data", 0o600)})
    materialize_data(artifact, requirement, data_root, schema_probe=lambda _root: "2.1.0")
    (data_root / f"{requirement.sha256}.identity.json").chmod(0o644)

    with pytest.raises(DataVerificationError, match="identity"):
        rollback_data(data_root, f"sha256:{requirement.sha256}", "2.0.0", "2.9.9")


def test_rollback_rejects_non_hex_digest(tmp_path: Path) -> None:
    with pytest.raises(DataVerificationError, match="digest"):
        rollback_data(tmp_path, f"sha256:{'z' * 64}", "2.0.0", "2.9.9")


def test_checked_in_data_manifest_schema_matches_authoritative_model() -> None:
    schema_path = (
        Path(__file__).parents[2] / "genefoundry_router/data/data-release-manifest.schema.json"
    )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema == DataReleaseManifest.model_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)
    assert not list(Draft202012Validator(schema).iter_errors(manifest_fixture()))


def test_data_manifest_json_schema_rejects_unknown_fields() -> None:
    schema = DataReleaseManifest.model_json_schema()
    document = manifest_fixture(unreviewed_override=True)

    assert list(Draft202012Validator(schema).iter_errors(document))


def test_data_release_tag_schema_rejects_mutable_and_malformed_tags() -> None:
    schema = DataReleaseManifest.model_json_schema()
    validator = Draft202012Validator(schema)

    for release in ("latest", "", "bad/tag", "x" * 129):
        document = manifest_fixture()
        dataset = document["dataset"]
        assert isinstance(dataset, dict)
        dataset["release"] = release
        assert list(validator.iter_errors(document)), release


def test_data_materialization_imports_without_data_reexport_cycle() -> None:
    code = "import genefoundry_router.release.data_materialization"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _write_manifest(path: Path, requirement: DataRequirement) -> None:
    document = manifest_fixture()
    document["artifact"] = {
        "filename": "bundle.tar.gz",
        "sha256": requirement.sha256,
        "compressed_size": requirement.compressed_size,
        "max_compressed_size": requirement.max_compressed_size,
        "expanded_tree_sha256": requirement.expanded_tree_sha256,
        "expanded_size": requirement.expanded_size,
        "max_expanded_size": requirement.max_expanded_size,
        "member_count": requirement.member_count,
        "max_members": requirement.max_members,
    }
    document["previous_known_good_digest"] = requirement.previous_known_good_digest
    path.write_text(json.dumps(document), encoding="utf-8")


def test_validate_data_manifest_cli_gates_public_redistribution(tmp_path: Path) -> None:
    manifest = tmp_path / "data-release.json"
    document = manifest_fixture()
    license_evidence = document["license"]
    assert isinstance(license_evidence, dict)
    license_evidence["redistribution_allowed"] = False
    manifest.write_text(json.dumps(document), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "validate-data-manifest",
            "--manifest",
            str(manifest),
            "--manifest-sha256",
            _manifest_digest(manifest),
        ],
    )

    assert result.exit_code == ReleaseExitCode.POLICY_VIOLATION
    assert json.loads(result.stdout)["verdict"] == "policy_violation"


def test_validate_data_manifest_cli_rejects_untrusted_manifest_digest(tmp_path: Path) -> None:
    manifest = tmp_path / "data-release.json"
    manifest.write_text(json.dumps(manifest_fixture()), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "validate-data-manifest",
            "--manifest",
            str(manifest),
            "--manifest-sha256",
            "0" * 64,
        ],
    )

    assert result.exit_code == ReleaseExitCode.INVALID_EVIDENCE
    assert json.loads(result.stdout)["verdict"] == "invalid_evidence"


def test_materialize_and_rollback_cli_commands_select_exact_versions(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    selections: list[tuple[Path, DataRequirement]] = []
    previous_digest: str | None = None
    for content in (b"first", b"second"):
        artifact = tmp_path / f"{content.decode()}.tar.gz"
        schema = b'{"schema_version":"2.1.0"}'
        _write_tar(
            artifact,
            [
                ("data.txt", content, 0o600, None),
                ("schema.json", schema, 0o644, None),
            ],
        )
        updates = (
            {"previous_known_good_digest": f"sha256:{previous_digest}"}
            if previous_digest is not None
            else {}
        )
        requirement = _archive_requirement(
            artifact,
            {"data.txt": (content, 0o600), "schema.json": (schema, 0o644)},
            **updates,
        )
        manifest = tmp_path / f"{content.decode()}.json"
        _write_manifest(manifest, requirement)

        result = runner.invoke(
            app,
            [
                "materialize-data",
                "--manifest",
                str(manifest),
                "--manifest-sha256",
                _manifest_digest(manifest),
                "--artifact",
                str(artifact),
                "--data-root",
                str(data_root),
                "--schema-version",
                "2.1.0",
                "--schema-file",
                "schema.json",
            ],
        )
        assert result.exit_code == ReleaseExitCode.SUCCESS, result.stdout
        selections.append((Path(json.loads(result.stdout)["selected"]), requirement))
        previous_digest = requirement.sha256

    rollback = runner.invoke(
        app,
        [
            "rollback-data",
            "--data-root",
            str(data_root),
            "--digest",
            f"sha256:{selections[0][1].sha256}",
            "--schema-minimum",
            "2.0.0",
            "--schema-maximum",
            "2.9.9",
        ],
    )

    assert rollback.exit_code == ReleaseExitCode.SUCCESS
    assert Path(json.loads(rollback.stdout)["selected"]) == selections[0][0]
    assert (data_root / "current").resolve() == selections[0][0].resolve()


def test_materialize_cli_schema_probe_reads_extracted_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "bundle.tar.gz"
    schema = b'{"schema_version":"3.0.0"}'
    _write_tar(
        artifact,
        [
            ("data.txt", b"data", 0o600, None),
            ("schema.json", schema, 0o644, None),
        ],
    )
    requirement = _archive_requirement(
        artifact,
        {"data.txt": (b"data", 0o600), "schema.json": (schema, 0o644)},
    )
    manifest = tmp_path / "data-release.json"
    _write_manifest(manifest, requirement)

    result = runner.invoke(
        app,
        [
            "materialize-data",
            "--manifest",
            str(manifest),
            "--manifest-sha256",
            _manifest_digest(manifest),
            "--artifact",
            str(artifact),
            "--data-root",
            str(tmp_path / "data"),
            "--schema-version",
            "2.1.0",
            "--schema-file",
            "schema.json",
        ],
    )

    assert result.exit_code == ReleaseExitCode.POLICY_VIOLATION
    assert "schema" in json.loads(result.stdout)["reason"]


def test_container_release_script_exposes_data_commands() -> None:
    script = Path(__file__).parents[2] / "scripts/container_release.py"

    assert "container and data release tooling" in script.read_text(encoding="utf-8")
