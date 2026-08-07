"""Durable, privacy-bounded refresh-observability ledger contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from genefoundry_router.refresh_observability import (
    CLIENT_CLASSES,
    EVENT_RETENTION_SECONDS,
    FAILURE_REASONS,
    MAX_DATABASE_BYTES,
    MAX_EVENT_ROWS,
    MAX_WAL_BYTES,
    PRUNE_INTERVAL_SECONDS,
    SCHEMA_VERSION,
    RefreshEvent,
    RefreshLedger,
    RefreshLedgerCapacityError,
    RefreshLedgerUnavailable,
)
from genefoundry_router.refresh_report import read_refresh_report
from genefoundry_router.refresh_schema import initialize_schema


class FakeClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _event(
    at: float,
    *,
    outcome: str = "failure",
    reason: str | None = "local_not_found",
    request_id: str = "request-1",
    client_class: str = "chatgpt",
    client_hmac: str = "a" * 64,
    token_hash_prefix: str | None = "b" * 12,
) -> RefreshEvent:
    return RefreshEvent(
        at=at,
        request_id=request_id,
        client_class=client_class,
        client_hmac=client_hmac,
        event_type="refresh",
        outcome=outcome,
        reason=reason,
        token_hash_prefix=token_hash_prefix,
    )


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _raw_event_count(path: Path) -> int:
    with _connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM refresh_events").fetchone()[0])


def _insert_raw_event(path: Path, event: RefreshEvent) -> None:
    with _connect(path) as connection:
        connection.execute(
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


def _write_pre_fix_schema_v1(path: Path, *, now: float) -> None:
    """Create the exact Task 7 base schema before availability gaps were added."""
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE refresh_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE refresh_counters (
                counter TEXT NOT NULL,
                client_class TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                value INTEGER NOT NULL CHECK(value >= 0),
                PRIMARY KEY (counter, client_class, reason),
                CHECK(client_class IN ('chatgpt', 'claude', 'other'))
            );
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
            );
            CREATE INDEX refresh_events_at_idx ON refresh_events(at);
            CREATE INDEX refresh_events_client_at_idx
                ON refresh_events(client_hmac, at);
            CREATE TABLE refresh_tombstones (
                token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64),
                client_hmac TEXT NOT NULL CHECK(length(client_hmac) = 64),
                rotated_at REAL NOT NULL
            );
            CREATE INDEX refresh_tombstones_at_idx
                ON refresh_tombstones(rotated_at);
            CREATE TABLE router_lifecycle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                boot_id TEXT NOT NULL,
                marker TEXT NOT NULL CHECK(marker IN ('startup', 'shutdown')),
                at REAL NOT NULL,
                version TEXT NOT NULL CHECK(length(version) <= 32)
            );
            PRAGMA user_version=1;
            """
        )
        connection.execute("INSERT INTO refresh_meta VALUES ('legacy_marker', 'preserve-me')")
        connection.execute("INSERT INTO refresh_counters VALUES ('attempt', 'chatgpt', '', 1)")
        connection.execute(
            """
            INSERT INTO refresh_events (
                at, request_id, client_class, client_hmac, event_type,
                outcome, reason, token_hash_prefix, reuse_delay_seconds
            ) VALUES (?, 'legacy-event', 'chatgpt', ?, 'refresh',
                      'failure', 'reuse_after_rotation', 'bbbbbbbbbbbb', 2.0)
            """,
            (now - 10, "a" * 64),
        )
        connection.execute(
            "INSERT INTO refresh_tombstones VALUES (?, ?, ?)",
            ("c" * 64, "a" * 64, now - 5),
        )
        connection.execute(
            "INSERT INTO router_lifecycle VALUES (NULL, ?, 'startup', ?, '0.8.0')",
            ("d" * 32, now - 100),
        )


def test_schema_uses_wal_mode_0600_and_normalized_tables(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    ledger = RefreshLedger(path, hmac_key=b"signing-key", clock=FakeClock(2_000_000.0))
    try:
        with _connect(path) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }

        assert journal_mode == "wal"
        assert user_version == SCHEMA_VERSION
        # journal_size_limit is connection-local in SQLite; inspect the live ledger
        # connection that performs all production writes.
        assert ledger._db.execute("PRAGMA journal_size_limit").fetchone()[0] == MAX_WAL_BYTES
        assert path.stat().st_mode & 0o777 == 0o600
        assert {
            "refresh_meta",
            "refresh_counters",
            "refresh_events",
            "refresh_tombstones",
            "router_lifecycle",
        } <= tables
    finally:
        ledger.close()


