import asyncio

from genefoundry_router.discovery import PollingRefresher


async def test_refresher_calls_relist_each_interval():
    calls = {"n": 0}

    async def relist():
        calls["n"] += 1

    refresher = PollingRefresher(interval_seconds=0.01, relist=relist)
    await refresher.start()
    await asyncio.sleep(0.05)
    await refresher.stop()
    assert calls["n"] >= 2  # fired multiple times over 50ms at 10ms interval


async def test_zero_interval_is_disabled():
    async def relist():  # pragma: no cover - must never run
        raise AssertionError("should not be called when disabled")

    refresher = PollingRefresher(interval_seconds=0, relist=relist)
    await refresher.start()
    await asyncio.sleep(0.02)
    await refresher.stop()
    assert refresher.running is False
