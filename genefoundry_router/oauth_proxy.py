"""Router-owned OAuth metadata and JWT issuer compatibility seams.

FastMCP stores a bare-origin ``base_url`` as a Pydantic ``AnyHttpUrl``, whose
default string form adds ``/``.  That representation previously leaked into
metadata and router-issued JWT ``iss`` claims.  This module keeps one slashless
issuer identity while accepting only explicitly configured legacy identities
for a fixed migration window.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import structlog
from authlib.integrations.base_client.errors import OAuthError  # type: ignore[import-untyped]
from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.jwt_issuer import JWTIssuer
from joserfc.errors import JoseError
from mcp.server.auth.handlers.metadata import (
    MetadataHandler,
    ProtectedResourceMetadataHandler,
)
from mcp.server.auth.provider import AuthorizationParams, RefreshToken, TokenError
from mcp.server.auth.routes import build_metadata, cors_middleware
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import AnyHttpUrl, ConfigDict
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from genefoundry_router.config import (
    _canonical_oauth_issuer,
    _validate_oauth_legacy_issuers,
)
from genefoundry_router.observability import (
    install_oauth_proxy_privacy_filter,
    record_refresh_attempt,
    record_refresh_outcome,
)
from genefoundry_router.refresh_contract import validate_fastmcp_refresh_contract
from genefoundry_router.refresh_models import REFRESH_ATTEMPT_STALE_SECONDS
from genefoundry_router.refresh_observability import (
    RefreshEvent,
    RefreshLedger,
    RefreshLedgerCapacityError,
)

log = structlog.get_logger(__name__)
_REFRESH_INFLIGHT_TTL_SECONDS = REFRESH_ATTEMPT_STALE_SECONDS
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def classify_oauth_client(client: OAuthClientInformationFull) -> str:
    """Map client-controlled identity to one of three fixed metric classes."""
    return _classify_oauth_client_values(client.client_id or "", client.client_name)


def _classify_oauth_client_values(raw_id: str, client_name: str | None = None) -> str:
    try:
        hostname = (urlsplit(raw_id).hostname or "").lower()
    except ValueError:
        # OAuth client IDs may be opaque, client-controlled strings rather than URLs.
        hostname = ""
    name = (client_name or "").strip().lower()
    if hostname == "chatgpt.com" or hostname.endswith(".chatgpt.com") or name.startswith("chatgpt"):
        return "chatgpt"
    if hostname == "claude.ai" or hostname.endswith(".claude.ai") or name.startswith("claude"):
        return "claude"
    return "other"


def _safe_request_id(value: str | None) -> str:
    return value if value is not None and _SAFE_REQUEST_ID.fullmatch(value) else "_unknown"


@dataclass(slots=True)
class _InFlight:
    count: int
    started_at: float


@dataclass(slots=True)
class _RefreshAttempt:
    token_hash: str
    client_hmac: str
    client_class: str
    request_id: str
    started_at: float
    inflight_started_at: float
    overlapping: bool
    tracked: bool
    ledger_event_id: int | None = None
    metrics_started: bool = False


class _RefreshCleanupEndpoint:
    """ASGI-transparent token-route wrapper that clears abandoned observations."""

    def __init__(self, endpoint: ASGIApp, cleanup: Callable[[], None]) -> None:
        self._endpoint = endpoint
        self._cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self._endpoint(scope, receive, send)
        finally:
            self._cleanup()


def canonical_issuer(base_url: str) -> str:
    """Return the validated issuer URL without a trailing root slash."""
    return _canonical_oauth_issuer(base_url)


class CanonicalOAuthMetadata(OAuthMetadata):
    """OAuth metadata whose bare-origin URLs retain an empty path."""

    model_config = ConfigDict(url_preserve_empty_path=True)


class CanonicalProtectedResourceMetadata(ProtectedResourceMetadata):
    """Protected-resource metadata with byte-stable authorization servers."""

    model_config = ConfigDict(url_preserve_empty_path=True)


class TransitionalJWTIssuer(JWTIssuer):
    """Issue canonical JWTs and temporarily verify configured legacy issuers."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        signing_key: bytes,
        *,
        legacy_issuers: Sequence[str],
        legacy_accept_until: datetime,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        canonical = canonical_issuer(issuer)
        super().__init__(issuer=canonical, audience=audience, signing_key=signing_key)
        if legacy_accept_until.tzinfo is None:
            raise ValueError("legacy issuer deadline must include a UTC offset")
        self._legacy_accept_until = legacy_accept_until.astimezone(UTC)
        self._clock = clock or (lambda: datetime.now(UTC))
        validated_legacy_issuers = _validate_oauth_legacy_issuers(canonical, legacy_issuers)
        self._legacy_verifiers = tuple(
            JWTIssuer(issuer=value, audience=audience, signing_key=signing_key)
            for value in validated_legacy_issuers
        )

    def verify_token(
        self,
        token: str,
        expected_token_use: str = "access",  # noqa: S107 - token class, not a secret
    ) -> dict[str, Any]:
        """Verify canonically, then try configured legacy issuers before the deadline."""
        try:
            return super().verify_token(token, expected_token_use=expected_token_use)
        except JoseError as canonical_error:
            if self._clock().astimezone(UTC) >= self._legacy_accept_until:
                raise

            for verifier in self._legacy_verifiers:
                try:
                    return verifier.verify_token(token, expected_token_use=expected_token_use)
                except JoseError:
                    continue
            raise canonical_error


