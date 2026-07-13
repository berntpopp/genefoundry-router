from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

import genefoundry_router.release.content as content_module
from genefoundry_router.release.content import (
    ContentPolicy,
    ContentPolicyError,
    inspect_oci_layout,
    inspect_rootfs,
)


def _outer_tar(tmp_path: Path, payload: bytes, *, name: str = "opt/app/neutral.bin") -> Path:
    archive_path = tmp_path / f"outer-{len(list(tmp_path.iterdir()))}.tar"
    with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return archive_path


def _der(tag: int, payload: bytes) -> bytes:
    assert len(payload) < 128
    return bytes((tag, len(payload))) + payload


def _layer(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return path


def _blob(layout: Path, payload: bytes) -> tuple[str, int, Path]:
    digest = hashlib.sha256(payload).hexdigest()
    target = layout / "blobs" / "sha256" / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return f"sha256:{digest}", len(payload), target


def _oci(layout: Path, layer: Path, *, applications: int = 1) -> tuple[Path, Path]:
    result, layer_blobs = _oci_layers(layout, [layer] * applications)
    return result, layer_blobs[0]


def _oci_layers(layout: Path, layers: list[Path]) -> tuple[Path, list[Path]]:
    layer_payloads = [layer.read_bytes() for layer in layers]
    blob_records = [_blob(layout, payload) for payload in layer_payloads]
    layer_blobs = [record[2] for record in blob_records]
    diff_ids = [f"sha256:{hashlib.sha256(payload).hexdigest()}" for payload in layer_payloads]
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"User": "10001"},
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
        "history": [],
    }
    config_payload = json.dumps(config, separators=(",", ":")).encode()
    config_digest, config_size, _ = _blob(layout, config_payload)
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": config_digest,
            "size": config_size,
        },
        "layers": [
            {
                "mediaType": "application/vnd.oci.image.layer.v1.tar",
                "digest": digest,
                "size": size,
            }
            for digest, size, _ in blob_records
        ],
    }
    manifest_payload = json.dumps(manifest, separators=(",", ":")).encode()
    manifest_digest, manifest_size, _ = _blob(layout, manifest_payload)
    (layout / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": manifest_digest,
                        "size": manifest_size,
                        "platform": {"architecture": "amd64", "os": "linux"},
                    }
                ],
            }
        )
    )
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    return layout, layer_blobs


def _pkcs12_fixture() -> bytes:
    # PFX ::= SEQUENCE { version v3, authSafe ContentInfo, macData OPTIONAL }.
    data_oid = _der(0x06, bytes.fromhex("2a864886f70d010701"))
    content_info = _der(0x30, data_oid + _der(0xA0, _der(0x04, b"encrypted-safe")))
    return _der(0x30, _der(0x02, b"\x03") + content_info)


