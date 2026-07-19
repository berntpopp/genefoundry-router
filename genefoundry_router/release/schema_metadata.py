"""JSON Schema metadata for cross-field release configuration contracts."""

from __future__ import annotations

from pydantic.json_schema import JsonDict

SEMANTIC_SCHEMA_COMMENT = (
    "This checked-in schema enforces structural constraints. Acceptance additionally requires "
    "semantic validation through ReleaseConfig or ApplicationReleaseManifest; cross-field "
    "equality is enforced by Pydantic validators."
)
TOP_LEVEL_SCHEMA_METADATA: JsonDict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$comment": SEMANTIC_SCHEMA_COMMENT,
}

# These constraints overlay the generated field schemas instead of repeating their literals,
# patterns, or discriminated data union. The overlay only removes the nullable/default branches
# that ReleaseConfig's validator rejects for a data-bound definition contract.
RELEASE_CONFIG_SCHEMA_METADATA: JsonDict = TOP_LEVEL_SCHEMA_METADATA | {
    "allOf": [
        {
            "if": {
                "properties": {
                    "definitions": {
                        "properties": {"contract": {"const": "data-bound"}},
                        "required": ["contract"],
                    }
                },
                "required": ["definitions"],
            },
            "then": {
                "properties": {
                    "data": {
                        "properties": {
                            "mode": {"not": {"const": "none"}},
                            "release_tag": {"type": "string"},
                            "digest": {"type": "string"},
                        },
                        "required": ["mode", "release_tag", "digest"],
                    },
                    "data_identity_contract": {"not": {"type": "null"}},
                },
                "required": ["data", "data_identity_contract"],
            },
            "else": {
                "properties": {"data_identity_contract": {"type": "null"}},
            },
        }
    ]
}

APPLICATION_RELEASE_SCHEMA_METADATA: JsonDict = TOP_LEVEL_SCHEMA_METADATA | {
    "allOf": [
        {
            "if": {
                "properties": {
                    "mcp": {
                        "properties": {"definition_contract": {"const": "data-bound"}},
                        "required": ["definition_contract"],
                    }
                },
                "required": ["mcp"],
            },
            "then": {
                "properties": {
                    "data_requirements": {
                        "properties": {
                            "data_identity_contract": {"enum": ["unadopted", "runtime-v1"]}
                        },
                    }
                }
            },
            "else": {
                "properties": {
                    "data_requirements": {
                        "properties": {"data_identity_contract": {"type": "null"}}
                    }
                }
            },
        }
    ]
}

__all__ = [
    "APPLICATION_RELEASE_SCHEMA_METADATA",
    "RELEASE_CONFIG_SCHEMA_METADATA",
    "TOP_LEVEL_SCHEMA_METADATA",
]
