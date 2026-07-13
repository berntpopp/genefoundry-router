from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from genefoundry_router.release.content import ContentPolicy, ContentPolicyError, inspect_rootfs
from genefoundry_router.release.content_archive import uncompressed_layer_digest


def _tar(path: Path, files: dict[str, bytes], *, format: int = tarfile.PAX_FORMAT) -> Path:
    with tarfile.open(path, "w", format=format) as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_plain_and_gzip_reject_nonzero_tail_but_allow_zero_padding(tmp_path: Path) -> None:
    raw = _tar(tmp_path / "base.tar", {"opt/app/file": b"ok"}).read_bytes()
    for compression, payload in (
        ("plain", raw + b"hidden"),
        ("gzip", gzip.compress(raw + b"hidden")),
    ):
        path = tmp_path / f"bad-{compression}"
        path.write_bytes(payload)
        with pytest.raises(ContentPolicyError, match="tar EOF"):
            uncompressed_layer_digest(path, compression, len(raw) * 2)
        with pytest.raises(ContentPolicyError, match="tar EOF"):
            inspect_rootfs(path, ContentPolicy.default())
    padded = raw + b"\0" * 1024
    for compression, payload in (("plain", padded), ("gzip", gzip.compress(padded))):
        path = tmp_path / f"good-{compression}"
        path.write_bytes(payload)
        assert uncompressed_layer_digest(path, compression, len(padded)) == (
            f"sha256:{hashlib.sha256(padded).hexdigest()}"
        )


