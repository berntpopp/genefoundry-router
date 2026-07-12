"""Contract checks for the staged fleet HTTP-policy-v1 rollout."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

MANIFEST_PATH = Path("ci/http-policy-v1.json")
CONFORMANCE_FIXTURE = Path("ci/http-policy-v1-conformance.py")
EVIDENCE_ROOT = Path("ci/http-policy-v1-evidence")
EXPECTED_REPOSITORIES = {
    "gtex-link",
    "litvar-link",
    "metadome-link",
    "panelapp-link",
    "spliceailookup-link",
    "stringdb-link",
    "uniprot-link",
    "vep-link",
}
EXPECTED_CASES = [
    "https_only",
    "reject_syntactic_userinfo",
    "normalized_exact_origin",
    "request_hook_checks_each_redirect_hop",
    "redirect_limit_at_most_five",
    "decoded_streaming_byte_cap",
    "fixed_host_free_non_retryable_error",
]


def _fixture_hash() -> str:
    return hashlib.sha256(CONFORMANCE_FIXTURE.read_bytes()).hexdigest()


def _validate_reviewer_attestation(
    repository: str, entry: dict[str, object], expected_hash: str
) -> None:
    reviewed_commit = entry["reviewed_commit"]
    assert isinstance(reviewed_commit, str) and re.fullmatch(r"[0-9a-f]{40}", reviewed_commit)
    assert reviewed_commit != "0" * 40, repository
    attestation = entry["attestation"]
    assert isinstance(attestation, dict), repository
    attestation_path = EVIDENCE_ROOT / repository / "attestation.json"
    conformance_copy = EVIDENCE_ROOT / repository / "test_http_policy_v1.py"
    assert attestation == {
        "kind": "reviewer-attested",
        "reviewer": "reviewer-handle",
        "reviewed_on": "YYYY-MM-DD",
        "attestation": str(attestation_path),
        "conformance_copy": str(conformance_copy),
    }, repository
    assert attestation_path.is_file(), repository
    assert conformance_copy.is_file(), repository

    attestation_record = json.loads(attestation_path.read_text())
    assert attestation_record == {
        "kind": "reviewer-attested",
        "repository": repository,
        "reviewed_commit": reviewed_commit,
        "conformance_file": entry["conformance_file"],
        "conformance_file_sha256": expected_hash,
        "canonical_fixture_sha256": expected_hash,
        "reviewer": attestation["reviewer"],
        "reviewed_on": attestation["reviewed_on"],
    }
    assert hashlib.sha256(conformance_copy.read_bytes()).hexdigest() == expected_hash


def test_http_policy_v1_adoption_manifest_covers_exact_issue_repositories() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())

    assert manifest["policy_version"] == "v1"
    assert set(manifest["repositories"]) == EXPECTED_REPOSITORIES
    assert manifest["required_conformance_cases"] == EXPECTED_CASES
    assert manifest["conformance_fixture"] == str(CONFORMANCE_FIXTURE)
    assert manifest["conformance_contract_sha256"] == _fixture_hash()


def test_http_policy_v1_adoption_manifest_allows_only_truthful_pending_or_adopted_rows() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected_hash = manifest["conformance_contract_sha256"]

    for repository, entry in manifest["repositories"].items():
        assert entry["status"] in {"pending", "adopted"}, repository
        assert entry["version"] == manifest["policy_version"], repository
        assert entry["conformance_file"] == "tests/conformance/test_http_policy_v1.py"
        if entry["status"] == "pending":
            assert entry["reviewed_commit"] is None, repository
            assert entry["conformance_sha256"] is None, repository
            assert entry["attestation"] is None, repository
        else:
            assert entry["conformance_sha256"] == expected_hash, repository
            _validate_reviewer_attestation(repository, entry, expected_hash)


def test_http_policy_v1_fixture_covers_every_normative_case() -> None:
    fixture = CONFORMANCE_FIXTURE.read_text()

    for case in EXPECTED_CASES:
        assert f"def test_{case}" in fixture


def test_http_policy_v1_rejects_adoption_without_reviewer_attestation() -> None:
    entry: dict[str, object] = {
        "status": "adopted",
        "version": "v1",
        "conformance_file": "tests/conformance/test_http_policy_v1.py",
        "reviewed_commit": "0" * 40,
        "conformance_sha256": _fixture_hash(),
        "attestation": None,
    }

    with pytest.raises(AssertionError):
        _validate_reviewer_attestation("gtex-link", entry, _fixture_hash())


def test_http_policy_v1_rejects_malformed_reviewed_commit(tmp_path: Path) -> None:
    conformance_copy = tmp_path / "test_http_policy_v1.py"
    conformance_copy.write_bytes(CONFORMANCE_FIXTURE.read_bytes())
    attestation = tmp_path / "attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "repository": "gtex-link",
                "reviewed_commit": "not-a-commit",
                "conformance_file": "tests/conformance/test_http_policy_v1.py",
                "conformance_file_sha256": _fixture_hash(),
                "canonical_fixture_sha256": _fixture_hash(),
                "reviewer": "reviewer-handle",
                "reviewed_on": "YYYY-MM-DD",
            }
        )
    )
    entry: dict[str, object] = {
        "status": "adopted",
        "version": "v1",
        "conformance_file": "tests/conformance/test_http_policy_v1.py",
        "reviewed_commit": "not-a-commit",
        "conformance_sha256": _fixture_hash(),
        "attestation": {
            "kind": "reviewer-attested",
            "reviewer": "reviewer-handle",
            "reviewed_on": "YYYY-MM-DD",
            "attestation": str(attestation),
            "conformance_copy": str(conformance_copy),
        },
    }

    with pytest.raises(AssertionError):
        _validate_reviewer_attestation("gtex-link", entry, _fixture_hash())


def test_http_policy_v1_rejects_attestation_evidence_outside_checked_in_layout(
    tmp_path: Path,
) -> None:
    conformance_copy = tmp_path / "untracked-copy.py"
    conformance_copy.write_bytes(CONFORMANCE_FIXTURE.read_bytes())
    attestation = tmp_path / "untracked-attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "repository": "gtex-link",
                "reviewed_commit": "a" * 40,
                "conformance_file": "tests/conformance/test_http_policy_v1.py",
                "conformance_file_sha256": _fixture_hash(),
                "canonical_fixture_sha256": _fixture_hash(),
                "reviewer": "reviewer-handle",
                "reviewed_on": "YYYY-MM-DD",
            }
        )
    )
    entry: dict[str, object] = {
        "status": "adopted",
        "version": "v1",
        "conformance_file": "tests/conformance/test_http_policy_v1.py",
        "reviewed_commit": "a" * 40,
        "conformance_sha256": _fixture_hash(),
        "attestation": {
            "kind": "reviewer-attested",
            "reviewer": "reviewer-handle",
            "reviewed_on": "YYYY-MM-DD",
            "attestation": str(attestation),
            "conformance_copy": str(conformance_copy),
        },
    }

    with pytest.raises(AssertionError):
        _validate_reviewer_attestation("gtex-link", entry, _fixture_hash())


def test_http_policy_v1_policy_artifact_contains_normative_requirements() -> None:
    policy = Path("docs/HTTP-POLICY-STANDARD-v1.md").read_text()

    for required_clause in (
        "HTTPS",
        "userinfo",
        "effective port",
        "every redirect hop",
        "five",
        "decoded-byte",
        "fixed, host-free",
        "non-retryable",
    ):
        assert required_clause in policy


def test_http_policy_v1_policy_never_claims_independent_backend_verification() -> None:
    policy = Path("docs/HTTP-POLICY-STANDARD-v1.md").read_text()

    assert "does not independently verify" in policy
    assert "external review and push" in policy
