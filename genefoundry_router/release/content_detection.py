"""Bounded payload and filename classification for public image layers."""

from __future__ import annotations

import bz2
import lzma
import re
import unicodedata
import zlib
from pathlib import PurePosixPath

_DATABASE_EXTENSIONS = {".db", ".duckdb", ".mdb", ".sqlite", ".sqlite3"}
_PRIVATE_EXTENSIONS = {".jks", ".key", ".keystore", ".p12", ".pfx", ".pkcs12"}
_BULK_EXTENSIONS = {
    ".bcf",
    ".csv",
    ".feather",
    ".obo",
    ".owl",
    ".parquet",
    ".rdf",
    ".tsv",
    ".vcf",
}
_ARCHIVE_EXTENSIONS = {
    ".7z",
    ".bz2",
    ".gz",
    ".rar",
    ".tar",
    ".tbz",
    ".tbz2",
    ".tgz",
    ".txz",
    ".xz",
    ".zip",
    ".zst",
}
_ONTOLOGY_EXTENSIONS = {".gaf", ".jsonld", ".nq", ".nt", ".rdf", ".trig", ".ttl"}
_DATA_WORD = re.compile(
    r"(?i)(database|dataset|corpus|ontology|cohort|reference|dump|full.?text|vcf|bcf)"
)
_BEGIN_TO_END = {
    b"-----BEGIN PRIVATE KEY-----": b"-----END PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----": b"-----END RSA PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----": b"-----END OPENSSH PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----": b"-----END EC PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----": b"-----END ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----": b"-----END DSA PRIVATE KEY-----",
}
_BASE64_LINE = re.compile(rb"[A-Za-z0-9+/]+={0,2}\Z")
_DEK_INFO = re.compile(rb"DEK-Info: [A-Z0-9-]{3,32},[0-9A-F]{16,64}\Z")
_PKCS7_OID_PREFIX = bytes.fromhex("2a864886f70d0107")


def looks_compressed(prefix: bytes) -> bool:
    """Return whether bytes carry a supported or fail-closed compressed signature."""
    return (
        prefix.startswith(b"\x1f\x8b")
        or (prefix.startswith(b"BZh") and prefix[3:4] in b"123456789")
        or prefix.startswith(b"\xfd7zXZ\x00")
        or prefix.startswith(b"\x28\xb5\x2f\xfd")
    )


