"""Canonical MCP definition capture and release-contract verification."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from genefoundry_router.drift import ToolDefinition, canonical_json_schema
from genefoundry_router.release.runtime_identity import (
    DataIdentityAdoption,
    RuntimeIdentityPair,
)

DefinitionContract = Literal["data-independent", "data-bound"]
_DATA_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class DefinitionEvidenceError(ValueError):
    """MCP definition evidence cannot prove its declared release contract."""


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes, rejecting non-JSON values."""
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise DefinitionEvidenceError("evidence must contain only finite JSON values") from exc
    return encoded.encode("utf-8")


def _json_copy(value: object) -> object:
    return json.loads(canonical_json_bytes(value))


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _data_identity(release_tag: str | None, digest: str | None) -> dict[str, str] | None:
    if (release_tag is None) != (digest is None):
        raise DefinitionEvidenceError("both data release tag and digest are required")
    if release_tag is None or digest is None:
        return None
    if _DATA_TAG.fullmatch(release_tag) is None:
        raise DefinitionEvidenceError("data release tag is not an exact immutable identity")
    if _SHA256_DIGEST.fullmatch(digest) is None:
        raise DefinitionEvidenceError("data digest must be an exact lowercase sha256 digest")
    return {"digest": digest, "release_tag": release_tag}


ToolCaptureInput = Mapping[str, object] | BaseModel


def _canonical_tool(raw: ToolCaptureInput) -> dict[str, object]:
    if isinstance(raw, BaseModel):
        candidate = raw.model_dump(mode="json", by_alias=True, exclude_none=False)
    else:
        candidate = dict(raw)
    if candidate.get("description") is None:
        candidate["description"] = ""
    try:
        tool = ToolDefinition.model_validate(candidate)
    except ValidationError as exc:
        raise DefinitionEvidenceError("invalid MCP tool definition") from exc
    payload = tool.model_dump(mode="json", by_alias=True, exclude_none=False)
    for schema_key in ("inputSchema", "outputSchema"):
        payload[schema_key] = canonical_json_schema(payload[schema_key])
    copied = _json_copy(payload)
    if not isinstance(copied, dict):  # pragma: no cover - model_dump is a mapping
        raise DefinitionEvidenceError("invalid MCP tool definition")
    return copied


@dataclass(frozen=True)
class DefinitionCapture:
    """One canonical definition set captured in one explicit data context."""

    definitions_document: dict[str, object]
    definitions_sha256: str
    context: dict[str, object]
    context_sha256: str
    data_identity: dict[str, str] | None
    data_identity_contract: DataIdentityAdoption | None


@dataclass(frozen=True)
class DefinitionEvidence:
    """A definition artifact plus the context proof for its release contract."""

    definitions_document: dict[str, object]
    context_document: dict[str, object]
    definitions_sha256: str
    capture_context_sha256: str
    definition_contract: DefinitionContract
    data_identity: dict[str, str] | None
    data_identity_contract: DataIdentityAdoption | None


def _observed_identity(value: Mapping[str, str]) -> dict[str, str]:
    try:
        identity = RuntimeIdentityPair.model_validate(dict(value))
    except ValidationError as exc:
        raise DefinitionEvidenceError("observed data identity is invalid") from exc
    return identity.model_dump(mode="json")


def _capture_data_identity(
    *,
    observed_identity: Mapping[str, str] | None,
    data_release_tag: str | None,
    data_digest: str | None,
    adoption: DataIdentityAdoption | None,
) -> tuple[dict[str, str] | None, DataIdentityAdoption | None]:
    if observed_identity is not None:
        if adoption not in {None, "runtime-v1"}:
            raise DefinitionEvidenceError("observed identity requires runtime-v1 adoption")
        if data_release_tag is not None or data_digest is not None:
            raise DefinitionEvidenceError(
                "observed and legacy data identities are mutually exclusive"
            )
        return _observed_identity(observed_identity), "runtime-v1"
    if data_release_tag is not None or data_digest is not None:
        if adoption != "unadopted":
            raise DefinitionEvidenceError(
                "legacy data identity is allowed only for an explicitly unadopted release"
            )
        identity = _data_identity(data_release_tag, data_digest)
        return identity, "unadopted"
    if adoption is not None:
        raise DefinitionEvidenceError("data-bound definitions require an exact data tag and digest")
    return None, None


