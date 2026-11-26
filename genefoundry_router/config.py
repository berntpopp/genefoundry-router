"""Router runtime settings and registry loading."""

from __future__ import annotations

import ipaddress
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from genefoundry_router.exceptions import RegistryError
from genefoundry_router.registry import BackendDef
from genefoundry_router.search_expansion import BUILTIN as BUILTIN_EXPANSIONS
from genefoundry_router.search_expansion import load_expansion_file
from genefoundry_router.search_rerank import MAX_CHOICE_OPTIONS, rerank_config

AuthMode = Literal["none", "jwt", "oauth"]
DeploymentMode = Literal["development", "production"]


def _canonical_oauth_issuer(base_url: str) -> str:
    """Validate an OAuth issuer URL and remove only its trailing root slash."""
    return str(AnyHttpUrl(base_url)).rstrip("/")


class SlashlessIssuerUrl(AnyHttpUrl):
    """An AnyHttpUrl whose bare-origin form renders without the trailing ``/``.

    FastMCP writes the RFC 9207 ``iss`` on every client redirect as ``str(issuer_url)``;
    a plain AnyHttpUrl renders ``https://genefoundry.org/``, which strict clients reject
    because the metadata ``issuer`` is the slashless canonical value.
    """

    def __str__(self) -> str:
        text = super().__str__()
        return text[:-1] if self.path == "/" and text.endswith("/") else text


def _validate_oauth_legacy_issuers(
    canonical: str, legacy_issuers: Sequence[str]
) -> tuple[str, ...]:
    """Allow only the one historical root-slash alias for this transition."""
    configured = tuple(legacy_issuers)
    expected = (f"{_canonical_oauth_issuer(canonical)}/",)
    if configured not in ((), expected):
        raise ValueError(f"legacy issuers must be empty or exactly {list(expected)!r}")
    return configured


