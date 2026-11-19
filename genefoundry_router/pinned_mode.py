"""Which pinned tools ``tools/list`` shows: the server default or a per-request override (#320).

``GF_PINNED_MODE`` sets the default; the ``X-GF-Pinned-Mode`` request header overrides it so
one deployment can be measured in every mode. Applied in the search transform's
``transform_tools`` (the tools/list projection), NOT a list middleware: middleware also wraps
the catalog that ``search_tools``/``call_tool`` read, which must keep every tool. The same
mode picks the ``initialize``/``server/discover`` instructions and the hint call form. None of
it reaches authorization, call routing or rate limits, and every tool stays callable via
``call_tool``.

``GF_SCHEMA_MODE`` / ``X-GF-Schema-Mode`` pick the listed schema form the same way: the
backends' standard schemas, the OpenAI-strict projection (#293) for clients that need it, or
the token-lean slim projection (``schema_slim``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, get_args

from fastmcp.server.dependencies import get_http_request

PinnedMode = Literal["default", "minimal", "none"]
PINNED_MODE_HEADER = "x-gf-pinned-mode"
_MODES: tuple[str, ...] = get_args(PinnedMode)
SchemaMode = Literal["standard", "strict", "slim"]
SCHEMA_MODE_HEADER = "x-gf-schema-mode"


def _header_mode[M: str](header: str, modes: tuple[str, ...], default: M) -> M:
    try:
        raw = get_http_request().headers.get(header, "")
    except RuntimeError:  # in-memory / stdio: no HTTP request
        return default
    value = raw.strip().lower()
    return value if value in modes else default  # type: ignore[return-value]


def request_mode(default: PinnedMode) -> PinnedMode:
    """The caller's valid ``X-GF-Pinned-Mode``, else ``default`` (also off-HTTP)."""
    return _header_mode(PINNED_MODE_HEADER, _MODES, default)


def request_schema_mode(default: SchemaMode) -> SchemaMode:
    """The caller's valid ``X-GF-Schema-Mode``, else ``default`` (also off-HTTP)."""
    return _header_mode(SCHEMA_MODE_HEADER, get_args(SchemaMode), default)


@dataclass(frozen=True)
class PinPolicy:
    """The pinned names per mode: ``pins`` (servers.yaml entry points) or ``minimal``."""

    pins: tuple[str, ...]
    minimal: tuple[str, ...] = ()
    default: PinnedMode = "default"
    # Slim-mode optional-param allowlist per namespaced tool (servers.yaml ``slim_keep``).
    slim_keep: Mapping[str, Sequence[str]] = field(default_factory=dict, hash=False)

    def all(self) -> list[str]:
        """Every name any mode may list: what the search transform keeps visible."""
        return list(dict.fromkeys(self.pins + self.minimal))

    def listed(self, mode: PinnedMode | None = None) -> frozenset[str]:
        """Pinned names listed in ``mode`` (the request's mode when omitted)."""
        mode = mode or request_mode(self.default)
        if mode == "none":
            return frozenset()
        return frozenset(self.minimal if mode == "minimal" else self.pins)
