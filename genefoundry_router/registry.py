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
