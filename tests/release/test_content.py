from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from genefoundry_router.release.content import (
    ContentPolicy,
    ContentPolicyError,
    inspect_build_context,
    inspect_oci_layout,
    inspect_rootfs,
)


def _tar(path: Path, files: dict[str, bytes], *, gzip_layer: bool = False) -> Path:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    path.write_bytes(gzip.compress(raw.getvalue()) if gzip_layer else raw.getvalue())
    return path


def _blob(layout: Path, payload: bytes) -> tuple[str, int]:
    digest = hashlib.sha256(payload).hexdigest()
    target = layout / "blobs" / "sha256" / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return f"sha256:{digest}", len(payload)


def _oci(layout: Path, layers: list[Path], *, config: object | None = None) -> Path:
    descriptors = []
    diff_ids = []
    for layer in layers:
        payload = layer.read_bytes()
        digest, size = _blob(layout, payload)
        media = (
            "application/vnd.oci.image.layer.v1.tar+gzip"
            if payload[:2] == b"\x1f\x8b"
            else "application/vnd.oci.image.layer.v1.tar"
        )
        descriptors.append({"mediaType": media, "digest": digest, "size": size})
        uncompressed = gzip.decompress(payload) if media.endswith("+gzip") else payload
        diff_ids.append(f"sha256:{hashlib.sha256(uncompressed).hexdigest()}")
    selected_config = (
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {"User": "65532:65532", "Env": ["PATH=/usr/bin"]},
            "history": [],
        }
        if config is None
        else config
    )
    if isinstance(selected_config, dict):
        selected_config = dict(selected_config)
        selected_config.setdefault("rootfs", {"type": "layers", "diff_ids": diff_ids})
    config_bytes = json.dumps(selected_config, separators=(",", ":")).encode()
    config_digest, config_size = _blob(layout, config_bytes)
    manifest_bytes = json.dumps(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": config_size,
            },
            "layers": descriptors,
        },
        separators=(",", ":"),
    ).encode()
    digest, size = _blob(layout, manifest_bytes)
    (layout / "index.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": digest,
                        "size": size,
                        "platform": {"architecture": "amd64", "os": "linux"},
                    }
                ],
            }
        )
    )
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    return layout


def _replace_manifest(layout: Path, manifest: dict[str, object]) -> None:
    payload = json.dumps(manifest, separators=(",", ":")).encode()
    digest, size = _blob(layout, payload)
    index = json.loads((layout / "index.json").read_text())
    index["manifests"][0]["digest"] = digest
    index["manifests"][0]["size"] = size
    (layout / "index.json").write_text(json.dumps(index))


def _manifest(layout: Path) -> dict[str, object]:
    index = json.loads((layout / "index.json").read_text())
    digest = index["manifests"][0]["digest"].split(":", 1)[1]
    return json.loads((layout / "blobs" / "sha256" / digest).read_text())


def _replace_config(layout: Path, payload: bytes) -> None:
    manifest = _manifest(layout)
    digest, size = _blob(layout, payload)
    manifest["config"]["digest"] = digest
    manifest["config"]["size"] = size
    _replace_manifest(layout, manifest)


def test_rootfs_denies_sensitive_data_and_magic_even_if_whiteouted(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "layer.tar",
        {
            "opt/app/.env.prod": b"TOKEN=x",
            "opt/app/pkg/data/reference.bin": b"SQLite format 3\x00rest",
            "opt/app/.wh.reference.bin": b"",
            "opt/app/sample.vcf.gz": b"not actually compressed",
            "opt/app/private.pem": b"-----BEGIN PRIVATE KEY-----",
            "opt/app/resources/ontology.json": b"{}",
        },
    )
    result = inspect_rootfs(archive, ContentPolicy.default(), ())
    assert result.denied_paths == tuple(sorted(result.denied_paths))
    assert {
        "opt/app/.env.prod",
        "opt/app/pkg/data/reference.bin",
        "opt/app/private.pem",
        "opt/app/resources/ontology.json",
        "opt/app/sample.vcf.gz",
    } <= set(result.denied_paths)
    assert "opt/app/.wh.reference.bin" not in result.denied_paths


