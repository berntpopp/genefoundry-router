"""Router runtime settings and registry loading."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from genefoundry_router.exceptions import RegistryError
from genefoundry_router.registry import BackendDef

AuthMode = Literal["none", "jwt", "oauth"]


class RouterSettings(BaseSettings):
    """Environment-driven runtime settings (prefix ``GF_``)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Transport / server
    GF_HOST: str = "127.0.0.1"
    GF_PORT: int = 8000
    GF_MCP_PATH: str = "/mcp"
    GF_LOG_LEVEL: str = "INFO"

    # Registry
    GF_SERVERS_FILE: str = "servers.yaml"

    # Tool search
    GF_SEARCH_MAX_RESULTS: int = 5

    # Outbound timeout (seconds) for calls to backends. Generous so slow backends
    # (e.g. spliceai cold ~60s) aren't cut off, while still bounding a hung backend.
    GF_BACKEND_TIMEOUT: float = 120.0

    # Inbound request limits (DoS/abuse guard). <=0 disables that limit.
    GF_MAX_BODY_BYTES: int = 4_000_000  # 4 MB cap on request bodies (413 over)
    GF_RATE_LIMIT_RPM: int = 0  # per-client requests/min (429 over); 0 = off, enable in prod
    GF_TRUSTED_PROXY_HOPS: int = 1  # trusted hops at the tail of X-Forwarded-For
    GF_METRICS_TOKEN: str | None = None  # optional bearer token for GET /metrics

    # Rewrite bare tool references in backend responses to namespaced form (Finding 1).
    GF_REWRITE_HINTS: bool = True

    # Discovery
    GF_POLL_INTERVAL: float = 0  # seconds; 0 disables the polling re-list
    GF_DRIFT_MODE: Literal["off", "warn", "enforce"] = "warn"
    GF_DRIFT_BASELINE: str | None = None

    # Transport security (R1.4 — MCP Origin/DNS-rebinding MUST)
    # NoDecode: suppress pydantic-settings' JSON pre-decode of complex env values so the
    # CSV string reaches the mode="before" validator below (pydantic-settings 2.14 behavior).
    GF_ALLOWED_HOSTS: Annotated[list[str], NoDecode] = []
    GF_ALLOWED_ORIGINS: Annotated[
        list[str], NoDecode
    ] = []  # CSV in env; [] = reject any present Origin
    GF_PUBLIC_BASE_URL: str | None = None  # public URL behind the proxy (OAuth resource URI)

    # Auth
    GF_AUTH_MODE: AuthMode = "none"
    # Explicit escape hatch: allow serving with GF_AUTH_MODE=none on a non-loopback bind.
    # Default False so an open, unauthenticated endpoint is never started by accident (R-sec.1).
    GF_ALLOW_INSECURE: bool = False
    GF_JWT_ISSUER: str | None = None
    GF_JWT_JWKS_URL: str | None = None
    GF_JWT_AUDIENCE: str | None = None
    GF_OAUTH_PROVIDER: str | None = None
    GF_OAUTH_CLIENT_ID: str | None = None
    GF_OAUTH_CLIENT_SECRET: str | None = None
    GF_OAUTH_BASE_URL: str | None = None
    GF_OAUTH_AUTHORIZE_URL: str | None = None
    GF_OAUTH_TOKEN_URL: str | None = None
    # Fixed secret for signing the router's OWN FastMCP JWT tokens (the OAuthProxy-minted
    # access/refresh tokens and the on-disk client store's encryption key). When unset,
    # fastmcp derives a (deterministic) key from GF_OAUTH_CLIENT_SECRET — stable, but it
    # couples token validity and the persisted client store to that secret's rotation.
    # Set an explicit value to decouple them: issued tokens + registered DCR clients then
    # survive a Keycloak client-secret rotation. MUST stay constant once set (rotating it
    # invalidates all live sessions and orphans the persisted client store).
    GF_OAUTH_JWT_SIGNING_KEY: str | None = None
    # OAuthProxy built-in consent ("Allow Access") screen. Keycloak is the real
    # authorization gate + branded login UI, so the proxy's own consent page is redundant
    # and unstyled; default "external" skips it for a unified single-page login.
    #   external → skip entirely (consent handled upstream)   true  → always show
    #   remember → show once per client, then silent          false → skip (dev-only warning)
    GF_OAUTH_REQUIRE_CONSENT: Literal["external", "remember", "true", "false"] = "external"

    @field_validator("GF_ALLOWED_HOSTS", "GF_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_csv_allowlist(cls, v: object) -> object:
        """Accept comma-separated allowlists from environment variables."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("GF_ALLOWED_HOSTS")
    @classmethod
    def _reject_host_wildcards(cls, value: list[str]) -> list[str]:
        if any("*" in item for item in value):
            raise ValueError("GF_ALLOWED_HOSTS must not contain wildcard entries")
        return value

    @field_validator("GF_METRICS_TOKEN", mode="before")
    @classmethod
    def _blank_metrics_token(cls, v: object) -> object:
        """Treat blank scrape-token env values as unset."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("GF_DRIFT_BASELINE", mode="before")
    @classmethod
    def _blank_drift_baseline(cls, v: object) -> object:
        """Treat blank overrides as a request to use the packaged reviewed baseline."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("GF_TRUSTED_PROXY_HOPS")
    @classmethod
    def _non_negative_hops(cls, v: int) -> int:
        """A negative hop count is invalid — it would silently disable XFF attribution."""
        if v < 0:
            raise ValueError("GF_TRUSTED_PROXY_HOPS must be >= 0")
        return v


def load_registry(path: str | Path, environ: Mapping[str, str]) -> list[BackendDef]:
    """Parse servers.yaml, merge ``defaults`` into each server, and resolve URLs.

    URLs come from ``environ[server.url_env]`` when present; a missing var leaves
    ``url=None`` (the caller decides whether to skip/warn). Raises RegistryError on
    a missing/malformed file, an invalid backend, or a duplicate namespace.
    """
    path = Path(path)
    if not path.exists():
        raise RegistryError(f"registry file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - exercised via malformed yaml
        raise RegistryError(f"invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RegistryError(f"{path} must be a mapping with 'servers'")

    defaults = raw.get("defaults") or {}
    servers = raw.get("servers")
    if not isinstance(servers, list) or not servers:
        raise RegistryError(f"{path} must define a non-empty 'servers' list")

    backends: list[BackendDef] = []
    seen_namespaces: set[str] = set()
    for entry in servers:
        if not isinstance(entry, dict):
            raise RegistryError(f"each server entry must be a mapping, got {entry!r}")
        merged = {**defaults, **entry}
        try:
            backend = BackendDef(**merged)
        except ValidationError as exc:
            raise RegistryError(f"invalid backend {entry.get('name', entry)!r}: {exc}") from exc
        if backend.namespace in seen_namespaces:
            raise RegistryError(f"duplicate namespace: {backend.namespace!r}")
        seen_namespaces.add(backend.namespace)
        backend.url = environ.get(backend.url_env)
        if backend.service_token_env is not None:
            raw_token = environ.get(backend.service_token_env)
            backend.service_token = raw_token.strip() if raw_token and raw_token.strip() else None
        backends.append(backend)
    return backends
