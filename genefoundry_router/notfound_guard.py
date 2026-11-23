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
* Layer 5 — :func:`install_notfound_log_filter` (``notfound_log_scrub``): neutralize the
  FastMCP/MCP framework and MCP-session log records that echo the raw name/URI at their
  SOURCE logger (and its handlers, including FastMCP's non-propagating Rich handler), so
  caller input never reaches a log sink at any level.

Layer 6 (OTel span redaction) is a NO-OP here and deliberately omitted: only
``opentelemetry-api`` is installed (transitively via FastMCP), not
``opentelemetry-sdk`` — the tracer provider is non-recording, so no span exception
attributes are ever captured. Fleet policy: do NOT add the SDK dependency.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import mcp.types
import structlog
from fastmcp.exceptions import NotFoundError as FastMCPNotFoundError
from fastmcp.exceptions import ResourceError, ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.shared.exceptions import MCPError
from mcp.types import TextContent
from mcp_types import INVALID_PARAMS, METHOD_NOT_FOUND

from genefoundry_router.audit_identity import unknown_name_fields
from genefoundry_router.registry import NAMESPACE_RE, nearest_tool
from genefoundry_router.skill_search import SKILL_MISS_MESSAGE, is_skill_name, skill_call_uri
from genefoundry_router.tool_error_taxonomy import (
    UNCLASSIFIED_ERROR_CODE,
    error_code,
    transport_failure,
)

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
    "Call search_tools(query=...), then copy the hit's `call` field: "
    'call_tool(name="<namespace>_<tool>", arguments={...}).'
)
_SKILL_MISS_SUGGESTION = 'Call call_tool(name="skill_<name>") to see the skill\'s listed files.'

# The synthetic meta-tools re-enter the middleware chain for their real target, so the
# outer pass carries these names; the preflight below skips them (they always resolve).
_SYNTHETIC = frozenset({"search_tools", "call_tool"})


def _not_found_payload(requested: object = None, nearest: str | None = None) -> dict[str, Any]:
    """The fixed, name-free ``not_found`` envelope body: constants plus, at most, a real
    catalog tool name (``nearest``, #380); never the requested name, no ``_meta.tool``."""
    skill = is_skill_name(requested)
    hints = [_SKILL_MISS_SUGGESTION] if skill else [_UNKNOWN_TOOL_SUGGESTION]
    if nearest:
        hints.insert(
            0, f'Did you mean `{nearest}`? call_tool(name="{nearest}", arguments={{...}}).'
        )
    return {
        "success": False,
        "error_code": "not_found",
        "message": SKILL_MISS_MESSAGE if skill else _UNKNOWN_TOOL_MESSAGE,
        "retryable": False,
        "suggestions": hints,
    }