def capture_definitions(
    tools: Sequence[ToolCaptureInput],
    *,
    context: Mapping[str, object],
    observed_identity: Mapping[str, str] | None = None,
    data_release_tag: str | None = None,
    data_digest: str | None = None,
    adoption: DataIdentityAdoption | None = None,
) -> DefinitionCapture:
    """Canonicalize one MCP tool list and bind it to an explicit context manifest."""
    canonical_tools = [_canonical_tool(tool) for tool in tools]
    names = [tool["name"] for tool in canonical_tools]
    if len(names) != len(set(names)):
        raise DefinitionEvidenceError("duplicate tool name in MCP definition capture")
    canonical_tools.sort(key=lambda tool: str(tool["name"]))
    definitions_document: dict[str, object] = {
        "schema_version": 1,
        "tools": canonical_tools,
    }
    copied_context = _json_copy(dict(context))
    if not isinstance(copied_context, dict):  # pragma: no cover - dict remains a dict
        raise DefinitionEvidenceError("capture context must be a JSON object")
    identity, identity_contract = _capture_data_identity(
        observed_identity=observed_identity,
        data_release_tag=data_release_tag,
        data_digest=data_digest,
        adoption=adoption,
    )
    context_manifest: dict[str, object] = {"context": copied_context}
    if identity is not None:
        context_manifest["data_identity"] = identity
        context_manifest["data_identity_contract"] = identity_contract
    return DefinitionCapture(
        definitions_document=definitions_document,
        definitions_sha256=_sha256_json(definitions_document),
        context=copied_context,
        context_sha256=_sha256_json(context_manifest),
        data_identity=identity,
        data_identity_contract=identity_contract,
    )


def _capture_record(capture: DefinitionCapture) -> dict[str, object]:
    record: dict[str, object] = {
        "context": capture.context,
        "context_sha256": capture.context_sha256,
        "definitions_sha256": capture.definitions_sha256,
    }
    if capture.data_identity is not None:
        record["data_identity"] = capture.data_identity
        record["data_identity_contract"] = capture.data_identity_contract
    return record


def _validate_definitions_document(document: dict[str, object], expected_sha256: str) -> None:
    if set(document) != {"schema_version", "tools"} or document["schema_version"] != 1:
        raise DefinitionEvidenceError("definition artifact has an invalid schema")
    tools = document["tools"]
    if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
        raise DefinitionEvidenceError("definition artifact has an invalid tool list")
    canonical_tools = [_canonical_tool(tool) for tool in tools]
    names = [tool["name"] for tool in canonical_tools]
    if len(names) != len(set(names)):
        raise DefinitionEvidenceError("duplicate tool name in MCP definition artifact")
    canonical_tools.sort(key=lambda tool: str(tool["name"]))
    expected = {"schema_version": 1, "tools": canonical_tools}
    if expected != document or _sha256_json(document) != expected_sha256:
        raise DefinitionEvidenceError("definition artifact is not canonical or hash-bound")


def _validate_capture_input(capture: DefinitionCapture) -> None:
    _validate_definitions_document(capture.definitions_document, capture.definitions_sha256)
    identity = capture.data_identity
    if identity is not None and (
        set(identity) != {"digest", "release_tag"}
        or _data_identity(identity.get("release_tag"), identity.get("digest")) != identity
    ):
        raise DefinitionEvidenceError("definition capture has an invalid data identity")
    if (identity is None) != (capture.data_identity_contract is None):
        raise DefinitionEvidenceError("definition capture has invalid identity provenance")
    if capture.data_identity_contract not in {None, "unadopted", "runtime-v1"}:
        raise DefinitionEvidenceError("definition capture has invalid identity provenance")
    context_manifest: dict[str, object] = {"context": capture.context}
    if identity is not None:
        context_manifest["data_identity"] = identity
        context_manifest["data_identity_contract"] = capture.data_identity_contract
    if _sha256_json(context_manifest) != capture.context_sha256:
        raise DefinitionEvidenceError("definition capture is not canonical or hash-bound")