def test_pax_metadata_is_allowlisted_bounded_and_secret_safe(tmp_path: Path) -> None:
    secret = tmp_path / "secret-pax.tar"
    with tarfile.open(secret, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("opt/app/file")
        info.pax_headers = {"comment": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"}
        archive.addfile(info)
    with pytest.raises(ContentPolicyError, match="archive metadata") as error:
        inspect_rootfs(secret, ContentPolicy.default())
    assert "ghp_" not in str(error.value)

    accepted_secret = tmp_path / "secret-path.tar"
    secret_path = "opt/app/ghp_abcdefghijklmnopqrstuvwxyz1234567890/file"  # noqa: S105
    with tarfile.open(accepted_secret, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo(secret_path)
        info.pax_headers = {"path": secret_path}
        archive.addfile(info)
    with pytest.raises(ContentPolicyError, match="archive metadata") as error:
        inspect_rootfs(accepted_secret, ContentPolicy.default())
    assert "ghp_" not in str(error.value)

    malformed = tmp_path / "malformed-pax.tar"
    with tarfile.open(malformed, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("opt/app/file")
        info.pax_headers = {"mtime": "nan"}
        archive.addfile(info)
    with pytest.raises(ContentPolicyError, match=r"metadata|tar"):
        inspect_rootfs(malformed, ContentPolicy.default())

    oversized = "opt/" + "a" * 100_000 + ".sqlite"
    archive = _tar(tmp_path / "oversized.tar", {oversized: b"x"})
    with pytest.raises(ContentPolicyError, match=r"(?:path|metadata) byte limit") as error:
        inspect_rootfs(archive, ContentPolicy.default())
    assert oversized[:100] not in str(error.value)


def test_ustar_owner_metadata_is_bounded_and_secret_safe(tmp_path: Path) -> None:
    archive_path = tmp_path / "owner.tar"
    with tarfile.open(archive_path, "w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("opt/app/file")
        info.uname = "ghp_abcdefghijklmnopqrstuvwx"
        archive.addfile(info)
    with pytest.raises(ContentPolicyError, match="archive metadata") as error:
        inspect_rootfs(archive_path, ContentPolicy.default())
    assert "ghp_" not in str(error.value)


def test_jwt_archive_paths_and_owner_metadata_reject_before_reporting(tmp_path: Path) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123"
    for name in (f"opt/app/{jwt}/file", f"opt/app/{jwt}.sqlite"):
        archive_path = tmp_path / f"jwt-{len(name)}.tar"
        with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
            info = tarfile.TarInfo(name)
            info.pax_headers = {"path": name}
            archive.addfile(info)
        with pytest.raises(ContentPolicyError, match="archive metadata") as error:
            inspect_rootfs(archive_path, ContentPolicy.default())
        assert jwt not in str(error.value)
    owner = tmp_path / "jwt-owner.tar"
    with tarfile.open(owner, "w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("opt/app/file")
        info.uname = "eyJ12345678.eyJ12345678.sig12345"
        archive.addfile(info)
    with pytest.raises(ContentPolicyError, match="archive metadata"):
        inspect_rootfs(owner, ContentPolicy.default())


def test_semantic_word_code_paths_are_allowed_but_real_credential_files_deny(
    tmp_path: Path,
) -> None:
    ordinary = {
        "usr/local/lib/python3.12/token.py": b"",
        "usr/local/lib/python3.12/tokenize.py": b"",
        "opt/app/auth/authorization.py": b"",
        "opt/app/secretary.py": b"",
        "opt/app/pkg/credentials.py": b"",
        "opt/app/docs/credentials.md": b"documentation",
    }
    assert inspect_rootfs(_tar(tmp_path / "ordinary.tar", ordinary)).denied_paths == ()
    credentials = {
        "root/.netrc": b"machine example",
        "root/.npmrc": b"registry=x",
        "opt/app/credentials.json": b"{}",
        "opt/app/secrets.yaml": b"{}",
        "root/.docker/config.json": b"{}",
        "root/.ssh/id_ed25519": b"key",
        "root/.git-credentials": b"credential store",
        "opt/app/client_secret.json": b"{}",
        "root/.config/gcloud/application_default_credentials.json": b"{}",
        "opt/app/service-account.json": b"{}",
        "opt/app/service_account.json": b"{}",
        "opt/app/credentials-prod.yaml": b"{}",
        "opt/app/prod-credentials.toml": b"{}",
        "opt/app/client-secrets.yml": b"{}",
    }
    result = inspect_rootfs(_tar(tmp_path / "credentials.tar", credentials))
    assert result.denied_paths == tuple(sorted(credentials))


def test_safe_pax_and_gnu_long_paths_remain_compatible(tmp_path: Path) -> None:
    path = "opt/app/" + "nested/" * 30 + "schema.sql"
    for format in (tarfile.PAX_FORMAT, tarfile.GNU_FORMAT):
        archive = _tar(tmp_path / f"long-{format}.tar", {path: b"select 1;"}, format=format)
        result = inspect_rootfs(archive, ContentPolicy.default(), (path,))
        assert result.allowlisted_paths == (path,)


def test_full_file_private_markers_are_found_with_rolling_overlap(tmp_path: Path) -> None:
    marker = (
        b"\n-----BEGIN ENCRYPTED PRIVATE KEY-----\nMAMCAQE=\n-----END ENCRYPTED PRIVATE KEY-----\n"
    )
    files = {
        "opt/app/late.bin": b"x" * 80_000 + marker,
        "opt/app/split.bin": b"x" * (64 * 1024 - 10) + marker,
        "opt/app/ordinary.bin": b"x" * (2 * 1024 * 1024),
    }
    result = inspect_rootfs(_tar(tmp_path / "markers.tar", files), ContentPolicy.default())
    assert result.denied_paths == ("opt/app/late.bin", "opt/app/split.bin")
    assert result.inspected_bytes == sum(map(len, files.values()))


def test_path_and_serialized_diagnostics_are_byte_bounded(tmp_path: Path) -> None:
    policy = ContentPolicy.default()
    multibyte = "opt/" + "é" * (policy.max_path_bytes // 2) + ".sqlite"
    with pytest.raises(ContentPolicyError, match="path byte limit"):
        inspect_rootfs(_tar(tmp_path / "unicode.tar", {multibyte: b"x"}), policy)
    stem = "x" * (policy.max_path_bytes - len("/00.sqlite") - 4)
    files = {f"opt/{stem}/{index:02}.sqlite": b"SQLite format 3\x00" for index in range(40)}
    result = inspect_rootfs(_tar(tmp_path / "diagnostics.tar", files), policy)
    assert result.diagnostics_truncated
    assert result.denied_paths == tuple(sorted(result.denied_paths))
    assert len(json.dumps(result.to_dict(), sort_keys=True, ensure_ascii=True).encode()) <= (
        policy.max_diagnostic_bytes
    )


@pytest.mark.parametrize(
    "name",
    [
        "reference-dataset.tgz",
        "reference-dataset.tbz",
        "reference-dataset.tbz2",
        "reference-dataset.txz",
        "ontology.ttl",
        "ontology.gaf",
        "ontology.nq",
        "ontology.trig",
        "ontology.jsonld",
        "hp.ttl",
        "goa_human.gaf",
        "graph.nq",
        "terms.jsonld",
        "neutral.trig",
    ],
)
def test_compound_archives_and_ontology_serializations_are_denied(
    tmp_path: Path, name: str
) -> None:
    result = inspect_rootfs(_tar(tmp_path / "deny.tar", {f"opt/app/{name}": b"content"}))
    assert result.denied_paths == (f"opt/app/{name}",)