def test_pre_fix_schema_v1_migrates_transactionally_without_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 2_500_000.0
    _write_pre_fix_schema_v1(path, now=now)

    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    try:
        with _connect(path) as connection:
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            gap_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='refresh_availability_gaps'
                """
            ).fetchone()
            legacy_marker = connection.execute(
                "SELECT value FROM refresh_meta WHERE key='legacy_marker'"
            ).fetchone()[0]
            request_id = connection.execute("SELECT request_id FROM refresh_events").fetchone()[0]
            lifecycle_count = connection.execute(
                "SELECT COUNT(*) FROM router_lifecycle"
            ).fetchone()[0]

        assert user_version == SCHEMA_VERSION == 2
        assert gap_table is not None
        assert legacy_marker == "preserve-me"
        assert request_id == "legacy-event"
        assert lifecycle_count == 1
        assert ledger.counter_snapshot().attempts == {"chatgpt": 1}
        assert ledger.classify_missing("c" * 64, "a" * 64, now) == "reuse_after_rotation"
    finally:
        ledger.close()


def test_pre_fix_schema_v1_migration_rolls_back_on_intermediate_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 2_600_000.0
    _write_pre_fix_schema_v1(path, now=now)
    connection = sqlite3.connect(path)

    def deny_version_write(
        action: int,
        first: str | None,
        second: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_PRAGMA and first == "user_version" and second is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_version_write)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            initialize_schema(connection, created=False, schema_version=2)
    finally:
        connection.set_authorizer(None)

    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        gap_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='refresh_availability_gaps'
            """
        ).fetchone()
        assert gap_table is None
        assert connection.execute("SELECT COUNT(*) FROM refresh_events").fetchone()[0] == 1
    finally:
        connection.close()


