"""Layer 5 of the FastMCP-core not-found reflection guard (see ``notfound_guard``).

Neutralizes the FastMCP/MCP framework and MCP-session log records that echo a
caller-supplied tool name / resource URI at their SOURCE logger (and its handlers,
including FastMCP's non-propagating Rich handler), so caller input never reaches a log
sink at any level.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

# ---------------------------------------------------------------------------
# Layer 5 — validation / reflection log-scrub filter (panelapp / hpo pattern)
# ---------------------------------------------------------------------------
#
# Each marker is a substring of the ``record.msg`` (f-string prefix or %-template) of a
# FastMCP-core / MCP-SDK / MCP-session record that reflects the caller-supplied name/URI
# — carried in ``record.args`` or interpolated into the message. Matching on ``msg``
# covers both forms because the scrub replaces the message AND clears the args.
_SCRUB_MARKERS: tuple[str, ...] = (
    "Handler called: call_tool",
    "Handler called: read_resource",
    "Handler called: get_prompt",
    "Tool cache miss for",
    "Invalid arguments for tool",
    "Error calling tool",
    "Error reading resource",
    "Failed to validate request",
    "Failed to validate notification",
    "Message that failed validation",
    # FastMCP is itself an AggregateProvider fanning get_tool / get_resource /
    # get_prompt across every mounted backend proxy — the router's HIGHEST-reachability
    # place for this leak (it genuinely aggregates 21 providers). When a provider raises
    # a non-NotFoundError during a lookup (e.g. a transport / 5xx fault resolving a
    # hostile name), ``fastmcp.server.providers.aggregate`` logs a WARNING whose message
    # is ALREADY f-string-formatted: ``Error during get_tool('<name>') from provider
    # <p>: <exc>`` — ``operation`` embeds the caller-requested name/URI verbatim and
    # ``<exc>`` (``str(exc)``) can repeat it, with forbidden code points. The leak is in
    # ``record.msg`` (args is empty), so the marker branch — which replaces the WHOLE
    # msg — is what closes it; clearing args alone would not. ``Duplicate`` covers the
    # sibling component-collision warning (echoes the component ``{key!r}``).
    "Error during ",
    "Duplicate ",
    # MCP SDK 2 ``mcp.server.runner``: echo the caller-chosen method / pre-request frame.
    "dropped %r",
    "dropped %s",
    "dropped a frame",
    "no handler for notification",
)

# Logger-name prefixes for the WARNING+ args-redacting fallback: the MCP REQUEST-DISPATCH
# loggers — the only ones that handle a caller-supplied tool name / resource URI and can
# therefore reflect one through an arg that no marker above anticipated (verified against
# the installed fastmcp/mcp: `mcp.server.lowlevel.server` "Tool '%s' not listed",
# `fastmcp.server.middleware.response_limiting` "Tool %r response exceeds size limit",
# `fastmcp.server.server` "Invalid arguments for tool %r" — every OTHER framework site that
# echoes caller input builds its message with an f-string, where args are already empty and
# clearing them protects nothing).
#
# Deliberately NOT the whole ``fastmcp`` / ``mcp`` trees. That swept in every unrelated
# subsystem — auth, HTTP, transport, the ProxyClients — whose WARNING args are pure
# operator diagnostics containing no caller input, and blanking them is what cost hours on
# 2026-08-07: `fastmcp.server.auth.providers.jwt` printed "audience mismatch (got %r,
# expected %r)" with the two values that identified the misconfiguration stripped out.
# Narrowing by SUBSYSTEM (not by message) keeps that class of outage fixed for the next
# framework warning too, which a per-message allowlist would not. The marker branch above
# still runs for EVERY logger at EVERY level, so a known reflecting message stays scrubbed
# no matter which subsystem emits it.
_SCRUBBED_LOGGER_PREFIXES = (
    "fastmcp.server.server",
    "fastmcp.server.mixins",
    "fastmcp.server.middleware",
    "fastmcp.server.providers",
    "mcp.server",
    "mcp.shared.session",
)

# SOURCE loggers on which those records are CREATED. A logging filter runs only for
# records emitted on the logger it is attached to (ancestor filters are skipped during
# propagation), so attach directly to each originator — including ROOT (where
# ``mcp.shared.session`` emits request-validation failures via a bare ``logging.warning``)
# and the ``fastmcp`` parent, whose non-propagating Rich handlers would otherwise bypass
# a root-only filter. Also attach to each logger's handlers as belt-and-braces.
_SOURCE_LOGGERS: tuple[str, ...] = (
    "",  # root — mcp.shared.session request-validation failures
    "fastmcp",  # non-propagating parent + its Rich handlers (handler-level scrub)
    "fastmcp.server.server",
    "fastmcp.server.mixins.mcp_operations",
    "fastmcp.server.providers.aggregate",  # provider-fault WARNING echoes the name/URI
    "mcp",
    "mcp.server.lowlevel.server",
    "mcp.server.runner",  # SDK 2 dispatch: "dropped %r" / "no handler for notification %s"
    "mcp.shared.session",
)

_SCRUBBED_MESSAGE = "MCP request detail omitted (caller input redacted)."
_REDACTED_ARG = "<redacted>"


def redacted_message(record: logging.LogRecord, *, fallback: str = _SCRUBBED_MESSAGE) -> str:
    """Render ``record``'s message with a fixed token IN PLACE OF every ``%``-placeholder.

    Redaction must leave a READABLE line. Dropping ``record.args`` alone does not:
    ``LogRecord.getMessage`` skips ``msg % args`` when args is falsy, so the raw printf
    template ("... got %r, expected %r") is what reaches the sink — a line an operator
    reads as a broken formatter, not as a deliberate redaction. Interpolating the token
    keeps the template's own (input-free) prose, which is the only remaining clue as to
    what happened, and states that something was withheld.

    Falls back to ``fallback`` when the template cannot accept a string token (e.g. a
    ``%d`` conversion): a redaction may never raise inside a logging handler. Callers pass
    their own subsystem's fixed message so the fallback line still says which guard fired
    (the OAuth privacy filter reuses this — see ``observability.OAuthProxyPrivacyFilter``).
    """
    template = str(record.msg)
    args = record.args
    if not args:
        return template
    # Mapping args drive ``%(name)s`` templates; everything else is positional.
    tokens: dict[str, str] | tuple[str, ...]
    if isinstance(args, Mapping):
        tokens = dict.fromkeys(args, _REDACTED_ARG)
    else:
        tokens = tuple(_REDACTED_ARG for _ in args)
    try:
        return template % tokens
    except TypeError, ValueError, KeyError:
        return fallback


class NotFoundLogScrubFilter(logging.Filter):
    """Scrub framework log records that would echo a caller-supplied tool name / URI.

    Replaces the reflecting record's payload with fixed metadata (redacting ``args``,
    clearing ``exc_info`` / ``exc_text`` / ``stack_info``) so the caller-chosen name/URI —
    and any control/zero-width/bidi/NUL code points it carries — can never reach a log or
    telemetry sink at ANY level. Always returns ``True``: the (now input-free) record is
    still emitted for operational visibility.

    Two branches, deliberately different in reach. A **marker** match (any logger, any
    level) means the message itself is known to reflect caller input, so the WHOLE message
    goes. The **fallback** is a net for args no marker anticipated, and is scoped to the
    request-dispatch loggers (:data:`_SCRUBBED_LOGGER_PREFIXES`) at WARNING+ — scoping it
    to a subsystem rather than the whole framework is what keeps unrelated diagnostics
    (auth, transport) intact, and both branches leave a rendered, placeholder-free line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg if isinstance(record.msg, str) else ""
        # Records that reflect the caller-supplied name/URI (any logger, any level).
        if any(marker in msg for marker in _SCRUB_MARKERS):
            record.msg = _SCRUBBED_MESSAGE
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            return True
        # Fallback: a request-dispatch WARNING+ record may carry caller-derived detail in
        # its interpolated args — redact it. Interpolate the token FIRST, then clear args:
        # the two together are what make the record both input-free and still legible.
        if record.levelno < logging.WARNING:
            return True
        if not record.name.startswith(_SCRUBBED_LOGGER_PREFIXES):
            return True
        record.msg = redacted_message(record)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


# One shared filter instance so idempotent installs never stack duplicates.
_SHARED_FILTER = NotFoundLogScrubFilter()


def _has_filter(target: logging.Logger | logging.Handler) -> bool:
    return any(isinstance(existing, NotFoundLogScrubFilter) for existing in target.filters)


def install_notfound_log_filter() -> None:
    """Idempotently attach the scrub filter to each source logger (and its handlers).

    Call after the FastMCP server is built so the framework's own (non-propagating)
    handlers already exist and receive the filter.
    """
    for name in _SOURCE_LOGGERS:
        logger = logging.getLogger(name)
        if not _has_filter(logger):
            logger.addFilter(_SHARED_FILTER)
        for handler in logger.handlers:
            if not _has_filter(handler):
                handler.addFilter(_SHARED_FILTER)
