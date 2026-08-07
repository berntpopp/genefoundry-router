"""Bounded data vocabulary for refresh observability."""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 2
EVENT_RETENTION_SECONDS = 14 * 24 * 60 * 60
PRUNE_INTERVAL_SECONDS = 60 * 60
REFRESH_HEARTBEAT_INTERVAL_SECONDS = 30
REFRESH_HEARTBEAT_STALE_SECONDS = 120
MAX_EVENT_ROWS = 100_000
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_WAL_BYTES = 8 * 1024 * 1024
MAX_LIFECYCLE_ROWS = 10_000
INTEGRITY_REASONS = frozenset({"write_failure", "wal_checkpoint", "wal_over_cap"})

CLIENT_CLASSES = frozenset({"chatgpt", "claude", "other"})
FAILURE_REASONS = frozenset(
    {
        "local_not_found",
        "local_rejected",
        "reuse_after_rotation",
        "overlapping_attempt",
        "client_mismatch",
        "jwt_invalid",
        "mapping_missing",
        "upstream_invalid_grant",
        "upstream_other",
        "internal_error",
    }
)
EVENT_TYPES = frozenset({"refresh", "authorize"})
EVENT_OUTCOMES = frozenset({"success", "failure", "started"})


class RefreshLedgerError(RuntimeError):
    """Base class for bounded refresh-ledger failures."""


class RefreshLedgerUnavailable(RefreshLedgerError):  # noqa: N818 - public task interface
    """The configured ledger cannot be opened or written safely."""


class RefreshLedgerCapacityError(RefreshLedgerError):
    """A configured durability cap was reached."""


@dataclass(frozen=True, slots=True)
class RefreshEvent:
    """One bounded refresh outcome or authorization-start marker."""

    at: float
    request_id: str
    client_class: str
    client_hmac: str
    event_type: str
    outcome: str
    reason: str | None = None
    token_hash_prefix: str | None = None
    reuse_delay_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    """Persisted aggregate counters used to restore Prometheus on startup."""

    attempts: dict[str, int]
    successes: dict[str, int]
    failures: dict[tuple[str, str], int]
