"""Unit tests for OAuthProxy async offloading, WAL maintenance, overlap race, and correlation ID."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fastmcp.server.auth import OAuthProxy
from mcp.server.auth.provider import AuthorizationParams, RefreshToken, TokenError
from mcp.shared.auth import OAuthToken

from genefoundry_router.oauth_proxy import _safe_request_id
from genefoundry_router.observability import correlation_id
from genefoundry_router.refresh_async import observe_in_thread
from genefoundry_router.refresh_observability import RefreshEvent, RefreshLedger
from tests.unit.test_refresh_proxy import (
    SIGNING_KEY,
    FakeClock,
    _client,
    _proxy,
    _refresh,
    _success,
)


@pytest.mark.asyncio
async def test_observe_in_thread_success_and_exception() -> None:
    def sync_add(a: int, b: int) -> int:
        return a + b

    assert await observe_in_thread(sync_add, 2, 3) == 5

    def sync_fail() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await observe_in_thread(sync_fail)


@pytest.mark.asyncio
async def test_observe_in_thread_shielded_cancellation() -> None:
    worker_started = threading.Event()
    worker_can_finish = threading.Event()
    worker_finished = threading.Event()

    def blocking_work() -> str:
        worker_started.set()
        worker_can_finish.wait(timeout=5.0)
        worker_finished.set()
        return "done"

    task = asyncio.create_task(observe_in_thread(blocking_work))
    assert await asyncio.to_thread(worker_started.wait, 5.0)

    task.cancel()
    await asyncio.sleep(0.01)
    assert not worker_finished.is_set()

    worker_can_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert worker_finished.is_set()


@pytest.mark.asyncio
async def test_cancelled_abandoned_refresh_waits_for_worker_before_finishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("client-abandoned-cancel")
    token = _refresh("token-abandoned-cancel", client.client_id or "")

    async def load_ok(*_args: Any, **_kwargs: Any) -> RefreshToken:
        return token

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_ok)
    assert await proxy.load_refresh_token(client, token.token) == token

    finish_started = threading.Event()
    finish_gate = threading.Event()
    finish_done = threading.Event()
    original_finish = ledger.finish_attempt

    def gated_finish(
        event_id: int,
        *,
        outcome: str,
        reason: str | None = None,
        reuse_delay_seconds: float | None = None,
    ) -> None:
        finish_started.set()
        finish_gate.wait(timeout=5.0)
        original_finish(
            event_id, outcome=outcome, reason=reason, reuse_delay_seconds=reuse_delay_seconds
        )
        finish_done.set()

    monkeypatch.setattr(ledger, "finish_attempt", gated_finish)

    cleanup_task = asyncio.create_task(proxy._finish_abandoned_refresh_attempt())
    assert await asyncio.to_thread(finish_started.wait, 5.0)

    cleanup_task.cancel()
    await asyncio.sleep(0.01)
    assert not cleanup_task.done()

    finish_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await cleanup_task

    assert finish_done.is_set()
    assert len(proxy._refresh_inflight) == 0
    ledger.close()


@pytest.mark.asyncio
async def test_blocked_refresh_start_keeps_loop_live_and_cancellation_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify issue #223: SQLite I/O offloaded, loop stays responsive, cancellation is ordered."""
    proxy, ledger = _proxy(tmp_path)
    client = _client("client-block-test")
    token = _refresh("token-start-block", client.client_id or "")

    original_begin = ledger.begin_attempt
    gate = threading.Event()
    begin_started = threading.Event()

    def gated_begin_attempt(event: RefreshEvent) -> int:
        begin_started.set()
        gate.wait(timeout=5.0)
        return original_begin(event)

    monkeypatch.setattr(ledger, "begin_attempt", gated_begin_attempt)

    task1 = asyncio.create_task(proxy.load_refresh_token(client, token.token))
    assert await asyncio.to_thread(begin_started.wait, 5.0)

    ticks = 0
    for _ in range(5):
        await asyncio.sleep(0.005)
        ticks += 1
    assert ticks == 5

    task2 = asyncio.create_task(proxy.load_refresh_token(client, token.token))
    await asyncio.sleep(0.01)

    task1.cancel()
    await asyncio.sleep(0.01)

    gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task1

    res2 = await task2
    assert res2 is None
    assert len(proxy._refresh_inflight) == 0

    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT outcome, reason FROM refresh_events WHERE event_type='refresh' ORDER BY id ASC"
        ).fetchall()

    assert len(rows) == 2
    assert set(rows) == {("failure", "internal_error"), ("failure", "overlapping_attempt")}
    ledger.close()