def test_exact_allowlist_is_bounded_and_not_recursive(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "layer.tar",
        {
            "opt/app/data/schema.sql": b"create table example(id integer);",
            "opt/app/data/hidden.sqlite": b"SQLite format 3\x00",
        },
    )
    result = inspect_rootfs(archive, ContentPolicy.default(), ("opt/app/data/schema.sql",))
    assert result.allowlisted_paths == ("opt/app/data/schema.sql",)
    assert result.denied_paths == ("opt/app/data/hidden.sqlite",)
    with pytest.raises(ContentPolicyError, match="allowlist"):
        inspect_rootfs(archive, ContentPolicy.default(), ("opt/app/data",))


def test_allowlist_never_overrides_forbidden_magic_or_central_caps(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "layer.tar",
        {"opt/app/data/schema.sql": b"SQLite format 3\x00", "opt/app/.env.json": b"{}"},
    )
    result = inspect_rootfs(
        archive,
        ContentPolicy.default(),
        ("opt/app/data/schema.sql", "opt/app/.env.json"),
    )
    assert result.denied_paths == ("opt/app/.env.json", "opt/app/data/schema.sql")
    assert result.allowlisted_paths == ()
    policy = replace(ContentPolicy.default(), max_allowlist_entries=1)
    with pytest.raises(ContentPolicyError, match="entry limit"):
        inspect_rootfs(archive, policy, ("a.sql", "b.sql"))
    policy = replace(ContentPolicy.default(), max_allowlist_file_bytes=2)
    with pytest.raises(ContentPolicyError, match="file byte"):
        inspect_rootfs(archive, policy, ("opt/app/data/schema.sql",))


def test_allowlist_requires_declared_safe_utf8_media(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "media.tar",
        {
            "opt/app/data/good.sql": b"CREATE TABLE example (id INTEGER);\n",
            "opt/app/data/elf.sql": b"\x7fELF\x02\x01\x00binary",
            "opt/app/data/control.sql": b"select 1;\x00secret",
        },
    )
    result = inspect_rootfs(
        archive,
        ContentPolicy.default(),
        (
            "opt/app/data/good.sql",
            "opt/app/data/elf.sql",
            "opt/app/data/control.sql",
        ),
    )
    assert result.allowlisted_paths == ("opt/app/data/good.sql",)
    assert result.denied_paths == ("opt/app/data/control.sql", "opt/app/data/elf.sql")


@pytest.mark.parametrize(
    "bad_name",
    ["/etc/shadow", "../secret", "opt/../secret", "opt\\secret", "opt//secret", "./opt/app"],
)
def test_ambiguous_or_escaping_tar_paths_are_rejected(tmp_path: Path, bad_name: str) -> None:
    archive = _tar(tmp_path / "layer.tar", {bad_name: b"x"})
    with pytest.raises(ContentPolicyError, match="unsafe archive path"):
        inspect_rootfs(archive, ContentPolicy.default(), ())


def test_tar_link_special_setid_duplicate_and_malformed_are_rejected(tmp_path: Path) -> None:
    cases: list[tarfile.TarInfo] = []
    symlink = tarfile.TarInfo("opt/link")
    symlink.type, symlink.linkname = tarfile.SYMTYPE, "../../etc/passwd"
    cases.append(symlink)
    device = tarfile.TarInfo("opt/device")
    device.type = tarfile.CHRTYPE
    cases.append(device)
    setid = tarfile.TarInfo("opt/tool")
    setid.mode = 0o4755
    cases.append(setid)
    for index, info in enumerate(cases):
        path = tmp_path / f"bad-{index}.tar"
        with tarfile.open(path, "w") as archive:
            archive.addfile(info)
        with pytest.raises(ContentPolicyError):
            inspect_rootfs(path, ContentPolicy.default(), ())
    duplicate = tmp_path / "duplicate.tar"
    with tarfile.open(duplicate, "w") as archive:
        for _ in range(2):
            info = tarfile.TarInfo("opt/same")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ContentPolicyError, match="duplicate"):
        inspect_rootfs(duplicate, ContentPolicy.default(), ())
    malformed = tmp_path / "malformed.tar"
    malformed.write_bytes(b"not a tar")
    with pytest.raises(ContentPolicyError, match="tar"):
        inspect_rootfs(malformed, ContentPolicy.default(), ())