def test_client_identity_is_hmac_sha256_with_configured_signing_key(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    key = b"GF_OAUTH_JWT_SIGNING_KEY fixture"
    raw_client = "https://chatgpt.example/client?private=value"
    ledger = RefreshLedger(path, hmac_key=key, clock=FakeClock(2_000_000.0))
    try:
        expected = hmac.new(key, raw_client.encode(), hashlib.sha256).hexdigest()
        assert ledger.client_hmac(raw_client) == expected
        assert raw_client not in ledger.client_hmac(raw_client)
    finally:
        ledger.close()


def test_aggregate_counters_persist_across_ledger_instances(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    clock = FakeClock(2_000_000.0)
    first = RefreshLedger(path, hmac_key=b"key", clock=clock)
    first.record_event(_event(clock(), outcome="failure", reason="mapping_missing"))
    first.record_event(
        _event(
            clock(),
            outcome="success",
            reason=None,
            client_class="claude",
            client_hmac="c" * 64,
        )
    )
    first.close()

    second = RefreshLedger(path, hmac_key=b"key", clock=clock)
    try:
        snapshot = second.counter_snapshot()
        assert snapshot.attempts == {"chatgpt": 1, "claude": 1}
        assert snapshot.successes == {"claude": 1}
        assert snapshot.failures == {("chatgpt", "mapping_missing"): 1}
    finally:
        second.close()


def test_read_only_report_works_while_writer_has_live_wal(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 2_500_000.0
    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    try:
        ledger.record_startup(version="0.8.0", at=now - 7 * 24 * 60 * 60)
        ledger.record_event(_event(now - 1, outcome="success", reason=None))
        wal_path = Path(f"{path}-wal")
        assert wal_path.is_file()
        assert wal_path.stat().st_size > 0

        report = read_refresh_report(
            path,
            now=now,
            schema_version=SCHEMA_VERSION,
            client_classes=CLIENT_CLASSES,
            failure_reasons=FAILURE_REASONS,
        )

        assert report.attempts == 1
        assert report.successes == 1
    finally:
        ledger.close()


def test_startup_prunes_events_and_tombstones_older_than_fourteen_days(
    tmp_path: Path,
) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 3_000_000.0
    clock = FakeClock(now)
    old_at = now - EVENT_RETENTION_SECONDS - 1
    retained_at = now - EVENT_RETENTION_SECONDS + 1

    first = RefreshLedger(path, hmac_key=b"key", clock=clock)
    first.record_event(_event(old_at, request_id="old"))
    first.record_event(_event(retained_at, request_id="retained"))
    first.record_rotation("1" * 64, "a" * 64, old_at)
    first.record_rotation("2" * 64, "a" * 64, retained_at)
    first.close()

    second = RefreshLedger(path, hmac_key=b"key", clock=clock)
    try:
        with _connect(path) as connection:
            request_ids = {
                row[0]
                for row in connection.execute(
                    "SELECT request_id FROM refresh_events ORDER BY request_id"
                )
            }
            tombstones = {
                row[0]
                for row in connection.execute(
                    "SELECT token_hash FROM refresh_tombstones ORDER BY token_hash"
                )
            }
        assert request_ids == {"retained"}
        assert tombstones == {"2" * 64}
        assert second.classify_missing("1" * 64, "a" * 64, now) == "local_not_found"
        assert second.classify_missing("2" * 64, "a" * 64, now) == "reuse_after_rotation"
    finally:
        second.close()


def test_pruned_tombstone_hash_is_erased_from_sqlite_storage(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 3_500_000.0
    token_hash = "0123456789abcdef" * 4
    first = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    first.record_rotation(token_hash, "a" * 64, now - EVENT_RETENTION_SECONDS - 1)
    first.close()

    second = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    assert second.classify_missing(token_hash, "a" * 64, now) == "local_not_found"
    assert second._db.execute("PRAGMA secure_delete").fetchone()[0] == 1
    second.close()

    for sqlite_file in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sqlite_file.exists():
            assert token_hash.encode() not in sqlite_file.read_bytes()


def test_runtime_retention_pruning_is_throttled_to_once_per_hour(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 4_000_000.0
    clock = FakeClock(now)
    ledger = RefreshLedger(path, hmac_key=b"key", clock=clock)
    old = _event(now - EVENT_RETENTION_SECONDS - 1, request_id="old")
    _insert_raw_event(path, old)

    clock.value = now + PRUNE_INTERVAL_SECONDS - 1
    ledger.record_event(_event(clock(), request_id="before-hour"))
    assert _raw_event_count(path) == 2

    clock.value = now + PRUNE_INTERVAL_SECONDS
    ledger.record_event(_event(clock(), request_id="at-hour"))
    try:
        with _connect(path) as connection:
            request_ids = {
                row[0] for row in connection.execute("SELECT request_id FROM refresh_events")
            }
        assert request_ids == {"before-hour", "at-hour"}
    finally:
        ledger.close()


def test_classification_excludes_expired_tombstone_before_hourly_prune(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 4_500_000.0
    token_hash = "9" * 64
    client_hmac = "a" * 64
    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO refresh_tombstones (token_hash, client_hmac, rotated_at)
            VALUES (?, ?, ?)
            """,
            (token_hash, client_hmac, now - EVENT_RETENTION_SECONDS - 1),
        )

    try:
        assert ledger.classify_missing(token_hash, client_hmac, now) == "local_not_found"
        assert ledger.reuse_delay_seconds(token_hash, now) is None
        with _connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM refresh_tombstones").fetchone()[0] == 1
    finally:
        ledger.close()


def test_startup_enforces_exact_one_hundred_thousand_event_row_cap(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    now = 5_000_000.0
    first = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    first.close()

    with _connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO refresh_events (
                at, request_id, client_class, client_hmac, event_type,
                outcome, reason, token_hash_prefix, reuse_delay_seconds
            ) VALUES (?, ?, 'other', ?, 'refresh', 'failure',
                      'local_not_found', NULL, NULL)
            """,
            ((now, f"r-{index}", "d" * 64) for index in range(MAX_EVENT_ROWS + 5)),
        )

    second = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    try:
        assert _raw_event_count(path) == MAX_EVENT_ROWS
        with _connect(path) as connection:
            oldest = connection.execute(
                "SELECT request_id FROM refresh_events ORDER BY id LIMIT 1"
            ).fetchone()[0]
        assert oldest == "r-5"
    finally:
        second.close()


def test_database_size_over_sixty_four_mib_fails_closed_without_reset(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(6_000_000.0))
    ledger.close()
    with path.open("r+b") as database_file:
        database_file.truncate(MAX_DATABASE_BYTES + 1)

    oversized = path.stat().st_size
    with pytest.raises(RefreshLedgerCapacityError, match="64 MiB"):
        RefreshLedger(path, hmac_key=b"key", clock=FakeClock(6_000_000.0))
    assert path.stat().st_size == oversized


def test_runtime_write_recovers_over_cap_wal_before_capacity_rejection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-wal.sqlite3"
    now = 6_500_000.0
    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM refresh_events").fetchone()
    ledger._db.execute("PRAGMA wal_autocheckpoint=0")
    with ledger._db:
        ledger._db.execute("CREATE TABLE runtime_wal_fixture (payload BLOB NOT NULL)")
        ledger._db.execute(
            "INSERT INTO runtime_wal_fixture VALUES (zeroblob(?))",
            (MAX_WAL_BYTES + 4096,),
        )
    wal_path = Path(f"{path}-wal")
    assert wal_path.stat().st_size > MAX_WAL_BYTES
    reader.close()

    try:
        ledger.record_event(
            RefreshEvent(
                at=now,
                request_id="post-cap-recovery",
                client_class="other",
                client_hmac="a" * 64,
                event_type="authorize",
                outcome="started",
            )
        )
        assert wal_path.stat().st_size <= MAX_WAL_BYTES
    finally:
        ledger.close()


def test_startup_recovers_over_cap_wal_before_capacity_rejection(tmp_path: Path) -> None:
    path = tmp_path / "startup-wal.sqlite3"
    now = 6_600_000.0
    first = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    first._db.execute("PRAGMA wal_autocheckpoint=0")
    with first._db:
        first._db.execute("CREATE TABLE startup_wal_fixture (payload BLOB NOT NULL)")
        first._db.execute(
            "INSERT INTO startup_wal_fixture VALUES (zeroblob(?))",
            (MAX_WAL_BYTES + 4096,),
        )
    wal_path = Path(f"{path}-wal")
    assert wal_path.stat().st_size > MAX_WAL_BYTES

    second: RefreshLedger | None = None
    try:
        second = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now + 1))
        assert wal_path.stat().st_size <= MAX_WAL_BYTES
    finally:
        if second is not None:
            second.close()
        first.close()


def test_unreadable_existing_database_fails_closed_without_replacement(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_000_000.0))
    ledger.close()
    original_inode = path.stat().st_ino
    os.chmod(path, 0)
    try:
        with pytest.raises(RefreshLedgerUnavailable, match="readable and writable"):
            RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_000_000.0))
        assert path.stat().st_ino == original_inode
    finally:
        os.chmod(path, 0o600)


