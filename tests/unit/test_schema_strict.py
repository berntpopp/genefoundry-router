"""Unit tests for OpenAI strict schema transformation (#116)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from genefoundry_router.schema_strict import to_openai_strict_schema


def assert_strict_invariants(node: Any, path: str = "root") -> None:
    """Verify strict mode invariants on all object nodes in the JSON schema tree."""
    if not isinstance(node, dict):
        return

    is_object = (
        node.get("type") == "object"
        or (isinstance(node.get("type"), list) and "object" in node["type"])
        or ("properties" in node and node.get("type") in (None, "object"))
    )

    if is_object:
        assert node.get("additionalProperties") is False, (
            f"additionalProperties is not False at {path}: {node}"
        )
        assert isinstance(node.get("properties"), dict), f"properties is not dict at {path}: {node}"
        assert isinstance(node.get("required"), list), f"required is not list at {path}: {node}"
        assert set(node["required"]) == set(node["properties"].keys()), (
            f"required does not match properties.keys() at {path}"
        )

    for k, v in node.items():
        if isinstance(v, dict):
            assert_strict_invariants(v, f"{path}.{k}")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    assert_strict_invariants(item, f"{path}.{k}[{i}]")


def test_required_vs_optional_properties() -> None:
    """Optional properties become required + nullable via anyOf with default stripped."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "req_field": {"type": "string", "description": "A required string"},
            "opt_field": {
                "type": "integer",
                "description": "An optional int",
                "default": 42,
            },
        },
        "required": ["req_field"],
    }
    original = copy.deepcopy(schema)
    result = to_openai_strict_schema(schema)

    assert schema == original
    assert result["type"] == "object"
    assert result["additionalProperties"] is False
    assert set(result["required"]) == {"req_field", "opt_field"}

    req = result["properties"]["req_field"]
    assert req["type"] == "string"
    assert req["description"] == "A required string"
    assert "anyOf" not in req

    opt = result["properties"]["opt_field"]
    assert "default" not in opt
    assert opt["description"] == "An optional int"
    assert "anyOf" in opt
    assert {"type": "null"} in opt["anyOf"]
    non_null = [sub for sub in opt["anyOf"] if sub != {"type": "null"}]
    assert non_null == [{"type": "integer"}]


def test_already_nullable_optional_property_not_duplicated() -> None:
    """Already-nullable optional properties do not duplicate {'type': 'null'}."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "anyof_nullable": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
            },
            "type_list_nullable": {
                "type": ["string", "null"],
                "default": None,
            },
            "explicit_null_type": {
                "type": "null",
            },
            "oneof_nullable": {
                "oneOf": [{"type": "string"}, {"type": "null"}],
            },
        },
    }
    result = to_openai_strict_schema(schema)

    assert set(result["required"]) == {
        "anyof_nullable",
        "type_list_nullable",
        "explicit_null_type",
        "oneof_nullable",
    }
    for prop in result["properties"].values():
        assert "default" not in prop

    p1 = result["properties"]["anyof_nullable"]
    null_count = sum(1 for sub in p1["anyOf"] if sub == {"type": "null"})
    assert null_count == 1

    p2 = result["properties"]["type_list_nullable"]
    assert "anyOf" not in p2
    assert p2["type"] == ["string", "null"]

    p3 = result["properties"]["explicit_null_type"]
    assert "anyOf" not in p3
    assert p3["type"] == "null"


def test_optional_with_existing_anyof_not_nullable() -> None:
    """Optional property with non-nullable anyOf has {'type': 'null'} appended."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "union_field": {
                "anyOf": [{"type": "string"}, {"type": "integer"}],
                "description": "String or int",
            }
        },
    }
    result = to_openai_strict_schema(schema)
    assert result["required"] == ["union_field"]
    prop = result["properties"]["union_field"]
    assert prop["description"] == "String or int"
    assert {"type": "null"} in prop["anyOf"]
    assert len(prop["anyOf"]) == 3
    assert {"type": "string"} in prop["anyOf"]
    assert {"type": "integer"} in prop["anyOf"]