def _credential_filename(parts: tuple[str, ...], name: str) -> bool:
    if name in {
        ".git-credentials",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "secrets",
        "id_ed25519",
        "id_rsa",
        "kubeconfig",
    }:
        return True
    suffix = PurePosixPath(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    if suffix in {".ini", ".json", ".toml", ".yaml", ".yml"}:
        if len(name.encode("utf-8")) > 512:
            return True
        words = tuple(word for word in re.split(r"[._-]+", stem.strip(".")) if word)
        if any(word in {"credential", "credentials", "secret", "secrets"} for word in words):
            return True
        if any(
            words[index : index + 2] == ("service", "account") for index in range(len(words) - 1)
        ):
            return True
    return name in {"config.json", "dockerconfigjson"} and ".docker" in parts


def denied_by_name(path: str) -> bool:
    pure = PurePosixPath(path)
    lower_parts = tuple(part.lower() for part in pure.parts)
    name = pure.name.lower()
    suffixes = tuple(suffix.lower() for suffix in pure.suffixes)
    if any(
        part
        in {
            ".git",
            ".github",
            ".idea",
            ".vscode",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "htmlcov",
        }
        for part in lower_parts
    ):
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if name in {"coverage.xml", ".coverage", "junit.xml", "test-results.xml"}:
        return True
    if _credential_filename(lower_parts, name):
        return True
    if any(
        part
        in {"data", "datasets", "dataset", "corpus", "corpora", "runtime-state", "runtime_state"}
        for part in lower_parts
    ):
        return True
    if pure.suffix.lower() in _DATABASE_EXTENSIONS | _BULK_EXTENSIONS | _ONTOLOGY_EXTENSIONS:
        return True
    if pure.suffix.lower() in _PRIVATE_EXTENSIONS:
        return True
    if any(suffix in {".vcf", ".bcf"} for suffix in suffixes):
        return True
    if pure.suffix.lower() in _ARCHIVE_EXTENSIONS and _DATA_WORD.search(name):
        return True
    if pure.suffix.lower() in {
        ".json",
        ".nt",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    } and _DATA_WORD.search(name):
        return True
    return name.endswith((".key", ".pem")) and any(word in name for word in ("private", "secret"))


def never_allow_by_name(path: str) -> bool:
    pure = PurePosixPath(path)
    parts = tuple(part.lower() for part in pure.parts)
    name = pure.name.lower()
    return (
        name == ".env"
        or name.startswith(".env.")
        or _credential_filename(parts, name)
        or any(
            part
            in {
                ".git",
                ".github",
                ".idea",
                ".vscode",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                ".mypy_cache",
            }
            for part in parts
        )
    )


def _tlv(data: bytes, offset: int) -> tuple[int, bytes, int] | None:
    header = _tlv_header(data, offset)
    if header is None:
        return None
    tag, start, length = header
    end = start + length
    return (tag, data[start:end], end) if end <= len(data) else None


def _tlv_header(data: bytes, offset: int) -> tuple[int, int, int] | None:
    if offset + 2 > len(data):
        return None
    tag, first = data[offset], data[offset + 1]
    if first < 128:
        length, start = first, offset + 2
    else:
        count = first & 0x7F
        if not 1 <= count <= 4 or offset + 2 + count > len(data):
            return None
        raw_length = data[offset + 2 : offset + 2 + count]
        if raw_length[0] == 0:
            return None
        length, start = int.from_bytes(raw_length, "big"), offset + 2 + count
        if length < 128:
            return None
    return tag, start, length


def _children(payload: bytes) -> list[tuple[int, bytes]] | None:
    result: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(payload):
        item = _tlv(payload, offset)
        if item is None or item[2] <= offset:
            return None
        result.append((item[0], item[1]))
        offset = item[2]
    return result


def der_private_key(data: bytes) -> bool:
    """Recognize traditional, PKCS#8, and encrypted PKCS#8 DER private keys."""
    outer = _tlv(data, 0)
    if outer is None or outer[0] != 0x30 or outer[2] != len(data):
        return False
    fields = _children(outer[1])
    if not fields:
        return False
    if fields[0][0] == 0x02 and fields[0][1] in {b"\0", b"\1"}:
        if len(fields) >= 5 and all(tag == 0x02 for tag, _ in fields):
            return True
        if len(fields) >= 2 and fields[0][1] == b"\1" and fields[1][0] == 0x04:
            return True
        if len(fields) >= 3 and fields[1][0] == 0x30 and fields[2][0] == 0x04:
            algorithm = _children(fields[1][1])
            return bool(algorithm and algorithm[0][0] == 0x06)
    if len(fields) >= 2 and fields[0][0] == 0x30 and fields[1][0] == 0x04:
        algorithm = _children(fields[0][1])
        return bool(algorithm and algorithm[0][0] == 0x06)
    return False


def _pkcs12_container(data: bytes) -> bool:
    """Recognize a PFX header without buffering the complete key store."""
    outer = _tlv_header(data, 0)
    if outer is None or outer[0] != 0x30:
        return False
    outer_end = outer[1] + outer[2]
    version = _tlv(data, outer[1])
    if version is None or version[0] != 0x02 or version[1] != b"\x03":
        return False
    content_info = _tlv_header(data, version[2])
    if content_info is None or content_info[0] != 0x30:
        return False
    content_end = content_info[1] + content_info[2]
    if content_end > outer_end:
        return False
    content_type = _tlv(data, content_info[1])
    return bool(
        content_type
        and content_type[0] == 0x06
        and content_type[2] <= content_end
        and content_type[1].startswith(_PKCS7_OID_PREFIX)
    )


def _key_store_magic(data: bytes) -> bool:
    return (
        len(data) >= 12
        and data.startswith(b"\xfe\xed\xfe\xed")
        and int.from_bytes(data[4:8], "big") in {1, 2}
    ) or _pkcs12_container(data)


def _archive_magic(data: bytes) -> bool:
    return data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")) or (
        len(data) >= 263 and data[257:263] in {b"ustar\0", b"ustar "}
    )


