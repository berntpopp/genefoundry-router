"""Async thread offloading helper for refresh ledger observation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

__all__ = ["observe_in_thread"]


async def observe_in_thread[T](fn: Callable[..., T], /, *args: Any) -> T:
    """Run a blocking function in a thread while shielding worker completion upon cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(fn, *args))
    cancelled: asyncio.CancelledError | None = None
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception:
            break
    if cancelled is not None:
        if not worker.cancelled():
            worker.exception()
        raise cancelled
    return worker.result()