def test_parent_symlink_is_rejected_before_sqlite_opens_any_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    path = linked_parent / "refresh.sqlite3"

    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("SQLite opened an unsafe parent path"),
    )

    with pytest.raises(RefreshLedgerUnavailable, match="symlink"):
        RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_500_000.0))
    assert not (real_parent / path.name).exists()


def test_sticky_world_writable_final_ledger_directory_is_rejected_before_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final_parent = tmp_path / "unsafe-final"
    final_parent.mkdir(mode=0o700)
    final_parent.chmod(0o1777)
    path = final_parent / "refresh.sqlite3"
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("SQLite opened an unsafe final directory"),
    )

    with pytest.raises(RefreshLedgerUnavailable, match="private"):
        RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_500_000.0))


def test_different_owner_final_ledger_directory_is_rejected_before_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final_parent = tmp_path / "different-owner"
    final_parent.mkdir(mode=0o700)
    path = final_parent / "refresh.sqlite3"
    current_uid = os.geteuid()
    monkeypatch.setattr(
        "genefoundry_router.refresh_path.os.geteuid",
        lambda: current_uid + 1,
    )
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("SQLite opened another owner's directory"),
    )

    with pytest.raises(RefreshLedgerUnavailable, match="owner"):
        RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_500_000.0))


def test_private_final_directory_under_sticky_ancestor_remains_usable(
    tmp_path: Path,
) -> None:
    sticky_ancestor = tmp_path / "sticky-ancestor"
    sticky_ancestor.mkdir(mode=0o700)
    sticky_ancestor.chmod(0o1777)
    final_parent = sticky_ancestor / "private-ledger"
    final_parent.mkdir(mode=0o700)

    ledger = RefreshLedger(
        final_parent / "refresh.sqlite3",
        hmac_key=b"key",
        clock=FakeClock(7_500_000.0),
    )
    try:
        assert final_parent.stat().st_mode & 0o777 == 0o700
    finally:
        ledger.close()


