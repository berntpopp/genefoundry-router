"""SQLite configuration and schema creation for refresh observability."""

from __future__ import annotations

import sqlite3


class RefreshSchemaError(RuntimeError):
    """A refresh ledger has an unsafe or unsupported schema configuration."""


def configure_sqlite(
    db: sqlite3.Connection,
    *,
    max_database_bytes: int,
    max_wal_bytes: int,
) -> None:
    """Enable the durability and size controls required by the ledger."""
    journal_mode = str(db.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
    if journal_mode != "wal":
        raise RefreshSchemaError("configured refresh ledger cannot enable WAL mode")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA secure_delete=ON")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute(f"PRAGMA journal_size_limit={max_wal_bytes}")
    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    max_pages = max_database_bytes // page_size
    db.execute(f"PRAGMA max_page_count={max_pages}")
    # Keep automatic checkpoints below the byte cap, including WAL frame headers.
    wal_frames = max(1, (max_wal_bytes - 32) // (page_size + 24))
    db.execute(f"PRAGMA wal_autocheckpoint={wal_frames}")


def initialize_schema(
    db: sqlite3.Connection,
    *,
    created: bool,
    schema_version: int,
) -> None:
    """Create the fixed bounded schema or validate an existing ledger."""
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version not in (0, schema_version):
        raise RefreshSchemaError("refresh ledger schema version is unsupported")
    if version == 0 and not created:
        raise RefreshSchemaError("configured refresh ledger has no recognized schema")
    if version == schema_version:
        return

    with db:
        db.execute("CREATE TABLE refresh_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(
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
        db.execute(
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
        db.execute("CREATE INDEX refresh_events_at_idx ON refresh_events(at)")
        db.execute("CREATE INDEX refresh_events_client_at_idx ON refresh_events(client_hmac, at)")
        db.execute(
            """
            CREATE TABLE refresh_tombstones (
                token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64),
                client_hmac TEXT NOT NULL CHECK(length(client_hmac) = 64),
                rotated_at REAL NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX refresh_tombstones_at_idx ON refresh_tombstones(rotated_at)")
        db.execute(
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
        db.execute(
            """
            CREATE TABLE refresh_availability_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                ended_at REAL,
                reason TEXT NOT NULL,
                CHECK(ended_at IS NULL OR ended_at >= started_at),
                CHECK(reason IN ('write_failure', 'wal_checkpoint', 'wal_over_cap'))
            )
            """
        )
        db.execute(f"PRAGMA user_version={schema_version}")
