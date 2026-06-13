"""Router runtime settings and registry loading."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # Discovery
    GF_POLL_INTERVAL: int = 0  # seconds; 0 disables the polling re-list

    # Transport security (R1.4 — MCP Origin/DNS-rebinding MUST)
    # NoDecode: suppress pydantic-settings' JSON pre-decode of complex env values so the
    # CSV string reaches the mode="before" validator below (pydantic-settings 2.14 behavior).
    GF_ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = []  # CSV in env; [] = reject any present Origin
    GF_PUBLIC_BASE_URL: str | None = None  # public URL behind the proxy (OAuth resource URI)

    # Auth
    GF_AUTH_MODE: AuthMode = "none"
    GF_JWT_ISSUER: str | None = None
    GF_JWT_JWKS_URL: str | None = None
    GF_JWT_AUDIENCE: str | None = None
    GF_OAUTH_PROVIDER: str | None = None
    GF_OAUTH_CLIENT_ID: str | None = None
    GF_OAUTH_CLIENT_SECRET: str | None = None
    GF_OAUTH_BASE_URL: str | None = None
    GF_OAUTH_AUTHORIZE_URL: str | None = None
    GF_OAUTH_TOKEN_URL: str | None = None

    @field_validator("GF_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept a comma-separated string from env and split into a list."""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v