class RouterSettings(BaseSettings):
    """Environment-driven runtime settings (prefix ``GF_``)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        # Validation errors must never echo raw settings: they can hold secrets (rerank
        # API key, OAuth client secret) and startup errors land in container logs.
        hide_input_in_errors=True,
    )

    # Transport / server
    GF_HOST: str = "127.0.0.1"
    GF_PORT: int = 8000
    GF_MCP_PATH: str = "/mcp"
    GF_LOG_LEVEL: str = "INFO"
    # Reachability is a deployment concern, not a socket-address heuristic. Production
    # Compose sets this explicitly because a loopback listener may sit behind a proxy.
    GF_DEPLOYMENT_MODE: DeploymentMode = "development"

    # Registry
    GF_SERVERS_FILE: str = "servers.yaml"
    # Research workflow skills served as skill:// resources (#336); must exist.
    GF_SKILLS_DIR: str = "skills"

    # Federation Standard v1 §3 (#410). Community backends load only from their own file,
    # only on an instance that opts in, and only with drift enforcement on.
    GF_COMMUNITY_SERVERS_FILE: str | None = None
    GF_ALLOW_COMMUNITY_BACKENDS: bool = False
    GF_TOMBSTONES_FILE: str = "registry/tombstones.yaml"
    # CSV of community backend names/namespaces to disable without editing the file.
    GF_DISABLED_BACKENDS: Annotated[list[str], NoDecode] = []
    # patient: no community backends and no reranker that sends queries off the host.
    GF_PRIVACY_PROFILE: Literal["default", "patient"] = "default"
    # Federation Standard v1 §5 (#412): community response, tool-list and argument caps.
    GF_COMMUNITY_MAX_RESPONSE_BYTES: int = Field(default=262_144, ge=1)
    GF_COMMUNITY_MAX_TOOLS: int = Field(default=32, ge=1)
    GF_COMMUNITY_MAX_ARG_BYTES: int = Field(default=8_192, ge=1)
    GF_COMMUNITY_MAX_INFLIGHT: int = Field(default=4, ge=1)

    # Tool search
    GF_SEARCH_MAX_RESULTS: int = 5
    # Offline document expansion folded into the BM25 index: "builtin" (packaged
    # data/tool-expansions.json), "off", or a path to a file in the same format.
    GF_SEARCH_EXPANSIONS: str = "builtin"
    # Optional second-stage reranker over the BM25 shortlist via the System One API
    # (hosted Jev through TypeSafe/OpenRouter, or a self-hosted OneJev). Off by default;
    # when on, the search query is sent to GF_SEARCH_RERANK_URL. Fails open to BM25.
    GF_SEARCH_RERANK: Literal["off", "systemone"] = "off"
    GF_SEARCH_RERANK_URL: str | None = None  # full endpoint, e.g. .../api/v1/systemone
    GF_SEARCH_RERANK_MODEL: str = "jev-latest"
    GF_SEARCH_RERANK_API_KEY: SecretStr | None = None  # the router's own key, never the caller's
    GF_SEARCH_RERANK_QUESTION: Literal["noul", "choice"] = "noul"
    GF_SEARCH_RERANK_POOL: int = 20  # BM25 candidates scored per search
    GF_SEARCH_RERANK_MIN_SCORE: float = 0.0  # noul P(yes) floor; 0 keeps every candidate
    GF_SEARCH_RERANK_TIMEOUT: float = 3.0  # seconds for the whole round-trip

    # Outbound timeout (seconds) for calls to backends. Generous so slow backends
    # (e.g. spliceai cold ~60s) aren't cut off, while still bounding a hung backend.
    GF_BACKEND_TIMEOUT: float = 120.0

    # Inbound request limits (DoS/abuse guard). <=0 disables that limit.
    GF_MAX_BODY_BYTES: int = 4_000_000  # 4 MB cap on request bodies (413 over)
    GF_RATE_LIMIT_RPM: int = 0  # default per-key requests/min (429 over); 0 = off, enable in prod
    # Pre-auth per-IP DoS floor; unset = GF_RATE_LIMIT_RPM (the pre-0.13 behaviour). With
    # auth on, raise it above the per-account limits so NAT-shared callers are not starved.
    GF_RATE_LIMIT_IP_RPM: int | None = None
    # Per-account overrides (JSON object): verified token subject or client id -> positive
    # requests/min or "unlimited". Example: {"gfbench": 600, "<owner-sub>": "unlimited"}.
    GF_RATE_LIMIT_OVERRIDES: dict[str, StrictInt | Literal["unlimited"]] = {}
    GF_TRUSTED_PROXY_HOPS: int = 1  # trusted hops at the tail of X-Forwarded-For
    GF_METRICS_TOKEN: str | None = None  # optional bearer token for GET /metrics
    # Durable, privacy-bounded OAuth refresh-rotation measurements. Production Compose
    # places this beside FastMCP's persistent state on the existing /data volume.
    GF_REFRESH_OBSERVABILITY_DB: str | None = None
    # Development-only acknowledgement for an authenticated local router without the
    # production observability controls. It never weakens production checks.
    GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY: bool = False

    # Rewrite bare tool references in backend responses to namespaced form (Finding 1).
    GF_REWRITE_HINTS: bool = True

    # Pinned tools in tools/list (#320): default = every servers.yaml entry point; minimal =
    # GF_PINNED_MINIMAL; none = search_tools + call_tool only. A request may override it with
    # the X-GF-Pinned-Mode header (listing, instructions, hint call form). Unlisted tools stay callable via call_tool.
    GF_PINNED_MODE: Literal["default", "minimal", "none"] = "default"
    GF_PINNED_MINIMAL: list[str] = [
        "hgnc_resolve_symbol",
        "gnomad_resolve_variant_id",
        "clinvar_get_variant",
        "clingen_get_gene_validity",
        "mondo_search_diseases",
        "hpo_search_terms",
    ]
    # Listed inputSchema form: standard = the backends' own schemas; strict = the OpenAI
    # strict projection (#293: every field required + nullable, call_tool.arguments a JSON
    # string); slim = pinned tools trimmed to required + <=2 optional params and a one-sentence
    # description (calls still accept every param). A request may override it with the
    # X-GF-Schema-Mode header.
    GF_SCHEMA_MODE: Literal["standard", "strict", "slim"] = "standard"

    # Discovery
    GF_POLL_INTERVAL: float = 0  # seconds; 0 disables the polling re-list
    GF_DRIFT_MODE: Literal["off", "warn", "enforce"] = "warn"
    GF_DRIFT_BASELINE: str | None = None
    # Federation Standard v1 §4 (#411), community instance only: one reviewed pin file per
    # backend, a kill-switch file of namespaces (re-read every poll and <=5 s on calls), and
    # the latch state file the `release` command edits (without it a restart clears latches).
    GF_COMMUNITY_PINS_DIR: str = "registry/pins/community"
    GF_KILL_SWITCH_FILE: str | None = None
    GF_QUARANTINE_FILE: str | None = None

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
    # Router-issued token identity. These defaults encode the 2026-08-07 release
    # transition and must not be calculated from process start time.
    GF_OAUTH_CANONICAL_ISSUER: str = "https://genefoundry.org"
    GF_OAUTH_LEGACY_ISSUERS: Annotated[list[str], NoDecode] = ["https://genefoundry.org/"]
    GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL: datetime = datetime(2026, 9, 6, tzinfo=UTC)
    # Fixed secret for signing the router's OWN FastMCP JWT tokens (the OAuthProxy-minted
    # access/refresh tokens and the on-disk client store's encryption key). When unset,
    # fastmcp derives a (deterministic) key from GF_OAUTH_CLIENT_SECRET — stable, but it
    # couples token validity and the persisted client store to that secret's rotation.
    # Set an explicit value to decouple them: issued tokens + registered DCR clients then
    # survive a Keycloak client-secret rotation. MUST stay constant once set (rotating it
    # invalidates all live sessions and orphans the persisted client store).
    GF_OAUTH_JWT_SIGNING_KEY: str | None = None
    # Lifetime of the router's FastMCP reference access token, not the upstream IdP bearer
    # token. The OAuthProxy validates (and when needed refreshes) the upstream token on each
    # request, so this can span a normal working day without extending the IdP token's
    # exposure window. Keep a deliberate upper bound: a connector-held bearer token must not
    # become a multi-day credential.
    GF_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS: int = Field(default=43_200, ge=300, le=86_400)
    # OAuthProxy built-in consent ("Allow Access") screen. Keycloak is the real
    # authorization gate + branded login UI, so the proxy's own consent page is redundant
    # and unstyled; default "external" skips it for a unified single-page login.
    #   external → skip entirely (consent handled upstream)   true  → always show
    #   remember → show once per client, then silent          false → skip (dev-only warning)
    GF_OAUTH_REQUIRE_CONSENT: Literal["external", "remember", "true", "false"] = "external"

    @field_validator(
        "GF_ALLOWED_HOSTS",
        "GF_ALLOWED_ORIGINS",
        "GF_OAUTH_LEGACY_ISSUERS",
        "GF_DISABLED_BACKENDS",
        mode="before",
    )
    @classmethod
    def _split_csv_allowlist(cls, v: object) -> object:
        """Accept comma-separated allowlists from environment variables."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL")
    @classmethod
    def _utc_legacy_issuer_deadline(cls, value: datetime) -> datetime:
        """Require one absolute instant so the compatibility window cannot slide."""
        if value.tzinfo is None:
            raise ValueError("GF_OAUTH_LEGACY_ISSUER_ACCEPT_UNTIL must include a UTC offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _legacy_issuer_is_the_exact_root_slash_alias(self) -> RouterSettings:
        """Reject widening the temporary issuer trust set through configuration."""
        _validate_oauth_legacy_issuers(
            self.GF_OAUTH_CANONICAL_ISSUER,
            self.GF_OAUTH_LEGACY_ISSUERS,
        )
        return self

    @field_validator("GF_ALLOWED_HOSTS")
    @classmethod
    def _reject_host_wildcards(cls, value: list[str]) -> list[str]:
        if any("*" in item for item in value):
            raise ValueError("GF_ALLOWED_HOSTS must not contain wildcard entries")
        return value

    @field_validator(
        "GF_METRICS_TOKEN",
        "GF_REFRESH_OBSERVABILITY_DB",
        "GF_COMMUNITY_SERVERS_FILE",
        mode="before",
    )
    @classmethod
    def _blank_optional_string(cls, v: object) -> object:
        """Treat blank optional string settings as unset."""
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

    @field_validator("GF_SEARCH_RERANK_URL", "GF_SEARCH_RERANK_API_KEY", mode="before")
    @classmethod
    def _blank_rerank_optional(cls, v: object) -> object:
        """Blank URL/key in an env profile means unset (e.g. a keyless self-hosted OneJev)."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("GF_SEARCH_EXPANSIONS")
    @classmethod
    def _expansion_source_is_loadable(cls, v: str) -> str:
        """Fail at startup, not at the first search, on an unusable expansion file."""
        choice = v.strip()
        if choice.lower() not in {"", "off", BUILTIN_EXPANSIONS}:
            load_expansion_file(Path(choice))
        return v

    @model_validator(mode="after")
    def _rerank_settings_are_usable(self) -> RouterSettings:
        """Validate the reranker only when enabled, so a half-filled profile can stay off."""
        if self.GF_SEARCH_RERANK == "off":
            return self
        if not self.GF_SEARCH_RERANK_URL:
            raise ValueError("GF_SEARCH_RERANK=systemone requires GF_SEARCH_RERANK_URL")
        if not self.GF_SEARCH_MAX_RESULTS <= self.GF_SEARCH_RERANK_POOL <= MAX_CHOICE_OPTIONS:
            raise ValueError(
                "GF_SEARCH_RERANK_POOL must be between GF_SEARCH_MAX_RESULTS and "
                f"{MAX_CHOICE_OPTIONS}"
            )
        try:
            rerank_config(self)
        except ValidationError as exc:
            raise ValueError(f"invalid GF_SEARCH_RERANK_* settings: {exc}") from exc
        return self

    @model_validator(mode="after")
    def _community_and_privacy_guards(self) -> RouterSettings:
        """Federation Standard v1 §3: refuse startup rather than run community unguarded."""
        if self.GF_COMMUNITY_SERVERS_FILE:
            if not self.GF_ALLOW_COMMUNITY_BACKENDS:
                raise ValueError(
                    "GF_COMMUNITY_SERVERS_FILE is set but GF_ALLOW_COMMUNITY_BACKENDS is false"
                )
            if self.GF_DRIFT_MODE != "enforce":
                raise ValueError("community backends require GF_DRIFT_MODE=enforce")
        if self.GF_ALLOW_COMMUNITY_BACKENDS and not self.GF_QUARANTINE_FILE:
            # #411: an in-memory latch would be cleared by a restart.
            raise ValueError("GF_ALLOW_COMMUNITY_BACKENDS=true requires GF_QUARANTINE_FILE")
        if self.GF_PRIVACY_PROFILE == "patient":
            if self.GF_COMMUNITY_SERVERS_FILE:
                raise ValueError("GF_PRIVACY_PROFILE=patient forbids community backends")
            if self.GF_SEARCH_RERANK != "off" and not _is_local_url(
                self.GF_SEARCH_RERANK_URL or ""
            ):
                raise ValueError(
                    "GF_PRIVACY_PROFILE=patient requires a loopback or private reranker URL"
                )
        return self

    @field_validator("GF_RATE_LIMIT_OVERRIDES")
    @classmethod
    def _valid_rate_overrides(
        cls, v: dict[str, int | Literal["unlimited"]]
    ) -> dict[str, int | Literal["unlimited"]]:
        """Keys are exact account ids; values a positive limit or ``"unlimited"``.

        ``0`` is rejected rather than guessed at (block? unlimited?).
        """
        for key, value in v.items():
            if not key or key != key.strip() or len(key) > 255:
                raise ValueError("GF_RATE_LIMIT_OVERRIDES keys must be non-empty account ids")
            if isinstance(value, int) and value < 1:
                raise ValueError('GF_RATE_LIMIT_OVERRIDES values must be >= 1 or "unlimited"')
        return v

    @model_validator(mode="after")
    def _override_does_not_name_the_shared_oauth_client(self) -> RouterSettings:
        """Every interactive OAuth user's upstream token carries the router's own client id
        as ``azp``; an override keyed on it would silently apply to all of them."""
        if self.GF_OAUTH_CLIENT_ID and self.GF_OAUTH_CLIENT_ID in self.GF_RATE_LIMIT_OVERRIDES:
            raise ValueError(
                "GF_RATE_LIMIT_OVERRIDES must not name GF_OAUTH_CLIENT_ID (shared by every "
                "OAuth user); key on the account subject instead"
            )
        return self

    @field_validator("GF_TRUSTED_PROXY_HOPS")
    @classmethod
    def _non_negative_hops(cls, v: int) -> int:
        """A negative hop count is invalid — it would silently disable XFF attribution."""
        if v < 0:
            raise ValueError("GF_TRUSTED_PROXY_HOPS must be >= 0")
        return v

    @model_validator(mode="after")
    def _development_unsafe_observability_is_never_a_production_setting(self) -> RouterSettings:
        """Reject a development-only acknowledgement in the production profile."""
        if (
            self.GF_DEPLOYMENT_MODE == "production"
            and self.GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY
        ):
            raise ValueError(
                "GF_ALLOW_DEVELOPMENT_UNSAFE_OBSERVABILITY is valid only in development mode"
            )
        return self

    @model_validator(mode="after")
    def _jwt_audience_must_match_the_advertised_resource(self) -> RouterSettings:
        """A mismatch makes the router mint tokens it then rejects."""
        if self.GF_AUTH_MODE == "oauth" and self.GF_PUBLIC_BASE_URL and self.GF_JWT_AUDIENCE:
            expected = (
                f"{self.GF_PUBLIC_BASE_URL.rstrip('/')}/{self.GF_MCP_PATH.strip('/')}".rstrip("/")
            )
            if self.GF_JWT_AUDIENCE.rstrip("/") != expected:
                raise ValueError(
                    f"GF_JWT_AUDIENCE ({self.GF_JWT_AUDIENCE}) must equal "
                    f"GF_PUBLIC_BASE_URL + GF_MCP_PATH ({expected}) — otherwise the router "
                    "advertises one resource identifier and validates another."
                )
        return self


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
        if not backend.url_env:
            raise RegistryError(f"backend {backend.name!r} has no url_env")
        backend.url = environ.get(backend.url_env)
        if backend.service_token_env is not None:
            raw_token = environ.get(backend.service_token_env)
            backend.service_token = raw_token.strip() if raw_token and raw_token.strip() else None
        backends.append(backend)
    return backends


def _is_local_url(url: str) -> bool:
    """True for a loopback/private IP, ``localhost`` or a single-label (container) host name."""
    host = urlsplit(url).hostname or ""
    # ponytail: no DNS lookup; a dotted name counts as public even if it resolves privately.
    if host == "localhost" or (host and "." not in host and ":" not in host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


COMMUNITY_REQUIRED = (
    "name",
    "namespace",
    "url",
    "operator",
    "contact",
    "ownership",
    "server_name",
    "source_name",
    "data_egress",
)
COMMUNITY_FORBIDDEN = (
    "trust",
    "entrypoints",
    "transform",
    "url_env",
    "service_token_env",
    "enabled",  # disabling uses only the `disabled:` list or GF_DISABLED_BACKENDS
)


def _read_yaml_mapping(path: Path) -> dict[str, object]:
    if not path.exists():
        raise RegistryError(f"registry file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegistryError(f"{path} must be a mapping")
    return raw


def _mapping_list(raw: dict[str, object], key: str, path: Path) -> list[dict[str, object]]:
    items = raw.get(key) or []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise RegistryError(f"{path}: {key!r} must be a list of mappings")
    return items


def _is_conforming_community_url(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and port in (None, 443)
        and parts.path == "/mcp"
        and not parts.query
        and not parts.fragment
    )


def load_community_registry(
    path: str | Path,
    core: Sequence[BackendDef],
    tombstones_path: str | Path,
    disabled: Collection[str] = (),
) -> list[BackendDef]:
    """Load ``GF_COMMUNITY_SERVERS_FILE`` (Federation Standard v1 §3).

    Every entry is forced to ``trust=community``. Entries under ``disabled:`` reserve their
    name/namespace but are not loaded; ``disabled`` (GF_DISABLED_BACKENDS) loads matching
    entries with ``enabled=False``. Raises RegistryError on any rule violation.
    """
    path = Path(path)
    raw = _read_yaml_mapping(path)
    if unknown := set(raw) - {"servers", "disabled"}:
        raise RegistryError(f"{path}: unknown top-level keys {sorted(unknown)}")
    servers = _mapping_list(raw, "servers", path)
    retired = _mapping_list(raw, "disabled", path)
    tombstones_path = Path(tombstones_path)
    tombs = _mapping_list(_read_yaml_mapping(tombstones_path), "tombstones", tombstones_path)
    tombstoned = {str(t[k]) for t in tombs for k in ("name", "namespace") if k in t}
    core_names = {b.name for b in core} | {b.namespace for b in core}

    seen: set[str] = set()
    for entry in [*servers, *retired]:
        for key in ("name", "namespace"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise RegistryError(f"{path}: community entry {entry!r} is missing {key!r}")
            if value in core_names:
                raise RegistryError(f"community {key} {value!r} is used by a core entry")
        keys = {str(entry["name"]), str(entry["namespace"])}
        if keys & seen:
            raise RegistryError(f"duplicate community name/namespace: {sorted(keys & seen)!r}")
        seen |= keys

    backends: list[BackendDef] = []
    for entry in servers:
        label = entry["name"]
        if forbidden := [k for k in COMMUNITY_FORBIDDEN if k in entry]:
            raise RegistryError(f"community backend {label!r}: forbidden key(s) {forbidden!r}")
        if missing := [k for k in COMMUNITY_REQUIRED if k not in entry]:
            raise RegistryError(f"community backend {label!r}: missing required {missing!r}")
        if tombstoned & {entry["name"], entry["namespace"]}:
            raise RegistryError(f"community backend {label!r}: name or namespace is tombstoned")
        if not _is_conforming_community_url(entry["url"]):
            raise RegistryError(f"community backend {label!r}: url must be https://<host>/mcp")
        try:
            backend = BackendDef.model_validate({**entry, "trust": "community"})
        except ValidationError as exc:
            raise RegistryError(f"invalid community backend {label!r}: {exc}") from exc
        backend.enabled = not ({backend.name, backend.namespace} & set(disabled))
        backends.append(backend)
    return backends


def load_serving_registry(
    settings: RouterSettings, servers_file: str | Path, environ: Mapping[str, str]
) -> list[BackendDef]:
    """Core registry plus, on a community instance, the community entries (§3 guards ran
    when ``settings`` validated)."""
    registry = load_registry(servers_file, environ)
    if settings.GF_COMMUNITY_SERVERS_FILE:
        registry += load_community_registry(
            settings.GF_COMMUNITY_SERVERS_FILE,
            registry,
            settings.GF_TOMBSTONES_FILE,
            settings.GF_DISABLED_BACKENDS,
        )
    return registry
