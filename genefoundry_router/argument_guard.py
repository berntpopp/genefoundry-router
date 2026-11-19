"""Answer an unexpected argument with a structured ``invalid_input`` envelope (issue #158).

FastMCP's own answer is a raw pydantic dump (``1 validation error for call[...] ...
input_value='…'``): no ``error_code``, no ``retryable``, and it reflects the caller's key
and value. The router already holds every tool's advertised ``inputSchema``, so it rejects
keys outside a closed schema (``additionalProperties: false``) at the edge — for its own
meta-tools, for the ``call_tool`` target, and for every federated tool alike. The envelope
names only the ACCEPTED parameters (from the schema), never the caller's input.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

from genefoundry_router.notfound_guard import _envelope

log = structlog.get_logger(__name__)


async def _accepted(fctx: Any, name: Any, arguments: Any) -> list[str] | None:
    """Sorted accepted parameter names if ``arguments`` has a key the schema forbids."""
    if not isinstance(name, str) or not isinstance(arguments, dict) or not arguments:
        return None
    try:
        tool = await fctx.fastmcp.get_tool(name)
    except Exception:  # resolution failure: defer to core, as NotFoundGuard does
        return None
    schema = getattr(tool, "parameters", None)
    if not isinstance(schema, dict) or schema.get("additionalProperties") is not False:
        return None
    accepted = sorted(schema.get("properties") or {})
    return accepted if set(arguments) - set(accepted) else None


class ArgumentGuard(Middleware):
    """Reject arguments outside a tool's closed ``inputSchema`` before dispatch."""

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, ToolResult]
    ) -> ToolResult:
        fctx = context.fastmcp_context
        message = context.message
        if fctx is not None:
            accepted = await _accepted(fctx, message.name, message.arguments)
            if accepted is None and message.name == "call_tool" and message.arguments:
                # StrictNullMiddleware (outer) already decoded a JSON-string `arguments`.
                inner = message.arguments
                accepted = await _accepted(fctx, inner.get("name"), inner.get("arguments"))
            if accepted is not None:
                log.warning("mcp_unexpected_argument")
                return _envelope(
                    {
                        "success": False,
                        "error_code": "invalid_input",
                        "message": "The tool does not accept one or more supplied arguments.",
                        "retryable": False,
                        "allowed_values": accepted,
                        "suggestions": [
                            "Retry with only the parameters listed in allowed_values "
                            "(the tool's inputSchema properties)."
                        ],
                    }
                )
        return await call_next(context)