def test_main_file_symlink_is_rejected_before_sqlite_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"do-not-open")
    path = tmp_path / "refresh.sqlite3"
    path.symlink_to(target)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("SQLite opened a symlinked database"),
    )

    with pytest.raises(RefreshLedgerUnavailable, match="regular file"):
        RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_500_000.0))


def test_preexisting_sidecar_symlink_is_rejected_before_sqlite_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "refresh.sqlite3"
    seed = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_500_000.0))
    seed.close()
    sidecar_target = tmp_path / "sidecar-target"
    sidecar_target.write_bytes(b"do-not-open")
    Path(f"{path}-wal").symlink_to(sidecar_target)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("SQLite opened an unsafe sidecar path"),
    )

    with pytest.raises(RefreshLedgerUnavailable, match="sidecar"):
        RefreshLedger(path, hmac_key=b"key", clock=FakeClock(7_500_000.0))
    assert sidecar_target.read_bytes() == b"do-not-open"


@pytest.mark.parametrize(
    "event",
    [
        replace(_event(8_000_000.0), request_id="x" * 129),
        replace(_event(8_000_000.0), client_class="unbounded-client"),
        replace(_event(8_000_000.0), client_hmac="not-a-full-hmac"),
        replace(_event(8_000_000.0), reason="raw upstream response"),
        replace(_event(8_000_000.0), token_hash_prefix="f" * 17),
    ],
)
def test_event_text_fields_are_bounded_before_sqlite_write(
    tmp_path: Path, event: RefreshEvent
) -> None:
    ledger = RefreshLedger(
        tmp_path / "refresh.sqlite3", hmac_key=b"key", clock=FakeClock(8_000_000.0)
    )
    try:
        with pytest.raises(ValueError):
            ledger.record_event(event)
    finally:
        ledger.close()


def test_full_token_hash_is_confined_to_tombstones_not_event_rows(tmp_path: Path) -> None:
    path = tmp_path / "refresh.sqlite3"
    token_hash = "e" * 64
    ledger = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(9_000_000.0))
    try:
        ledger.record_event(_event(9_000_000.0, token_hash_prefix=token_hash[:12]))
        ledger.record_rotation(token_hash, "a" * 64, 9_000_000.0)
        with _connect(path) as connection:
            event_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(refresh_events)")
            }
            stored_prefix = connection.execute(
                "SELECT token_hash_prefix FROM refresh_events"
            ).fetchone()[0]
            stored_hash = connection.execute(
                "SELECT token_hash FROM refresh_tombstones"
            ).fetchone()[0]
        assert "token_hash" not in event_columns
        assert stored_prefix == token_hash[:12]
        assert stored_hash == token_hash
    finally:
        ledger.close()


DAY = 24 * 60 * 60


def _record_attempts(
    ledger: RefreshLedger,
    *,
    at: float,
    total: int,
    failures: dict[int, tuple[str, str, str, float | None]] | None = None,
) -> None:
    """Record ``total`` attempts; indexed failures carry reason/class/HMAC/delay."""
    failures = failures or {}
    for index in range(total):
        failure = failures.get(index)
        if failure is None:
            event = _event(
                at + index / 1000,
                request_id=f"attempt-{index}",
                outcome="success",
                reason=None,
                client_class="other",
                client_hmac="f" * 64,
                token_hash_prefix=None,
            )
        else:
            reason, client_class, client_hmac, delay = failure
            event = replace(
                _event(at + index / 1000, request_id=f"attempt-{index}"),
                reason=reason,
                client_class=client_class,
                client_hmac=client_hmac,
                reuse_delay_seconds=delay,
            )
        ledger.record_event(event)


def _observed_ledger(tmp_path: Path, now: float, days: int) -> RefreshLedger:
    ledger = RefreshLedger(tmp_path / "report.sqlite3", hmac_key=b"key", clock=FakeClock(now))
    ledger.record_startup(version="0.8.0", at=now - days * DAY)
    return ledger


def test_report_is_ready_at_exactly_seven_days_and_fifty_attempts(tmp_path: Path) -> None:
    now = 20 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    try:
        _record_attempts(ledger, at=now - DAY, total=50)
        assert ledger.heartbeat(now) is True
        report = read_refresh_report(
            ledger.path,
            now=now,
            schema_version=SCHEMA_VERSION,
            client_classes=CLIENT_CLASSES,
            failure_reasons=FAILURE_REASONS,
        )
        assert report.sample_status == "ready"
        assert report.decision == "not_material"
        assert report.window_days == 7
        assert report.attempts == 50
        assert report.successes == 50
        assert report.failures == 0
        assert report.failure_rate == 0.0
        assert report.consecutive_observation_seconds == 7 * DAY
    finally:
        ledger.close()


