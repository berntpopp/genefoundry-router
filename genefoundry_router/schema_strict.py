"""OpenAI strict schema projection for tool search and function calling (#116).

Transforms arbitrary valid JSON schemas into OpenAI Structured Outputs-compatible
strict schemas:
- Every object node has additionalProperties: False, explicit properties, and required.
- Optional properties are placed in required and made nullable via anyOf.
- Defaults on optional properties are stripped.
- Invariants are recursively applied across properties, items, prefixItems,
  unions (anyOf, oneOf, allOf), and definitions ($defs, definitions).
"""

from __future__ import annotations

import copy
from typing import Any


def _is_nullable(schema: dict[str, Any]) -> bool:
    """Return True if schema already permits null."""
    t = schema.get("type")
    if t == "null" or (isinstance(t, list) and "null" in t):
        return True
    if any(isinstance(sub, dict) and sub.get("type") == "null" for sub in schema.get("anyOf", [])):
        return True
    return any(
        isinstance(sub, dict) and sub.get("type") == "null" for sub in schema.get("oneOf", [])
    )


def _is_object_node(node: dict[str, Any]) -> bool:
    """Return True if node represents an object schema."""
    t = node.get("type")
    if t == "object" or (isinstance(t, list) and "object" in t):
        return True
    return bool("properties" in node and (t is None or t == "object"))


def _transform_node(node: dict[str, Any]) -> None:
    """Recursively transform schema node in-place into strict mode."""
    if _is_object_node(node):
        if "type" not in node:
            node["type"] = "object"
        node["additionalProperties"] = False
        raw_props = node.get("properties")
        props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        orig_required = set(node.get("required") or [])
        transformed_props: dict[str, Any] = {}

        for name, prop in props.items():
            if isinstance(prop, dict):
                _transform_node(prop)
                if name not in orig_required:
                    prop.pop("default", None)
                    if not _is_nullable(prop):
                        if "anyOf" in prop and isinstance(prop["anyOf"], list):
                            prop["anyOf"].append({"type": "null"})
                        else:
                            meta = {k: prop.pop(k) for k in ("description", "title") if k in prop}
                            prop = {"anyOf": [prop, {"type": "null"}], **meta}
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
    _transform_node(copied)
    return copied
