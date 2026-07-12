"""Contract checks for the staged fleet HTTP-policy-v1 rollout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST_PATH = Path("ci/http-policy-v1.json")
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


def _contract_hash(cases: list[str]) -> str:
    content = "\n".join(cases) + "\n"
    return hashlib.sha256(content.encode()).hexdigest()


def test_http_policy_v1_adoption_manifest_covers_exact_issue_repositories() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())

    assert manifest["policy_version"] == "v1"
    assert set(manifest["repositories"]) == EXPECTED_REPOSITORIES
    assert manifest["required_conformance_cases"] == EXPECTED_CASES
    assert manifest["conformance_contract_sha256"] == _contract_hash(EXPECTED_CASES)


def test_http_policy_v1_adoption_manifest_allows_only_truthful_pending_or_adopted_rows() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    expected_hash = manifest["conformance_contract_sha256"]

    for repository, entry in manifest["repositories"].items():
        assert entry["status"] in {"pending", "adopted"}, repository
        assert entry["version"] == manifest["policy_version"], repository
        assert entry["conformance_file"] == "tests/conformance/test_http_policy_v1.py"
        if entry["status"] == "pending":
            assert entry["revision"] is None, repository
            assert entry["conformance_sha256"] is None, repository
        else:
            assert entry["conformance_sha256"] == expected_hash, repository
            assert isinstance(entry["revision"], str) and len(entry["revision"]) == 40


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
