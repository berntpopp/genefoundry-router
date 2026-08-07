"""Refresh-proxy instrumentation without OAuth semantic changes or secret leakage."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp.server.auth import OAuthProxy
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.applications import Starlette

from genefoundry_router.oauth_proxy import (
    GeneFoundryOAuthProxy,
    classify_oauth_client,
)
from genefoundry_router.observability import OAUTH_REFRESH_ATTEMPTS, OAUTH_REFRESH_FAILURES
from genefoundry_router.refresh_observability import (
    MAX_WAL_BYTES,
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
) -> tuple[GeneFoundryOAuthProxy, RefreshLedger]:
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
    return proxy, ledger


def _refresh(token: str, client_id: str) -> RefreshToken:
    return RefreshToken(token=token, client_id=client_id, scopes=["openid"], expires_at=None)


def _success() -> OAuthToken:
    return OAuthToken(
        access_token="new-access-secret",  # noqa: S106 - synthetic fixture
        refresh_token="new-refresh-secret",  # noqa: S106 - synthetic fixture
        token_type="Bearer",  # noqa: S106 - OAuth token type
        expires_in=3600,
        scope="openid",
    )


def _one_failure_reason(ledger: RefreshLedger) -> str:
    failures = ledger.counter_snapshot().failures
    assert sum(failures.values()) == 1
    return next(iter(failures))[1]


@pytest.mark.parametrize(
    ("client_id", "client_name", "expected"),
    [
        ("https://chatgpt.com/.well-known/oauth-client/app", None, "chatgpt"),
        ("opaque", "ChatGPT Connector", "chatgpt"),
        ("https://claude.ai/oauth/client", None, "claude"),
        ("opaque", "Claude Desktop", "claude"),
        ("https://caller.example/?client=chatgpt", "Unrelated", "other"),
        ("https://[malformed", "Unrelated", "other"),
    ],
)
def test_client_classification_is_bounded(
    client_id: str, client_name: str | None, expected: str
) -> None:
    assert classify_oauth_client(_client(client_id, name=client_name)) == expected


@pytest.mark.asyncio
async def test_successful_refresh_delegates_once_then_records_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("https://chatgpt.com/.well-known/oauth-client/app")
    old_token = "old-refresh-secret"  # noqa: S105 - synthetic fixture
    loaded = _refresh(old_token, client.client_id or "")
    returned = _success()
    calls = {"load": 0, "exchange": 0}

    async def load_once(*_args: Any, **_kwargs: Any) -> RefreshToken:
        calls["load"] += 1
        return loaded

    async def exchange_once(*_args: Any, **_kwargs: Any) -> OAuthToken:
        calls["exchange"] += 1
        return returned

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_once)
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", exchange_once)
    try:
        assert await proxy.load_refresh_token(client, old_token) is loaded
        assert await proxy.exchange_refresh_token(client, loaded, ["openid"]) is returned

        token_hash = hashlib.sha256(old_token.encode()).hexdigest()
        client_hmac = ledger.client_hmac(client.client_id or "")
        assert calls == {"load": 1, "exchange": 1}
        assert ledger.classify_missing(token_hash, client_hmac, 2_000_001.0) == (
            "reuse_after_rotation"
        )
        assert ledger.counter_snapshot().successes == {"chatgpt": 1}
        with sqlite3.connect(ledger.path) as connection:
            dump = "\n".join(connection.iterdump())
        assert old_token not in dump
        assert returned.access_token not in dump
        assert returned.refresh_token not in dump
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("local", "local_not_found"),
        ("reuse", "reuse_after_rotation"),
        ("mismatch", "client_mismatch"),
        ("invalid", "jwt_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_missing_refresh_classifications_preserve_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected: str,
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("https://chatgpt.com/.well-known/oauth-client/app")
    token_client = "different-client" if case == "mismatch" else client.client_id or ""
    token = (
        "raw-invalid-refresh-secret"
        if case == "invalid"
        else proxy.jwt_issuer.issue_refresh_token(
            client_id=token_client,
            scopes=["openid"],
            jti=f"jti-{case}",
            expires_in=3600,
        )
    )
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if case == "reuse":
        ledger.record_rotation(token_hash, ledger.client_hmac(client.client_id or ""), 1_999_999)

    calls = 0

    async def missing(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", missing)
    try:
        assert await proxy.load_refresh_token(client, token) is None
        assert calls == 1
        assert _one_failure_reason(ledger) == expected
        assert proxy._refresh_inflight == {}
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_observer_failure_cannot_change_superclass_none_or_leak_inflight_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("https://chatgpt.com/.well-known/oauth-client/app")

    async def missing(*_args: Any, **_kwargs: Any) -> None:
        return None

    def observer_failure(*_args: Any, **_kwargs: Any) -> str:
        raise PermissionError("sensitive observer path")

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", missing)
    monkeypatch.setattr(ledger, "classify_missing", observer_failure)
    try:
        assert await proxy.load_refresh_token(client, "opaque-refresh") is None
        assert proxy._refresh_inflight == {}
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_post_success_observer_oserror_cannot_replace_oauth_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("https://chatgpt.com/.well-known/oauth-client/app")
    loaded = _refresh("successful-rotation", client.client_id or "")
    returned = _success()

    async def load_once(*_args: Any, **_kwargs: Any) -> RefreshToken:
        return loaded

    async def exchange_once(*_args: Any, **_kwargs: Any) -> OAuthToken:
        return returned

    def observer_failure(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("sensitive observer path")

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_once)
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", exchange_once)
    monkeypatch.setattr(ledger, "record_rotation", observer_failure)
    try:
        assert await proxy.load_refresh_token(client, loaded.token) is loaded
        assert await proxy.exchange_refresh_token(client, loaded, ["openid"]) is returned
        assert proxy._refresh_inflight == {}
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_metrics_observer_failure_cannot_replace_superclass_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("opaque-client")

    async def missing(*_args: Any, **_kwargs: Any) -> None:
        return None

    def metrics_failure(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("sensitive metrics detail")

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", missing)
    monkeypatch.setattr("genefoundry_router.oauth_proxy.record_refresh_attempt", metrics_failure)
    try:
        assert await proxy.load_refresh_token(client, "opaque-refresh") is None
        assert proxy._refresh_inflight == {}
    finally:
        ledger.close()


class UpstreamFailureError(Exception):
    def __init__(self, error: str) -> None:
        self.error = error
        super().__init__(f"sensitive upstream body for {error}")


@pytest.mark.parametrize(
    ("case", "expected_reason", "exception_type"),
    [
        ("jwt", "jwt_invalid", TokenError),
        ("mapping", "mapping_missing", TokenError),
        ("upstream_invalid", "upstream_invalid_grant", TokenError),
        ("upstream_other", "upstream_other", TokenError),
        ("internal", "internal_error", RuntimeError),
    ],
)
@pytest.mark.asyncio
async def test_exchange_failure_classifications_reraise_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_reason: str,
    exception_type: type[Exception],
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("https://claude.ai/oauth/client")
    token = "connector-refresh-secret"  # noqa: S105 - synthetic fixture
    loaded = _refresh(token, client.client_id or "")
    calls = {"load": 0, "exchange": 0}

    async def load_once(*_args: Any, **_kwargs: Any) -> RefreshToken:
        calls["load"] += 1
        return loaded

    async def fail_once(*_args: Any, **_kwargs: Any) -> OAuthToken:
        calls["exchange"] += 1
        if case == "jwt":
            raise TokenError("invalid_grant", "Invalid refresh token")
        if case == "mapping":
            raise TokenError("invalid_grant", "Refresh token mapping not found")
        if case.startswith("upstream"):
            code = "invalid_grant" if case == "upstream_invalid" else "server_error"
            try:
                raise UpstreamFailureError(code)
            except UpstreamFailureError as upstream:
                raise TokenError("invalid_grant", "Upstream refresh failed") from upstream
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_once)
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", fail_once)
    try:
        assert await proxy.load_refresh_token(client, token) is loaded
        with pytest.raises(exception_type) as caught:
            await proxy.exchange_refresh_token(client, loaded, ["openid"])
        assert calls == {"load": 1, "exchange": 1}
        assert _one_failure_reason(ledger) == expected_reason
        assert proxy._refresh_inflight == {}
        if isinstance(caught.value, TokenError):
            assert caught.value.error == "invalid_grant"
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_genuinely_overlapping_attempt_is_classified_and_map_cleans_in_finally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path, inflight_limit=1)
    client = _client("https://chatgpt.com/.well-known/oauth-client/app")
    token = "overlapping-refresh-secret"  # noqa: S105 - synthetic fixture
    loaded = _refresh(token, client.client_id or "")
    first_exchange_started = asyncio.Event()
    second_exchange_started = asyncio.Event()
    calls = 0

    async def load_once(*_args: Any, **_kwargs: Any) -> RefreshToken:
        return loaded

    async def exchange(*_args: Any, **_kwargs: Any) -> OAuthToken:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_exchange_started.set()
            await second_exchange_started.wait()
            return _success()
        second_exchange_started.set()
        raise TokenError("invalid_grant", "Refresh token mapping not found")

    async def refresh_flow() -> OAuthToken:
        current = await proxy.load_refresh_token(client, token)
        assert current is not None
        return await proxy.exchange_refresh_token(client, current, ["openid"])

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_once)
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", exchange)
    try:
        first = asyncio.create_task(refresh_flow())
        await first_exchange_started.wait()
        second = asyncio.create_task(refresh_flow())
        with pytest.raises(TokenError):
            await second
        assert await first == _success()
        assert len(proxy._refresh_inflight) == 0
        assert _one_failure_reason(ledger) == "overlapping_attempt"
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_framework_and_instrumentation_logs_exclude_refresh_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    proxy, ledger = _proxy(tmp_path)
    raw_client = "https://chatgpt.com/oauth/client?private=raw-query"
    client = _client(raw_client)
    raw_token = "raw-refresh-token-never-log"  # noqa: S105 - synthetic fixture
    full_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    raw_code = "raw-authorization-code-never-log"
    raw_query_error = "raw-callback-query-never-log"
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="fastmcp.server.auth.oauth_proxy.proxy")
    try:
        assert await proxy.load_refresh_token(client, raw_token) is None
        framework_log = logging.getLogger("fastmcp.server.auth.oauth_proxy.proxy")
        framework_log.debug("Authorization code not found: %s", raw_code)
        framework_log.debug(
            "Issued FastMCP tokens for client=%s (access_jti=%s, refresh_jti=%s)",
            raw_client,
            "raw-access-jti-never-log",
            "raw-refresh-jti-never-log",
        )
        callback = TestClient(Starlette(routes=proxy.get_routes(""))).get(
            "/auth/callback",
            params={
                "error": "access_denied",
                "error_description": raw_query_error,
                "state": "raw-state-query-never-log",
            },
        )
        assert callback.status_code == 400
        invalid_state = TestClient(Starlette(routes=proxy.get_routes(""))).get(
            "/auth/callback",
            params={"code": "untrusted-code", "state": "raw-state-query-never-log"},
        )
        assert invalid_state.status_code == 400
        rendered = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name.startswith(("fastmcp", "genefoundry_router"))
        )
        for forbidden in (
            raw_client,
            "raw-query",
            raw_token,
            full_hash,
            raw_code,
            raw_query_error,
            "raw-state-query-never-log",
            "raw-access-jti-never-log",
            "raw-refresh-jti-never-log",
        ):
            assert forbidden not in rendered
        assert "oauth detail omitted" in rendered.lower()
    finally:
        ledger.close()


def test_invalid_authorize_requests_do_not_record_forced_reauthorization(
    tmp_path: Path,
) -> None:
    proxy, ledger = _proxy(tmp_path)
    raw_client = "http://chatgpt.com/oauth/client?private=raw-query"
    raw_state = "raw-state-never-store"
    raw_pkce = "raw-pkce-never-store"
    app = Starlette(routes=proxy.get_routes(""))
    client = TestClient(app)
    try:
        unregistered = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "http://spoofed.example/client?private=unregistered",
                "redirect_uri": "https://connector.example/callback",
                "scope": "openid",
                "state": raw_state,
                "code_challenge": raw_pkce,
                "code_challenge_method": "S256",
            },
        )
        assert unregistered.status_code == 400

        asyncio.run(proxy.register_client(_client(raw_client)))
        invalid_redirect = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": raw_client,
                "redirect_uri": "https://attacker.example/callback",
                "scope": "openid",
                "state": raw_state,
                "code_challenge": raw_pkce,
                "code_challenge_method": "S256",
            },
        )
        assert invalid_redirect.status_code == 400
        with sqlite3.connect(ledger.path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM refresh_events WHERE event_type='authorize'"
            ).fetchone()[0]
        assert count == 0
    finally:
        ledger.close()


def test_validated_authorize_start_records_only_safe_correlation_fields(tmp_path: Path) -> None:
    proxy, ledger = _proxy(tmp_path)
    raw_client = "http://chatgpt.com/oauth/client?private=raw-query"
    raw_state = "raw-state-never-store"
    raw_pkce = "raw-pkce-never-store"
    asyncio.run(proxy.register_client(_client(raw_client)))
    try:
        response = TestClient(Starlette(routes=proxy.get_routes(""))).get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": raw_client,
                "redirect_uri": "https://connector.example/callback",
                "scope": "openid",
                "state": raw_state,
                "code_challenge": raw_pkce,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        with sqlite3.connect(ledger.path) as connection:
            row = connection.execute(
                """
                SELECT request_id, client_class, client_hmac, event_type, outcome
                FROM refresh_events WHERE event_type='authorize'
                """
            ).fetchone()
            dump = "\n".join(connection.iterdump())
        assert row == (
            "_unknown",
            "chatgpt",
            ledger.client_hmac(raw_client),
            "authorize",
            "started",
        )
        for forbidden in (raw_client, "raw-query", raw_state, raw_pkce):
            assert forbidden not in dump
    finally:
        ledger.close()


def test_unregistered_authorize_client_is_redacted_from_installed_handler_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    proxy, ledger = _proxy(tmp_path)
    raw_client = "http://spoofed.example/oauth/client?private=never-log"
    caplog.set_level(logging.INFO, logger="fastmcp.server.auth.handlers.authorize")
    try:
        response = TestClient(Starlette(routes=proxy.get_routes(""))).get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": raw_client,
                "redirect_uri": "https://connector.example/callback",
                "scope": "openid",
                "state": "opaque-state",
                "code_challenge": "challenge",
                "code_challenge_method": "S256",
            },
            headers={"Accept": "application/json"},
        )
        assert response.status_code == 400
        rendered = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.name == "fastmcp.server.auth.handlers.authorize"
        )
        assert rendered
        assert raw_client not in rendered
        assert "private=never-log" not in rendered
        assert "oauth detail omitted" in rendered.lower()
    finally:
        ledger.close()


def test_token_handler_early_scope_rejection_cleans_inflight_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client_info = _client("opaque-client")
    loaded = _refresh("early-return-refresh", client_info.client_id or "")
    asyncio.run(proxy.register_client(client_info))

    async def load_once(*_args: Any, **_kwargs: Any) -> RefreshToken:
        return loaded

    async def exchange_must_not_run(*_args: Any, **_kwargs: Any) -> OAuthToken:
        pytest.fail("invalid scope must return before refresh exchange")

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_once)
    monkeypatch.setattr(OAuthProxy, "exchange_refresh_token", exchange_must_not_run)
    before_attempts = OAUTH_REFRESH_ATTEMPTS.labels(client_class="other")._value.get()
    before_failures = OAUTH_REFRESH_FAILURES.labels(
        client_class="other", reason="local_rejected"
    )._value.get()
    try:
        response = TestClient(Starlette(routes=proxy.get_routes(""))).post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": loaded.token,
                "client_id": client_info.client_id,
                "scope": "openid extra",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_scope"
        assert proxy._refresh_inflight == {}
        assert proxy._refresh_attempt.get() is None
        snapshot = ledger.counter_snapshot()
        assert snapshot.attempts == {"other": 1}
        assert snapshot.successes == {}
        assert snapshot.failures == {("other", "local_rejected"): 1}
        with sqlite3.connect(ledger.path) as connection:
            rows = connection.execute(
                "SELECT outcome, reason FROM refresh_events WHERE event_type='refresh'"
            ).fetchall()
        assert rows == [("failure", "local_rejected")]
        assert OAUTH_REFRESH_ATTEMPTS.labels(client_class="other")._value.get() == (
            before_attempts + 1
        )
        assert OAUTH_REFRESH_FAILURES.labels(
            client_class="other", reason="local_rejected"
        )._value.get() == (before_failures + 1)
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_refresh_attempt_is_counted_at_successful_load_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy, ledger = _proxy(tmp_path)
    client = _client("opaque-client")
    loaded = _refresh("loaded-refresh", client.client_id or "")

    async def load_once(*_args: Any, **_kwargs: Any) -> RefreshToken:
        return loaded

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", load_once)
    before_attempts = OAUTH_REFRESH_ATTEMPTS.labels(client_class="other")._value.get()
    try:
        assert await proxy.load_refresh_token(client, loaded.token) == loaded
        snapshot = ledger.counter_snapshot()
        assert snapshot.attempts == {"other": 1}
        assert snapshot.successes == {}
        assert snapshot.failures == {}
        with sqlite3.connect(ledger.path) as connection:
            row = connection.execute(
                "SELECT outcome, reason FROM refresh_events WHERE event_type='refresh'"
            ).fetchone()
        assert row == ("started", None)
        assert OAUTH_REFRESH_ATTEMPTS.labels(client_class="other")._value.get() == (
            before_attempts + 1
        )
    finally:
        proxy._finish_abandoned_refresh_attempt()
        ledger.close()


@pytest.mark.asyncio
async def test_recovered_observer_write_outage_makes_decision_window_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 20 * 24 * 60 * 60
    clock = FakeClock(now)
    proxy, ledger = _proxy(tmp_path, clock=clock)
    ledger.record_startup(version="0.8.0", at=now - 7 * 24 * 60 * 60)
    for index in range(50):
        ledger.record_event(
            RefreshEvent(
                at=now - 60 + index / 100,
                request_id=f"baseline-{index}",
                client_class="other",
                client_hmac="f" * 64,
                event_type="refresh",
                outcome="success",
            )
        )
    client = _client("opaque-client")

    async def missing(*_args: Any, **_kwargs: Any) -> None:
        return None

    original_begin = ledger.begin_attempt

    def unavailable(*_args: Any, **_kwargs: Any) -> int:
        raise PermissionError("observer unavailable")

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", missing)
    monkeypatch.setattr(ledger, "begin_attempt", unavailable)
    assert await proxy.load_refresh_token(client, "unrecorded-refresh") is None

    monkeypatch.setattr(ledger, "begin_attempt", original_begin)
    clock.value += 10
    ledger.record_event(
        RefreshEvent(
            at=clock(),
            request_id="recovery-probe",
            client_class="other",
            client_hmac="f" * 64,
            event_type="authorize",
            outcome="started",
        )
    )
    try:
        report = ledger.report(clock())
        assert report.attempts == 50
        assert report.sample_status == "incomplete"
        assert report.decision == "incomplete"
        assert report.evidence_complete is False
        assert report.incomplete_reasons == ("availability_gap",)
        assert report.availability_gap_count == 1
        assert report.availability_gap_seconds == pytest.approx(10)
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_over_cap_wal_is_incomplete_without_replacing_superclass_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 22 * 24 * 60 * 60
    clock = FakeClock(now)
    proxy, ledger = _proxy(tmp_path, clock=clock)
    ledger.record_startup(version="0.8.0", at=now - 7 * 24 * 60 * 60)
    for index in range(50):
        ledger.record_event(
            RefreshEvent(
                at=now - 60 + index / 100,
                request_id=f"baseline-{index}",
                client_class="other",
                client_hmac="f" * 64,
                event_type="refresh",
                outcome="success",
            )
        )
    reader = sqlite3.connect(ledger.path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM refresh_events").fetchone()
    ledger._db.execute("PRAGMA wal_autocheckpoint=0")
    with ledger._db:
        ledger._db.execute("CREATE TABLE wal_capacity_fixture (payload BLOB NOT NULL)")
        ledger._db.execute(
            "INSERT INTO wal_capacity_fixture VALUES (zeroblob(?))",
            (MAX_WAL_BYTES + 4096,),
        )
    assert Path(f"{ledger.path}-wal").stat().st_size > MAX_WAL_BYTES

    async def missing(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(OAuthProxy, "load_refresh_token", missing)
    try:
        assert await proxy.load_refresh_token(_client("opaque-client"), "over-cap-refresh") is None
        report = ledger.report(clock())
        assert report.sample_status == "incomplete"
        assert report.decision == "incomplete"
        assert "wal_over_cap" in report.incomplete_reasons
    finally:
        reader.close()
        ledger.maintain(clock() + 1)
        ledger.close()
