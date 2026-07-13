"""Bounded, low-noise inspection of OCI image configuration metadata."""

from __future__ import annotations

import base64
import contextlib
import re

from genefoundry_router.release.content_diagnostics import FindingBuffer
from genefoundry_router.release.content_policy import ContentPolicyError
from genefoundry_router.release.content_secrets import secret_shaped

_MAX_ITEMS = 4096
_MAX_STRING_BYTES = 64 * 1024
_COMMAND_SECRET = re.compile(
    r"(?ix)(?:^|[\s;'\"])(?:[a-z0-9]+[_-])*(?:api[_-]?key|token|secret|password|"
    r"passwd|credential|authorization|private[_-]?key)(?:[_-][a-z0-9]+)*\s*(?:=|:)|"
    r"--(?:api-key|token|secret|password|passwd|credential|authorization)(?:\s|=|$)"
)
_HEALTHCHECK_KEYS = {
    "Test",
    "Interval",
    "Timeout",
    "StartPeriod",
    "StartInterval",
    "Retries",
}


def _bounded_string(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8", "surrogatepass")) > _MAX_STRING_BYTES
    ):
        raise ContentPolicyError(f"{label} must be a bounded string")
    return value


def _string_array(value: object, label: str, *, nullable: bool = False) -> list[str] | None:
    if nullable and value is None:
        return None
    if not isinstance(value, list) or len(value) > _MAX_ITEMS:
        raise ContentPolicyError(f"{label} must be a bounded string array")
    return [_bounded_string(item, label) for item in value]


def _command_secret_shaped(value: str) -> bool:
    if secret_shaped(value, semantic_words=False) or _COMMAND_SECRET.search(value):
        return True
    compact = "".join(value.split())
    if len(compact) >= 12:
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            decoded = base64.b64decode(compact, validate=True).decode("utf-8", "strict")
            return secret_shaped(decoded, semantic_words=False) or bool(
                _COMMAND_SECRET.search(decoded)
            )
    return False


def add_annotation_findings(value: object, label: str, findings: FindingBuffer) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or len(value) > _MAX_ITEMS:
        raise ContentPolicyError(f"{label} annotations must contain bounded strings")
    items: list[tuple[str, str]] = []
    for key, item in value.items():
        items.append(
            (
                _bounded_string(key, f"{label} annotation key"),
                _bounded_string(item, f"{label} annotations value"),
            )
        )
    for index, (key, item) in enumerate(sorted(items)):
        if secret_shaped(key) or secret_shaped(item):
            findings.add(f"{label}.annotations[{index}]: secret-shaped value")


def _validate_healthcheck(value: object, findings: FindingBuffer) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - _HEALTHCHECK_KEYS or "Test" not in value:
        raise ContentPolicyError("config.Healthcheck has invalid keys")
    test = _string_array(value["Test"], "config.Healthcheck.Test")
    assert test is not None
    if not test:
        raise ContentPolicyError("config.Healthcheck.Test may not be empty")
    for field in ("Interval", "Timeout", "StartPeriod", "StartInterval", "Retries"):
        item = value.get(field)
        if item is not None and (type(item) is not int or item < 0):
            raise ContentPolicyError(f"config.Healthcheck.{field} must be a nonnegative integer")
    if any(_command_secret_shaped(item) for item in test):
        findings.add("config.Healthcheck.Test: secret-shaped value")


def inspect_config(
    config: object, count_limit: int, byte_limit: int
) -> tuple[tuple[str, ...], bool]:
    if not isinstance(config, dict):
        raise ContentPolicyError("image config must be an object")
    runtime = config.get("config")
    if not isinstance(runtime, dict):
        raise ContentPolicyError("image config.config must be an object")
    findings = FindingBuffer(count_limit, byte_limit)
    user = runtime.get("User")
    principal = user.strip().lower().partition(":")[0] if isinstance(user, str) else ""
    root_principal = principal == "root" or (principal.isdecimal() and int(principal) == 0)
    if not isinstance(user, str) or not principal or root_principal:
        findings.add("config.User: root or empty user")

    env = _string_array(runtime.get("Env", []), "config.Env")
    assert env is not None
    for index, item in enumerate(env):
        key, _, value = item.partition("=")
        if secret_shaped(key.strip()) or secret_shaped(value):
            findings.add(f"config.Env[{index}]: secret-shaped value")
    for field in ("Entrypoint", "Cmd"):
        values = _string_array(runtime.get(field), f"config.{field}", nullable=True)
        if values is not None and _command_secret_shaped(" ".join(values)):
            findings.add(f"config.{field}: secret-shaped value")
    on_build = _string_array(runtime.get("OnBuild", []), "config.OnBuild")
    assert on_build is not None
    if any(_command_secret_shaped(item) for item in on_build):
        findings.add("config.OnBuild: secret-shaped value")
    _validate_healthcheck(runtime.get("Healthcheck"), findings)

    labels = runtime.get("Labels", {})
    if labels is None:
        labels = {}
    if not isinstance(labels, dict) or len(labels) > _MAX_ITEMS:
        raise ContentPolicyError("config.Labels must be a bounded object or null")
    label_items: list[tuple[str, str]] = []
    for key, value in labels.items():
        label_items.append(
            (
                _bounded_string(key, "config.Labels key"),
                _bounded_string(value, "config.Labels value"),
            )
        )
    for index, (key, value) in enumerate(sorted(label_items)):
        if secret_shaped(key) or secret_shaped(value):
            findings.add(f"config.Labels[{index}]: secret-shaped value")

    history = config.get("history", [])
    if not isinstance(history, list) or len(history) > _MAX_ITEMS:
        raise ContentPolicyError("image history must be a bounded array")
    for index, item in enumerate(history):
        if not isinstance(item, dict):
            raise ContentPolicyError("image history entries must be objects")
        created_by = item.get("created_by")
        comment = item.get("comment")
        for history_value in (created_by, comment):
            if history_value is not None:
                _bounded_string(history_value, "image history value")
        if (isinstance(created_by, str) and _command_secret_shaped(created_by)) or (
            isinstance(comment, str) and _command_secret_shaped(comment)
        ):
            findings.add(f"history[{index}]: secret-shaped value")
    return findings.values, findings.truncated
