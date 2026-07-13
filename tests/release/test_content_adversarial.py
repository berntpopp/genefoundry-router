from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

import genefoundry_router.release.content as content_module
from genefoundry_router.release.content import (
    ContentPolicy,
    ContentPolicyError,
    inspect_build_context,
    inspect_oci_layout,
    inspect_rootfs,
)
from genefoundry_router.release.content_archive import uncompressed_layer_digest
from genefoundry_router.release.content_policy import json_bytes


def _write_members(path: Path, members: list[tarfile.TarInfo]) -> Path:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for member in members:
            payload = b"x" * member.size
            archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)
    return path


def _directory(name: str, *, mode: int = 0o755) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = mode
    return member


def _symlink(name: str, target: str) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.SYMTYPE
    member.linkname = target
    return member


def _regular(name: str, payload: bytes) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    return member, payload


def _write_files(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return path


def _blob(layout: Path, payload: bytes) -> tuple[str, int]:
    digest = hashlib.sha256(payload).hexdigest()
    target = layout / "blobs" / "sha256" / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return f"sha256:{digest}", len(payload)


def _oci(layout: Path, layer: Path, config: dict[str, object]) -> Path:
    layer_payload = layer.read_bytes()
    layer_digest, layer_size = _blob(layout, layer_payload)
    selected = dict(config)
    selected.setdefault(
        "rootfs",
        {
            "type": "layers",
            "diff_ids": [f"sha256:{hashlib.sha256(layer_payload).hexdigest()}"],
        },
    )
    config_digest, config_size = _blob(layout, json.dumps(selected).encode())
    manifest_payload = json.dumps(
        {
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
                    "digest": layer_digest,
                    "size": layer_size,
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    manifest_digest, manifest_size = _blob(layout, manifest_payload)
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
    return layout


def _der(tag: int, payload: bytes) -> bytes:
    assert len(payload) < 128
    return bytes((tag, len(payload))) + payload


def test_canonical_root_directory_is_accepted_once_and_still_validated(tmp_path: Path) -> None:
    accepted = _write_members(
        tmp_path / "accepted.tar",
        [_directory("."), _directory("usr"), _directory("usr/bin")],
    )
    assert inspect_rootfs(accepted).denied_paths == ()

    duplicate = _write_members(tmp_path / "duplicate.tar", [_directory("."), _directory(".")])
    with pytest.raises(ContentPolicyError, match="duplicate"):
        inspect_rootfs(duplicate)

    setid = _write_members(tmp_path / "setid.tar", [_directory(".", mode=0o2755)])
    with pytest.raises(ContentPolicyError, match="set-id"):
        inspect_rootfs(setid)

    for ambiguous in ("./usr", ".//"):
        archive = _write_members(
            tmp_path / f"ambiguous-{len(ambiguous)}.tar", [_directory(ambiguous)]
        )
        with pytest.raises(ContentPolicyError, match="unsafe"):
            inspect_rootfs(archive)


def test_root_contained_symlinks_accept_real_package_shapes_and_pax_linkpath(
    tmp_path: Path,
) -> None:
    long_target = "/usr/share/ca-certificates/" + "nested/" * 18 + "root-ca.crt"
    members = [
        _directory("."),
        _symlink("bin", "usr/bin"),
        _symlink("etc/os-release", "../usr/lib/os-release"),
        _symlink("opt/app/passwd", "../../etc/passwd"),
        _symlink("usr/bin/awk", "/usr/bin/mawk"),
        _symlink("etc/ssl/certs/long-ca.pem", long_target),
    ]
    archive = _write_members(tmp_path / "safe-links.tar", members)
    assert inspect_rootfs(archive).denied_paths == ()


@pytest.mark.parametrize(
    "target",
    [
        "../../../outside",
        "/../outside",
        "usr/\N{ZERO WIDTH SPACE}bin",
        "usr/line\nbreak",
        "usr/e\N{COMBINING ACUTE ACCENT}",
        "usr/ghp_abcdefghijklmnopqrstuvwxyz1234567890",
    ],
)
def test_symlink_target_must_be_canonical_root_contained_and_secret_safe(
    tmp_path: Path, target: str
) -> None:
    archive = _write_members(tmp_path / "unsafe-link.tar", [_symlink("opt/app/link", target)])
    with pytest.raises(ContentPolicyError, match=r"symlink|metadata") as error:
        inspect_rootfs(archive)
    assert "ghp_" not in str(error.value)


def test_hardlinks_and_setid_entries_remain_structural_failures(tmp_path: Path) -> None:
    hardlink = tarfile.TarInfo("usr/bin/perl-alias")
    hardlink.type = tarfile.LNKTYPE
    hardlink.linkname = "usr/bin/perl"
    with pytest.raises(ContentPolicyError, match="hardlink"):
        inspect_rootfs(_write_members(tmp_path / "hardlink.tar", [hardlink]))

    executable = tarfile.TarInfo("usr/bin/helper")
    executable.mode = 0o4755
    executable.size = 1
    with pytest.raises(ContentPolicyError, match="set-id"):
        inspect_rootfs(_write_members(tmp_path / "setid.tar", [executable]))


def test_build_context_rejects_same_inode_hardlinks(tmp_path: Path) -> None:
    policy = ContentPolicy.default()
    (tmp_path / ".dockerignore").write_text("\n".join(policy.required_dockerignore) + "\n")
    (tmp_path / "source.py").write_text("pass\n")
    os.link(tmp_path / "source.py", tmp_path / "alias.py")
    with pytest.raises(ContentPolicyError, match="hardlink"):
        inspect_build_context(tmp_path, policy)


def test_exact_allowlist_permits_only_declared_packaged_json_resource(tmp_path: Path) -> None:
    allowed = "build/site-packages/genefoundry_router/data/fleet-baseline.json"
    denied = "build/site-packages/genefoundry_router/data/unreviewed.json"
    archive = _write_files(tmp_path / "resources.tar", {allowed: b"{}", denied: b"{}"})
    result = inspect_rootfs(archive, allowlist=(allowed,))
    assert result.allowlisted_paths == (allowed,)
    assert result.denied_paths == (denied,)


def test_exact_allowlist_cap_accommodates_reviewed_router_baseline_size(tmp_path: Path) -> None:
    path = "build/site-packages/genefoundry_router/data/fleet-baseline.json"
    payload = b"{}" + b" " * (3 * 1024 * 1024)
    archive = _write_files(tmp_path / "baseline.tar", {path: payload})
    result = inspect_rootfs(archive, allowlist=(path,))
    assert result.allowlisted_paths == (path,)


def test_private_pem_detection_requires_complete_ordered_valid_envelope(tmp_path: Path) -> None:
    body = base64.b64encode(_der(0x30, _der(0x02, b"\0") + _der(0x02, b"\1")))
    valid = b"-----BEGIN RSA PRIVATE KEY-----\n" + body + b"\n-----END RSA PRIVATE KEY-----\n"
    files = {
        "opt/app/valid.bin": valid,
        "opt/app/begin-doc.txt": b"the marker -----BEGIN PRIVATE KEY----- is documented here",
        "opt/app/reversed.txt": (
            b"-----END PRIVATE KEY-----\nthen later\n-----BEGIN PRIVATE KEY-----"
        ),
        "opt/app/malformed.txt": (
            b"-----BEGIN PRIVATE KEY-----\nnot base64!\n-----END PRIVATE KEY-----"
        ),
    }
    result = inspect_rootfs(_write_files(tmp_path / "pem.tar", files))
    assert result.denied_paths == ("opt/app/valid.bin",)


@pytest.mark.parametrize(
    "payload",
    [
        # PKCS#1 RSA: SEQUENCE { version and eight integer key parameters }
        _der(0x30, b"".join(_der(0x02, bytes((value,))) for value in range(9))),
        # SEC1 EC: SEQUENCE { version=1, privateKey OCTET STRING }
        _der(0x30, _der(0x02, b"\1") + _der(0x04, b"private")),
        # PKCS#8: SEQUENCE { version, AlgorithmIdentifier, privateKey }
        _der(0x30, _der(0x02, b"\0") + _der(0x30, _der(0x06, b"*")) + _der(0x04, b"key")),
        # Encrypted PKCS#8: SEQUENCE { AlgorithmIdentifier, encryptedData }
        _der(0x30, _der(0x30, _der(0x06, b"*")) + _der(0x04, b"ciphertext")),
    ],
)
def test_der_private_key_formats_are_denied_even_under_neutral_names(
    tmp_path: Path, payload: bytes
) -> None:
    result = inspect_rootfs(_write_files(tmp_path / "der.tar", {"opt/app/neutral.bin": payload}))
    assert result.denied_paths == ("opt/app/neutral.bin",)


def test_compressed_package_docs_pass_but_compressed_dataset_magic_is_classified(
    tmp_path: Path,
) -> None:
    manual = gzip.compress(b".TH ROUTER 1\nordinary manual page\n")
    hidden_vcf = gzip.compress(b"##fileformat=VCFv4.3\n#CHROM\tPOS\n")
    result = inspect_rootfs(
        _write_files(
            tmp_path / "compressed.tar",
            {
                "usr/share/man/man1/genefoundry.1.gz": manual,
                "opt/app/neutral.gz": hidden_vcf,
            },
        )
    )
    assert result.denied_paths == ("opt/app/neutral.gz",)


def test_gzip_compressed_sqlite_is_denied_under_a_neutral_binary_name(tmp_path: Path) -> None:
    payload = gzip.compress(b"SQLite format 3\x00" + b"schema and rows")
    result = inspect_rootfs(_write_files(tmp_path / "sqlite.tar", {"opt/app/assets.bin": payload}))
    assert result.denied_paths == ("opt/app/assets.bin",)


def test_config_onbuild_and_complete_healthcheck_are_strict_with_low_false_positives(
    tmp_path: Path,
) -> None:
    layer = _write_files(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    clean_runtime = {
        "User": "10001:10001",
        "OnBuild": ["COPY authorization.py /app/", "RUN python -m tokenize source.py"],
        "Healthcheck": {
            "Test": ["CMD", "python", "-m", "secretary"],
            "Interval": 30_000_000_000,
            "Timeout": 10_000_000_000,
            "StartPeriod": 1_000_000_000,
            "StartInterval": 500_000_000,
            "Retries": 3,
        },
    }
    clean = _oci(
        tmp_path / "clean-oci",
        layer,
        {"architecture": "amd64", "os": "linux", "config": clean_runtime, "history": []},
    )
    assert inspect_oci_layout(clean).denied_config == ()

    malformed_runtime = dict(clean_runtime)
    malformed_runtime["Healthcheck"] = {"Test": ["NONE"], "Retries": True, "Unknown": 1}
    malformed = _oci(
        tmp_path / "malformed-oci",
        layer,
        {"architecture": "amd64", "os": "linux", "config": malformed_runtime, "history": []},
    )
    with pytest.raises(ContentPolicyError, match="Healthcheck"):
        inspect_oci_layout(malformed)

    secret_runtime = dict(clean_runtime)
    secret_runtime["OnBuild"] = ["RUN API_TOKEN=do-not-leak command"]
    secret_runtime["Healthcheck"] = {"Test": ["CMD-SHELL", "curl -H 'Authorization: Bearer x'"]}
    secret = _oci(
        tmp_path / "secret-oci",
        layer,
        {"architecture": "amd64", "os": "linux", "config": secret_runtime, "history": []},
    )
    report = inspect_oci_layout(secret)
    assert report.denied_config == (
        "config.Healthcheck.Test: secret-shaped value",
        "config.OnBuild: secret-shaped value",
    )
    assert "do-not-leak" not in json.dumps(report.to_dict())


def test_duplicate_json_key_error_is_constant_and_never_echoes_input(tmp_path: Path) -> None:
    secret_key = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"  # noqa: S105
    document = tmp_path / "duplicate.json"
    document.write_text(f'{{"{secret_key}":1,"{secret_key}":2}}')
    with pytest.raises(ContentPolicyError) as error:
        json_bytes(document)
    assert str(error.value) == "duplicate JSON key"


@pytest.mark.parametrize(
    "name", ["bad\nname", "bad\N{ZERO WIDTH SPACE}name", "e\N{COMBINING ACUTE ACCENT}"]
)
def test_allowlist_and_build_context_paths_require_canonical_visible_unicode(
    tmp_path: Path, name: str
) -> None:
    archive = _write_files(tmp_path / "paths.tar", {name + ".json": b"{}"})
    with pytest.raises(ContentPolicyError, match=r"path|character"):
        inspect_rootfs(archive, allowlist=(name + ".json",))

    context = tmp_path / "context"
    context.mkdir()
    policy = ContentPolicy.default()
    (context / ".dockerignore").write_text("\n".join(policy.required_dockerignore) + "\n")
    (context / name).write_text("x")
    with pytest.raises(ContentPolicyError, match=r"path|character"):
        inspect_build_context(context, policy)


def test_policy_lists_cannot_be_changed_by_replace() -> None:
    policy = ContentPolicy.default()
    with pytest.raises(ContentPolicyError, match="policy"):
        replace(
            policy,
            allowlist_media_types=(
                *policy.allowlist_media_types,
                (".rst", "text/plain; charset=utf-8"),
            ),
        )
    with pytest.raises(ContentPolicyError, match="policy"):
        replace(policy, required_dockerignore=policy.required_dockerignore[:-1])


@pytest.mark.parametrize(
    ("format", "parser_name"),
    [
        (tarfile.PAX_FORMAT, "_proc_pax"),
        (tarfile.GNU_FORMAT, "_proc_gnulong"),
    ],
)
def test_oversized_extended_tar_metadata_is_rejected_before_tarfile_allocates_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    format: int,
    parser_name: str,
) -> None:
    archive_path = tmp_path / f"oversized-{format}.tar"
    long_name = "opt/" + "x" * 100_000
    with tarfile.open(archive_path, "w", format=format) as archive:
        member = tarfile.TarInfo(long_name)
        archive.addfile(member)

    def forbidden_parser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tarfile extended metadata parser was reached")

    monkeypatch.setattr(tarfile.TarInfo, parser_name, forbidden_parser)
    with pytest.raises(ContentPolicyError, match="metadata byte limit"):
        inspect_rootfs(archive_path, ContentPolicy.default())


def test_secret_diagnostics_are_bounded_before_report_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = _write_files(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    labels = {
        f"token-{index}": f"ghp_{index:020d}abcdefghijklmnopqrstuvwxyz" for index in range(20)
    }
    layout = _oci(
        tmp_path / "oci",
        layer,
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"User": "10001", "Labels": labels},
            "history": [],
        },
    )
    observed: list[int] = []
    original = content_module._bounded_report_diagnostics

    def record_sizes(
        policy: ContentPolicy,
        denied: tuple[str, ...] | set[str],
        allowlisted: tuple[str, ...] | set[str],
        config: tuple[str, ...] | set[str],
    ) -> tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], bool]:
        observed.extend((len(denied), len(allowlisted), len(config)))
        return original(policy, denied, allowlisted, config)

    monkeypatch.setattr(content_module, "_bounded_report_diagnostics", record_sizes)
    policy = replace(ContentPolicy.default(), max_diagnostics=2)
    report = inspect_oci_layout(layout, policy)
    assert report.diagnostics_truncated
    assert max(observed) <= policy.max_diagnostics