def test_report_extends_to_fourteen_days_when_seven_day_sample_is_small(
    tmp_path: Path,
) -> None:
    now = 20 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    try:
        _record_attempts(ledger, at=now - DAY, total=49)
        report = ledger.report(now)
        assert report.sample_status == "extended"
        assert report.decision == "insufficient_sample"
        assert report.window_days == 14
        assert report.attempts == 49
    finally:
        ledger.close()


def test_report_marks_fourteen_day_fallback_insufficient_below_fifty_attempts(
    tmp_path: Path,
) -> None:
    now = 30 * DAY
    ledger = _observed_ledger(tmp_path, now, 14)
    try:
        _record_attempts(ledger, at=now - 13 * DAY, total=49)
        report = ledger.report(now)
        assert report.sample_status == "insufficient_sample"
        assert report.decision == "insufficient_sample"
        assert report.window_days == 14
        assert report.attempts == 49
    finally:
        ledger.close()


def test_report_uses_ready_fourteen_day_fallback_at_fifty_attempts(tmp_path: Path) -> None:
    now = 30 * DAY
    ledger = _observed_ledger(tmp_path, now, 14)
    try:
        _record_attempts(ledger, at=now - 13 * DAY, total=50)
        report = ledger.report(now)
        assert report.sample_status == "ready"
        assert report.decision == "not_material"
        assert report.window_days == 14
        assert report.attempts == 50
    finally:
        ledger.close()