def _validated_capture_record(raw: object, expected_definitions_sha256: str) -> DefinitionCapture:
    if not isinstance(raw, dict):
        raise DefinitionEvidenceError("definition context capture must be a JSON object")
    allowed = {
        "context",
        "context_sha256",
        "definitions_sha256",
        "data_identity",
        "data_identity_contract",
    }
    if not {"context", "context_sha256", "definitions_sha256"}.issubset(raw) or not set(
        raw
    ).issubset(allowed):
        raise DefinitionEvidenceError("definition context capture has invalid fields")
    context = raw["context"]
    if not isinstance(context, dict):
        raise DefinitionEvidenceError("definition context must be a JSON object")
    data = raw.get("data_identity")
    if data is not None and not isinstance(data, dict):
        raise DefinitionEvidenceError("captured data identity must be a JSON object")
    release_tag = data.get("release_tag") if isinstance(data, dict) else None
    digest = data.get("digest") if isinstance(data, dict) else None
    if release_tag is not None and not isinstance(release_tag, str):
        raise DefinitionEvidenceError("captured data release tag must be a string")
    if digest is not None and not isinstance(digest, str):
        raise DefinitionEvidenceError("captured data digest must be a string")
    identity = _data_identity(release_tag, digest)
    if data is not None and (set(data) != {"digest", "release_tag"} or data != identity):
        raise DefinitionEvidenceError("captured data identity has invalid fields")
    identity_contract = raw.get("data_identity_contract")
    if identity_contract not in {None, "unadopted", "runtime-v1"} or (data is None) != (
        identity_contract is None
    ):
        raise DefinitionEvidenceError("captured data identity has invalid provenance")
    manifest: dict[str, object] = {"context": context}
    if identity is not None:
        manifest["data_identity"] = identity
        manifest["data_identity_contract"] = identity_contract
    context_sha256 = raw["context_sha256"]
    definitions_sha256 = raw["definitions_sha256"]
    if (
        not isinstance(context_sha256, str)
        or not isinstance(definitions_sha256, str)
        or context_sha256 != _sha256_json(manifest)
        or definitions_sha256 != expected_definitions_sha256
    ):
        raise DefinitionEvidenceError("definition context capture is not hash-bound")
    return DefinitionCapture(
        definitions_document={},
        definitions_sha256=definitions_sha256,
        context=context,
        context_sha256=context_sha256,
        data_identity=identity,
        data_identity_contract=identity_contract,
    )


def validate_definition_evidence(evidence: DefinitionEvidence) -> None:
    """Revalidate a complete evidence object at an assembly trust boundary."""
    _validate_definitions_document(evidence.definitions_document, evidence.definitions_sha256)
    document = evidence.context_document
    base_fields = {"captures", "definition_contract", "schema_version"}
    if (
        document.get("schema_version") != 1
        or document.get("definition_contract") != evidence.definition_contract
        or not base_fields.issubset(document)
        or not set(document).issubset(base_fields | {"data_identity", "data_identity_contract"})
        or _sha256_json(document) != evidence.capture_context_sha256
    ):
        raise DefinitionEvidenceError("definition contract document is not hash-bound")
    raw_captures = document["captures"]
    if not isinstance(raw_captures, list):
        raise DefinitionEvidenceError("definition contract captures must be a JSON array")
    captures = [_validated_capture_record(raw, evidence.definitions_sha256) for raw in raw_captures]
    if evidence.definition_contract == "data-independent":
        if (
            len(captures) != 2
            or len({capture.context_sha256 for capture in captures}) != 2
            or evidence.data_identity is not None
            or evidence.data_identity_contract is not None
            or "data_identity" in document
            or "data_identity_contract" in document
        ):
            raise DefinitionEvidenceError(
                "data-independent definition contract lacks two different contexts"
            )
    else:
        top_identity = document.get("data_identity")
        top_identity_contract = document.get("data_identity_contract")
        if (
            len(captures) != 1
            or evidence.data_identity is None
            or evidence.data_identity_contract not in {"unadopted", "runtime-v1"}
            or top_identity != evidence.data_identity
            or top_identity_contract != evidence.data_identity_contract
            or captures[0].data_identity != evidence.data_identity
            or captures[0].data_identity_contract != evidence.data_identity_contract
        ):
            raise DefinitionEvidenceError(
                "data-bound definition contract lacks its exact data identity"
            )


