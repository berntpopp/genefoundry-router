"""FastMCP-core not-found reflection guard for the router (Response-Envelope v1.1 fast-follow).

The router is a FastMCP 3.x aggregator. Its OWN FastMCP core reflects the caller's
requested tool name / resource URI / prompt name back to the caller (and to logs)
BEFORE any router middleware runs, for a name/URI the router rejects *itself* (an
unknown federated name, a bad resource URI, an unknown prompt) — the residual that
each backend's own guard cannot cover because the call never reaches a backend.

This is a caller *self-reflection* surface (the hostile bytes are supplied by the
caller and reflected back to that same caller), so it is materially lower-risk than
the upstream-injection leak the prior sweep closed. It is still worth closing: the
reflected name/URI — with any control/zero-width/bidi/NUL code points — lands in
shared operator logs and, in an agent loop, in the model's tool-result context.
Fixed, constant-only messages remove the channel entirely.

Empirically observed on this stack (fastmcp ``>=3.4.4,<4.0.0`` / mcp), probing the
COMPOSED router built with the in-process fake fleet:

* Unknown TOOL name — core returns an ``isError`` ``CallToolResult`` whose TextContent
  echoes ``Unknown tool: '<name>'`` (the FastMCP ``Client`` re-raises it as ``ToolError``).
  The ``call_tool`` meta-tool echoes the bogus target name as
  ``Error calling tool 'call_tool': Unknown tool: '<name>'``.
* Unknown (URL-valid) RESOURCE URI — core raises ``Unknown resource: '<uri>'`` into the
  caller frame.
* Unknown PROMPT — core surfaces ``Unknown prompt: '<name>'`` into the caller frame.
* Malformed / forbidden-code-point URI — rejected at MCP *session deserialization*
  (before any request handler) with a GENERIC ``-32602 Invalid request parameters``
  caller frame (already fixed), but the raw URI + code points leak to the ROOT logger
  (``Failed to validate request`` / ``Message that failed validation``).
* Every path also leaks the raw name/URI to FastMCP/MCP DEBUG source loggers
  (``Tool cache miss for <name>``, ``Handler called: call_tool/get_prompt <name/uri>``).
* AggregateProvider fault — FastMCP is itself an AggregateProvider fanning the lookup
  across every mounted backend proxy (the router's highest-reachability surface: it
  aggregates 21 providers). A provider that raises a non-``NotFoundError`` during a
  lookup makes ``fastmcp.server.providers.aggregate`` log a WARNING whose ALREADY
  f-string-formatted message (``Error during get_tool('<name>') from provider <p>:
  <exc>``) embeds the caller-requested name/URI + code points.

Layers (spec §3 / §4.1), copied from the ratified fleet references
(``mondo``/``hpo`` preflight, ``clinvar`` protocol backstop, ``panelapp`` log filter):

* Layer 1 — :meth:`NotFoundGuard.on_call_tool` registry preflight: ``get_tool(name)``
  returns ``None`` for an unknown federated/meta name (verified: it resolves mounted
  proxy tools from mount-cached metadata WITHOUT a blocking remote round-trip, and
  never raises), so we return a fixed, name-free ``not_found`` envelope BEFORE core
  dispatch. Because the ``call_tool`` meta-tool re-enters the middleware chain for its
  real target, this ALSO closes the meta-tool bogus-target echo.
* Layer 2 — :meth:`NotFoundGuard.on_read_resource`: re-raise a fixed URI-free
  ``ResourceError`` for the (URL-valid) unknown-resource path that reaches the handler.
* Layer 3 — :func:`install_protocol_error_handler`: OUTERMOST wrapper on the raw
  ``CallTool`` / ``ReadResource`` / ``GetPrompt`` request handlers. Replaces any
  non-envelope ``isError`` tool result (the unknown-tool *return* path, if preflight is
  ever bypassed) and re-raises fixed input-free messages for resource/prompt dispatch
  failures — the ONLY layer that covers the unknown-PROMPT surface.
* Layer 5 — :func:`install_notfound_log_filter`: neutralize the FastMCP/MCP framework
  and MCP-session log records that echo the raw name/URI at their SOURCE logger (and
  its handlers, including FastMCP's non-propagating Rich handler), so caller input
  never reaches a log sink at any level.

Layer 6 (OTel span redaction) is a NO-OP here and deliberately omitted: only
``opentelemetry-api`` is installed (transitively via FastMCP), not
``opentelemetry-sdk`` — the tracer provider is non-recording, so no span exception
attributes are ever captured. Fleet policy: do NOT add the SDK dependency.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import mcp.types
import structlog
from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

log = structlog.get_logger(__name__)

# Fixed, input-free public messages. They NEVER contain the requested name/URI (nor a
# ``_meta.tool`` echo of it): the prior error-sanitation sweep established that
# sanitation strips code points but PRESERVES injection prose, so a fixed constant is
# the only safe caller-visible source (spec §3.1). ``not_found`` reuses the fleet
# Response-Envelope error-code vocabulary.
_UNKNOWN_TOOL_MESSAGE = "The requested tool is not available."
_UNKNOWN_RESOURCE_MESSAGE = "The requested resource is not available."
_UNKNOWN_PROMPT_MESSAGE = "The requested prompt is not available."
_UNKNOWN_TOOL_SUGGESTION = (
    "Call search_tools to discover federated tools, then call_tool with the exact "
    "`<namespace>_<tool>` name."
)

# The synthetic meta-tools re-enter the middleware chain for their real target, so the
# outer pass carries these names; the preflight below skips them (they always resolve).
_SYNTHETIC = frozenset({"search_tools", "call_tool"})


def _not_found_payload() -> dict[str, Any]:
    """The fixed, name-free ``not_found`` envelope body (constants only, no ``_meta.tool``)."""
    return {
        "success": False,
        "error_code": "not_found",
        "message": _UNKNOWN_TOOL_MESSAGE,
        "retryable": False,
        "suggestions": [_UNKNOWN_TOOL_SUGGESTION],
    }


def unknown_tool_result() -> ToolResult:
    """Return a fixed, name-free ``not_found`` envelope for an unknown tool.

    Carries both ``structured_content`` and a matching TextContent JSON mirror, built
    from constants only. There is deliberately no ``_meta.tool`` — the requested
    (caller-controlled) name is never reflected back.
    """
    payload = _not_found_payload()
    return ToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            )
        ],
        structured_content=payload,
        is_error=True,
    )


class NotFoundGuard(Middleware):
    """Layer 1 (tool-name preflight) + Layer 2 (resource-read boundary)."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Preflight the tool NAME so an unknown name never reaches core dispatch.

        ``get_tool`` resolves mounted-proxy tool names (from mount-cached metadata,
        no blocking remote round-trip) and the synthetic meta-tools, and returns
        ``None`` — it does not raise — for an unknown name. An unknown name is
        answered here with a fixed, name-free envelope. The ``call_tool`` meta-tool
        re-enters this hook for its real target, so a bogus target is caught on the
        inner pass. On any resolution failure we defer to core rather than mask a
        legitimate call (the Layer-3 backstop still covers a reflected return).
        """
        fctx = getattr(context, "fastmcp_context", None)
        name = getattr(getattr(context, "message", None), "name", None)
        if fctx is not None and isinstance(name, str) and name not in _SYNTHETIC:
            try:
                tool = await fctx.fastmcp.get_tool(name)
            except Exception:
                tool = object()
            if tool is None:
                log.warning("mcp_unknown_tool")
                return unknown_tool_result()
        return await call_next(context)

    async def on_read_resource(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        """Emit a FIXED, URI-free error for a resource not-found / read failure.

        The requested URI is caller-controlled; FastMCP core echoes it
        (``Unknown resource: '<uri>'`` / ``Error reading resource '<uri>'``) in both
        the direct exception and the protocol error. Re-raise a fixed message so the
        URI never reaches the caller/protocol. NEVER re-publish ``str(exc)`` —
        sanitation strips code points but preserves injection prose.
        """
        try:
            return await call_next(context)
        except FastMCPNotFoundError:
            log.warning("mcp_resource_not_found")
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None
        except ResourceError as exc:
            log.warning("mcp_resource_error", error_type=type(exc).__name__)
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None
        except Exception as exc:
            log.warning("mcp_resource_error", error_type=type(exc).__name__)
            raise ResourceError(_UNKNOWN_RESOURCE_MESSAGE) from None


# ---------------------------------------------------------------------------
# Layer 3 — protocol-handler backstop (clinvar pattern)
# ---------------------------------------------------------------------------


class ProtocolError(Exception):
    """A dispatch-level failure re-raised with a FIXED, input-free message."""


def _is_structured_envelope(call_result: mcp.types.CallToolResult) -> bool:
    """True if an ``isError`` result carries a structured envelope (has ``error_code``).

    Distinguishes an already-fixed router/backend error (Layer-1's ``not_found``
    envelope, or a backend's own envelope) from a RAW FastMCP dispatch error whose
    plain-text message echoes the caller-supplied tool name (``Unknown tool: '<name>'``).
    """
    if not call_result.content:
        return False
    text = getattr(call_result.content[0], "text", None)
    if not isinstance(text, str):
        return False
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> mcp.types.ServerResult:
    """A fixed, input-free ServerResult for an unknown/failed tool dispatch.

    Built as an explicit typed ``CallToolResult`` (rather than via ``ToolResult``) so the
    structured envelope and its TextContent mirror match :func:`unknown_tool_result`.
    """
    payload = _not_found_payload()
    return mcp.types.ServerResult(
        mcp.types.CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
                )
            ],
            structuredContent=payload,
            isError=True,
        )
    )


async def _tool_is_unresolved(mcp_server: Any, name: Any) -> bool:
    """True ONLY when the registry PROVES ``name`` is absent (``get_tool`` -> ``None``).

    Used to gate the tool not-found replacement: a KNOWN tool's validation / execution
    error (a non-envelope ``isError`` from a proxied backend tool) must pass through
    UNCHANGED — never be misreported as ``not_found``. If membership cannot be confirmed
    (``get_tool`` raises, or no name), return ``False`` (do not mask) so a real error is
    preserved; a genuinely-unknown name is already answered by the Layer-1 preflight.
    """
    if not isinstance(name, str) or not name:
        return False
    try:
        return await mcp_server.get_tool(name) is None
    except Exception:
        return False


def install_protocol_error_handler(mcp_server: Any) -> None:
    """Wrap the tool/resource/prompt request handlers as the OUTERMOST layer.

    A FastMCP core not-found (or read) error can no longer reflect the caller-supplied
    name/URI. Install AFTER all tools/resources/prompts are registered (i.e. after the
    tool-search transform and every proxy mount) so the handlers exist.
    """
    handlers = mcp_server._mcp_server.request_handlers

    call_tool = handlers.get(mcp.types.CallToolRequest)
    if call_tool is not None:

        async def wrapped_call_tool(
            request: mcp.types.CallToolRequest,
            *,
            _orig: Any = call_tool,
        ) -> mcp.types.ServerResult:
            try:
                result = cast(mcp.types.ServerResult, await _orig(request))
            except FastMCPNotFoundError:
                # Unknown-tool *raise* drift (should not reach here once Layer 1 is
                # active) — the exception itself proves not-found; answer with the fixed,
                # name-free envelope.
                return _fixed_tool_not_found_result()
            # FastMCP *returns* an isError CallToolResult with a raw plain-text message
            # ("Unknown tool: '<name>'") for an unknown tool. Replace a non-envelope
            # isError ONLY when the registry PROVES the tool is absent — otherwise a
            # KNOWN proxied tool's validation/execution error would be misreported as
            # not_found. Layer 1 already returns a structured envelope for unknown names,
            # so this fires only if preflight was bypassed (resolution drift).
            root = getattr(result, "root", None)
            if (
                isinstance(root, mcp.types.CallToolResult)
                and root.isError
                and not _is_structured_envelope(root)
                and await _tool_is_unresolved(mcp_server, getattr(request.params, "name", None))
            ):
                return _fixed_tool_not_found_result()
            return result

        handlers[mcp.types.CallToolRequest] = wrapped_call_tool

    for request_type, message in (
        (mcp.types.ReadResourceRequest, _UNKNOWN_RESOURCE_MESSAGE),
        (mcp.types.GetPromptRequest, _UNKNOWN_PROMPT_MESSAGE),
    ):
        orig = handlers.get(request_type)
        if orig is None:
            continue

        async def wrapped(
            request: Any,
            *,
            _orig: Any = orig,
            _message: str = message,
        ) -> Any:
            try:
                return await _orig(request)
            except Exception:
                # Re-raise with a FIXED, input-free message so no requested name/URI
                # (or its code points) reaches the JSON-RPC error frame.
                raise ProtocolError(_message) from None

        handlers[request_type] = wrapped


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
)

# Framework logger-name prefixes for the WARNING+ args-clearing fallback (catch any
# other reflecting record not covered by a marker).
_SCRUBBED_LOGGER_PREFIXES = ("fastmcp", "mcp")

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
    "mcp.shared.session",
)

_SCRUBBED_MESSAGE = "MCP request detail omitted (caller input redacted)."


class NotFoundLogScrubFilter(logging.Filter):
    """Scrub framework log records that would echo a caller-supplied tool name / URI.

    Replaces the reflecting record's payload with fixed metadata (clearing ``args`` /
    ``exc_info`` / ``exc_text`` / ``stack_info``) so the caller-chosen name/URI — and
    any control/zero-width/bidi/NUL code points it carries — can never reach a log or
    telemetry sink at ANY level. Always returns ``True``: the (now input-free) record is
    still emitted for operational visibility.
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
        # Fallback: other FastMCP/MCP framework WARNING+ records may carry
        # caller-derived detail in their interpolated args — drop it.
        if record.levelno < logging.WARNING:
            return True
        if not record.name.startswith(_SCRUBBED_LOGGER_PREFIXES):
            return True
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
