"""Input validation for the bounded refresh-observability vocabulary."""

from __future__ import annotations

import math
import re

from genefoundry_router.refresh_models import (
    CLIENT_CLASSES,
    EVENT_OUTCOMES,
    EVENT_TYPES,
    FAILURE_REASONS,
    RefreshEvent,
)

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_PREFIX = re.compile(r"^[0-9a-f]{1,16}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def validate_timestamp(value: float) -> None:
    """Reject non-finite and negative ledger timestamps."""
    if not math.isfinite(value) or value < 0:
        raise ValueError("refresh ledger timestamp must be finite and non-negative")


def validate_hash(value: str, label: str) -> None:
    """Require a complete lowercase SHA-256 hexadecimal value."""
    if not _HEX_64.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hexadecimal value")


def validate_lifecycle(version: str, at: float) -> None:
    """Validate aggregate router lifecycle metadata."""
    validate_timestamp(at)
    if not re.fullmatch(r"[A-Za-z0-9.+_-]{1,32}", version):
        raise ValueError("router lifecycle version is invalid or too long")


def validate_event(event: RefreshEvent) -> None:
    """Validate one event against every bounded field vocabulary."""
    validate_timestamp(event.at)
    if not _REQUEST_ID.fullmatch(event.request_id):
        raise ValueError("refresh event request ID is invalid or too long")
    if event.client_class not in CLIENT_CLASSES:
        raise ValueError("refresh event client class is not bounded")
    validate_hash(event.client_hmac, "client HMAC")
    if event.event_type not in EVENT_TYPES:
        raise ValueError("refresh event type is not bounded")
    if event.outcome not in EVENT_OUTCOMES:
        raise ValueError("refresh event outcome is not bounded")
    if event.event_type == "refresh" and event.outcome == "failure":
        if event.reason not in FAILURE_REASONS:
            raise ValueError("refresh event failure reason is not bounded")
    elif event.reason is not None:
        raise ValueError("non-failure refresh event must not have a reason")
    if event.token_hash_prefix is not None and not _HEX_PREFIX.fullmatch(event.token_hash_prefix):
        raise ValueError("refresh event token-hash prefix is invalid or too long")
    if event.reuse_delay_seconds is not None and (
        not math.isfinite(event.reuse_delay_seconds) or event.reuse_delay_seconds < 0
    ):
        raise ValueError("refresh event reuse delay must be finite and non-negative")
