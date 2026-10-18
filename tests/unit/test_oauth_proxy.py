"""Unit tests for OAuthProxy async offloading, WAL maintenance, overlap race, and correlation ID."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastmcp.server.auth import OAuthProxy
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from genefoundry_router.oauth_proxy import (
    GeneFoundryOAuthProxy,
    _safe_request_id,
)
from genefoundry_router.observability import correlation_id
from genefoundry_router.refresh_async import observe_in_thread
from genefoundry_router.refresh_observability import (
    RefreshEvent,
    RefreshLedger,
)

BASE_URL = "https://genefoundry.org"
AUDIENCE = f"{BASE_URL}/mcp"
SIGNING_KEY = b"test-key-with-at-least-32-bytes!!"
DEADLINE = datetime(2026, 9, 6, tzinfo=UTC)


class FakeClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, delta: float) -> None:
        self.value += delta


class StubVerifier:
    required_scopes: list[str] | None = None


def _client(client_id: str, *, name: str | None = None) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_name=name,
        redirect_uris=["https://connector.example/callback"],
        scope="openid",
        token_endpoint_auth_method="none",  # noqa: S106 - OAuth method name
        grant_types=["authorization_code", "refresh_token"],
    )


def _proxy(
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    inflight_limit: int = 1024,
) -> tuple[GeneFoundryOAuthProxy, RefreshLedger, FakeClock]:
    clock = clock or FakeClock(2_000_000.0)
    ledger = RefreshLedger(tmp_path / "refresh.sqlite3", hmac_key=SIGNING_KEY, clock=clock)
    proxy = GeneFoundryOAuthProxy(
        upstream_authorization_endpoint="https://idp.example/authorize",
        upstream_token_endpoint="https://idp.example/token",  # noqa: S106 - endpoint URL
        upstream_client_id="router",
        upstream_client_secret="test-secret",  # noqa: S106 - synthetic fixture
        token_verifier=StubVerifier(),
        base_url=BASE_URL,
        resource_base_url=AUDIENCE,
        client_storage=MemoryStore(),
        jwt_signing_key=SIGNING_KEY,
        canonical_issuer_url=BASE_URL,
        legacy_issuer_urls=[f"{BASE_URL}/"],
        legacy_issuer_accept_until=DEADLINE,
        refresh_ledger=ledger,
        refresh_clock=clock,
        refresh_inflight_limit=inflight_limit,
    )
    proxy.set_mcp_path("")
    return proxy, ledger, clock


def _refresh(token: str, client_id: str) -> RefreshToken:
    return RefreshToken(token=token, client_id=client_id, scopes=["openid"], expires_at=None)


def _success() -> OAuthToken:
    return OAuthToken(
        access_token="upstream-access",  # noqa: S106 - synthetic OAuth response
        token_type="Bearer",  # noqa: S106 - OAuth token type
        expires_in=3600,
        refresh_token="upstream-refresh",  # noqa: S106 - synthetic OAuth response
        scope="openid",
    )


@pytest.mark.asyncio
async def test_observe_in_thread_success_and_exception() -> None:
    def sync_add(a: int, b: int) -> int:
        return a + b

    result = await observe_in_thread(sync_add, 2, 3)
    assert result == 5

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
    # Allow loop to deliver cancellation
    await asyncio.sleep(0.01)
    assert not worker_finished.is_set()

    worker_can_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert worker_finished.is_set()


@pytest.mark.asyncio
async def test_blocked_refresh_start_keeps_loop_live_and_cancellation_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify issue #223: SQLite I/O offloaded, loop stays responsive, cancellation is ordered."""
    proxy, ledger, _clock = _proxy(tmp_path)
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

    # Start first request which will block in begin_attempt in worker thread
    task1 = asyncio.create_task(proxy.load_refresh_token(client, token.token))
    assert await asyncio.to_thread(begin_started.wait, 5.0)

    # Event loop ticker MUST advance while worker is blocked
    ticks = 0
    for _ in range(5):
        await asyncio.sleep(0.005)
        ticks += 1
    assert ticks == 5

    # Start a second concurrent request for same token hash - marks overlapping
    task2 = asyncio.create_task(proxy.load_refresh_token(client, token.token))
    await asyncio.sleep(0.01)

    # Cancel task1 while blocked
    task1.cancel()
    await asyncio.sleep(0.01)

    # Release worker gate
    gate.set()

    with pytest.raises(asyncio.CancelledError):
        await task1

    res2 = await task2
    assert res2 is None  # Loaded token is None in mock

    # Cleanup tracking state
    assert len(proxy._refresh_inflight) == 0

    # Verify SQLite recorded events cleanly with terminal outcomes
    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT outcome, reason FROM refresh_events WHERE event_type='refresh' ORDER BY id ASC"
        ).fetchall()

    assert len(rows) == 2
    assert set(rows) == {("failure", "internal_error"), ("failure", "overlapping_attempt")}
    ledger.close()