def test_oci_layout_checks_shape_digests_sizes_and_all_layers(tmp_path: Path) -> None:
    clean = _tar(
        tmp_path / "clean.tar",
        {"opt/app/main.py": b"print('ok')", "opt/app/data/.wh.cohort.parquet": b""},
    )
    dirty = _tar(
        tmp_path / "dirty.tar.gz", {"opt/app/data/cohort.parquet": b"PAR1"}, gzip_layer=True
    )
    layout = _oci(tmp_path / "oci", [dirty, clean])
    result = inspect_oci_layout(layout, ContentPolicy.default(), ())
    assert result.denied_paths == ("opt/app/data/cohort.parquet",)
    assert len(result.policy_digest) == 64
    assert len(result.allowlist_digest) == 64
    assert json.dumps(result.to_dict(), sort_keys=True)

    index = json.loads((layout / "index.json").read_text())
    manifest_digest = index["manifests"][0]["digest"].split(":", 1)[1]
    manifest_blob = layout / "blobs" / "sha256" / manifest_digest
    manifest_blob.write_bytes(manifest_blob.read_bytes() + b" ")
    with pytest.raises(ContentPolicyError, match=r"digest|size"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())


def test_oci_rejects_duplicate_json_keys_and_unsupported_shape(tmp_path: Path) -> None:
    layout = tmp_path / "oci"
    layout.mkdir()
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (layout / "index.json").write_text('{"schemaVersion":2,"schemaVersion":2,"manifests":[]}')
    with pytest.raises(ContentPolicyError, match="duplicate JSON key"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())
    (layout / "index.json").write_text('{"schemaVersion":2,"manifests":[]}')
    with pytest.raises(ContentPolicyError, match="exactly one"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())