def test_in_window_event_cap_truncation_cannot_produce_exact_decision(tmp_path: Path) -> None:
    now = 35 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    path = ledger.path
    _record_attempts(ledger, at=now - DAY, total=60)
    ledger.close()

    with _connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO refresh_events (
                at, request_id, client_class, client_hmac, event_type,
                outcome, reason, token_hash_prefix, reuse_delay_seconds
            ) VALUES (?, ?, 'other', ?, 'authorize', 'started', NULL, NULL, NULL)
            """,
            (
                (now - DAY / 2, f"auth-{index}", f"{index:064x}")
                for index in range(MAX_EVENT_ROWS - 50)
            ),
        )

    reopened = RefreshLedger(path, hmac_key=b"key", clock=FakeClock(now))
    try:
        report = reopened.report(now)
        assert report.attempts == 50
        assert report.sample_status == "incomplete"
        assert report.decision == "incomplete"
        assert report.evidence_complete is False
        assert report.incomplete_reasons == ("event_truncation",)
        assert report.truncated_through is not None
        assert report.truncated_through >= now - DAY
    finally:
        reopened.close()


def test_busy_checkpoint_marks_window_incomplete_until_bounded_maintenance(
    tmp_path: Path,
) -> None:
    now = 38 * DAY
    clock = FakeClock(now)
    path = tmp_path / "busy.sqlite3"
    ledger = RefreshLedger(path, hmac_key=b"key", clock=clock)
    ledger.record_startup(version="0.8.0", at=now - 7 * DAY)
    _record_attempts(ledger, at=now - DAY, total=50)
    token_hash = "0123456789abcdef" * 4
    ledger.record_rotation(
        token_hash,
        "a" * 64,
        now - EVENT_RETENTION_SECONDS - 1,
    )

    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    assert reader.execute("SELECT COUNT(*) FROM refresh_tombstones").fetchone()[0] == 1
    ledger._db.execute("PRAGMA busy_timeout=0")
    clock.value = now + PRUNE_INTERVAL_SECONDS
    try:
        ledger.record_event(
            RefreshEvent(
                at=clock(),
                request_id="maintenance-trigger",
                client_class="other",
                client_hmac="b" * 64,
                event_type="authorize",
                outcome="started",
            )
        )
        wal_path = Path(f"{path}-wal")
        assert wal_path.exists()
        assert token_hash.encode() in wal_path.read_bytes()
        report = ledger.report(clock())
        assert report.sample_status == "incomplete"
        assert report.decision == "incomplete"
        assert "wal_checkpoint" in report.incomplete_reasons
    finally:
        reader.close()

    clock.value += PRUNE_INTERVAL_SECONDS
    ledger.record_event(
        RefreshEvent(
            at=clock(),
            request_id="bounded-maintenance-retry",
            client_class="other",
            client_hmac="c" * 64,
            event_type="authorize",
            outcome="started",
        )
    )
    try:
        assert token_hash.encode() not in Path(f"{path}-wal").read_bytes()
        with _connect(path) as connection:
            ended_at = connection.execute(
                "SELECT ended_at FROM refresh_availability_gaps"
            ).fetchone()[0]
        assert ended_at == clock()
        recovered = ledger.report(clock())
        assert recovered.sample_status == "incomplete"
        assert recovered.availability_gap_count == 1
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("total", "material_failures", "expected"),
    [
        (300, 3, "not_material"),  # exactly 1% does not EXCEED the threshold
        (299, 3, "material"),
        (50, 2, "not_material"),  # rate exceeds 1%, but fewer than three events
    ],
)
def test_report_applies_exact_rotation_failure_materiality_threshold(
    tmp_path: Path,
    total: int,
    material_failures: int,
    expected: str,
) -> None:
    now = 40 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    failures = {
        index: (
            "reuse_after_rotation" if index % 2 == 0 else "upstream_invalid_grant",
            "chatgpt",
            "a" * 64,
            float(index + 1),
        )
        for index in range(material_failures)
    }
    try:
        _record_attempts(ledger, at=now - DAY, total=total, failures=failures)
        report = ledger.report(now)
        assert report.sample_status == "ready"
        assert report.decision == expected
        assert report.material_rotation_failures == material_failures
        assert report.material_rotation_rate == pytest.approx(material_failures / total)
    finally:
        ledger.close()


def test_two_distinct_failed_clients_authorizing_within_fifteen_minutes_is_material(
    tmp_path: Path,
) -> None:
    now = 50 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    client_a = "a" * 64
    client_b = "b" * 64
    failures = {
        0: ("local_not_found", "chatgpt", client_a, None),
        1: ("mapping_missing", "claude", client_b, None),
    }
    try:
        attempt_at = now - DAY
        _record_attempts(ledger, at=attempt_at, total=50, failures=failures)
        ledger.record_event(
            RefreshEvent(
                at=attempt_at + 900,
                request_id="authorize-a",
                client_class="chatgpt",
                client_hmac=client_a,
                event_type="authorize",
                outcome="started",
            )
        )
        ledger.record_event(
            RefreshEvent(
                at=attempt_at + 900.001,
                request_id="authorize-b",
                client_class="claude",
                client_hmac=client_b,
                event_type="authorize",
                outcome="started",
            )
        )

        report = ledger.report(now)
        assert report.decision == "material"
        assert report.subsequent_authorizations == 2
        assert report.forced_reauthorization_clients == 2
    finally:
        ledger.close()


def test_non_rotation_rejections_do_not_trigger_forced_reauthorization_gate(
    tmp_path: Path,
) -> None:
    now = 55 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    client_a = "a" * 64
    client_b = "b" * 64
    failures = {
        0: ("local_rejected", "chatgpt", client_a, None),
        1: ("jwt_invalid", "claude", client_b, None),
    }
    try:
        attempt_at = now - DAY
        _record_attempts(ledger, at=attempt_at, total=50, failures=failures)
        for index, (client_class, client_hmac) in enumerate(
            (("chatgpt", client_a), ("claude", client_b))
        ):
            ledger.record_event(
                RefreshEvent(
                    at=attempt_at + 60 + index,
                    request_id=f"authorize-{index}",
                    client_class=client_class,
                    client_hmac=client_hmac,
                    event_type="authorize",
                    outcome="started",
                )
            )

        report = ledger.report(now)
        assert report.decision == "not_material"
        assert report.subsequent_authorizations == 0
        assert report.forced_reauthorization_clients == 0
    finally:
        ledger.close()


def test_report_groups_only_bounded_aggregate_values_and_reuse_delays(tmp_path: Path) -> None:
    now = 60 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    hidden_hmac = "c" * 64
    hidden_prefix = "d" * 12
    failures = {
        0: ("reuse_after_rotation", "chatgpt", hidden_hmac, 4.999),
        1: ("reuse_after_rotation", "chatgpt", hidden_hmac, 5.0),
        2: ("reuse_after_rotation", "chatgpt", hidden_hmac, 60.0),
        3: ("reuse_after_rotation", "chatgpt", hidden_hmac, 901.0),
    }
    try:
        _record_attempts(ledger, at=now - DAY, total=50, failures=failures)
        with _connect(ledger.path) as connection:
            connection.execute(
                "UPDATE refresh_events SET token_hash_prefix = ? WHERE outcome = 'failure'",
                (hidden_prefix,),
            )

        report = ledger.report(now)
        payload = report.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        assert set(report.attempts_by_client_class) == {"chatgpt", "claude", "other"}
        assert set(report.failures_by_reason) == {
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
        assert report.affected_clients == 1
        assert report.reuse_delay_buckets == {
            "under_5s": 1,
            "5s_to_under_60s": 1,
            "1m_to_15m": 1,
            "over_15m": 1,
        }
        assert hidden_hmac not in serialized
        assert hidden_prefix not in serialized
        assert "token_hash" not in serialized
        assert "request_id" not in serialized
    finally:
        ledger.close()


def test_report_reconciles_stale_started_attempts_as_internal_failures(tmp_path: Path) -> None:
    now = 70 * DAY
    ledger = _observed_ledger(tmp_path, now, 7)
    try:
        ledger.begin_attempt(
            RefreshEvent(
                at=now - 301,
                request_id="interrupted-refresh",
                client_class="other",
                client_hmac="e" * 64,
                event_type="refresh",
                outcome="started",
                token_hash_prefix="f" * 12,
            )
        )

        assert ledger.heartbeat(now) is True
        report = read_refresh_report(
            ledger.path,
            now=now,
            schema_version=SCHEMA_VERSION,
            client_classes=CLIENT_CLASSES,
            failure_reasons=FAILURE_REASONS,
        )

        assert report.attempts == 1
        assert report.failures == 1
        assert report.failures_by_reason["internal_error"] == 1
        assert "unterminated_attempts" not in report.incomplete_reasons
    finally:
        ledger.close()


def test_report_exposes_restart_intervals_without_boot_identifiers(tmp_path: Path) -> None:
    now = 70 * DAY
    ledger = RefreshLedger(tmp_path / "report.sqlite3", hmac_key=b"key", clock=FakeClock(now))
    try:
        old_boot = ledger.record_startup(version="0.8.0", at=now - 10 * DAY)
        ledger.record_shutdown(old_boot, version="0.8.0", at=now - 9 * DAY)
        ledger.record_startup(version="0.8.0", at=now - 7 * DAY)
        _record_attempts(ledger, at=now - DAY, total=50)

        report = ledger.report(now)
        assert len(report.restart_intervals) == 2
        assert report.restart_intervals[0].clean_shutdown is True
        assert report.restart_intervals[1].clean_shutdown is False
        assert report.consecutive_observation_seconds == 7 * DAY
        assert "boot_id" not in json.dumps(report.to_dict())
    finally:
        ledger.close()


def test_clean_container_replacement_preserves_consecutive_observation(
    tmp_path: Path,
) -> None:
    now = 80 * DAY
    replacement_gap = 30.0
    ledger = RefreshLedger(tmp_path / "report.sqlite3", hmac_key=b"key", clock=FakeClock(now))
    try:
        previous = ledger.record_startup(version="0.8.0", at=now - 7 * DAY - replacement_gap)
        ledger.record_shutdown(previous, version="0.8.0", at=now - DAY)
        ledger.record_startup(version="0.8.0", at=now - DAY + replacement_gap)
        _record_attempts(ledger, at=now - DAY / 2, total=50)

        report = ledger.report(now)

        assert report.sample_status == "ready"
        assert report.consecutive_observation_seconds == 7 * DAY
    finally:
        ledger.close()


def test_unclean_container_replacement_resets_consecutive_observation(
    tmp_path: Path,
) -> None:
    now = 90 * DAY
    ledger = RefreshLedger(tmp_path / "report.sqlite3", hmac_key=b"key", clock=FakeClock(now))
    try:
        ledger.record_startup(version="0.8.0", at=now - 7 * DAY)
        ledger.record_startup(version="0.8.0", at=now - DAY)
        _record_attempts(ledger, at=now - DAY / 2, total=50)

        report = ledger.report(now)

        assert report.sample_status != "ready"
        assert report.consecutive_observation_seconds == DAY
    finally:
        ledger.close()