class GeneFoundryOAuthProxy(OAuthProxy):
    """FastMCP OAuth proxy with canonical metadata and transitional JWT checks."""

    def __init__(
        self,
        *,
        canonical_issuer_url: str,
        legacy_issuer_urls: Sequence[str],
        legacy_issuer_accept_until: datetime,
        refresh_ledger: RefreshLedger | None = None,
        refresh_clock: Callable[[], float] | None = None,
        refresh_inflight_limit: int = 1024,
        **kwargs: Any,
    ) -> None:
        if refresh_inflight_limit <= 0:
            raise ValueError("refresh in-flight limit must be positive")
        self._canonical_issuer_url = canonical_issuer(canonical_issuer_url)
        self._legacy_issuer_urls = _validate_oauth_legacy_issuers(
            self._canonical_issuer_url, legacy_issuer_urls
        )
        self._legacy_issuer_accept_until = legacy_issuer_accept_until
        self._refresh_ledger = refresh_ledger
        self._refresh_clock = refresh_clock or time.time
        self._refresh_inflight_limit = refresh_inflight_limit
        self._refresh_inflight: dict[str, _InFlight] = {}
        self._refresh_attempt: ContextVar[_RefreshAttempt | None] = ContextVar(
            f"refresh_attempt_{id(self)}", default=None
        )
        if refresh_ledger is not None:
            validate_fastmcp_refresh_contract()
        install_oauth_proxy_privacy_filter()
        super().__init__(**kwargs)

    def _begin_refresh_attempt(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> _RefreshAttempt:
        assert self._refresh_ledger is not None
        now = self._refresh_clock()
        stale = [
            token_hash
            for token_hash, entry in self._refresh_inflight.items()
            if now - entry.started_at >= _REFRESH_INFLIGHT_TTL_SECONDS
        ]
        for token_hash in stale:
            del self._refresh_inflight[token_hash]
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        entry = self._refresh_inflight.get(token_hash)
        overlapping = entry is not None
        tracked = entry is not None or len(self._refresh_inflight) < self._refresh_inflight_limit
        if entry is not None:
            entry.count += 1
            inflight_started_at = entry.started_at
        elif tracked:
            inflight_started_at = now
            self._refresh_inflight[token_hash] = _InFlight(1, now)
        else:
            inflight_started_at = now
        attempt = _RefreshAttempt(
            token_hash=token_hash,
            client_hmac=self._refresh_ledger.client_hmac(client.client_id or ""),
            client_class=classify_oauth_client(client),
            request_id=_safe_request_id(None),
            started_at=now,
            inflight_started_at=inflight_started_at,
            overlapping=overlapping,
            tracked=tracked,
        )
        self._refresh_attempt.set(attempt)
        return attempt

    def _finish_refresh_attempt(self, attempt: _RefreshAttempt) -> None:
        if attempt.tracked:
            entry = self._refresh_inflight.get(attempt.token_hash)
            if entry is not None and entry.started_at == attempt.inflight_started_at:
                entry.count -= 1
                if entry.count <= 0:
                    del self._refresh_inflight[attempt.token_hash]
        self._refresh_attempt.set(None)

    def _finish_abandoned_refresh_attempt(self) -> None:
        """Clear a loaded attempt when the SDK returns before exchange."""
        attempt = self._refresh_attempt.get()
        if attempt is not None:
            try:
                self._record_refresh(attempt, "failure", "local_rejected")
            finally:
                self._finish_refresh_attempt(attempt)

    def _note_observer_unavailable(self, reason: str = "write_failure") -> None:
        if self._refresh_ledger is None:
            return
        try:
            self._refresh_ledger.note_unavailable(at=self._refresh_clock(), reason=reason)
        except Exception as exc:
            log.error("refresh_observability_gap_failed", error_type=type(exc).__name__)

    def _note_observer_error(self, error: Exception) -> None:
        reason = (
            "wal_over_cap"
            if isinstance(error, RefreshLedgerCapacityError) and "WAL" in str(error)
            else "write_failure"
        )
        self._note_observer_unavailable(reason)

    def _record_refresh_start(self, attempt: _RefreshAttempt) -> None:
        assert self._refresh_ledger is not None
        try:
            attempt.ledger_event_id = self._refresh_ledger.begin_attempt(
                RefreshEvent(
                    at=attempt.started_at,
                    request_id=attempt.request_id,
                    client_class=attempt.client_class,
                    client_hmac=attempt.client_hmac,
                    event_type="refresh",
                    outcome="started",
                    token_hash_prefix=attempt.token_hash[:12],
                )
            )
        except Exception as exc:
            self._note_observer_error(exc)
            log.error("refresh_observability_write_failed", error_type=type(exc).__name__)
        try:
            record_refresh_attempt(attempt.client_class)
            attempt.metrics_started = True
        except Exception as exc:
            log.error("refresh_observability_metric_failed", error_type=type(exc).__name__)

    def _record_refresh(
        self,
        attempt: _RefreshAttempt,
        outcome: str,
        reason: str | None = None,
        reuse_delay: float | None = None,
    ) -> None:
        assert self._refresh_ledger is not None
        if attempt.ledger_event_id is not None:
            try:
                self._refresh_ledger.finish_attempt(
                    attempt.ledger_event_id,
                    outcome=outcome,
                    reason=reason,
                    reuse_delay_seconds=reuse_delay,
                )
            except Exception as exc:
                self._note_observer_error(exc)
                log.error("refresh_observability_write_failed", error_type=type(exc).__name__)
        if attempt.metrics_started:
            try:
                record_refresh_outcome(attempt.client_class, outcome, reason)
            except Exception as exc:
                log.error("refresh_observability_metric_failed", error_type=type(exc).__name__)

    def _classify_missing(
        self,
        client: OAuthClientInformationFull,
        attempt: _RefreshAttempt,
        refresh_token: str,
    ) -> tuple[str, float | None]:
        assert self._refresh_ledger is not None
        if attempt.overlapping:
            return "overlapping_attempt", None
        reason = self._refresh_ledger.classify_missing(
            attempt.token_hash, attempt.client_hmac, self._refresh_clock()
        )
        if reason != "local_not_found":
            delay = (
                self._refresh_ledger.reuse_delay_seconds(attempt.token_hash, self._refresh_clock())
                if reason == "reuse_after_rotation"
                else None
            )
            return reason, delay
        try:
            payload = self.jwt_issuer.verify_token(
                # The raw token is used only for superclass-equivalent verification.
                # It is never stored or logged.
                refresh_token,
                expected_token_use="refresh",  # noqa: S106 - JWT claim, not a credential
            )
        except Exception:
            return "jwt_invalid", None
        claimed_client = payload.get("client_id") or payload.get("sub")
        if claimed_client is not None and claimed_client != client.client_id:
            return "client_mismatch", None
        return "local_not_found", None

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """Observe local misses while delegating FastMCP semantics exactly once."""
        if self._refresh_ledger is None:
            return await super().load_refresh_token(client, refresh_token)
        try:
            attempt = self._begin_refresh_attempt(client, refresh_token)
        except Exception as exc:
            self._note_observer_error(exc)
            log.error("refresh_observability_start_failed", error_type=type(exc).__name__)
            return await super().load_refresh_token(client, refresh_token)
        self._record_refresh_start(attempt)
        try:
            loaded = await super().load_refresh_token(client, refresh_token)
        except BaseException:
            try:
                self._record_refresh(attempt, "failure", "internal_error")
            finally:
                self._finish_refresh_attempt(attempt)
            raise
        if loaded is None:
            try:
                try:
                    reason, delay = self._classify_missing(client, attempt, refresh_token)
                except Exception as exc:
                    self._note_observer_error(exc)
                    log.error("refresh_observability_read_failed", error_type=type(exc).__name__)
                    reason, delay = "internal_error", None
                self._record_refresh(attempt, "failure", reason, delay)
            finally:
                self._finish_refresh_attempt(attempt)
        return loaded

    @staticmethod
    def _exchange_failure_reason(error: TokenError) -> str:
        description = error.error_description or ""
        if description == "Invalid refresh token":
            return "jwt_invalid"
        if description in {
            "Refresh token mapping not found",
            "Upstream token not found",
            "Refresh not supported for this token",
        }:
            return "mapping_missing"
        cause = error.__cause__
        if isinstance(cause, OAuthError):
            code = cause.error
            if code == "invalid_grant":
                return "upstream_invalid_grant"
            return "upstream_other"
        if description.startswith("Upstream refresh failed"):
            return "upstream_other"
        return "internal_error"

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Observe the exchange result without retrying, reordering, or replacing it."""
        if self._refresh_ledger is None:
            return await super().exchange_refresh_token(client, refresh_token, scopes)
        attempt = self._refresh_attempt.get()
        expected_hash = hashlib.sha256(refresh_token.token.encode()).hexdigest()
        if attempt is not None and attempt.token_hash != expected_hash:
            # FastMCP's current contract returns the original raw token verbatim from
            # load_refresh_token. If that ever changes, terminate the one observation
            # already counted and preserve OAuth behavior without inventing a second
            # attempt for the same request.
            try:
                self._record_refresh(attempt, "failure", "internal_error")
                log.error("refresh_observability_token_contract_changed")
            finally:
                self._finish_refresh_attempt(attempt)
            return await super().exchange_refresh_token(client, refresh_token, scopes)
        if attempt is None:
            try:
                attempt = self._begin_refresh_attempt(client, refresh_token.token)
            except Exception as exc:
                self._note_observer_error(exc)
                log.error("refresh_observability_start_failed", error_type=type(exc).__name__)
                return await super().exchange_refresh_token(client, refresh_token, scopes)
            self._record_refresh_start(attempt)
        try:
            result = await super().exchange_refresh_token(client, refresh_token, scopes)
        except TokenError as exc:
            reason = (
                "overlapping_attempt" if attempt.overlapping else self._exchange_failure_reason(exc)
            )
            self._record_refresh(attempt, "failure", reason)
            raise
        except BaseException:
            self._record_refresh(attempt, "failure", "internal_error")
            raise
        else:
            try:
                self._refresh_ledger.record_rotation(
                    attempt.token_hash, attempt.client_hmac, self._refresh_clock()
                )
            except Exception as exc:
                self._note_observer_error(exc)
                log.error("refresh_observability_write_failed", error_type=type(exc).__name__)
            self._record_refresh(attempt, "success")
            return result
        finally:
            self._finish_refresh_attempt(attempt)

    async def _record_authorize(self, client: OAuthClientInformationFull) -> None:
        if self._refresh_ledger is None:
            return
        try:
            raw_client_id = client.client_id or ""
            event = RefreshEvent(
                at=self._refresh_clock(),
                request_id=_safe_request_id(None),
                client_class=classify_oauth_client(client),
                client_hmac=self._refresh_ledger.client_hmac(raw_client_id),
                event_type="authorize",
                outcome="started",
            )
            self._refresh_ledger.record_event(event)
        except Exception as exc:
            self._note_observer_error(exc)
            log.error("refresh_observability_write_failed", error_type=type(exc).__name__)

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Record only a validated authorization that successfully starts upstream."""
        result = await super().authorize(client, params)
        await self._record_authorize(client)
        return result

    def set_mcp_path(self, mcp_path: str | None) -> None:
        """Install the router issuer after FastMCP computes the resource audience."""
        super().set_mcp_path(mcp_path)
        self._jwt_issuer = TransitionalJWTIssuer(
            issuer=self._canonical_issuer_url,
            audience=str(self._resource_url),
            signing_key=self._jwt_signing_key,
            legacy_issuers=self._legacy_issuer_urls,
            legacy_accept_until=self._legacy_issuer_accept_until,
        )

    def _authorization_server_metadata(self) -> CanonicalOAuthMetadata:
        registration = self.client_registration_options or ClientRegistrationOptions()
        revocation = self.revocation_options or RevocationOptions()
        assert self.base_url is not None
        metadata = build_metadata(
            self.base_url,
            self.service_documentation_url,
            registration,
            revocation,
        )
        if self._cimd_manager is not None:
            metadata.client_id_metadata_document_supported = True
            existing = metadata.token_endpoint_auth_methods_supported or []
            metadata.token_endpoint_auth_methods_supported = [
                *existing,
                "private_key_jwt",
                "none",
            ]
        payload = metadata.model_dump(mode="json")
        payload["issuer"] = self._canonical_issuer_url
        return CanonicalOAuthMetadata.model_validate(payload)

    def _protected_resource_metadata(self) -> CanonicalProtectedResourceMetadata:
        registration = self.client_registration_options
        supported_scopes = (
            registration.valid_scopes
            if registration is not None and registration.valid_scopes
            else self.required_scopes
        )
        assert self._resource_url is not None
        return CanonicalProtectedResourceMetadata(
            resource=self._resource_url,
            # Pydantic validates the string under this model's empty-path-preserving
            # config. The cast satisfies the inherited SDK model's static annotation.
            authorization_servers=[cast(AnyHttpUrl, self._canonical_issuer_url)],
            scopes_supported=supported_scopes,
        )

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Replace canonical metadata and observe the existing authorize endpoint."""
        routes = super().get_routes(mcp_path)
        authorization_handler = MetadataHandler(self._authorization_server_metadata())
        protected_handler = ProtectedResourceMetadataHandler(self._protected_resource_metadata())
        replaced: list[Route] = []

        for route in routes:
            endpoint: Any
            if route.path.startswith("/.well-known/oauth-authorization-server"):
                endpoint = cors_middleware(authorization_handler.handle, ["GET", "OPTIONS"])
            elif route.path.startswith("/.well-known/oauth-protected-resource"):
                endpoint = cors_middleware(protected_handler.handle, ["GET", "OPTIONS"])
            elif route.path == "/token" and self._refresh_ledger is not None:
                endpoint = _RefreshCleanupEndpoint(
                    cast(ASGIApp, route.endpoint),
                    self._finish_abandoned_refresh_attempt,
                )
            else:
                replaced.append(route)
                continue
            replaced.append(
                Route(
                    path=route.path,
                    endpoint=endpoint,
                    methods=route.methods or ["GET", "OPTIONS"],
                    name=route.name,
                    include_in_schema=route.include_in_schema,
                )
            )

        return replaced