def test_oci_requires_layout_and_exact_descriptor_manifest_platform(tmp_path: Path) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout = _oci(tmp_path / "oci", [layer])
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.1.0"}')
    with pytest.raises(ContentPolicyError, match="OCI layout"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    manifest = _manifest(layout)
    manifest["subject"] = {"digest": "sha256:" + "0" * 64}
    _replace_manifest(layout, manifest)
    with pytest.raises(ContentPolicyError, match="manifest"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())


def test_oci_requires_config_platform_and_exact_layer_descriptors(tmp_path: Path) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout = _oci(tmp_path / "oci", [layer], config={"config": {"User": "65532"}})
    with pytest.raises(ContentPolicyError, match="platform"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())
    layout = _oci(tmp_path / "oci-2", [layer])
    manifest = _manifest(layout)
    manifest["layers"][0]["platform"] = {"architecture": "amd64", "os": "linux"}
    _replace_manifest(layout, manifest)
    with pytest.raises(ContentPolicyError, match="descriptor"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())


def test_oci_requires_rootfs_diff_ids_and_binds_plain_and_gzip_layers(tmp_path: Path) -> None:
    plain = _tar(tmp_path / "plain.tar", {"opt/app/a": b"a"})
    compressed = _tar(tmp_path / "gzip.tar", {"opt/app/b": b"b"}, gzip_layer=True)
    layout = _oci(tmp_path / "valid", [plain, compressed])
    assert inspect_oci_layout(layout).denied_paths == ()
    manifest = _manifest(layout)
    config_digest = manifest["config"]["digest"].split(":", 1)[1]
    config = json.loads((layout / "blobs" / "sha256" / config_digest).read_text())
    config["rootfs"]["diff_ids"].reverse()
    _replace_config(layout, json.dumps(config).encode())
    with pytest.raises(ContentPolicyError, match="diff_id"):
        inspect_oci_layout(layout)
    config["rootfs"]["diff_ids"].pop()
    _replace_config(layout, json.dumps(config).encode())
    with pytest.raises(ContentPolicyError, match="diff_ids"):
        inspect_oci_layout(layout)
    del config["rootfs"]
    _replace_config(layout, json.dumps(config).encode())
    with pytest.raises(ContentPolicyError, match="rootfs"):
        inspect_oci_layout(layout)


def test_plain_media_never_auto_decompresses_other_formats(tmp_path: Path) -> None:
    raw = _tar(tmp_path / "plain.tar", {"opt/app/a": b"a"}).read_bytes()
    for name, payload in (
        ("bz2", bz2.compress(raw)),
        ("xz", lzma.compress(raw)),
        ("zstd", b"\x28\xb5\x2f\xfd" + raw),
    ):
        blob = tmp_path / f"{name}.tar"
        blob.write_bytes(payload)
        layout = _oci(tmp_path / name, [blob])
        with pytest.raises(ContentPolicyError, match=r"encoding|tar"):
            inspect_oci_layout(layout)


def test_oci_rejects_missing_blob_size_media_and_config_duplicate_keys(tmp_path: Path) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout = _oci(tmp_path / "missing", [layer])
    manifest = _manifest(layout)
    layer_digest = manifest["layers"][0]["digest"].split(":", 1)[1]
    (layout / "blobs" / "sha256" / layer_digest).unlink()
    with pytest.raises(ContentPolicyError, match="missing OCI blob"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())

    layout = _oci(tmp_path / "size", [layer])
    manifest = _manifest(layout)
    manifest["layers"][0]["size"] += 1
    _replace_manifest(layout, manifest)
    with pytest.raises(ContentPolicyError, match="size mismatch"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())

    layout = _oci(tmp_path / "media", [layer])
    manifest = _manifest(layout)
    manifest["layers"][0]["mediaType"] = "application/octet-stream"
    _replace_manifest(layout, manifest)
    with pytest.raises(ContentPolicyError, match="unsupported"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())

    layout = _oci(tmp_path / "encoding", [layer])
    manifest = _manifest(layout)
    manifest["layers"][0]["mediaType"] = "application/vnd.oci.image.layer.v1.tar+gzip"
    _replace_manifest(layout, manifest)
    with pytest.raises(ContentPolicyError, match="encoding"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())

    layout = _oci(tmp_path / "duplicate-config", [layer])
    _replace_config(
        layout,
        b'{"architecture":"amd64","architecture":"amd64","os":"linux","config":{"User":"65532"}}',
    )
    with pytest.raises(ContentPolicyError, match="duplicate JSON key"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())


def test_blob_caps_and_oci_annotation_metadata_are_bounded_and_secret_scanned(
    tmp_path: Path,
) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    with pytest.raises(ContentPolicyError, match="blob byte limit"):
        inspect_rootfs(layer, replace(ContentPolicy.default(), max_blob_bytes=1), ())

    layout = _oci(tmp_path / "annotations", [layer])
    index = json.loads((layout / "index.json").read_text())
    index["annotations"] = {"revision": "ghp_abcdefghijklmnopqrstuvwxyz1234567890"}
    (layout / "index.json").write_text(json.dumps(index))
    result = inspect_oci_layout(layout)
    assert result.denied_config == ("index.annotations[0]: secret-shaped value",)
    assert "ghp_" not in json.dumps(result.to_dict())

    manifest = _manifest(layout)
    manifest["annotations"] = {"invalid": {"nested": "object"}}
    _replace_manifest(layout, manifest)
    with pytest.raises(ContentPolicyError, match="annotations"):
        inspect_oci_layout(layout)


def test_policy_schema_identity_and_tightening_only(tmp_path: Path) -> None:
    raw = json.loads(
        (
            Path(__file__).parents[2] / "genefoundry_router/data/image-content-policy-v1.json"
        ).read_text()
    )
    for key, value in (("unknown", 1), ("version", 2), ("max_entries", True), ("max_entries", 0)):
        candidate = dict(raw)
        candidate[key] = value
        path = tmp_path / f"{key}-{value}.json"
        path.write_text(json.dumps(candidate))
        with pytest.raises(ContentPolicyError, match="policy"):
            ContentPolicy.from_path(path)
    inconsistent = dict(raw)
    inconsistent["max_allowlist_file_bytes"] = inconsistent["max_file_bytes"] + 1
    path = tmp_path / "inconsistent.json"
    path.write_text(json.dumps(inconsistent))
    with pytest.raises(ContentPolicyError, match="policy"):
        ContentPolicy.from_path(path)
    invalid_media = dict(raw)
    invalid_media["allowlist_media_types"] = {".sql": "application/octet-stream"}
    path.write_text(json.dumps(invalid_media))
    with pytest.raises(ContentPolicyError, match="policy"):
        ContentPolicy.from_path(path)
    policy = ContentPolicy.default()
    tightened = policy.with_limits(max_entries=policy.max_entries - 1)
    assert tightened.digest != policy.digest
    assert replace(policy, max_diagnostics=policy.max_diagnostics - 1).digest != policy.digest
    with pytest.raises(ContentPolicyError, match="relax"):
        tightened.with_limits(max_entries=policy.max_entries)
    with pytest.raises(ContentPolicyError, match="limit"):
        replace(policy, max_entries=policy.max_entries + 1)


@pytest.mark.parametrize(
    "token",
    [
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "github_pat_11AAabcdefghijklmnopqrstuvwxyz1234567890",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyA123456789012345678901234567890",
        "".join(("xoxb", "-123456789012-123456789012-abcdefghijklmnop")),
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123",
    ],
)
def test_innocuous_config_keys_still_detect_targeted_token_formats(
    tmp_path: Path, token: str
) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"User": "65532", "Labels": {"revision": token}},
    }
    result = inspect_oci_layout(_oci(tmp_path / "oci", [layer], config=config))
    assert result.denied_config
    assert token not in json.dumps(result.to_dict())


