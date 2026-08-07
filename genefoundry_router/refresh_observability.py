"""Durable, aggregate-only observability for OAuth refresh rotation.

The SQLite ledger is the durability authority.  It deliberately stores no token
or token response: only a full one-way token hash in short-lived tombstones, a
bounded prefix in event rows, and HMAC-derived client identities.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from genefoundry_router.refresh_report import RefreshReport, build_refresh_report

SCHEMA_VERSION = 1
EVENT_RETENTION_SECONDS = 14 * 24 * 60 * 60
PRUNE_INTERVAL_SECONDS = 60 * 60
MAX_EVENT_ROWS = 100_000
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_WAL_BYTES = 8 * 1024 * 1024
MAX_LIFECYCLE_ROWS = 10_000

CLIENT_CLASSES = frozenset({"chatgpt", "claude", "other"})
FAILURE_REASONS = frozenset(
    {
        "local_not_found",
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

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_PREFIX = re.compile(r"^[0-9a-f]{1,16}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


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


class RefreshLedger:
    """Bounded SQLite refresh ledger with fail-closed initialization."""

    def __init__(
        self,
        path: str | Path,
        *,
        hmac_key: str | bytes,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock or time.time
        self._hmac_key = hmac_key.encode() if isinstance(hmac_key, str) else hmac_key
        if not self._hmac_key:
            raise ValueError("refresh ledger HMAC key must not be empty")
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

        try:
            created = self._prepare_path()
            self._check_file_capacity()
            self._connection = sqlite3.connect(
                self.path,
                timeout=5.0,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._configure_sqlite()
            self._initialize_schema(created=created)
            self._prune(now=self._clock(), force=True)
            self._secure_sqlite_files()
        except RefreshLedgerError:
            self._close_after_failed_init()
            raise
        except (OSError, sqlite3.Error) as exc:
            self._close_after_failed_init()
            raise RefreshLedgerUnavailable("configured refresh ledger is unavailable") from exc

    @property
    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RefreshLedgerUnavailable("refresh ledger is closed")
        return self._connection

    def _prepare_path(self) -> bool:
        """Create a new mode-0600 file, or validate an existing regular file."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            existing = self.path.lstat()
        except FileNotFoundError:
            flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.path, flags, 0o600)
            os.close(descriptor)
            return True

        if not stat.S_ISREG(existing.st_mode):
            raise RefreshLedgerUnavailable("configured refresh ledger must be a regular file")
        owner_access = stat.S_IRUSR | stat.S_IWUSR
        if existing.st_mode & owner_access != owner_access:
            raise RefreshLedgerUnavailable(
                "configured refresh ledger must be readable and writable by its owner"
            )
        if existing.st_size == 0:
            raise RefreshLedgerUnavailable("configured refresh ledger is empty")
        os.chmod(self.path, 0o600, follow_symlinks=False)
        return False

    def _configure_sqlite(self) -> None:
        journal_mode = str(self._db.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        if journal_mode != "wal":
            raise RefreshLedgerUnavailable("configured refresh ledger cannot enable WAL mode")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute(f"PRAGMA journal_size_limit={MAX_WAL_BYTES}")
        page_size = int(self._db.execute("PRAGMA page_size").fetchone()[0])
        max_pages = MAX_DATABASE_BYTES // page_size
        self._db.execute(f"PRAGMA max_page_count={max_pages}")
        # Keep automatic checkpoints below the byte cap, including WAL frame headers.
        wal_frames = max(1, (MAX_WAL_BYTES - 32) // (page_size + 24))
        self._db.execute(f"PRAGMA wal_autocheckpoint={wal_frames}")

    def _initialize_schema(self, *, created: bool) -> None:
        version = int(self._db.execute("PRAGMA user_version").fetchone()[0])
        if version not in (0, SCHEMA_VERSION):
            raise RefreshLedgerUnavailable("refresh ledger schema version is unsupported")
        if version == 0 and not created:
            raise RefreshLedgerUnavailable("configured refresh ledger has no recognized schema")
        if version == SCHEMA_VERSION:
            return

        with self._db:
            self._db.execute(
                "CREATE TABLE refresh_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._db.execute(
                """
                CREATE TABLE refresh_counters (
                    counter TEXT NOT NULL,
                    client_class TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    value INTEGER NOT NULL CHECK(value >= 0),
                    PRIMARY KEY (counter, client_class, reason),
                    CHECK(client_class IN ('chatgpt', 'claude', 'other'))
                )
                """
            )
            self._db.execute(
                """
                CREATE TABLE refresh_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL,
                    request_id TEXT NOT NULL CHECK(length(request_id) <= 128),
                    client_class TEXT NOT NULL,
                    client_hmac TEXT NOT NULL CHECK(length(client_hmac) = 64),
                    event_type TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reason TEXT,
                    token_hash_prefix TEXT CHECK(
                        token_hash_prefix IS NULL OR length(token_hash_prefix) <= 16
                    ),
                    reuse_delay_seconds REAL,
                    CHECK(client_class IN ('chatgpt', 'claude', 'other')),
                    CHECK(event_type IN ('refresh', 'authorize')),
                    CHECK(outcome IN ('success', 'failure', 'started'))
                )
                """
            )
            self._db.execute("CREATE INDEX refresh_events_at_idx ON refresh_events(at)")
            self._db.execute(
                "CREATE INDEX refresh_events_client_at_idx ON refresh_events(client_hmac, at)"
            )
            self._db.execute(
                """
                CREATE TABLE refresh_tombstones (
                    token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64),
                    client_hmac TEXT NOT NULL CHECK(length(client_hmac) = 64),
                    rotated_at REAL NOT NULL
                )
                """
            )
            self._db.execute(
                "CREATE INDEX refresh_tombstones_at_idx ON refresh_tombstones(rotated_at)"
            )
            self._db.execute(
                """
                CREATE TABLE router_lifecycle (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    boot_id TEXT NOT NULL,
                    marker TEXT NOT NULL CHECK(marker IN ('startup', 'shutdown')),
                    at REAL NOT NULL,
                    version TEXT NOT NULL CHECK(length(version) <= 32)
                )
                """
            )
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def client_hmac(self, raw_client_id: str) -> str:
        """Return a stable keyed identity without retaining the raw client ID."""
        return hmac.new(self._hmac_key, raw_client_id.encode(), hashlib.sha256).hexdigest()

    def record_event(self, event: RefreshEvent) -> None:
        """Persist one event and its aggregate counters in one transaction."""
        self._validate_event(event)
        with self._lock:
            self._check_file_capacity()
            self._prune(now=self._clock(), force=False)
            try:
                with self._db:
                    self._db.execute(
                        """
                        INSERT INTO refresh_events (
                            at, request_id, client_class, client_hmac, event_type,
                            outcome, reason, token_hash_prefix, reuse_delay_seconds
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.at,
                            event.request_id,
                            event.client_class,
                            event.client_hmac,
                            event.event_type,
                            event.outcome,
                            event.reason,
                            event.token_hash_prefix,
                            event.reuse_delay_seconds,
                        ),
                    )
                    if event.event_type == "refresh":
                        self._increment_counter("attempt", event.client_class, "")
                        if event.outcome == "success":
                            self._increment_counter("success", event.client_class, "")
                        elif event.outcome == "failure" and event.reason is not None:
                            self._increment_counter("failure", event.client_class, event.reason)
                    self._enforce_event_row_cap()
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            self._after_write()

    def _increment_counter(self, counter: str, client_class: str, reason: str) -> None:
        self._db.execute(
            """
            INSERT INTO refresh_counters (counter, client_class, reason, value)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(counter, client_class, reason)
            DO UPDATE SET value = value + 1
            """,
            (counter, client_class, reason),
        )

    def counter_snapshot(self) -> CounterSnapshot:
        """Read durable aggregate counters without exposing event identities."""
        attempts: dict[str, int] = {}
        successes: dict[str, int] = {}
        failures: dict[tuple[str, str], int] = {}
        with self._lock:
            rows = self._db.execute(
                "SELECT counter, client_class, reason, value FROM refresh_counters"
            ).fetchall()
        for row in rows:
            counter = str(row["counter"])
            client_class = str(row["client_class"])
            value = int(row["value"])
            if counter == "attempt":
                attempts[client_class] = value
            elif counter == "success":
                successes[client_class] = value
            elif counter == "failure":
                failures[(client_class, str(row["reason"]))] = value
        return CounterSnapshot(attempts, successes, failures)

    def record_rotation(self, token_hash: str, client_hmac: str, at: float) -> None:
        """Store a short-lived full-hash tombstone after successful rotation."""
        self._validate_hash(token_hash, "token hash")
        self._validate_hash(client_hmac, "client HMAC")
        self._validate_timestamp(at)
        with self._lock:
            self._check_file_capacity()
            self._prune(now=self._clock(), force=False)
            try:
                with self._db:
                    self._db.execute(
                        """
                        INSERT INTO refresh_tombstones (token_hash, client_hmac, rotated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(token_hash) DO UPDATE SET
                            client_hmac=excluded.client_hmac,
                            rotated_at=excluded.rotated_at
                        """,
                        (token_hash, client_hmac, at),
                    )
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            self._after_write()

    def classify_missing(self, token_hash: str, client_hmac: str, at: float) -> str:
        """Classify a local miss using only bounded rotation tombstones."""
        self._validate_hash(token_hash, "token hash")
        self._validate_hash(client_hmac, "client HMAC")
        self._validate_timestamp(at)
        with self._lock:
            row = self._db.execute(
                "SELECT client_hmac FROM refresh_tombstones WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None:
            return "local_not_found"
        if not hmac.compare_digest(str(row["client_hmac"]), client_hmac):
            return "client_mismatch"
        return "reuse_after_rotation"

    def reuse_delay_seconds(self, token_hash: str, at: float) -> float | None:
        """Return a reuse delay for an existing tombstone, never its stored identity."""
        self._validate_hash(token_hash, "token hash")
        self._validate_timestamp(at)
        with self._lock:
            row = self._db.execute(
                "SELECT rotated_at FROM refresh_tombstones WHERE token_hash=?", (token_hash,)
            ).fetchone()
        return None if row is None else max(0.0, at - float(row["rotated_at"]))

    def record_startup(self, *, version: str, at: float) -> str:
        """Record one router startup and return its non-client boot identifier."""
        self._validate_lifecycle(version, at)
        boot_id = secrets.token_hex(16)
        with self._lock:
            try:
                with self._db:
                    self._db.execute(
                        """
                        INSERT INTO router_lifecycle (boot_id, marker, at, version)
                        VALUES (?, 'startup', ?, ?)
                        """,
                        (boot_id, at, version),
                    )
                    self._enforce_lifecycle_cap()
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            self._after_write()
        return boot_id

    def record_shutdown(self, boot_id: str, *, version: str, at: float) -> None:
        """Record a clean shutdown for an existing startup marker."""
        self._validate_lifecycle(version, at)
        if not re.fullmatch(r"[0-9a-f]{32}", boot_id):
            raise ValueError("router boot ID is invalid")
        with self._lock:
            startup = self._db.execute(
                """
                SELECT at FROM router_lifecycle
                WHERE boot_id=? AND marker='startup'
                """,
                (boot_id,),
            ).fetchone()
            shutdown = self._db.execute(
                """
                SELECT 1 FROM router_lifecycle
                WHERE boot_id=? AND marker='shutdown'
                """,
                (boot_id,),
            ).fetchone()
            if startup is None or shutdown is not None or at < float(startup["at"]):
                raise ValueError("router shutdown marker does not match an open startup")
            try:
                with self._db:
                    self._db.execute(
                        """
                        INSERT INTO router_lifecycle (boot_id, marker, at, version)
                        VALUES (?, 'shutdown', ?, ?)
                        """,
                        (boot_id, at, version),
                    )
                    self._enforce_lifecycle_cap()
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            self._after_write()

    def report(self, now: float) -> RefreshReport:
        """Return aggregate-only measurements and the exact materiality decision."""
        self._validate_timestamp(now)
        with self._lock:
            return build_refresh_report(
                self._db,
                now=now,
                schema_version=SCHEMA_VERSION,
                client_classes=CLIENT_CLASSES,
                failure_reasons=FAILURE_REASONS,
            )

    @classmethod
    def _validate_lifecycle(cls, cls_version: str, at: float) -> None:
        cls._validate_timestamp(at)
        if not re.fullmatch(r"[A-Za-z0-9.+_-]{1,32}", cls_version):
            raise ValueError("router lifecycle version is invalid or too long")

    def _validate_event(self, event: RefreshEvent) -> None:
        self._validate_timestamp(event.at)
        if not _REQUEST_ID.fullmatch(event.request_id):
            raise ValueError("refresh event request ID is invalid or too long")
        if event.client_class not in CLIENT_CLASSES:
            raise ValueError("refresh event client class is not bounded")
        self._validate_hash(event.client_hmac, "client HMAC")
        if event.event_type not in EVENT_TYPES:
            raise ValueError("refresh event type is not bounded")
        if event.outcome not in EVENT_OUTCOMES:
            raise ValueError("refresh event outcome is not bounded")
        if event.event_type == "refresh" and event.outcome == "failure":
            if event.reason not in FAILURE_REASONS:
                raise ValueError("refresh event failure reason is not bounded")
        elif event.reason is not None:
            raise ValueError("non-failure refresh event must not have a reason")
        if event.token_hash_prefix is not None and not _HEX_PREFIX.fullmatch(
            event.token_hash_prefix
        ):
            raise ValueError("refresh event token-hash prefix is invalid or too long")
        if event.reuse_delay_seconds is not None and (
            not math.isfinite(event.reuse_delay_seconds) or event.reuse_delay_seconds < 0
        ):
            raise ValueError("refresh event reuse delay must be finite and non-negative")

    @staticmethod
    def _validate_timestamp(value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError("refresh ledger timestamp must be finite and non-negative")

    @staticmethod
    def _validate_hash(value: str, label: str) -> None:
        if not _HEX_64.fullmatch(value):
            raise ValueError(f"{label} must be a lowercase SHA-256 hexadecimal value")

    def _prune(self, *, now: float, force: bool) -> None:
        self._validate_timestamp(now)
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM refresh_meta WHERE key = 'last_prune_at'"
            ).fetchone()
            last_prune = float(row["value"]) if row is not None else None
            if not force and last_prune is not None and now - last_prune < PRUNE_INTERVAL_SECONDS:
                return
            cutoff = now - EVENT_RETENTION_SECONDS
            try:
                with self._db:
                    self._db.execute("DELETE FROM refresh_events WHERE at < ?", (cutoff,))
                    self._db.execute(
                        "DELETE FROM refresh_tombstones WHERE rotated_at < ?", (cutoff,)
                    )
                    self._enforce_event_row_cap()
                    self._db.execute(
                        """
                        INSERT INTO refresh_meta (key, value) VALUES ('last_prune_at', ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value
                        """,
                        (repr(now),),
                    )
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            # Remove obsolete WAL frames so expired full hashes are not retained in
            # recoverable journal bytes after their logical tombstone is deleted.
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _enforce_event_row_cap(self) -> None:
        excess = int(self._db.execute("SELECT COUNT(*) FROM refresh_events").fetchone()[0])
        excess -= MAX_EVENT_ROWS
        if excess > 0:
            self._db.execute(
                """
                DELETE FROM refresh_events WHERE id IN (
                    SELECT id FROM refresh_events ORDER BY id LIMIT ?
                )
                """,
                (excess,),
            )

    def _enforce_lifecycle_cap(self) -> None:
        excess = int(self._db.execute("SELECT COUNT(*) FROM router_lifecycle").fetchone()[0])
        excess -= MAX_LIFECYCLE_ROWS
        if excess > 0:
            self._db.execute(
                """
                DELETE FROM router_lifecycle WHERE id IN (
                    SELECT id FROM router_lifecycle ORDER BY id LIMIT ?
                )
                """,
                (excess,),
            )

    def _check_file_capacity(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_DATABASE_BYTES:
                raise RefreshLedgerCapacityError("refresh ledger exceeds the 64 MiB database cap")
            wal_path = Path(f"{self.path}-wal")
            if wal_path.exists() and wal_path.stat().st_size > MAX_WAL_BYTES:
                raise RefreshLedgerCapacityError("refresh ledger exceeds the 8 MiB WAL cap")
        except OSError as exc:
            raise RefreshLedgerUnavailable("configured refresh ledger cannot be inspected") from exc

    def _after_write(self) -> None:
        self._secure_sqlite_files()
        wal_path = Path(f"{self.path}-wal")
        if wal_path.exists() and wal_path.stat().st_size > MAX_WAL_BYTES:
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._check_file_capacity()

    @staticmethod
    def _bounded_sqlite_error(exc: sqlite3.Error) -> RefreshLedgerError:
        text = str(exc).lower()
        if "full" in text or "too big" in text:
            return RefreshLedgerCapacityError("refresh ledger reached its configured size cap")
        return RefreshLedgerUnavailable("refresh ledger write failed")

    def _secure_sqlite_files(self) -> None:
        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(mode):
                raise RefreshLedgerUnavailable("refresh ledger SQLite file is not regular")
            os.chmod(path, 0o600, follow_symlinks=False)

    def _close_after_failed_init(self) -> None:
        if self._connection is not None:
            with suppress(sqlite3.Error):
                self._connection.close()
            self._connection = None

    def close(self) -> None:
        """Checkpoint and close the ledger without deleting durable state."""
        with self._lock:
            if self._connection is None:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._secure_sqlite_files()
            finally:
                self._connection.close()
                self._connection = None
