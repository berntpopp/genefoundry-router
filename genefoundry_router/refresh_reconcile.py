"""Recovery of refresh observations interrupted before a terminal outcome."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Collection


def enforce_event_row_cap(db: sqlite3.Connection, *, max_rows: int) -> None:
    """Drop oldest detail rows while preserving an aggregate truncation watermark."""
    excess = int(db.execute("SELECT COUNT(*) FROM refresh_events").fetchone()[0]) - max_rows
    if excess <= 0:
        return
    truncated = db.execute(
        "SELECT MAX(at) FROM (SELECT at FROM refresh_events ORDER BY id LIMIT ?)",
        (excess,),
    ).fetchone()[0]
    db.execute(
        "DELETE FROM refresh_events WHERE id IN "
        "(SELECT id FROM refresh_events ORDER BY id LIMIT ?)",
        (excess,),
    )
    if truncated is None:
        return
    previous = db.execute(
        "SELECT value FROM refresh_meta WHERE key='events_truncated_through'"
    ).fetchone()
    watermark = max(
        float(truncated),
        float(previous["value"]) if previous is not None else float("-inf"),
    )
    db.execute(
        """
        INSERT INTO refresh_meta (key, value) VALUES ('events_truncated_through', ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (repr(watermark),),
    )


def reconcile_stale_attempts_in_transaction(
    db: sqlite3.Connection,
    *,
    now: float,
    stale_seconds: float,
    excluded_event_ids: Collection[int] = (),
) -> None:
    """Finalize only the stale rows atomically claimed by the caller's transaction."""
    cutoff = now - stale_seconds
    excluded = tuple(sorted(excluded_event_ids))
    exclusion = f" AND id NOT IN ({','.join('?' for _ in excluded)})" if excluded else ""
    rows = db.execute(
        f"""
        UPDATE refresh_events SET outcome='failure', reason='internal_error'
        WHERE event_type='refresh' AND outcome='started' AND at <= ?{exclusion}
        RETURNING client_class
        """,  # noqa: S608 - placeholders cover every dynamic value
        (cutoff, *excluded),
    ).fetchall()
    if not rows:
        return
    counts = Counter(str(row["client_class"]) for row in rows)
    for client_class, count in counts.items():
        db.execute(
            """
            INSERT INTO refresh_counters (counter, client_class, reason, value)
            VALUES ('failure', ?, 'internal_error', ?)
            ON CONFLICT(counter, client_class, reason)
            DO UPDATE SET value = value + excluded.value
            """,
            (client_class, count),
        )


def reconcile_stale_attempts(
    db: sqlite3.Connection,
    *,
    now: float,
    stale_seconds: float,
    excluded_event_ids: Collection[int] = (),
) -> None:
    """Finalize interrupted attempts in one self-contained transaction."""
    with db:
        reconcile_stale_attempts_in_transaction(
            db,
            now=now,
            stale_seconds=stale_seconds,
            excluded_event_ids=excluded_event_ids,
        )