def test_tar_pax_escape_malformed_whiteout_and_decompression_bomb(tmp_path: Path) -> None:
    pax = tmp_path / "pax.tar"
    with tarfile.open(pax, "w", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("safe")
        info.pax_headers = {"path": "../escape"}
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ContentPolicyError, match="unsafe archive path"):
        inspect_rootfs(pax, ContentPolicy.default(), ())

    whiteout = _tar(tmp_path / "whiteout.tar", {"opt/.wh.": b""})
    with pytest.raises(ContentPolicyError, match="whiteout"):
        inspect_rootfs(whiteout, ContentPolicy.default(), ())

    policy = ContentPolicy.default().with_limits(max_total_bytes=1024)
    bomb = tmp_path / "bomb.tar.gz"
    _tar(bomb, {"bomb": b"x" * 1025}, gzip_layer=True)
    with pytest.raises(ContentPolicyError, match="aggregate byte limit"):
        inspect_rootfs(bomb, policy, ())


def test_config_nonobject_base64_secret_and_bounded_diagnostics(tmp_path: Path) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    layout = _oci(tmp_path / "nonobject", [layer], config="invalid")
    with pytest.raises(ContentPolicyError, match=r"platform|object"):
        inspect_oci_layout(layout, ContentPolicy.default(), ())
    encoded = base64.b64encode(b"API_TOKEN=do-not-report").decode()
    config = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"User": "65532"},
        "history": [{"created_by": encoded}],
    }
    result = inspect_oci_layout(_oci(tmp_path / "encoded", [layer], config=config))
    assert result.denied_config == ("history[0]: secret-shaped value",)
    assert "do-not-report" not in json.dumps(result.to_dict())

    files = {f"z{index}.sqlite": b"SQLite format 3\x00" for index in range(5)}
    archive = _tar(tmp_path / "diagnostics.tar", files)
    result = inspect_rootfs(archive, replace(ContentPolicy.default(), max_diagnostics=2))
    assert result.denied_paths == ("z0.sqlite", "z1.sqlite")