def test_zip_with_clinical_member_is_denied_under_neutral_name(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("patients/cohort.vcf", "##fileformat=VCFv4.3\n#CHROM\tPOS\n")

    result = inspect_rootfs(_outer_tar(tmp_path, payload.getvalue()))

    assert result.denied_paths == ("opt/app/neutral.bin",)


def test_gzip_tar_with_database_member_is_denied_under_neutral_name(tmp_path: Path) -> None:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as archive:
        database = b"SQLite format 3\x00patient rows"
        member = tarfile.TarInfo("data/patients.sqlite")
        member.size = len(database)
        archive.addfile(member, io.BytesIO(database))

    result = inspect_rootfs(_outer_tar(tmp_path, gzip.compress(payload.getvalue())))

    assert result.denied_paths == ("opt/app/neutral.bin",)


@pytest.mark.parametrize(
    "payload",
    [
        _pkcs12_fixture(),
        bytes.fromhex("feedfeed0000000200000001"),
    ],
    ids=["pkcs12", "jks"],
)
def test_private_key_stores_are_denied_under_neutral_name(tmp_path: Path, payload: bytes) -> None:
    result = inspect_rootfs(_outer_tar(tmp_path, payload))

    assert result.denied_paths == ("opt/app/neutral.bin",)


def test_traditional_encrypted_private_pem_is_denied_under_neutral_name(tmp_path: Path) -> None:
    payload = (
        b"-----BEGIN RSA PRIVATE KEY-----\n"
        b"Proc-Type: 4,ENCRYPTED\n"
        b"DEK-Info: AES-256-CBC,0123456789ABCDEF0123456789ABCDEF\n"
        b"\n"
        b"MAMCAQE=\n"
        b"-----END RSA PRIVATE KEY-----\n"
    )

    result = inspect_rootfs(_outer_tar(tmp_path, payload))

    assert result.denied_paths == ("opt/app/neutral.bin",)


def test_production_image_removes_bundled_install_archives_before_flattening() -> None:
    dockerfile = (Path(__file__).parents[2] / "docker/Dockerfile").read_text()
    prepared = dockerfile.split("FROM scratch AS production", 1)[0]

    assert "/usr/local/lib/python3.14/ensurepip" in prepared


def test_layer_application_count_has_an_immutable_central_ceiling(tmp_path: Path) -> None:
    policy = ContentPolicy.default()
    layer = _layer(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout, _ = _oci(tmp_path / "oci", layer, applications=policy.max_layers + 1)

    with pytest.raises(ContentPolicyError, match="layer count"):
        inspect_oci_layout(layout, policy)


def test_aggregate_uncompressed_layer_streams_are_bounded_across_applications(
    tmp_path: Path,
) -> None:
    layer = _layer(tmp_path / "layer.tar", {"empty": b""})
    stream_size = layer.stat().st_size
    policy = ContentPolicy.default().with_limits(
        max_layers=2,
        max_uncompressed_image_bytes=stream_size * 2 - 1,
    )
    layout, _ = _oci(tmp_path / "oci", layer, applications=2)

    with pytest.raises(ContentPolicyError, match="uncompressed image byte limit"):
        inspect_oci_layout(layout, policy)


def test_aggregate_stream_budget_reports_image_limit_for_unique_layers(tmp_path: Path) -> None:
    first = _layer(tmp_path / "first.tar", {"first": b""})
    second = _layer(tmp_path / "second.tar", {"second": b""})
    stream_size = first.stat().st_size
    assert second.stat().st_size == stream_size
    policy = ContentPolicy.default().with_limits(
        max_layers=2,
        max_uncompressed_image_bytes=stream_size * 2 - 1,
    )
    layout, _ = _oci_layers(tmp_path / "oci", [first, second])

    with pytest.raises(ContentPolicyError, match="uncompressed image byte limit"):
        inspect_oci_layout(layout, policy)


def test_repeated_immutable_layer_is_scanned_once_but_counted_per_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = _layer(tmp_path / "layer.tar", {"opt/app/main.py": b"x"})
    layout, _ = _oci(tmp_path / "oci", layer, applications=2)
    calls = 0
    original = content_module.inspect_layer

    def count_real_scans(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(content_module, "inspect_layer", count_real_scans)
    report = inspect_oci_layout(layout)

    assert report.inspected_entries == 2
    assert report.inspected_bytes == 2
    assert calls == 1


def test_hardlinked_oci_blob_is_rejected(tmp_path: Path) -> None:
    layer = _layer(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout, layer_blob = _oci(tmp_path / "oci", layer)
    outside = tmp_path / "outside-layer"
    layer_blob.replace(outside)
    os.link(outside, layer_blob)

    with pytest.raises(ContentPolicyError, match="hardlink"):
        inspect_oci_layout(layout)


@pytest.mark.parametrize("name", ["oci-layout", "index.json"])
def test_hardlinked_oci_control_document_is_rejected(tmp_path: Path, name: str) -> None:
    layer = _layer(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout, _ = _oci(tmp_path / "oci", layer)
    document = layout / name
    outside = tmp_path / f"outside-{name}"
    document.replace(outside)
    os.link(outside, document)

    with pytest.raises(ContentPolicyError, match="hardlink"):
        inspect_oci_layout(layout)


def test_path_replacement_after_descriptor_validation_cannot_change_inspected_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = _layer(tmp_path / "clean.tar", {"opt/app/main.py": b"pass\n"})
    malicious = _layer(
        tmp_path / "malicious.tar",
        {"opt/app/patients.sqlite": b"SQLite format 3\x00patient rows"},
    )
    assert clean.stat().st_size == malicious.stat().st_size
    layout, source_blob = _oci(tmp_path / "oci", clean)
    original = content_module._descriptor_blob
    replaced = False

    def replace_source_after_validation(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal replaced
        descriptor, stable_blob = original(*args, **kwargs)
        if descriptor["digest"].removeprefix("sha256:") == source_blob.name and not replaced:
            replacement = source_blob.with_suffix(".replacement")
            replacement.write_bytes(malicious.read_bytes())
            replacement.replace(source_blob)
            replaced = True
        return descriptor, stable_blob

    monkeypatch.setattr(content_module, "_descriptor_blob", replace_source_after_validation)
    report = inspect_oci_layout(layout)

    assert replaced
    assert report.denied_paths == ()
