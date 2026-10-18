"""Real-client schema compatibility tests across the fleet baseline catalog (#77 §5).

Verifies that all inputSchema and outputSchema objects across all 22 backends:
1. Are valid JSON Schema Draft 2020-12 objects.
2. Meet MCP structural expectations (inputSchema is an object).
3. Contain zero external HTTP/HTTPS $ref pointers and zero dangling local $ref pointers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from genefoundry_router.devtools.fakes import load_manifest

BASELINE_PATH = Path("genefoundry_router/data/fleet-baseline.json")


def find_schema_refs(schema: Any, path: str = "") -> list[tuple[str, str]]:
    """Recursively collect all ($ref_location_path, $ref_target_string) in a schema."""
    refs: list[tuple[str, str]] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "$ref" and isinstance(value, str):
                refs.append((path, value))
            else:
                refs.extend(find_schema_refs(value, f"{path}/{key}"))
    elif isinstance(schema, list):
        for idx, item in enumerate(schema):
            refs.extend(find_schema_refs(item, f"{path}/{idx}"))
    return refs


def resolve_local_json_pointer(root: Any, ref: str) -> tuple[bool, Any, str]:
    """Resolve an RFC 6901 JSON pointer against a root schema document.

    Only intra-document local references starting with '#/' are permitted. External
    URIs (e.g. http://, https://, or non-URI schemes) are rejected because real MCP
    clients (Claude Desktop, VS Code Copilot) cannot reliably fetch remote schemas.
    """
    if not ref.startswith("#/"):
        return False, None, f"Non-local $ref not allowed for MCP clients: {ref!r}"

    tokens = ref[2:].split("/")
    current = root
    for token in tokens:
        # RFC 6901 decoding: ~1 becomes '/', ~0 becomes '~'
        unescaped = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and unescaped in current:
            current = current[unescaped]
        elif isinstance(current, list) and unescaped.isdigit():
            idx = int(unescaped)
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return False, None, f"Index {idx} out of range in {ref!r}"
        else:
            return False, None, f"Token {unescaped!r} not found while resolving {ref!r}"

    return True, current, ""


def test_baseline_has_22_backends_and_285_tools() -> None:
    manifest = load_manifest(BASELINE_PATH)
    assert len(manifest.backends) == 22
    total_tools = sum(len(b.tools) for b in manifest.backends.values())
    assert total_tools == 285


def test_baseline_input_schemas_are_valid_draft_2020_12_objects() -> None:
    """All 285 tools must advertise valid Draft 2020-12 object input schemas."""
    manifest = load_manifest(BASELINE_PATH)
    for namespace, backend in manifest.backends.items():
        for tool in backend.tools:
            schema = tool.inputSchema
            assert isinstance(schema, dict), (
                f"{namespace}.{tool.name}: inputSchema must be a dictionary"
            )
            assert schema.get("type") == "object", (
                f"{namespace}.{tool.name}: inputSchema.type must be 'object'"
            )
            # Draft202012Validator.check_schema raises SchemaError if invalid
            Draft202012Validator.check_schema(schema)


def test_baseline_output_schemas_are_valid_draft_2020_12_when_declared() -> None:
    """All declared outputSchema objects must be valid Draft 2020-12 schemas."""
    manifest = load_manifest(BASELINE_PATH)
    output_schema_count = 0
    for namespace, backend in manifest.backends.items():
        for tool in backend.tools:
            schema = tool.outputSchema
            if schema is not None:
                output_schema_count += 1
                assert isinstance(schema, dict), (
                    f"{namespace}.{tool.name}: outputSchema must be a dictionary"
                )
                Draft202012Validator.check_schema(schema)

    # In baseline, 12 tools explicitly advertise an outputSchema (e.g. gtex, panelapp, etc.)
    assert output_schema_count == 12


def test_baseline_schemas_contain_no_dangling_or_external_refs() -> None:
    """All $ref pointers in inputSchema and outputSchema must be local and resolvable."""
    manifest = load_manifest(BASELINE_PATH)
    for namespace, backend in manifest.backends.items():
        for tool in backend.tools:
            for schema_kind, schema in (
                ("inputSchema", tool.inputSchema),
                ("outputSchema", tool.outputSchema),
            ):
                if schema is None:
                    continue
                refs = find_schema_refs(schema)
                for loc, target in refs:
                    # Assert no remote references
                    assert not target.startswith(("http://", "https://", "urn:")), (
                        f"{namespace}.{tool.name} {schema_kind} at {loc} has external $ref {target!r}"
                    )
                    # Assert clean local resolution within the same root document
                    resolved, _, reason = resolve_local_json_pointer(schema, target)
                    assert resolved, (
                        f"{namespace}.{tool.name} {schema_kind} at {loc} has unresolvable $ref {target!r}: {reason}"
                    )


def test_resolve_json_pointer_handles_defs_and_definitions() -> None:
    root = {
        "$defs": {
            "Coordinate": {
                "type": "object",
                "properties": {"chr": {"type": "string"}, "pos": {"type": "integer"}},
            }
        },
        "definitions": {
            "Legacy": {"type": "string"},
        },
        "properties": {
            "loc": {"$ref": "#/$defs/Coordinate"},
            "tag": {"$ref": "#/definitions/Legacy"},
        },
    }
    resolved, target, reason = resolve_local_json_pointer(root, "#/$defs/Coordinate")
    assert resolved
    assert reason == ""
    assert isinstance(target, dict) and target.get("type") == "object"

    resolved, target, reason = resolve_local_json_pointer(root, "#/definitions/Legacy")
    assert resolved
    assert target == {"type": "string"}


def test_resolve_json_pointer_handles_escaped_tokens() -> None:
    root = {
        "$defs": {
            "a/b": {"type": "string"},
            "m~n": {"type": "integer"},
        }
    }
    resolved, target, _ = resolve_local_json_pointer(root, "#/$defs/a~1b")
    assert resolved
    assert target == {"type": "string"}

    resolved, target, _ = resolve_local_json_pointer(root, "#/$defs/m~0n")
    assert resolved
    assert target == {"type": "integer"}


def test_resolve_json_pointer_rejects_external_and_dangling_refs() -> None:
    root = {
        "$defs": {"Item": {"type": "string"}},
    }
    resolved, _, reason = resolve_local_json_pointer(root, "https://example.com/schema.json")
    assert not resolved
    assert "Non-local $ref not allowed" in reason

    resolved, _, reason = resolve_local_json_pointer(root, "#/$defs/NonExistent")
    assert not resolved
    assert "Token 'NonExistent' not found" in reason


def test_draft202012_validator_detects_malformed_schemas() -> None:
    invalid_schema = {"type": 42}
    with pytest.raises(SchemaError):
        Draft202012Validator.check_schema(invalid_schema)
