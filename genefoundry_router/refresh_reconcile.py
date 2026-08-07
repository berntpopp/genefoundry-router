"""Recovery of refresh observations interrupted before a terminal outcome."""

from __future__ import annotations

import sqlite3


def reconcile_stale_attempts(db: sqlite3.Connection, *, now: float, stale_seconds: float) -> None:
    """Finalize attempts old enough that they cannot belong to a live request."""
    cutoff = now - stale_seconds
    rows = db.execute(
        """
        SELECT client_class, COUNT(*) AS n
        FROM refresh_events
        WHERE event_type='refresh' AND outcome='started' AND at <= ?
        GROUP BY client_class
        """,
        (cutoff,),
    ).fetchall()
    if not rows:
        return
    with db:
        db.execute(
            """
            UPDATE refresh_events SET outcome='failure', reason='internal_error'
            WHERE event_type='refresh' AND outcome='started' AND at <= ?
            """,
            (cutoff,),
        )
        for row in rows:
            db.execute(
                """
                INSERT INTO refresh_counters (counter, client_class, reason, value)
                VALUES ('failure', ?, 'internal_error', ?)
                ON CONFLICT(counter, client_class, reason)
                DO UPDATE SET value = value + excluded.value
                """,
                (str(row["client_class"]), int(row["n"])),
            )
