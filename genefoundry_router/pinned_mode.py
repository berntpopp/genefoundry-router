"""Which pinned tools ``tools/list`` shows: the server default or a per-request override (#320).

``GF_PINNED_MODE`` sets the default; the ``X-GF-Pinned-Mode`` request header overrides it so
one deployment can be measured in every mode. Applied in the search transform's
``transform_tools`` (the tools/list projection), NOT a list middleware: middleware also wraps
the catalog that ``search_tools``/``call_tool`` read, which must keep every tool. Both only
filter the LISTING: authorization, call routing and rate limits never read them, and every
tool stays callable via ``call_tool``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from fastmcp.server.dependencies import get_http_request

PinnedMode = Literal["default", "minimal", "none"]
PINNED_MODE_HEADER = "x-gf-pinned-mode"
_MODES: tuple[str, ...] = get_args(PinnedMode)


def request_mode(default: PinnedMode) -> PinnedMode:
    """The caller's valid ``X-GF-Pinned-Mode``, else ``default`` (also off-HTTP)."""
    try:
        raw = get_http_request().headers.get(PINNED_MODE_HEADER, "")
    except RuntimeError:  # in-memory / stdio: no HTTP request
        return default
    value = raw.strip().lower()
    return value if value in _MODES else default  # type: ignore[return-value]


@dataclass(frozen=True)
class PinPolicy:
    """The pinned names per mode: ``pins`` (servers.yaml entry points) or ``minimal``."""

    pins: tuple[str, ...]
    minimal: tuple[str, ...] = ()
    default: PinnedMode = "default"

    def all(self) -> list[str]:
        """Every name any mode may list: what the search transform keeps visible."""
        return list(dict.fromkeys(self.pins + self.minimal))

    def listed(self, mode: PinnedMode | None = None) -> frozenset[str]:
        """Pinned names listed in ``mode`` (the request's mode when omitted)."""
        mode = mode or request_mode(self.default)
        if mode == "none":
            return frozenset()
        return frozenset(self.minimal if mode == "minimal" else self.pins)