def test_oci_layout_rejects_symlinked_layout_and_blob_ancestors(tmp_path: Path) -> None:
    layer = _write_files(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"User": "10001"},
        "history": [],
    }
    real = _oci(tmp_path / "real", layer, config)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ContentPolicyError, match="symlink"):
        inspect_oci_layout(alias)

    layout = _oci(tmp_path / "blob-parent", layer, config)
    blobs = layout / "blobs"
    moved = layout / "real-blobs"
    blobs.rename(moved)
    blobs.symlink_to(moved.name, target_is_directory=True)
    with pytest.raises(ContentPolicyError, match="symlink"):
        inspect_oci_layout(layout)


@pytest.mark.parametrize("compression", ["plain", "gzip"])
def test_nonzero_tar_file_alignment_padding_is_rejected(tmp_path: Path, compression: str) -> None:
    raw_path = _write_files(tmp_path / "base.tar", {"one-byte": b"x", "next": b"y"})
    corrupted = bytearray(raw_path.read_bytes())
    corrupted[tarfile.BLOCKSIZE + 1] = 0x41
    archive = tmp_path / f"bad-padding-{compression}.tar"
    archive.write_bytes(gzip.compress(corrupted) if compression == "gzip" else corrupted)
    with pytest.raises(ContentPolicyError, match="padding"):
        uncompressed_layer_digest(
            archive,
            compression,
            ContentPolicy.default().max_uncompressed_layer_bytes,
        )
    with pytest.raises(ContentPolicyError, match="padding"):
        inspect_rootfs(archive)


def test_production_dockerfile_flattens_a_sanitized_runtime_rootfs() -> None:
    dockerfile = (Path(__file__).parents[2] / "docker/Dockerfile").read_text()
    assert " AS prepared" in dockerfile
    assert "find / -xdev -perm /6000" in dockerfile
    assert "find / -xdev -type f -links +1" in dockerfile
    assert "rm -f /usr/bin/perl5.40.1" in dockerfile
    assert "-name __pycache__" in dockerfile
    production = dockerfile.split("FROM scratch AS production", 1)[1]
    assert "COPY --from=prepared / /" in production
    assert "COPY genefoundry_router" not in production
    assert "USER 10001:10001" in production
