"""Field validators and regular-expression patterns shared by the release models.

Split out of `models.py` so the model definitions stay inside the repository's
600-line-per-module budget; the exported names are unchanged.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath


def require_exact_schema_version(value: object) -> object:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must be the integer 1")
    return value


def require_exact_false(value: object) -> object:
    if type(value) is not bool or value is not False:
        raise ValueError("reproducible_rollback must be the boolean false")
    return value


def require_exact_true(value: object) -> object:
    if type(value) is not bool or value is not True:
        raise ValueError("cache deletability must be the boolean true")
    return value


def require_rfc3339_string(value: object) -> object:
    if not isinstance(value, str) or not re.fullmatch(RFC3339_PATTERN, value):
        raise ValueError("timestamp must be an RFC3339 string with an explicit timezone")
    return value


def _has_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def require_normalized_relative_path(value: str) -> str:
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or _has_control_character(value)
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).as_posix() != value
    ):
        raise ValueError("path must be a normalized nonempty relative POSIX path")
    return value


def require_normalized_absolute_path(value: str) -> str:
    parts = value.split("/")[1:]
    if (
        value == "/"
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or _has_control_character(value)
        or any(part in {"", ".", ".."} for part in parts)
        or PurePosixPath(value).as_posix() != value
    ):
        raise ValueError("path must be a normalized absolute non-root POSIX path")
    return value


def require_local_http_path(value: str) -> str:
    require_normalized_absolute_path(value)
    if "?" in value or "#" in value:
        raise ValueError("local HTTP path must not contain a query or fragment")
    return value


def require_dns_endpoint(value: str) -> str:
    if (
        not value
        or _has_control_character(value)
        or any(character.isspace() for character in value)
    ):
        raise ValueError("egress entry must be a DNS hostname with an optional numeric port")
    if value.count(":") > 1:
        raise ValueError("egress entry must not use an IPv6 literal")
    hostname, separator, port_text = value.partition(":")
    if separator and (
        not port_text.isascii()
        or not port_text.isdigit()
        or not 1 <= int(port_text) <= 65535
        or port_text != str(int(port_text))
    ):
        raise ValueError("egress port must be an integer from 1 through 65535")
    labels = hostname.split(".")
    if len(hostname) > 253 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("egress entry must contain only explicit DNS hostname labels")
    return value


def require_immutable_data_release_tag(value: str) -> str:
    if value.lower() in {"latest", "main", "master", "head", "stable", "current"}:
        raise ValueError("data release tag must be immutable")
    return value


RELATIVE_PATH_PATTERN = (
    r"^(?!/)(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)"
    r"(?!.*[\u0000-\u001f\u007f])[^/]+(?:/[^/]+)*$"
)
ABSOLUTE_PATH_PATTERN = (
    r"^/(?!/)(?!$)(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)"
    r"(?!.*[\u0000-\u001f\u007f])[^/]+(?:/[^/]+)*$"
)
LOCAL_HTTP_PATH_PATTERN = (
    r"^/(?!/)(?!$)(?!\.{1,2}(?:/|$))(?!.*\/\.{1,2}(?:/|$))(?!.*//)(?!.*\\)"
    r"(?!.*[?#\u0000-\u001f\u007f])[^/?#]+(?:/[^/?#]+)*$"
)
DNS_LABEL_PATTERN = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
DNS_PORT_PATTERN = (
    r"(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
    r"65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])"
)
DNS_ENDPOINT_PATTERN = (
    rf"^(?:{DNS_LABEL_PATTERN})(?:\.(?:{DNS_LABEL_PATTERN}))*(?::{DNS_PORT_PATTERN})?$"
)
RFC3339_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
