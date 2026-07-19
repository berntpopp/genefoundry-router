"""Tests for deterministic MCP definition capture and contract binding."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest
from mcp.types import Tool as McpTool

from genefoundry_router.release.definitions import (
    DefinitionEvidenceError,
    capture_definitions,
    load_definition_evidence,
    verify_definition_contract,
)
from genefoundry_router.release.runtime_identity import (
    RuntimeIdentityError,
    verify_readiness_data_identity,
)

RUNTIME_IDENTITY: dict[str, Any] = {
    "release_identity": {
        "schema_version": 1,
        "data_identity": {
            "expected": {
                "release_tag": "data-clingen-2026-07-16",
                "digest": "sha256:" + "a" * 64,
            },
            "actual": {
                "release_tag": "data-clingen-2026-07-16",
                "digest": "sha256:" + "a" * 64,
            },
        },
    }
}


def _tools() -> list[dict[str, object]]:
    return [
        {
            "name": "resolve_gene",
            "description": "Resolve a gene symbol.",
            "inputSchema": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
            "outputSchema": None,
            "annotations": {"readOnlyHint": True},
            "execution": None,
        },
        {
            "name": "get_capabilities",
            "description": "Describe the server.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "outputSchema": {"type": "object", "required": ["version", "name"]},
            "annotations": None,
            "execution": None,
        },
    ]


def test_capture_reuses_canonical_schema_normalization() -> None:
    first = _tools()
    second = copy.deepcopy(first)
    second.reverse()
    second[0]["inputSchema"] = {"properties": {}, "type": "object"}
    second[0]["outputSchema"] = {
        "required": ["name", "version"],
        "type": "object",
    }

    one = capture_definitions(first, context={"fixture": "empty"})
    two = capture_definitions(second, context={"fixture": "small"})

    assert one.definitions_sha256 == two.definitions_sha256
    assert one.definitions_document == two.definitions_document
    canonical_tools = one.definitions_document["tools"]
    assert isinstance(canonical_tools, list)
    assert [tool["name"] for tool in canonical_tools] == [
        "get_capabilities",
        "resolve_gene",
    ]


def test_capture_rejects_duplicate_tool_names() -> None:
    tools = _tools()
    tools.append(copy.deepcopy(tools[0]))

    with pytest.raises(DefinitionEvidenceError, match="duplicate tool name"):
        capture_definitions(tools, context={"fixture": "one"})


def test_capture_accepts_wire_tool_models_and_normalizes_null_description() -> None:
    wire_tool = McpTool(
        name="get_capabilities",
        description=None,
        inputSchema={"type": "object", "properties": {}, "required": []},
    )
    mapping_tool: dict[str, object] = {
        "name": "get_capabilities",
        "description": "",
        "inputSchema": {"properties": {}, "type": "object"},
    }

    from_model = capture_definitions((wire_tool,), context={"fixture": "one"})
    from_mapping = capture_definitions((mapping_tool,), context={"fixture": "two"})

    assert from_model.definitions_sha256 == from_mapping.definitions_sha256


def test_data_independent_requires_two_materially_different_contexts() -> None:
    capture = capture_definitions(_tools(), context={"fixture": "same"})

    with pytest.raises(DefinitionEvidenceError, match="different context-manifest hashes"):
        verify_definition_contract("data-independent", (capture, capture))


def test_data_independent_requires_equal_definition_hashes() -> None:
    changed = _tools()
    changed[0]["description"] = "Changed security-relevant description."
    first = capture_definitions(_tools(), context={"fixture": "empty"})
    second = capture_definitions(changed, context={"fixture": "populated"})

    with pytest.raises(DefinitionEvidenceError, match="equal definition hashes"):
        verify_definition_contract("data-independent", (first, second))


def test_contract_verification_rejects_forged_capture_hashes() -> None:
    first = capture_definitions(_tools(), context={"fixture": "empty"})
    second = capture_definitions(_tools(), context={"fixture": "populated"})
    forged = replace(first, definitions_sha256="f" * 64)
    matching_forgery = replace(second, definitions_sha256="f" * 64)

    with pytest.raises(DefinitionEvidenceError, match="not canonical or hash-bound"):
        verify_definition_contract("data-independent", (forged, matching_forgery))


def test_data_independent_evidence_records_both_context_hashes() -> None:
    first = capture_definitions(_tools(), context={"fixture": "empty", "rows": 0})
    second = capture_definitions(_tools(), context={"fixture": "populated", "rows": 12})

    evidence = verify_definition_contract("data-independent", (first, second))

    captures = evidence.context_document["captures"]
    assert isinstance(captures, list)
    assert [entry["context_sha256"] for entry in captures] == sorted(
        [first.context_sha256, second.context_sha256]
    )
    assert len({entry["context_sha256"] for entry in captures}) == 2
    assert {entry["definitions_sha256"] for entry in captures} == {first.definitions_sha256}
    assert evidence.definition_contract == "data-independent"


def test_data_bound_requires_exact_declared_data_identity() -> None:
    data_digest = f"sha256:{'a' * 64}"
    capture = capture_definitions(
        _tools(),
        context={"fixture": "production"},
        data_release_tag="data-2026.07.13",
        data_digest=data_digest,
        adoption="unadopted",
    )

    evidence = verify_definition_contract(
        "data-bound",
        (capture,),
        data_release_tag="data-2026.07.13",
        data_digest=data_digest,
        adoption="unadopted",
    )

    assert evidence.context_document["data_identity"] == {
        "digest": data_digest,
        "release_tag": "data-2026.07.13",
    }
    assert evidence.context_document["data_identity_contract"] == "unadopted"
    assert evidence.data_identity_contract == "unadopted"
    assert evidence.definition_contract == "data-bound"


@pytest.mark.parametrize(
    ("tag", "digest", "message"),
    [
        (None, None, "exact data tag and digest"),
        ("data-2026.07.14", f"sha256:{'a' * 64}", "does not match"),
        ("data-2026.07.13", f"sha256:{'b' * 64}", "does not match"),
    ],
)
def test_data_bound_rejects_missing_or_mismatched_identity(
    tag: str | None, digest: str | None, message: str
) -> None:
    capture = capture_definitions(
        _tools(),
        context={"fixture": "production"},
        data_release_tag="data-2026.07.13",
        data_digest=f"sha256:{'a' * 64}",
        adoption="unadopted",
    )

    with pytest.raises(DefinitionEvidenceError, match=message):
        verify_definition_contract(
            "data-bound",
            (capture,),
            data_release_tag=tag,
            data_digest=digest,
            adoption="unadopted",
        )


def test_data_identity_must_be_complete_and_exact() -> None:
    with pytest.raises(DefinitionEvidenceError, match="both data release tag and digest"):
        capture_definitions(
            _tools(),
            context={"fixture": "production"},
            data_release_tag="data-2026.07.13",
            adoption="unadopted",
        )

    with pytest.raises(DefinitionEvidenceError, match="sha256"):
        capture_definitions(
            _tools(),
            context={"fixture": "production"},
            data_release_tag="data-2026.07.13",
            data_digest="sha256:not-exact",
            adoption="unadopted",
        )


def test_adopted_data_bound_capture_seals_observed_health_identity() -> None:
    observed = verify_readiness_data_identity(
        RUNTIME_IDENTITY,
        release_tag="data-clingen-2026-07-16",
        digest="sha256:" + "a" * 64,
        adoption="runtime-v1",
    )
    capture = capture_definitions(
        _tools(), context={"runtime": "published"}, observed_identity=observed
    )

    evidence = verify_definition_contract("data-bound", [capture], observed_identity=observed)

    assert evidence.data_identity == RUNTIME_IDENTITY["release_identity"]["data_identity"]["actual"]
    assert evidence.data_identity_contract == "runtime-v1"
    assert evidence.context_document["data_identity_contract"] == "runtime-v1"


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.pop("release_identity"), "release_identity"),
        (
            lambda value: value["release_identity"].update({"unknown": True}),
            "invalid runtime-v1 release_identity",
        ),
        (
            lambda value: value["release_identity"]["data_identity"]["actual"].update(
                {"unknown": True}
            ),
            "invalid runtime-v1 release_identity",
        ),
        (
            lambda value: value["release_identity"]["data_identity"].pop("actual"),
            "invalid runtime-v1 release_identity",
        ),
        (
            lambda value: value["release_identity"].update({"schema_version": 2}),
            "invalid runtime-v1 release_identity",
        ),
        (
            lambda value: value["release_identity"].update({"schema_version": True}),
            "invalid runtime-v1 release_identity",
        ),
        (
            lambda value: value["release_identity"]["data_identity"]["expected"].update(
                {"digest": "sha256:" + "b" * 64}
            ),
            "expected identity does not match",
        ),
        (
            lambda value: value["release_identity"]["data_identity"]["actual"].update(
                {"release_tag": "data-clingen-2026-07-17"}
            ),
            "actual identity does not match",
        ),
    ],
)
def test_adopted_readiness_rejects_unprovable_identity(mutator: Any, message: str) -> None:
    readiness = copy.deepcopy(RUNTIME_IDENTITY)
    mutator(readiness)

    with pytest.raises(RuntimeIdentityError, match=message):
        verify_readiness_data_identity(
            readiness,
            release_tag="data-clingen-2026-07-16",
            digest="sha256:" + "a" * 64,
            adoption="runtime-v1",
        )


def test_runtime_v1_rejects_data_independent_requirements() -> None:
    with pytest.raises(RuntimeIdentityError, match="data-bound"):
        verify_readiness_data_identity(
            RUNTIME_IDENTITY,
            release_tag=None,
            digest=None,
            adoption="runtime-v1",
        )


def test_runtime_identity_rejects_unknown_adoption_state() -> None:
    with pytest.raises(RuntimeIdentityError, match="adoption"):
        verify_readiness_data_identity(
            RUNTIME_IDENTITY,
            release_tag="data-clingen-2026-07-16",
            digest="sha256:" + "a" * 64,
            adoption="unknown",  # type: ignore[arg-type]
        )


def test_unadopted_release_preserves_legacy_identity_only_when_explicit() -> None:
    assert (
        verify_readiness_data_identity(
            {"release_identity": {"legacy": "ignored"}},
            release_tag="data-clingen-2026-07-16",
            digest="sha256:" + "a" * 64,
            adoption="unadopted",
        )
        is None
    )

    with pytest.raises(DefinitionEvidenceError, match="explicitly unadopted"):
        capture_definitions(
            _tools(),
            context={"fixture": "legacy"},
            data_release_tag="data-clingen-2026-07-16",
            data_digest="sha256:" + "a" * 64,
        )


def test_contract_verification_rejects_unknown_contract() -> None:
    capture = capture_definitions(_tools(), context={"fixture": "production"})

    with pytest.raises(DefinitionEvidenceError, match="unknown definition contract"):
        verify_definition_contract("typo", (capture,))  # type: ignore[arg-type]


def test_definition_evidence_can_be_reloaded_for_separate_assembly() -> None:
    first = capture_definitions(_tools(), context={"fixture": "empty"})
    second = capture_definitions(_tools(), context={"fixture": "populated"})
    original = verify_definition_contract("data-independent", (first, second))

    loaded = load_definition_evidence(
        original.definitions_document,
        original.context_document,
    )

    assert loaded == original
