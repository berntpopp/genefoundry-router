"""``X-GF-Schema-Mode: slim``: a token-lean projection of the pinned tools in ``tools/list``.

A slim tool lists its required params plus at most two optional ones (the servers.yaml
``slim_keep`` allowlist, else the first two non-``response_mode`` optionals in schema order),
param descriptions cut to 80 chars, no ``examples``/``title``/``default``, the description's
first sentence and no outputSchema. Only the listing shrinks: calls resolve the original tool,
so validation still runs against the backend's full schema. :class:`SlimParamsMiddleware`
flags a call that used a param the listing hid (``_meta.unadvertised_params_used``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import structlog
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import Tool, ToolResult

from genefoundry_router.observability import UNADVERTISED_PARAMS
from genefoundry_router.pinned_mode import PinPolicy, SchemaMode, request_schema_mode

log = structlog.get_logger(__name__)

SLIM_OPTIONALS = 2
PARAM_DESCRIPTION_CHARS = 80
TOOL_DESCRIPTION_CHARS = 300
_DROPPED = frozenset({"examples", "title", "default"})
_NAMED = frozenset({"properties", "$defs", "definitions", "patternProperties"})
# End of the first sentence: . ! ? before whitespace, but not the "." of "e.g." / "i.e.".
_SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)[.!?](?=\s|$)")


def _truncate(text: str, limit: int) -> str:
    """``text`` on one line, cut at a word boundary to at most ``limit`` chars (with "…")."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def first_sentence(text: str) -> str:
    """The description's lead sentence (the D1 returns-lead), at most 300 chars."""
    text = " ".join(text.split())
    end = _SENTENCE_END.search(text)
    return _truncate(text[: end.end()] if end else text, TOOL_DESCRIPTION_CHARS)


def slim_params(schema: dict[str, Any], allow: Sequence[str] | None = None) -> list[str]:
    """Param names a slim listing keeps, in schema order: required + <= 2 optional."""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    optional = [p for p in props if p not in required]
    if allow is None:
        chosen = [p for p in optional if p != "response_mode"][:SLIM_OPTIONALS]
    else:
        chosen = [p for p in allow if p in optional][:SLIM_OPTIONALS]
    return [p for p in props if p in required or p in chosen]


def _clean(node: Any) -> Any:
    """Drop examples/title/default and cut descriptions, never touching property NAMES."""
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _DROPPED:
            continue
        if key == "description" and isinstance(value, str):
            out[key] = _truncate(value, PARAM_DESCRIPTION_CHARS)
        elif key in _NAMED and isinstance(value, dict):
            out[key] = {name: _clean(sub) for name, sub in value.items()}
        else:
            out[key] = _clean(value)
    return out


def slim_schema(schema: dict[str, Any], allow: Sequence[str] | None = None) -> dict[str, Any]:
    """The slim inputSchema: kept params only, open (no ``additionalProperties: false``) so a
    validating client still lets a hidden param through to the full-schema call path."""
    keep = slim_params(schema, allow)
    props = schema.get("properties") or {}
    out: dict[str, Any] = {"type": "object", "properties": {p: _clean(props[p]) for p in keep}}
    required = [p for p in schema.get("required") or () if p in keep]
    if required:
        out["required"] = required
    for key in ("$defs", "definitions"):
        if key in schema:
            out[key] = _clean({key: schema[key]})[key]
    return out


def slim_tool(tool: Tool, allow: Sequence[str] | None = None) -> Tool:
    """``tool`` as the slim listing shows it."""
    update: dict[str, Any] = {
        "parameters": slim_schema(tool.parameters or {}, allow),
        "output_schema": None,
    }
    if tool.description:
        update["description"] = first_sentence(tool.description)
    return tool.model_copy(update=update)


class SlimParamsMiddleware(Middleware):
    """Flag slim-mode calls that used a param the slim listing hid; log their failures.

    Router-owned ``_meta`` only (never backend data); logs carry tool + param NAMES, never
    values. Registered after :class:`StrictNullMiddleware` (``call_tool`` arguments decoded)
    and outside the response cache, so the flag never lands in a cached result.
    """

    def __init__(self, policy: PinPolicy, default: SchemaMode) -> None:
        self.policy = policy
        self.default = default

    async def _audit(self, context: MiddlewareContext[Any]) -> tuple[str, list[str], list[str]]:
        """(tool, hidden params used, required params missing) for a slim-listed call."""
        fctx, message = context.fastmcp_context, context.message
        if fctx is None or request_schema_mode(self.default) != "slim":
            return "", [], []
        # call_tool re-enters this chain with the target's own name: audit that inner call.
        name, arguments = message.name, message.arguments or {}
        if name not in self.policy.listed():
            return "", [], []
        try:
            tool = await fctx.fastmcp.get_tool(name)
        except Exception:  # resolution failure: the name guards answer it
            return "", [], []
        schema = (tool.parameters if tool is not None else None) or {}
        kept = set(slim_params(schema, self.policy.slim_keep.get(name)))
        props = schema.get("properties") or {}
        hidden = sorted(k for k in arguments if k in props and k not in kept)
        missing = sorted(set(schema.get("required") or ()) - set(arguments))
        return name, hidden, missing

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, ToolResult]
    ) -> ToolResult:
        tool, hidden, missing = await self._audit(context)
        if hidden:
            log.info("unadvertised_params_used", tool=tool, params=hidden)
            UNADVERTISED_PARAMS.labels(tool=tool).inc()
        try:
            result = await call_next(context)
        except Exception:
            self._failed(tool, hidden, missing)
            raise
        if result.is_error:
            self._failed(tool, hidden, missing)
        if hidden:  # a copy: the result object may be the response cache's
            meta = {**(result.meta or {}), "unadvertised_params_used": hidden}
            result = result.model_copy(update={"meta": meta})
        return result

    @staticmethod
    def _failed(tool: str, hidden: list[str], missing: list[str]) -> None:
        if hidden or missing:
            params = sorted({*hidden, *missing})
            log.warning("slim_hidden_param_validation_error", tool=tool, params=params)
