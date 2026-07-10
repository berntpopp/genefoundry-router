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
    service_token_env: str | None = None
    repo: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Canonical entry-point leaf tools for this backend — the first tool an agent reaches
    # for in this domain (a free-text->ID resolver and/or the primary query). They are
    # pinned (always_visible) AND named in the server instructions so they stay reliably
    # discoverable regardless of BM25 ranking (FastMCP's flat index has no field-weighting
    # or stemming, so a canonical tool can lose to verbose tools that repeat a keyword).
    # Leaf names; the router namespaces them to <namespace>_<leaf>.
    entrypoints: list[str] = Field(default_factory=list)
    # Override for the backend's ratified ``serverInfo.name`` when it legitimately differs
    # from ``<namespace>-link``. The router aliases a few backends to a shorter namespace
    # than their published identity (e.g. the ``spliceailookup-link`` backend is namespaced
    # ``spliceai`` for terse tool prefixes); fleet-probe MUST assert the name the backend
    # actually emits — which its OWN conformance CI ratifies — not the alias. Defaults to
    # ``<namespace>-link`` for the overwhelming common case.
    server_name: str | None = None
    enabled: bool = True
    cache_ttl: int = 300
    transport: Literal["http"] = "http"  # R1.1: present in servers.yaml defaults; SSE not offered
    transform: TransformConfig | None = None
    url: str | None = None  # resolved from os.environ[url_env] at load time
    service_token: str | None = Field(default=None, repr=False)

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


def expected_server_name(backend: BackendDef) -> str:
    """The ``serverInfo.name`` fleet-probe should assert for a backend.

    Defaults to ``<namespace>-link`` (the Transport Standard v1 §3 rule); a backend whose
    ratified name differs from the router's namespace alias declares it via ``server_name``.
    """
    return backend.server_name or f"{backend.namespace}-link"


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
