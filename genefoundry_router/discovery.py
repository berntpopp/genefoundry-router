"""Polling re-list fallback for proxy freshness (TTL-based backends)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)


class PollingRefresher:
    """Periodically invoke ``relist`` to refresh the federated tool index.

    Disabled when ``interval_seconds <= 0``.
    """

    def __init__(self, interval_seconds: float, relist: Callable[[], Awaitable[None]]) -> None:
        self._interval = interval_seconds
        self._relist = relist
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._interval <= 0:
            log.info("polling_disabled")
            return
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                try:
                    await self._relist()
                except Exception as exc:  # polling must survive errors
                    log.warning("relist_failed", error=str(exc))
        except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
            pass

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