@pytest.mark.parametrize(
    "config",
    [
        {"config": {"User": "", "Env": []}},
        {"config": {"User": "root", "Env": []}},
        {"architecture": "amd64", "os": "linux", "config": {"User": " 0:65532 ", "Env": []}},
        {"architecture": "amd64", "os": "linux", "config": {"User": "ROOT:65532", "Env": []}},
        {"config": {"User": "65532", "Env": ["API_TOKEN = secret"]}},
        {"config": {"User": "65532", "Cmd": ["--password", "hunter2"]}},
        {"config": {"User": "65532", "Labels": {"authorization": "Bearer abcdefghijklmnop"}}},
        {"config": {"User": "65532"}, "history": [{"created_by": "TOKEN=abcdefghi"}]},
        {"config": {"User": "65532", "Env": ["VALUE=ghp_abcdefghijklmnopqrstuvwxyz1234567890"]}},
        {"config": {"User": "65532", "Cmd": ["run", "AKIAIOSFODNN7EXAMPLE"]}},
        {
            "config": {
                "User": "65532",
                "Entrypoint": ["".join(("xoxb", "-123456789012-abcdefghijklmnop"))],
            }
        },
        {
            "config": {"User": "65532"},
            "history": [{"created_by": "AIzaSyA123456789012345678901234567890"}],
        },
    ],
)
def test_config_secret_surfaces_and_root_are_denied(tmp_path: Path, config: object) -> None:
    layer = _tar(tmp_path / "layer.tar", {"opt/app/main.py": b""})
    assert isinstance(config, dict)
    config = {"architecture": "amd64", "os": "linux", **config}
    result = inspect_oci_layout(
        _oci(tmp_path / "oci", [layer], config=config), ContentPolicy.default(), ()
    )
    assert result.denied_config
    rendered = " ".join(result.denied_config).lower()
    assert "hunter2" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert "abcdefghi" not in rendered
    assert "ghp_" not in rendered and "akia" not in rendered
    assert "xoxb" not in rendered and "aiza" not in rendered


def test_limits_and_deterministic_bounded_diagnostics(tmp_path: Path) -> None:
    policy = ContentPolicy.default().with_limits(max_entries=2, max_file_bytes=4, max_total_bytes=6)
    archive = _tar(tmp_path / "layer.tar", {"a": b"1", "b": b"22", "c": b"333"})
    with pytest.raises(ContentPolicyError, match="entry limit"):
        inspect_rootfs(archive, policy, ())
    archive = _tar(tmp_path / "huge.tar", {"large": b"12345"})
    result = inspect_rootfs(archive, policy, ())
    assert result.denied_paths == ("large",)


def test_build_context_requires_exclusions_sizes_files_and_rejects_symlinks(tmp_path: Path) -> None:
    required = ContentPolicy.default().required_dockerignore
    (tmp_path / ".dockerignore").write_text("\n".join(required) + "\n")
    (tmp_path / "app.py").write_bytes(b"123")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "huge.bin").write_bytes(b"x" * 1000)
    result = inspect_build_context(tmp_path, ContentPolicy.default())
    assert result.context_size == 3 + (tmp_path / ".dockerignore").stat().st_size
    assert result.denied_paths == ()
    (tmp_path / "escape").symlink_to("/etc/passwd")
    with pytest.raises(ContentPolicyError, match="symlink"):
        inspect_build_context(tmp_path, ContentPolicy.default())
    (tmp_path / "escape").unlink()
    (tmp_path / ".dockerignore").write_text(".git/\n")
    with pytest.raises(ContentPolicyError, match="missing required"):
        inspect_build_context(tmp_path, ContentPolicy.default())


def test_build_context_rejects_reincluded_required_tree_and_nested_cache(tmp_path: Path) -> None:
    required = ContentPolicy.default().required_dockerignore
    (tmp_path / ".dockerignore").write_text("\n".join(required) + "\n!data/secret.db\n")
    with pytest.raises(ContentPolicyError, match="re-include"):
        inspect_build_context(tmp_path, ContentPolicy.default())
    (tmp_path / ".dockerignore").write_text("\n".join(required) + "\n")
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "large.pyc").write_bytes(b"x" * 100)
    result = inspect_build_context(tmp_path, ContentPolicy.default())
    assert result.context_size == (tmp_path / ".dockerignore").stat().st_size
    (tmp_path / "app.py").write_bytes(b"123")
    with pytest.raises(ContentPolicyError, match="size limit"):
        inspect_build_context(tmp_path, ContentPolicy.default().with_limits(max_file_bytes=2))