@pytest.mark.asyncio
async def test_overlap_during_blocked_classify_missing_records_overlapping_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("client-classify-overlap")
    raw_token = "rotated-refresh-token"  # noqa: S105 - synthetic test token
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ledger.record_rotation(token_hash, ledger.client_hmac(client.client_id), 2_000_000.0)

    classify_started = threading.Event()
    classify_gate = threading.Event()
    original_classify = ledger.classify_missing

    def gated_classify(th: str, ch: str, at: float) -> str:
        if not classify_started.is_set():
            classify_started.set()
            classify_gate.wait(timeout=5.0)
        return original_classify(th, ch, at)

    monkeypatch.setattr(ledger, "classify_missing", gated_classify)

    t1 = asyncio.create_task(proxy.load_refresh_token(client, raw_token))
    assert await asyncio.to_thread(classify_started.wait, 5.0)

    # Second concurrent request arrives while t1 is inside ledger.classify_missing
    t2 = asyncio.create_task(proxy.load_refresh_token(client, raw_token))
    await asyncio.sleep(0.01)

    classify_gate.set()
    assert await t1 is None
    assert await t2 is None

    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT outcome, reason, reuse_delay_seconds FROM refresh_events "
            "WHERE event_type='refresh' ORDER BY id ASC"
        ).fetchall()

    assert rows == [
        ("failure", "overlapping_attempt", None),
        ("failure", "overlapping_attempt", None),
    ]
    ledger.close()


def test_heartbeat_recovers_pending_checkpoint_and_handles_busy(tmp_path: Path) -> None:
    """Verify issue #226: heartbeat() retries maintenance when checkpoint is pending."""
    clock = FakeClock(100.0)
    ledger = RefreshLedger(tmp_path / "refresh.sqlite3", hmac_key=SIGNING_KEY, clock=clock)

    ledger.record_event(
        RefreshEvent(
            at=clock(),
            request_id="req-1",
            client_class="other",
            client_hmac=ledger.client_hmac("client"),
            event_type="refresh",
            outcome="success",
        )
    )

    reader = sqlite3.connect(ledger.path)
    reader.execute("BEGIN DEFERRED")
    reader.execute("SELECT COUNT(*) FROM refresh_events").fetchone()

    ledger._checkpoint_pending = True

    clock.value += 1.0
    assert ledger.heartbeat(clock()) is False
    assert ledger._checkpoint_pending is True

    reader.close()

    clock.value += 1.0
    assert ledger.heartbeat(clock()) is True
    assert ledger._checkpoint_pending is False

    with sqlite3.connect(ledger.path) as conn:
        row = conn.execute(
            "SELECT value FROM refresh_meta WHERE key='observer_heartbeat_at'"
        ).fetchone()
    assert row is not None
    assert float(row[0]) == clock()
    ledger.close()


@pytest.mark.asyncio
async def test_concurrent_overlap_race_marks_first_attempt_overlapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify issue #235b: when a second request arrives, the first attempt is marked overlapping."""
    proxy, ledger = _proxy(tmp_path)
    client = _client("client-race")
    token = _refresh("same-token-string", client.client_id or "")

    req1_paused = asyncio.Event()
    req1_resume = asyncio.Event()

    async def hooked_super_exchange(self: Any, c: Any, r: Any, s: Any) -> OAuthToken:
        if r.token == token.token and not req1_paused.is_set():
            req1_paused.set()
            await req1_resume.wait()
            raise TokenError(error="invalid_grant", error_description="Upstream token not found")
        return _success()

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", hooked_super_exchange)

    t1 = asyncio.create_task(proxy.exchange_refresh_token(client, token, ["openid"]))
    await req1_paused.wait()

    t2 = asyncio.create_task(proxy.exchange_refresh_token(client, token, ["openid"]))
    res2 = await t2
    assert res2.access_token == "new-access-secret"  # noqa: S105 - synthetic assertion

    req1_resume.set()
    with pytest.raises(TokenError) as exc_info:
        await t1
    assert exc_info.value.error == "invalid_grant"
    assert len(proxy._refresh_inflight) == 0

    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT outcome, reason FROM refresh_events WHERE event_type='refresh' ORDER BY id ASC"
        ).fetchall()

    assert rows == [("failure", "overlapping_attempt"), ("success", None)]
    ledger.close()


@pytest.mark.asyncio
async def test_correlation_id_preserved_in_refresh_and_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify issue #239a: request_id captures correlation_id.get() when present."""
    proxy, ledger = _proxy(tmp_path)
    client = _client("client-correlation")
    token = _refresh("token-correlation", client.client_id or "")

    async def mock_super_exchange(*_args: Any, **_kwargs: Any) -> OAuthToken:
        return _success()

    async def mock_super_auth(*_args: Any, **_kwargs: Any) -> str:
        return "https://idp.example/authorize?code=test"

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", mock_super_exchange)
    monkeypatch.setattr(OAuthProxy, "authorize", mock_super_auth)

    valid_id = "req-valid-1234.56:abc_XYZ"
    ctx_token = correlation_id.set(valid_id)
    try:
        await proxy.exchange_refresh_token(client, token, ["openid"])
        await proxy.authorize(
            client,
            AuthorizationParams(
                redirect_uri="https://connector.example/callback",
                redirect_uri_provided_explicitly=True,
                scopes=["openid"],
                state="state1",
                code_challenge="challenge",
                code_challenge_method="S256",
            ),
        )
    finally:
        correlation_id.reset(ctx_token)

    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT event_type, request_id FROM refresh_events ORDER BY id ASC"
        ).fetchall()

    assert rows == [("refresh", valid_id), ("authorize", valid_id)]
    assert _safe_request_id(None) == "_unknown"
    assert _safe_request_id("bad\nid") == "_unknown"
    assert _safe_request_id("a" * 129) == "_unknown"
    assert _safe_request_id("") == "_unknown"
    ledger.close()