def _envelope(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(
        content=[
            TextContent(
                type="text", text=json.dumps(payload, separators=(",", ":"), sort_keys=True)
            )
        ],
        structured_content=payload,
        is_error=True,
    )


def backend_unavailable_result(namespace: str) -> ToolResult:
    """Envelope for a tool whose CONFIGURED backend harvested 0 tools (down/unreachable).

    No search_tools suggestion: search cannot find a tool whose backend serves nothing.
    """
    return _envelope(
        {
            "success": False,
            "error_code": "upstream_unavailable",
            "message": f"The {namespace} backend is not reachable from this deployment.",
            "retryable": True,
            "suggestions": [f"Retry later; {namespace} tools are unavailable until it recovers."],
        }
    )


def transport_failure_error(namespace: str, subcode: str) -> ToolError:
    """Fleet envelope for the router's OWN timeout/connect failure to a backend (#272)."""
    timed_out = subcode == "backend_timeout"
    payload = {
        "success": False,
        "error_code": "upstream_unavailable",
        "error_subcode": subcode,
        "message": (
            f"The {namespace} backend did not answer within the router's timeout."
            if timed_out
            else f"The {namespace} backend could not be reached."
        ),
        "retryable": True,
        "suggestions": [
            "Retry with a smaller request (fewer batch items)."
            if timed_out
            else f"Retry later; {namespace} tools are unavailable until it recovers."
        ],
    }
    return ToolError(json.dumps(payload, separators=(",", ":"), sort_keys=True))


def unknown_tool_result(requested: object = None, nearest: str | None = None) -> ToolResult:
    """Return a fixed, name-free ``not_found`` envelope for an unknown tool.

    Carries both ``structured_content`` and a matching TextContent JSON mirror, built
    from constants only. There is deliberately no ``_meta.tool`` — the requested
    (caller-controlled) name is never reflected back; the log gets only its hash (#325).
    """
    log.warning("mcp_unknown_tool", **unknown_name_fields(requested))
    return _envelope(_not_found_payload(requested, nearest))


def _caused_by_not_found(exc: BaseException) -> bool:
    """True if a FastMCP ``NotFoundError`` is anywhere in ``exc``'s cause chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, FastMCPNotFoundError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


class NotFoundGuard(Middleware):
    """Layer 1 (tool-name preflight) + Layer 2 (resource-read boundary)."""

    def __init__(self, backend_status: Mapping[str, bool] | None = None) -> None:
        # Configured namespaces that harvested 0 tools; refreshed by the catalog relist.
        # A namespace matches NAMESPACE_RE, so naming it never reflects hostile caller input.
        self.unavailable: frozenset[str] = frozenset()
        # Live per-namespace reachability, updated on every backend tools/list (#266).
        self.backend_status: Mapping[str, bool] = {} if backend_status is None else backend_status
        # Tool names of the last catalog harvest: the in-memory pool for #380 suggestions.
        self.catalog: frozenset[str] = frozenset()

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        """Preflight the tool NAME so an unknown name never reaches core dispatch.

        ``get_tool`` resolves mounted-proxy tool names (from mount-cached metadata, no
        remote round-trip) and the meta-tools, and returns ``None`` for an unknown name,
        answered here with a fixed, name-free envelope; so is the ``call_tool`` target (a
        served skill passes, #368). A resolution failure defers to core (Layer 3 backstop).
        """
        fctx = getattr(context, "fastmcp_context", None)
        message = getattr(context, "message", None)
        name = getattr(message, "name", None)
        target = name
        if name == "call_tool":  # fastmcp 4's meta-tool rejects (and echoes) unknown targets
            arguments = getattr(message, "arguments", None)
            target = arguments.get("name") if isinstance(arguments, dict) else None
        if fctx is not None and isinstance(target, str) and target not in _SYNTHETIC:
            try:
                tool = await fctx.fastmcp.get_tool(target)
                if tool is None and name == "call_tool":  # a served skill passes (#368)
                    uri = skill_call_uri(target, arguments.get("arguments"))  # type: ignore[union-attr]
                    tool = await fctx.fastmcp.get_resource(uri) if uri else None
            except Exception:
                tool = object()
            if tool is None:
                namespace = target.partition("_")[0]
                if namespace in self.unavailable or self.backend_status.get(namespace) is False:
                    log.warning("mcp_backend_unavailable", namespace=namespace)
                    return backend_unavailable_result(namespace)
                return unknown_tool_result(target, await self._nearest(fctx, target))
        try:
            return await call_next(context)
        except FastMCPNotFoundError:
            # The meta-tool's own catalog check (visibility drift past the preflight).
            return unknown_tool_result(target)
        except ToolError as exc:
            if name == "call_tool" and _caused_by_not_found(exc):
                return unknown_tool_result(target)
            # Only the router's own unclassified failure; never re-wrap a fleet envelope.
            subcode = transport_failure(exc) if error_code(exc) == UNCLASSIFIED_ERROR_CODE else None
            namespace = name.partition("_")[0] if isinstance(name, str) else ""
            # NAMESPACE_RE-bounded: a caller-supplied name never reaches the message or log.
            if subcode is None or name in _SYNTHETIC or not NAMESPACE_RE.match(namespace):
                raise
            log.warning("mcp_backend_transport_failure", namespace=namespace, subcode=subcode)
            raise transport_failure_error(namespace, subcode) from exc

    async def _nearest(self, fctx: Any, target: str) -> str | None:
        """The closest harvested tool the caller can still resolve (#380): ``get_tool``
        honours quarantine/visibility and reads the same provider cache as the preflight."""
        best = nearest_tool(target, self.catalog)
        try:
            return best if best and await fctx.fastmcp.get_tool(best) is not None else None
        except Exception:
            return None

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
    except ValueError, TypeError:
        return False
    return isinstance(obj, dict) and "error_code" in obj


def _fixed_tool_not_found_result() -> mcp.types.CallToolResult:
    """A fixed, input-free CallToolResult for an unknown/failed tool dispatch.

    Built as an explicit typed ``CallToolResult`` (rather than via ``ToolResult``) so the
    structured envelope and its TextContent mirror match :func:`unknown_tool_result`.
    """
    payload = _not_found_payload()
    return mcp.types.CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            )
        ],
        structured_content=payload,
        is_error=True,
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


async def _strip_method_not_found_data(ctx: Any, call_next: Any) -> Any:
    """Drop the caller-supplied method name SDK 2 puts in METHOD_NOT_FOUND ``data``."""
    try:
        return await call_next(ctx)
    except MCPError as exc:
        if exc.error.code == METHOD_NOT_FOUND and exc.error.data is not None:
            raise MCPError(code=METHOD_NOT_FOUND, message="Method not found") from None
        raise


def install_protocol_error_handler(mcp_server: Any) -> None:
    """Wrap the tool/resource/prompt request handlers as the OUTERMOST layer.

    A FastMCP core not-found (or read) error can no longer reflect the caller-supplied
    name/URI. Install AFTER all tools/resources/prompts are registered (i.e. after the
    tool-search transform and every proxy mount) so the handlers exist.
    """
    lowlevel = mcp_server._mcp_server
    # Outermost: MCP SDK 2 echoes an unknown JSON-RPC method name in METHOD_NOT_FOUND ``data``.
    lowlevel.middleware.insert(0, _strip_method_not_found_data)

    call_tool = lowlevel.get_request_handler("tools/call")
    if call_tool is not None:

        async def wrapped_call_tool(
            ctx: Any,
            params: Any,
            *,
            _orig: Any = call_tool.handler,
        ) -> Any:
            try:
                result = await _orig(ctx, params)
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
            if (
                isinstance(result, mcp.types.CallToolResult)
                and result.is_error
                and not _is_structured_envelope(result)
                and await _tool_is_unresolved(mcp_server, getattr(params, "name", None))
            ):
                return _fixed_tool_not_found_result()
            return result

        lowlevel.add_request_handler("tools/call", call_tool.params_type, wrapped_call_tool)

    for method, message in (
        ("resources/read", _UNKNOWN_RESOURCE_MESSAGE),
        ("prompts/get", _UNKNOWN_PROMPT_MESSAGE),
    ):
        entry = lowlevel.get_request_handler(method)
        if entry is None:
            continue

        async def wrapped(
            ctx: Any,
            params: Any,
            *,
            _orig: Any = entry.handler,
            _message: str = message,
        ) -> Any:
            try:
                return await _orig(ctx, params)
            except Exception:
                # FIXED, input-free message; MCPError keeps -32602 (SDK 2 sends plain errors as 0).
                raise MCPError(code=INVALID_PARAMS, message=_message) from None

        lowlevel.add_request_handler(method, entry.params_type, wrapped)
