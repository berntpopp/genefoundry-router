"""OpenAI strict schema projection for tool search and function calling (#116).

Transforms JSON schemas into OpenAI Structured Outputs-compatible strict schemas:
- Every object node has additionalProperties: False, explicit properties, and required.
- Optional properties are placed in required and made nullable via anyOf.
- Defaults are stripped and type-arrays (e.g. ["integer", "null"]) become anyOf unions.
- strip_strict_nulls omits None sentinels for optional non-nullable parameters on call_tool.
"""

from __future__ import annotations

import copy
from typing import Any


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


def _transform_node(node: dict[str, Any]) -> None:
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

    if _is_object_node(node):
        node["type"] = "object"
        # shortcut: open map schemas (additionalProperties without properties) close to empty
        # object in strict projection; convert to key-value entry arrays if a backend needs open maps.
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
    _transform_node(copied)
    return copied


def strip_strict_nulls(
    arguments: dict[str, Any] | None, schema: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Omit None values supplied for optional non-nullable properties in schema."""
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
        if isinstance(v, dict) and isinstance(prop_schema, dict):
            cleaned[k] = strip_strict_nulls(v, prop_schema)
        else:
            cleaned[k] = v
    return cleaned
