"""Durable, aggregate-only OAuth refresh observability.

The SQLite authority stores no token or response, only bounded one-way identities.
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from genefoundry_router.refresh_lifecycle import RefreshLifecycleMixin
from genefoundry_router.refresh_models import (
    CLIENT_CLASSES,
    EVENT_RETENTION_SECONDS,
    FAILURE_REASONS,
    INTEGRITY_REASONS,
    MAX_DATABASE_BYTES,
    MAX_EVENT_ROWS,
    MAX_WAL_BYTES,
    PRUNE_INTERVAL_SECONDS,
    REFRESH_ATTEMPT_STALE_SECONDS,
    REFRESH_HEARTBEAT_STALE_SECONDS,
    SCHEMA_VERSION,
    CounterSnapshot,
    RefreshEvent,
    RefreshLedgerCapacityError,
    RefreshLedgerError,
    RefreshLedgerUnavailable,
)
from genefoundry_router.refresh_path import (
    UnsafeRefreshPathError,
    prepare_refresh_sqlite_path,
    secure_refresh_sqlite_files,
)
from genefoundry_router.refresh_reconcile import reconcile_stale_attempts as reconcile_stale
from genefoundry_router.refresh_report import RefreshReport, build_refresh_report
from genefoundry_router.refresh_schema import (
    RefreshSchemaError,
    configure_sqlite,
    initialize_schema,
)
from genefoundry_router.refresh_validation import (
    validate_event,
    validate_hash,
    validate_timestamp,
)


class RefreshLedger(RefreshLifecycleMixin):
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
        self._pending_gap: tuple[float, str] | None = None
        self._checkpoint_pending = False

        try:
            self.path, created = prepare_refresh_sqlite_path(self.path)
            self._check_database_capacity()
            self._connection = sqlite3.connect(
                self.path,
                timeout=5.0,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            configure_sqlite(
                self._db,
                max_database_bytes=MAX_DATABASE_BYTES,
                max_wal_bytes=MAX_WAL_BYTES,
            )
            self._check_file_capacity()
            initialize_schema(
                self._db,
                created=created,
                schema_version=SCHEMA_VERSION,
            )
            self._prune(now=self._clock(), force=True)
            self.heartbeat(self._clock())
            secure_refresh_sqlite_files(self.path)
        except RefreshLedgerError:
            self._close_after_failed_init()
            raise
        except UnsafeRefreshPathError as exc:
            self._close_after_failed_init()
            raise RefreshLedgerUnavailable(str(exc)) from exc
        except RefreshSchemaError as exc:
            self._close_after_failed_init()
            raise RefreshLedgerUnavailable(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            self._close_after_failed_init()
            raise RefreshLedgerUnavailable("configured refresh ledger is unavailable") from exc

    @property
    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RefreshLedgerUnavailable("refresh ledger is closed")
        return self._connection

    def client_hmac(self, raw_client_id: str) -> str:
        """Return a stable keyed identity without retaining the raw client ID."""
        return hmac.new(self._hmac_key, raw_client_id.encode(), hashlib.sha256).hexdigest()

    def note_unavailable(self, *, at: float, reason: str) -> None:
        """Best-effort an observation gap without replacing the OAuth operation."""
        validate_timestamp(at)
        if reason not in INTEGRITY_REASONS:
            raise ValueError("refresh availability reason is not bounded")
        with self._lock:
            try:
                open_gap = self._db.execute(
                    "SELECT 1 FROM refresh_availability_gaps WHERE ended_at IS NULL LIMIT 1"
                ).fetchone()
                if open_gap is None:
                    with self._db:
                        self._db.execute(
                            """
                            INSERT INTO refresh_availability_gaps
                                (started_at, ended_at, reason)
                            VALUES (?, NULL, ?)
                            """,
                            (at, reason),
                        )
                self._pending_gap = None
            except (OSError, sqlite3.Error, RefreshLedgerError):
                if self._pending_gap is None or at < self._pending_gap[0]:
                    self._pending_gap = (at, reason)

    def _mark_available(self, at: float, *, heartbeat: bool = False) -> bool:
        if self._checkpoint_pending:
            return False
        with self._lock:
            try:
                with self._db:
                    open_gap = self._db.execute(
                        """
                        SELECT id FROM refresh_availability_gaps
                        WHERE ended_at IS NULL ORDER BY id LIMIT 1
                        """
                    ).fetchone()
                    if open_gap is not None:
                        self._db.execute(
                            "UPDATE refresh_availability_gaps SET ended_at=? WHERE id=?",
                            (at, int(open_gap["id"])),
                        )
                    elif self._pending_gap is not None:
                        started_at, reason = self._pending_gap
                        self._db.execute(
                            """
                            INSERT INTO refresh_availability_gaps
                                (started_at, ended_at, reason)
                            VALUES (?, ?, ?)
                            """,
                            (started_at, max(started_at, at), reason),
                        )
                    if heartbeat:
                        self._db.execute(
                            """
                            INSERT INTO refresh_meta (key, value)
                            VALUES ('observer_heartbeat_at', ?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value
                            """,
                            (repr(at),),
                        )
                self._pending_gap = None
                return True
            except (OSError, sqlite3.Error, RefreshLedgerError):
                return False

    def heartbeat(self, at: float) -> bool:
        """Persist writer freshness and recover pending gaps without OAuth coupling."""
        validate_timestamp(at)
        with self._lock:
            try:
                reconcile_stale(self._db, now=at, stale_seconds=REFRESH_ATTEMPT_STALE_SECONDS)
            except (OSError, sqlite3.Error, RefreshLedgerError):
                self.note_unavailable(at=at, reason="write_failure")
                return False
        if self._mark_available(at, heartbeat=True):
            self._after_write()
            return True
        self.note_unavailable(at=at, reason="write_failure")
        return False

    def record_event(self, event: RefreshEvent) -> None:
        """Persist one event and its aggregate counters in one transaction."""
        validate_event(event)
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
            self._mark_available(self._clock())

    def begin_attempt(self, event: RefreshEvent) -> int:
        """Persist one in-progress refresh and count its denominator exactly once."""
        validate_event(event)
        if event.event_type != "refresh" or event.outcome != "started":
            raise ValueError("refresh attempt start must be a started refresh event")
        with self._lock:
            self._check_file_capacity()
            self._prune(now=self._clock(), force=False)
            try:
                with self._db:
                    cursor = self._db.execute(
                        """
                        INSERT INTO refresh_events (
                            at, request_id, client_class, client_hmac, event_type,
                            outcome, reason, token_hash_prefix, reuse_delay_seconds
                        ) VALUES (?, ?, ?, ?, 'refresh', 'started', NULL, ?, NULL)
                        """,
                        (
                            event.at,
                            event.request_id,
                            event.client_class,
                            event.client_hmac,
                            event.token_hash_prefix,
                        ),
                    )
                    self._increment_counter("attempt", event.client_class, "")
                    self._enforce_event_row_cap()
                    if cursor.lastrowid is None:
                        raise RefreshLedgerUnavailable("started refresh observation is unavailable")
                    event_id = cursor.lastrowid
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            self._after_write()
            self._mark_available(self._clock())
            return event_id

    def finish_attempt(
        self,
        event_id: int,
        *,
        outcome: str,
        reason: str | None = None,
        reuse_delay_seconds: float | None = None,
    ) -> None:
        """Finalize one started refresh row without incrementing its attempt twice."""
        if outcome not in {"success", "failure"}:
            raise ValueError("refresh attempt outcome must be terminal")
        probe = RefreshEvent(
            at=self._clock(),
            request_id="_unknown",
            client_class="other",
            client_hmac="0" * 64,
            event_type="refresh",
            outcome=outcome,
            reason=reason,
            reuse_delay_seconds=reuse_delay_seconds,
        )
        validate_event(probe)
        with self._lock:
            try:
                with self._db:
                    row = self._db.execute(
                        """
                        SELECT client_class FROM refresh_events
                        WHERE id=? AND event_type='refresh' AND outcome='started'
                        """,
                        (event_id,),
                    ).fetchone()
                    if row is None:
                        raise RefreshLedgerUnavailable("started refresh observation is unavailable")
                    self._db.execute(
                        """
                        UPDATE refresh_events
                        SET outcome=?, reason=?, reuse_delay_seconds=?
                        WHERE id=?
                        """,
                        (outcome, reason, reuse_delay_seconds, event_id),
                    )
                    client_class = str(row["client_class"])
                    if outcome == "success":
                        self._increment_counter("success", client_class, "")
                    else:
                        assert reason is not None
                        self._increment_counter("failure", client_class, reason)
            except RefreshLedgerError:
                raise
            except sqlite3.Error as exc:
                raise self._bounded_sqlite_error(exc) from exc
            self._after_write()
            self._mark_available(self._clock())

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
        validate_hash(token_hash, "token hash")
        validate_hash(client_hmac, "client HMAC")
        validate_timestamp(at)
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
            self._mark_available(self._clock())

    def classify_missing(self, token_hash: str, client_hmac: str, at: float) -> str:
        """Classify a local miss using only bounded rotation tombstones."""
        validate_hash(token_hash, "token hash")
        validate_hash(client_hmac, "client HMAC")
        validate_timestamp(at)
        with self._lock:
            row = self._db.execute(
                """
                SELECT client_hmac FROM refresh_tombstones
                WHERE token_hash = ? AND rotated_at >= ? AND rotated_at <= ?
                """,
                (token_hash, at - EVENT_RETENTION_SECONDS, at),
            ).fetchone()
        if row is None:
            return "local_not_found"
        if not hmac.compare_digest(str(row["client_hmac"]), client_hmac):
            return "client_mismatch"
        return "reuse_after_rotation"

    def reuse_delay_seconds(self, token_hash: str, at: float) -> float | None:
        """Return a reuse delay for an existing tombstone, never its stored identity."""
        validate_hash(token_hash, "token hash")
        validate_timestamp(at)
        with self._lock:
            row = self._db.execute(
                """
                SELECT rotated_at FROM refresh_tombstones
                WHERE token_hash=? AND rotated_at >= ? AND rotated_at <= ?
                """,
                (token_hash, at - EVENT_RETENTION_SECONDS, at),
            ).fetchone()
        return None if row is None else max(0.0, at - float(row["rotated_at"]))

    def report(self, now: float) -> RefreshReport:
        """Return aggregate-only measurements and the exact materiality decision."""
        validate_timestamp(now)
        with self._lock:
            return build_refresh_report(
                self._db,
                now=now,
                schema_version=SCHEMA_VERSION,
                client_classes=CLIENT_CLASSES,
                failure_reasons=FAILURE_REASONS,
                pending_gap=self._pending_gap,
                heartbeat_stale_seconds=REFRESH_HEARTBEAT_STALE_SECONDS,
            )

    def _prune(self, *, now: float, force: bool) -> None:
        validate_timestamp(now)
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
                    reconcile_stale(self._db, now=now, stale_seconds=REFRESH_ATTEMPT_STALE_SECONDS)
                    self._db.execute("DELETE FROM refresh_events WHERE at < ?", (cutoff,))
                    self._db.execute(
                        "DELETE FROM refresh_tombstones WHERE rotated_at < ?", (cutoff,)
                    )
                    self._db.execute(
                        """
                        DELETE FROM refresh_availability_gaps
                        WHERE ended_at IS NOT NULL AND ended_at < ?
                        """,
                        (cutoff,),
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
            if not self._checkpoint_truncate():
                self._checkpoint_pending = True
                self.note_unavailable(at=now, reason="wal_checkpoint")
            else:
                self._checkpoint_pending = False
                self._mark_available(now)

    def _enforce_event_row_cap(self) -> None:
        excess = int(self._db.execute("SELECT COUNT(*) FROM refresh_events").fetchone()[0])
        excess -= MAX_EVENT_ROWS
        if excess > 0:
            truncated = self._db.execute(
                """
                SELECT MAX(at) FROM (
                    SELECT at FROM refresh_events ORDER BY id LIMIT ?
                )
                """,
                (excess,),
            ).fetchone()[0]
            self._db.execute(
                """
                DELETE FROM refresh_events WHERE id IN (
                    SELECT id FROM refresh_events ORDER BY id LIMIT ?
                )
                """,
                (excess,),
            )
            if truncated is not None:
                previous = self._db.execute(
                    "SELECT value FROM refresh_meta WHERE key='events_truncated_through'"
                ).fetchone()
                watermark = max(
                    float(truncated),
                    float(previous["value"]) if previous is not None else float("-inf"),
                )
                self._db.execute(
                    """
                    INSERT INTO refresh_meta (key, value)
                    VALUES ('events_truncated_through', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (repr(watermark),),
                )

    def _check_database_capacity(self) -> None:
        try:
            if self.path.exists() and self.path.stat().st_size > MAX_DATABASE_BYTES:
                raise RefreshLedgerCapacityError("refresh ledger exceeds the 64 MiB database cap")
        except OSError as exc:
            raise RefreshLedgerUnavailable("configured refresh ledger cannot be inspected") from exc

    def _check_file_capacity(self) -> None:
        self._check_database_capacity()
        try:
            wal_path = Path(f"{self.path}-wal")
            if not wal_path.exists() or wal_path.stat().st_size <= MAX_WAL_BYTES:
                return
            recovered = self._connection is not None and self._checkpoint_truncate()
            if recovered and (not wal_path.exists() or wal_path.stat().st_size <= MAX_WAL_BYTES):
                self._checkpoint_pending = False
                self._mark_available(self._clock())
                secure_refresh_sqlite_files(self.path)
                return
            self._checkpoint_pending = True
            self.note_unavailable(at=self._clock(), reason="wal_over_cap")
            raise RefreshLedgerCapacityError("refresh ledger exceeds the 8 MiB WAL cap")
        except RefreshLedgerError:
            raise
        except OSError as exc:
            raise RefreshLedgerUnavailable("configured refresh ledger cannot be inspected") from exc

    def _after_write(self) -> None:
        secure_refresh_sqlite_files(self.path)
        self._check_file_capacity()

    def _checkpoint_truncate(self) -> bool:
        try:
            busy, remaining, checkpointed = self._db.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            return int(busy) == 0 and int(remaining) == int(checkpointed)
        except sqlite3.Error:
            return False

    def maintain(self, at: float) -> bool:
        """Perform one bounded checkpoint retry outside OAuth exchange semantics."""
        validate_timestamp(at)
        with self._lock:
            if not self._checkpoint_truncate():
                self._checkpoint_pending = True
                self.note_unavailable(at=at, reason="wal_checkpoint")
                return False
            wal_path = Path(f"{self.path}-wal")
            if wal_path.exists() and wal_path.stat().st_size > MAX_WAL_BYTES:
                self._checkpoint_pending = True
                self.note_unavailable(at=at, reason="wal_over_cap")
                return False
            self._checkpoint_pending = False
            self._mark_available(at)
            secure_refresh_sqlite_files(self.path)
            return True

    @staticmethod
    def _bounded_sqlite_error(exc: sqlite3.Error) -> RefreshLedgerError:
        text = str(exc).lower()
        if "full" in text or "too big" in text:
            return RefreshLedgerCapacityError("refresh ledger reached its configured size cap")
        return RefreshLedgerUnavailable("refresh ledger write failed")

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
                secure_refresh_sqlite_files(self.path)
            finally:
                self._connection.close()
                self._connection = None
