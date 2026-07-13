"""Raw streaming tar structure validation before Python's metadata parser."""

from __future__ import annotations

import tarfile

from genefoundry_router.release.content_policy import ContentPolicyError

_EXTENDED_TYPES = {b"x", b"g", b"L", b"K"}
_ORDINARY_TYPES = {b"", b"0", b"1", b"2", b"3", b"4", b"5", b"6", b"7"}


class TarStructure:
    """Validate headers, file padding, logical EOF, and extended-record ceilings."""

    def __init__(self, metadata_limit: int) -> None:
        self._metadata_limit = metadata_limit
        self._buffer = bytearray()
        self._data_bytes = 0
        self._zero_headers = 0
        self._eof = False

    @staticmethod
    def _size(block: bytes) -> int:
        field = block[124:136]
        try:
            if field[0] & 0x80:
                value = int.from_bytes(bytes([field[0] & 0x7F]) + field[1:], "big")
            else:
                stripped = field.rstrip(b"\0 ").lstrip(b" ")
                value = int(stripped or b"0", 8)
        except ValueError as exc:
            raise ContentPolicyError("tar header size is invalid") from exc
        if value < 0:
            raise ContentPolicyError("tar header size is invalid")
        return value

    @staticmethod
    def _type(block: bytes) -> bytes:
        value = block[156:157]
        return b"" if value == b"\0" else value

    @staticmethod
    def _validate_raw_name(block: bytes, typeflag: bytes) -> None:
        if typeflag not in _ORDINARY_TYPES:
            return
        raw_name = block[:100].split(b"\0", 1)[0]
        if raw_name != b"./" and raw_name.startswith(b"./"):
            raise ContentPolicyError("unsafe archive path")

    def _header(self, block: bytes) -> None:
        typeflag = self._type(block)
        if typeflag not in _ORDINARY_TYPES | _EXTENDED_TYPES:
            # tarfile performs the final type decision; this pass only handles
            # allocation-bearing metadata extensions and raw structural facts.
            return
        self._validate_raw_name(block, typeflag)
        size = self._size(block)
        if typeflag in _EXTENDED_TYPES and size > self._metadata_limit:
            raise ContentPolicyError("extended tar metadata byte limit exceeded")
        self._data_bytes = size

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        while len(self._buffer) >= tarfile.BLOCKSIZE:
            block = bytes(self._buffer[: tarfile.BLOCKSIZE])
            del self._buffer[: tarfile.BLOCKSIZE]
            if self._eof:
                if any(block):
                    raise ContentPolicyError("nonzero bytes follow logical tar EOF")
                continue
            if self._data_bytes:
                payload_bytes = min(self._data_bytes, tarfile.BLOCKSIZE)
                if payload_bytes < tarfile.BLOCKSIZE and any(block[payload_bytes:]):
                    raise ContentPolicyError("nonzero tar file alignment padding")
                self._data_bytes -= payload_bytes
                continue
            if not any(block):
                self._zero_headers += 1
                if self._zero_headers == 2:
                    self._eof = True
                continue
            if self._zero_headers:
                raise ContentPolicyError("tar EOF marker is malformed")
            self._header(block)

    def finish(self) -> None:
        if self._buffer or self._data_bytes or not self._eof:
            raise ContentPolicyError("nonzero or unaligned bytes follow logical tar EOF")
