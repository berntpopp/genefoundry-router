from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from genefoundry_router.release.content import (
    ContentPolicy,
    ContentPolicyError,
    inspect_rootfs,
)
from genefoundry_router.release.content_archive import uncompressed_layer_digest
from genefoundry_router.release.content_secrets import secret_shaped as _secret_shaped


def _archive(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_complete_forbidden_name_extension_and_magic_matrix(tmp_path: Path) -> None:
    encrypted = (
        b"certificate header\n"
        + b"x" * 8192
        + b"\n-----BEGIN ENCRYPTED PRIVATE KEY-----\nMAMCAQE=\n"
        + b"-----END ENCRYPTED PRIVATE KEY-----\n"
    )
    files = {
        "opt/app/.git/config": b"repository state",
        "opt/app/innocent-bcf.bin": b"BCF\x02\x02payload",
        "opt/app/innocent-vcf.txt": b"\xef\xbb\xbf \r\n##fileformat=VCFv4.3\n",
        "opt/app/leading-certificate.txt": encrypted,
        "opt/app/private.p12": b"binary private material",
        "opt/app/corpus/articles.json": b"{}",
        "opt/app/reference-dataset.tar.gz": b"compressed",
        "opt/app/database.bin": b"SQLite format 3\x00payload",
        "opt/app/columns.bin": b"PAR1payload",
    }
    result = inspect_rootfs(_archive(tmp_path / "matrix.tar", files), ContentPolicy.default())
    assert result.denied_paths == tuple(sorted(files))


def test_allowlist_cannot_override_vcf_bcf_or_private_material_magic(tmp_path: Path) -> None:
    files = {
        "opt/app/data/renamed-vcf.txt": b"  ##fileformat=VCFv4.2\n",
        "opt/app/data/renamed-bcf.txt": b"BCF\x02payload",
        "opt/app/data/encrypted.txt": (
            b"-----BEGIN DSA PRIVATE KEY-----\nMAMCAQE=\n-----END DSA PRIVATE KEY-----\n"
        ),
    }
    result = inspect_rootfs(
        _archive(tmp_path / "allowlisted.tar", files),
        ContentPolicy.default(),
        tuple(files),
    )
    assert result.allowlisted_paths == ()
    assert result.denied_paths == tuple(sorted(files))


def test_gzip_diff_id_validates_full_stream_trailing_corruption_and_ceiling(tmp_path: Path) -> None:
    raw = _archive(tmp_path / "plain.tar", {"opt/app/file": b"payload"}).read_bytes()
    compressed = tmp_path / "layer.tar.gz"
    compressed.write_bytes(gzip.compress(raw))
    assert uncompressed_layer_digest(compressed, "gzip", len(raw)) == (
        f"sha256:{hashlib.sha256(raw).hexdigest()}"
    )
    with pytest.raises(ContentPolicyError, match="byte limit"):
        uncompressed_layer_digest(compressed, "gzip", len(raw) - 1)
    compressed.write_bytes(compressed.read_bytes() + b"corrupt trailing bytes")
    with pytest.raises(ContentPolicyError, match="compressed layer"):
        uncompressed_layer_digest(compressed, "gzip", len(raw) * 2)


def test_revision_and_digest_metadata_are_not_secret_false_positives() -> None:
    assert not _secret_shaped("sha256:" + "a" * 64)
    assert not _secret_shaped("0123456789abcdef" * 4)