def _raw_forbidden(prefix: bytes) -> bool:
    stripped = prefix.lstrip(b"\xef\xbb\xbf \t\r\n")
    return (
        prefix.startswith(b"SQLite format 3\x00")
        or prefix.startswith(b"PAR1")
        or prefix.startswith(b"BCF\x02")
        or stripped.startswith(b"##fileformat=VCF")
        or der_private_key(prefix)
        or _key_store_magic(prefix)
        or _archive_magic(prefix)
    )


def _decompressed_prefix(payload: bytes, limit: int) -> bytes | None:
    try:
        if payload.startswith(b"\x1f\x8b"):
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(payload, limit)
        if payload.startswith(b"BZh"):
            return bz2.BZ2Decompressor().decompress(payload, max_length=limit)
        if payload.startswith(b"\xfd7zXZ\x00"):
            return lzma.LZMADecompressor().decompress(payload, max_length=limit)
        if payload.startswith(b"\x28\xb5\x2f\xfd"):
            return None
    except (OSError, EOFError, ValueError, zlib.error, lzma.LZMAError):
        return None
    return b""


def forbidden_payload_prefix(prefix: bytes, scan_limit: int) -> bool:
    """Classify magic directly or through a bounded compressed prefix."""
    if _raw_forbidden(prefix):
        return True
    if not looks_compressed(prefix):
        return False
    decompressed = _decompressed_prefix(prefix, scan_limit)
    return decompressed is None or _raw_forbidden(decompressed)


def classified_media(payload: bytes) -> str | None:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    if any(
        unicodedata.category(character) == "Cc" and character not in "\t\n\r" for character in text
    ):
        return None
    return "text/plain; charset=utf-8"


class PrivatePemScanner:
    """Recognize a syntactically valid private-key PEM envelope in bounded state."""

    _MAX_LINE = 8192

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._discard_line = False
        self._end: bytes | None = None
        self._body_chars = 0
        self._body_lines = 0
        self._body_invalid = False
        self._padded = False
        self._pem_header = False
        self._proc_type = False
        self._dek_info = False
        self.found = False

    def _reset(self) -> None:
        self._end = None
        self._body_chars = 0
        self._body_lines = 0
        self._body_invalid = False
        self._padded = False
        self._pem_header = False
        self._proc_type = False
        self._dek_info = False

    def _line(self, raw: bytes) -> None:
        line = raw.removesuffix(b"\r")
        if self._end is None:
            self._end = _BEGIN_TO_END.get(line)
            return
        if line == self._end:
            if not self._body_invalid and self._body_lines > 0 and self._body_chars % 4 == 0:
                self.found = True
            self._reset()
            return
        if line in _BEGIN_TO_END:
            self._reset()
            self._end = _BEGIN_TO_END[line]
            return
        if self._body_lines == 0 and not self._body_invalid:
            if line == b"Proc-Type: 4,ENCRYPTED" and not self._proc_type:
                self._pem_header = True
                self._proc_type = True
                return
            if _DEK_INFO.fullmatch(line) and self._proc_type and not self._dek_info:
                self._dek_info = True
                return
            if not line and self._pem_header:
                self._body_invalid = not (self._proc_type and self._dek_info)
                self._pem_header = False
                return
        valid = bool(line and _BASE64_LINE.fullmatch(line) and not self._padded)
        self._body_invalid |= not valid
        if valid:
            self._body_lines += 1
            self._body_chars += len(line)
            self._padded = line.endswith(b"=")

    def feed(self, chunk: bytes) -> None:
        if self.found:
            return
        for byte in chunk:
            if byte == 0x0A:
                if not self._discard_line:
                    self._line(bytes(self._buffer))
                self._buffer.clear()
                self._discard_line = False
            elif not self._discard_line:
                if len(self._buffer) >= self._MAX_LINE:
                    self._buffer.clear()
                    self._discard_line = True
                    self._body_invalid = self._end is not None
                else:
                    self._buffer.append(byte)

    def finish(self) -> bool:
        if self._buffer and not self._discard_line:
            self._line(bytes(self._buffer))
        self._buffer.clear()
        return self.found