def verify_definition_contract(
    contract: DefinitionContract,
    captures: Sequence[DefinitionCapture],
    *,
    observed_identity: Mapping[str, str] | None = None,
    data_release_tag: str | None = None,
    data_digest: str | None = None,
    adoption: DataIdentityAdoption | None = None,
) -> DefinitionEvidence:
    """Prove two-context independence or one exact data-bound definition identity."""
    if contract not in {"data-independent", "data-bound"}:
        raise DefinitionEvidenceError(f"unknown definition contract: {contract}")
    for capture in captures:
        _validate_capture_input(capture)
    if contract == "data-independent":
        if (
            observed_identity is not None
            or data_release_tag is not None
            or data_digest is not None
            or adoption is not None
        ):
            raise DefinitionEvidenceError(
                "data-independent verification does not accept a production data identity"
            )
        if len(captures) != 2:
            raise DefinitionEvidenceError(
                "data-independent definitions require exactly two capture contexts"
            )
        context_hashes = {capture.context_sha256 for capture in captures}
        if len(context_hashes) != 2:
            raise DefinitionEvidenceError(
                "data-independent definitions require two different context-manifest hashes"
            )
        definition_hashes = {capture.definitions_sha256 for capture in captures}
        if len(definition_hashes) != 1:
            raise DefinitionEvidenceError(
                "data-independent captures require equal definition hashes"
            )
        identity = None
        identity_contract = None
    else:
        expected_identity, identity_contract = _capture_data_identity(
            observed_identity=observed_identity,
            data_release_tag=data_release_tag,
            data_digest=data_digest,
            adoption=adoption,
        )
        if expected_identity is None:
            raise DefinitionEvidenceError(
                "data-bound definitions require an exact data tag and digest"
            )
        if len(captures) != 1:
            raise DefinitionEvidenceError("data-bound definitions require exactly one capture")
        if captures[0].data_identity != expected_identity:
            raise DefinitionEvidenceError(
                "captured data identity does not match the exact declared data tag and digest"
            )
        if captures[0].data_identity_contract != identity_contract:
            raise DefinitionEvidenceError(
                "captured data identity provenance does not match the declared adoption"
            )
        identity = expected_identity

    ordered = sorted(captures, key=lambda capture: capture.context_sha256)
    context_document: dict[str, object] = {
        "captures": [_capture_record(capture) for capture in ordered],
        "definition_contract": contract,
        "schema_version": 1,
    }
    if identity is not None:
        context_document["data_identity"] = identity
        context_document["data_identity_contract"] = identity_contract
    definitions_document = ordered[0].definitions_document
    return DefinitionEvidence(
        definitions_document=definitions_document,
        context_document=context_document,
        definitions_sha256=ordered[0].definitions_sha256,
        capture_context_sha256=_sha256_json(context_document),
        definition_contract=contract,
        data_identity=identity,
        data_identity_contract=identity_contract,
    )


def load_definition_evidence(
    definitions_document: Mapping[str, object],
    context_document: Mapping[str, object],
) -> DefinitionEvidence:
    """Reconstruct and validate evidence from separately persisted capture assets."""
    copied_definitions = _json_copy(dict(definitions_document))
    copied_context = _json_copy(dict(context_document))
    if not isinstance(copied_definitions, dict) or not isinstance(copied_context, dict):
        raise DefinitionEvidenceError("definition evidence assets must be JSON objects")
    contract = copied_context.get("definition_contract")
    if contract not in {"data-independent", "data-bound"}:
        raise DefinitionEvidenceError(f"unknown definition contract: {contract}")
    raw_identity = copied_context.get("data_identity")
    identity: dict[str, str] | None = None
    if raw_identity is not None:
        if not isinstance(raw_identity, dict):
            raise DefinitionEvidenceError("definition data identity must be a JSON object")
        tag = raw_identity.get("release_tag")
        digest = raw_identity.get("digest")
        if not isinstance(tag, str) or not isinstance(digest, str):
            raise DefinitionEvidenceError("definition data identity must contain strings")
        identity = _data_identity(tag, digest)
        if set(raw_identity) != {"digest", "release_tag"} or raw_identity != identity:
            raise DefinitionEvidenceError("definition data identity has invalid fields")
    identity_contract = copied_context.get("data_identity_contract")
    if identity_contract not in {None, "unadopted", "runtime-v1"} or (identity is None) != (
        identity_contract is None
    ):
        raise DefinitionEvidenceError("definition data identity has invalid provenance")
    evidence = DefinitionEvidence(
        definitions_document=copied_definitions,
        context_document=copied_context,
        definitions_sha256=_sha256_json(copied_definitions),
        capture_context_sha256=_sha256_json(copied_context),
        definition_contract=contract,
        data_identity=identity,
        data_identity_contract=identity_contract,
    )
    validate_definition_evidence(evidence)
    return evidence


__all__ = [
    "DefinitionCapture",
    "DefinitionEvidence",
    "DefinitionEvidenceError",
    "capture_definitions",
    "canonical_json_bytes",
    "load_definition_evidence",
    "validate_definition_evidence",
    "verify_definition_contract",
]