def test_heartbeat_recovers_pending_checkpoint_and_handles_busy(tmp_path: Path) -> None:
    """Verify issue #226: heartbeat() retries maintenance when checkpoint is pending."""
    clock = FakeClock(100.0)
    ledger = RefreshLedger(tmp_path / "refresh.sqlite3", hmac_key=SIGNING_KEY, clock=clock)

    # Record an event to populate DB
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

    # Hold an open reader transaction with a read lock
    reader = sqlite3.connect(ledger.path)
    reader.execute("BEGIN DEFERRED")
    reader.execute("SELECT COUNT(*) FROM refresh_events").fetchone()

    # Manually flag checkpoint pending as if a prior checkpoint was busy
    ledger._checkpoint_pending = True

    # With reader holding active snapshot, maintenance checkpoint will be busy
    clock.advance(1.0)
    assert ledger.heartbeat(clock()) is False
    assert ledger._checkpoint_pending is True

    # Now close the reader lock
    reader.close()

    # Advance clock and call heartbeat again -> must maintain and succeed
    clock.advance(1.0)
    assert ledger.heartbeat(clock()) is True
    assert ledger._checkpoint_pending is False

    # Verify observer_heartbeat_at was persisted
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
    proxy, ledger, _clock = _proxy(tmp_path)
    client = _client("client-race")
    token = _refresh("same-token-string", client.client_id or "")

    req1_paused = asyncio.Event()
    req1_resume = asyncio.Event()

    async def hooked_super_exchange(self: Any, c: Any, r: Any, s: Any) -> OAuthToken:
        if r.token == token.token and not req1_paused.is_set():
            # First request enters exchange: pause here
            req1_paused.set()
            await req1_resume.wait()
            # Request 1 fails with upstream token not found (simulating race)
            raise TokenError(error="invalid_grant", error_description="Upstream token not found")
        # Request 2 succeeds
        return _success()

    # Hook superclass exchange
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", hooked_super_exchange)

    # Start request 1 (which will pause in hooked exchange)
    t1 = asyncio.create_task(proxy.exchange_refresh_token(client, token, ["openid"]))
    await req1_paused.wait()

    # While request 1 is paused, start request 2 for same token -> succeeds
    t2 = asyncio.create_task(proxy.exchange_refresh_token(client, token, ["openid"]))
    res2 = await t2
    assert res2.access_token == "upstream-access"  # noqa: S105 - synthetic assertion

    # Now resume request 1 -> it fails
    req1_resume.set()
    with pytest.raises(TokenError) as exc_info:
        await t1
    assert exc_info.value.error == "invalid_grant"

    # Both requests finished, in-flight must be empty
    assert len(proxy._refresh_inflight) == 0

    # In SQLite, request 1 must be recorded as overlapping_attempt, NOT mapping_missing
    with sqlite3.connect(ledger.path) as connection:
        rows = connection.execute(
            "SELECT outcome, reason FROM refresh_events WHERE event_type='refresh' ORDER BY id ASC"
        ).fetchall()

    assert len(rows) == 2
    assert rows[0] == ("failure", "overlapping_attempt")
    assert rows[1] == ("success", None)

    ledger.close()


@pytest.mark.asyncio
async def test_correlation_id_preserved_in_refresh_and_authorize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify issue #239a: request_id captures correlation_id.get() when present."""
    proxy, ledger, _clock = _proxy(tmp_path)
    client = _client("client-correlation")
    token = _refresh("token-correlation", client.client_id or "")

    async def mock_super_exchange(*_args: Any, **_kwargs: Any) -> OAuthToken:
        return _success()

    async def mock_super_auth(*_args: Any, **_kwargs: Any) -> str:
        return "https://idp.example/authorize?code=test"

    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", mock_super_exchange)
    monkeypatch.setattr(OAuthProxy, "authorize", mock_super_auth)

    # 1. Valid correlation ID
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

    assert len(rows) == 2
    assert rows[0] == ("refresh", valid_id)
    assert rows[1] == ("authorize", valid_id)

    # 2. Invalid correlation IDs fall back to _unknown
    assert _safe_request_id(None) == "_unknown"
    assert _safe_request_id("bad\nid") == "_unknown"
    assert _safe_request_id("a" * 129) == "_unknown"
    assert _safe_request_id("") == "_unknown"

    ledger.close()
