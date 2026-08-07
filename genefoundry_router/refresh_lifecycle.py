"""Aggregate-only router lifecycle persistence for the refresh ledger."""

from __future__ import annotations

import re
import secrets
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable

from genefoundry_router.refresh_models import MAX_LIFECYCLE_ROWS, RefreshLedgerError
from genefoundry_router.refresh_validation import validate_lifecycle


class RefreshLifecycleMixin(ABC):
    """Persist bounded startup and shutdown markers on a ledger connection."""

    _lock: threading.RLock
    _clock: Callable[[], float]

    @property
    @abstractmethod
    def _db(self) -> sqlite3.Connection: ...

    @abstractmethod
    def _after_write(self) -> None: ...

    @abstractmethod
    def _mark_available(self, at: float) -> None: ...

    @staticmethod
    @abstractmethod
    def _bounded_sqlite_error(exc: sqlite3.Error) -> RefreshLedgerError: ...

    def record_startup(self, *, version: str, at: float) -> str:
        """Record one router startup and return its non-client boot identifier."""
        validate_lifecycle(version, at)
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
            self._mark_available(self._clock())
        return boot_id

    def record_shutdown(self, boot_id: str, *, version: str, at: float) -> None:
        """Record a clean shutdown for an existing startup marker."""
        validate_lifecycle(version, at)
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
            self._mark_available(self._clock())

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
