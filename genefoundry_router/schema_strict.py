"""OpenAI strict schema projection for tool search and function calling (#116).

Transforms JSON schemas into OpenAI Structured Outputs-compatible strict schemas:
- Every object node has additionalProperties: False, explicit properties, and required.
- Optional properties are placed in required and made nullable via anyOf.
- Defaults are stripped and type-arrays (e.g. ["integer", "null"]) become anyOf unions.
- strip_strict_nulls omits None sentinels for optional non-nullable parameters on call_tool.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext


def _is_nullable(schema: dict[str, Any]) -> bool:
    """Return True if schema already permits null."""
    t = schema.get("type")
    if t == "null" or (isinstance(t, list) and "null" in t):
        return True
    if t is not None:
        return False
    return any(
        isinstance(sub, dict) and _is_nullable(sub)
        for key in ("anyOf", "oneOf")
        for sub in schema.get(key, [])
    )


def _is_object_node(node: dict[str, Any]) -> bool:
    """Return True if node represents an object schema."""
    t = node.get("type")
    return t == "object" or bool("properties" in node and t is None)


def _is_open_map(node: dict[str, Any]) -> bool:
    """An object that accepts arbitrary keys: no declared properties, extras not forbidden."""
    return (
        (_is_object_node(node) or node.get("type") == "object")
        and not node.get("properties")
        and node.get("additionalProperties", True) is not False
    )


def _accepts_open_map(schema: dict[str, Any]) -> bool:
    if _is_open_map(schema):
        return True
    branches = schema.get("anyOf") or schema.get("oneOf") or []
    return any(isinstance(b, dict) and _is_open_map(b) for b in branches)


def _transform_node(node: dict[str, Any], root: bool = False) -> None:
    """Recursively transform schema node in-place into strict mode."""
    node.pop("default", None)

    t = node.get("type")
    if isinstance(t, list):
        has_null = "null" in t
        non_null = [x for x in t if x != "null"]
        if len(non_null) == 1:
            node["type"] = non_null[0]
            _transform_node(node)
            if has_null:
                meta: dict[str, Any] = {
                    k: node.pop(k) for k in ("description", "title") if k in node
                }
                inner = dict(node)
                node.clear()
                node.update({"anyOf": [inner, {"type": "null"}], **meta})
            return
        if len(non_null) > 1:
            meta = {k: node.pop(k) for k in ("description", "title") if k in node}
            branches: list[dict[str, Any]] = [{"type": x} for x in non_null]
            for b in branches:
                _transform_node(b)
            if has_null:
                branches.append({"type": "null"})
            node.clear()
            node.update({"anyOf": branches, **meta})
            return

    if not root and _is_open_map(node):
        # Strict mode cannot express arbitrary keys, and closing the map would make it
        # unusable (call_tool.arguments!). Carry it as a JSON string instead; the call path
        # decodes it against the original schema (decode_strict_arguments).
        desc = node.get("description")
        node.clear()
        node["type"] = "string"
        node["description"] = f"{desc} " if desc else ""
        node["description"] += 'JSON-encoded object, e.g. \'{"key": "value"}\'.'
        return

    if _is_object_node(node):
        node["type"] = "object"
        node["additionalProperties"] = False
        raw_props = node.get("properties")
        props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        orig_required = set(node.get("required") or [])
        transformed_props: dict[str, Any] = {}

        for name, prop in props.items():
            if isinstance(prop, dict):
                _transform_node(prop)
                if name not in orig_required and not _is_nullable(prop):
                    if set(prop.keys()) <= {"anyOf", "description", "title"} and isinstance(
                        prop.get("anyOf"), list
                    ):
                        prop["anyOf"].append({"type": "null"})
                    else:
                        prop_meta: dict[str, Any] = {
                            k: prop.pop(k) for k in ("description", "title") if k in prop
                        }
                        prop = {"anyOf": [prop, {"type": "null"}], **prop_meta}
                transformed_props[name] = prop
            else:
                transformed_props[name] = prop

        node["properties"] = transformed_props
        node["required"] = list(transformed_props.keys())

    for key in ("$defs", "definitions"):
        defs = node.get(key)
        if isinstance(defs, dict):
            for sub in defs.values():
                if isinstance(sub, dict):
                    _transform_node(sub)

    for key in ("anyOf", "oneOf", "allOf"):
        subs = node.get(key)
        if isinstance(subs, list):
            for sub in subs:
                if isinstance(sub, dict):
                    _transform_node(sub)

    items = node.get("items")
    if isinstance(items, dict):
        _transform_node(items)
    elif isinstance(items, list):
        for sub in items:
            if isinstance(sub, dict):
                _transform_node(sub)

    prefix_items = node.get("prefixItems")
    if isinstance(prefix_items, list):
        for sub in prefix_items:
            if isinstance(sub, dict):
                _transform_node(sub)


def to_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied, recursively transformed OpenAI strict JSON schema."""
    copied = copy.deepcopy(schema)
    if not copied:
        copied["type"] = "object"
    _transform_node(copied, root=True)
    return copied


def strip_strict_nulls(
    arguments: dict[str, Any] | None, schema: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Undo the strict projection on a call: omit ``None`` sentinels for optional
    non-nullable properties and decode JSON-string open maps back into objects."""
    if not arguments or not isinstance(schema, dict):
        return arguments
    props = schema.get("properties")
    if not isinstance(props, dict):
        return arguments
    required = set(schema.get("required") or [])
    cleaned: dict[str, Any] = {}
    for k, v in arguments.items():
        prop_schema = props.get(k)
        if (
            v is None
            and k not in required
            and isinstance(prop_schema, dict)
            and not _is_nullable(prop_schema)
        ):
            continue
        if isinstance(v, str) and isinstance(prop_schema, dict) and _accepts_open_map(prop_schema):
            v = _decode_json_object(v)
        if isinstance(v, dict) and isinstance(prop_schema, dict):
            cleaned[k] = strip_strict_nulls(v, prop_schema)
        else:
            cleaned[k] = v
    return cleaned


def _decode_json_object(value: str) -> Any:
    """Decode a strict-mode JSON-string map; anything else is left for validation to reject."""
    try:
        decoded = json.loads(value)
    except ValueError:
        return value
    return decoded if isinstance(decoded, dict) else value


class StrictNullMiddleware(Middleware):
    """Undo the strict projection before any tool runs (issue #293): drop ``null``
    sentinels and decode JSON-string open maps (e.g. ``call_tool.arguments``).

    ``tools/list`` advertises strict-shaped schemas (optional fields required + nullable),
    so strict clients send ``null`` for every unset optional field. Strip those against the
    tool's ORIGINAL schema so backends and the synthetic tools see exactly what a
    non-strict client would have sent; genuinely nullable properties keep their ``null``.
    """

    async def on_call_tool(
        self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]
    ) -> Any:
        message = context.message
        arguments = getattr(message, "arguments", None)
        fctx = context.fastmcp_context
        if (
            arguments
            and fctx is not None
            and any(v is None or isinstance(v, str) for v in arguments.values())
        ):
            tool = await fctx.fastmcp.get_tool(message.name)
            if tool is not None and isinstance(tool.parameters, dict):
                cleaned = strip_strict_nulls(arguments, tool.parameters)
                context = context.copy(message=message.model_copy(update={"arguments": cleaned}))
        return await call_next(context)
