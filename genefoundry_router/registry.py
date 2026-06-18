"""Backend registry models and naming helpers."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAMESPACE_RE = re.compile(r"^[a-z0-9]+$")
MAX_QUALIFIED_NAME_LEN = 64


class TransformConfig(BaseModel):
    """Per-backend stopgap normalization until the source adopts Standard v1."""

    model_config = ConfigDict(extra="forbid")

    strip_prefix: str | None = None
    rename: dict[str, str] = Field(default_factory=dict)
    arg_rename: dict[str, dict[str, str]] = Field(default_factory=dict)


class BackendDef(BaseModel):
    """A single federated backend, resolved from servers.yaml + .env."""

    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    url_env: str
    repo: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Canonical resolver leaf tools (free-text -> stable ID) for this backend. They are
    # pinned (always_visible) and named in the server instructions so they stay reliably
    # discoverable despite BM25 ranking (FastMCP's index has no field-weighting, so a
    # terse resolver loses to verbose tools that repeat the keyword). Leaf names; the
    # router namespaces them to <namespace>_<leaf>.
    entrypoints: list[str] = Field(default_factory=list)
    enabled: bool = True
    cache_ttl: int = 300
    transport: Literal["http"] = "http"  # R1.1: present in servers.yaml defaults; SSE not offered
    transform: TransformConfig | None = None
    url: str | None = None  # resolved from os.environ[url_env] at load time

    @field_validator("namespace")
    @classmethod
    def _validate_namespace(cls, v: str) -> str:
        if not NAMESPACE_RE.match(v):
            raise ValueError(f"namespace must match {NAMESPACE_RE.pattern!r}, got {v!r}")
        return v


CLIENT_SAFE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def qualified_name(namespace: str, tool: str) -> str:
    """Return the gateway-visible name for a tool under a namespace."""
    return f"{namespace}_{tool}"


def exceeds_name_limit(namespace: str, tool: str) -> bool:
    """True when the namespaced tool name exceeds the MCP 64-char limit."""
    return len(qualified_name(namespace, tool)) > MAX_QUALIFIED_NAME_LEN


def is_client_safe_name(name: str) -> bool:
    """True when a tool name is portable across MCP clients incl. Gemini.

    snake_case, ``[A-Za-z0-9_]`` only (no dots/dashes), leading letter/underscore,
    <=64 chars. (R1.10 — Gemini's FunctionDeclaration.name is stricter than MCP's
    ``[A-Za-z0-9_-]`` and rewrites non-conforming names, which would desync routing.)
    """
    return bool(CLIENT_SAFE_NAME_RE.match(name))