def test_open_object_with_no_properties() -> None:
    """Open object gets properties: {}, required: [], additionalProperties: False."""
    schema = {"type": "object"}
    result = to_openai_strict_schema(schema)
    assert result == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_object_without_explicit_type() -> None:
    """Object identified by properties gets explicit type: object and strict invariants."""
    schema = {"properties": {"x": {"type": "string"}}}
    result = to_openai_strict_schema(schema)
    assert result["type"] == "object"
    assert result["additionalProperties"] is False
    assert result["required"] == ["x"]
    assert result["properties"]["x"]["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_object_with_type_object_null() -> None:
    """Object with type: ['object', 'null'] satisfies strict object invariants."""
    schema: dict[str, Any] = {
        "type": ["object", "null"],
        "properties": {"k": {"type": "string"}},
        "required": ["k"],
    }
    result = to_openai_strict_schema(schema)
    assert result["type"] == ["object", "null"]
    assert result["additionalProperties"] is False
    assert result["required"] == ["k"]
    assert result["properties"]["k"] == {"type": "string"}


def test_nested_objects_recursively_transformed() -> None:
    """Nested objects in properties, items, prefixItems, anyOf/oneOf/allOf, and defs are transformed."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "nested_obj": {
                "type": "object",
                "properties": {"foo": {"type": "string"}},
                "required": ["foo"],
            },
            "array_of_objs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"bar": {"type": "integer"}},
                },
            },
            "tuple_items": {
                "type": "array",
                "items": [
                    {
                        "type": "object",
                        "properties": {"item1": {"type": "string"}},
                    }
                ],
            },
            "prefix_items_field": {
                "type": "array",
                "prefixItems": [
                    {
                        "type": "object",
                        "properties": {"prefix1": {"type": "boolean"}},
                    }
                ],
            },
            "one_of_field": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {"choice1": {"type": "string"}},
                    }
                ]
            },
            "all_of_field": {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"part1": {"type": "string"}},
                    }
                ]
            },
            "ref_field": {"$ref": "#/$defs/MyObj"},
        },
        "$defs": {
            "MyObj": {
                "type": "object",
                "properties": {"val": {"type": "number"}},
            }
        },
        "definitions": {
            "LegacyObj": {
                "type": "object",
                "properties": {"legacy": {"type": "string"}},
            }
        },
        "required": ["nested_obj", "array_of_objs"],
    }
    result = to_openai_strict_schema(schema)
    assert_strict_invariants(result)

    nested = result["properties"]["nested_obj"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["foo"]

    arr_items = result["properties"]["array_of_objs"]["items"]
    assert arr_items["additionalProperties"] is False
    assert arr_items["required"] == ["bar"]

    def_obj = result["$defs"]["MyObj"]
    assert def_obj["additionalProperties"] is False
    assert def_obj["required"] == ["val"]

    legacy_obj = result["definitions"]["LegacyObj"]
    assert legacy_obj["additionalProperties"] is False
    assert legacy_obj["required"] == ["legacy"]


def test_input_schema_not_mutated() -> None:
    """to_openai_strict_schema must never mutate caller's dictionary."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "sample"},
            "sub": {"type": "object", "properties": {"child": {"type": "integer"}}},
        },
        "required": [],
    }
    schema_clone = copy.deepcopy(schema)
    _ = to_openai_strict_schema(schema)
    assert schema == schema_clone


def test_fleet_baseline_schemas_satisfy_strict_invariants() -> None:
    """Every tool inputSchema in fleet-baseline.json transforms and satisfies strict invariants."""
    baseline_path = (
        Path(__file__).resolve().parents[2] / "genefoundry_router" / "data" / "fleet-baseline.json"
    )
    with baseline_path.open() as f:
        data = json.load(f)
    tools = [t for b in data["backends"].values() for t in b.get("tools", [])]
    assert len(tools) > 0

    for tool in tools:
        raw_schema = tool.get("inputSchema")
        if not raw_schema:
            continue
        strict = to_openai_strict_schema(raw_schema)
        assert_strict_invariants(strict, tool["name"])
