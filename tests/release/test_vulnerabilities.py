"""Tests for deterministic Trivy evidence and vulnerability policy verdicts."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from genefoundry_router.release.vulnerabilities import (
    ReleaseExitCode,
    VulnerabilityVerdict,
    evaluate_trivy,
)


def evidence(*vulnerabilities: dict[str, object]) -> dict[str, object]:
    """Return native Trivy scan/version documents in the stable evidence envelope."""
    return {
        "schema_version": 1,
        "scan": {
            "SchemaVersion": 2,
            "CreatedAt": "2026-07-13T10:30:00Z",
            "ArtifactName": "genefoundry-router:test",
            "ArtifactType": "container_image",
            "Metadata": {"OS": {"Family": "debian", "Name": "13"}},
            "Results": [
                {
                    "Target": "genefoundry-router:test (debian 13)",
                    "Class": "os-pkgs",
                    "Type": "debian",
                    "Vulnerabilities": list(vulnerabilities),
                }
            ],
        },
        "version": {
            "Version": "0.66.0",
            "VulnerabilityDB": {
                "Version": 2,
                "UpdatedAt": "2026-07-13T06:00:00Z",
                "NextUpdate": "2026-07-14T06:00:00Z",
                "DownloadedAt": "2026-07-13T10:00:00Z",
            },
        },
    }


def vulnerability(
    *,
    severity: str = "HIGH",
    fixed_version: str = "1.2.4",
    identifier: str = "CVE-2026-1234",
) -> dict[str, object]:
    return {
        "VulnerabilityID": identifier,
        "PkgName": "example",
        "InstalledVersion": "1.2.3",
        "FixedVersion": fixed_version,
        "Status": "fixed" if fixed_version else "affected",
        "Severity": severity,
    }


def encoded(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


def test_shared_exit_code_contract_is_stable() -> None:
    assert {member.name: member.value for member in ReleaseExitCode} == {
        "SUCCESS": 0,
        "POLICY_VIOLATION": 1,
        "INVALID_EVIDENCE": 2,
        "INFRASTRUCTURE_FAILURE": 3,
    }


def test_valid_clean_report_passes_and_records_scanner_metadata() -> None:
    result = evaluate_trivy(encoded(evidence()), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.PASS
    assert result.exit_code is ReleaseExitCode.SUCCESS
    assert result.scanner == "trivy"
    assert result.scanner_exit == 0
    assert result.scanner_version == "0.66.0"
    assert result.database_updated_at == "2026-07-13T06:00:00Z"
    assert result.database_next_update == "2026-07-14T06:00:00Z"
    assert result.fixable_high_critical == ()
    assert result.unfixable_high_critical == ()
    assert result.to_dict()["verdict"] == "pass"
    assert result.to_dict()["exit_code"] == 0
    assert result.to_dict()["scanner_exit"] == 0


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_fixable_high_or_critical_is_a_policy_violation(severity: str) -> None:
    finding = vulnerability(severity=severity)

    result = evaluate_trivy(encoded(evidence(finding)), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.POLICY_VIOLATION
    assert result.exit_code is ReleaseExitCode.POLICY_VIOLATION
    assert [item.vulnerability_id for item in result.fixable_high_critical] == ["CVE-2026-1234"]
    assert result.fixable_high_critical[0].fixed_version == "1.2.4"


def test_unfixable_high_or_critical_is_retained_but_does_not_gate() -> None:
    result = evaluate_trivy(
        encoded(
            evidence(
                vulnerability(severity="HIGH", fixed_version=""),
                vulnerability(
                    severity="CRITICAL",
                    fixed_version="",
                    identifier="CVE-2026-5678",
                ),
            )
        ),
        scanner_exit=0,
    )

    assert result.verdict is VulnerabilityVerdict.PASS
    assert result.exit_code is ReleaseExitCode.SUCCESS
    assert [item.vulnerability_id for item in result.unfixable_high_critical] == [
        "CVE-2026-1234",
        "CVE-2026-5678",
    ]


def test_native_omitted_fixed_version_is_retained_as_unfixable() -> None:
    finding = vulnerability(severity="HIGH", fixed_version="")
    del finding["FixedVersion"]

    result = evaluate_trivy(encoded(evidence(finding)), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.SUCCESS
    assert result.unfixable_high_critical[0].fixed_version == ""


def test_lower_severity_findings_are_retained_outside_the_gate() -> None:
    result = evaluate_trivy(
        encoded(evidence(vulnerability(severity="MEDIUM", fixed_version="1.2.4"))),
        scanner_exit=0,
    )

    assert result.verdict is VulnerabilityVerdict.PASS
    assert result.other_findings_count == 1


def test_stale_database_metadata_is_invalid_evidence() -> None:
    document = evidence()
    version = document["version"]
    assert isinstance(version, dict)
    database = version["VulnerabilityDB"]
    assert isinstance(database, dict)
    database["NextUpdate"] = "2026-07-13T10:00:00Z"

    result = evaluate_trivy(encoded(document), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "vulnerability database was stale when the scan was created"


@pytest.mark.parametrize(
    "document",
    [
        b"not-json",
        b"",
        b'{"schema_version":1,"schema_version":1}',
        b"[]",
    ],
)
def test_malformed_or_ambiguous_json_is_invalid_evidence(document: bytes) -> None:
    result = evaluate_trivy(document, scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.scanner_exit == 0
    assert result.reason


@pytest.mark.parametrize(
    "missing_path",
    [
        ("version",),
        ("version", "Version"),
        ("version", "VulnerabilityDB"),
        ("version", "VulnerabilityDB", "UpdatedAt"),
        ("version", "VulnerabilityDB", "NextUpdate"),
        ("scan",),
        ("scan", "CreatedAt"),
        ("scan", "Results"),
    ],
)
def test_missing_required_metadata_is_invalid_evidence(
    missing_path: tuple[str, ...],
) -> None:
    document = deepcopy(evidence())
    parent: object = document
    for part in missing_path[:-1]:
        assert isinstance(parent, dict)
        parent = parent[part]
    assert isinstance(parent, dict)
    del parent[missing_path[-1]]

    result = evaluate_trivy(encoded(document), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_nonzero_scanner_process_result_is_infrastructure_failure() -> None:
    result = evaluate_trivy(b"partial output is deliberately ignored", scanner_exit=2)

    assert result.verdict is VulnerabilityVerdict.INFRASTRUCTURE_FAILURE
    assert result.exit_code is ReleaseExitCode.INFRASTRUCTURE_FAILURE
    assert result.reason == "Trivy process exited non-zero (2)"
    assert result.scanner_exit == 2
    assert result.scanner_version is None


@pytest.mark.parametrize("scanner_exit", [-1, 256, True])
def test_invalid_scanner_exit_capture_is_invalid_evidence(scanner_exit: object) -> None:
    result = evaluate_trivy(encoded(evidence()), scanner_exit=scanner_exit)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("VulnerabilityID", ""),
        ("PkgName", 7),
        ("InstalledVersion", None),
        ("FixedVersion", None),
        ("Severity", "SEVERE"),
    ],
)
def test_malformed_vulnerability_is_invalid_evidence(field: str, value: object) -> None:
    finding = vulnerability()
    finding[field] = value

    result = evaluate_trivy(encoded(evidence(finding)), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_database_timestamps_must_be_ordered_aware_rfc3339() -> None:
    for field, value in (
        ("UpdatedAt", "2026-07-13 06:00:00"),
        ("DownloadedAt", "2026-07-13T05:00:00Z"),
        ("NextUpdate", "2026-07-13T05:00:00Z"),
    ):
        document = evidence()
        version = document["version"]
        assert isinstance(version, dict)
        database = version["VulnerabilityDB"]
        assert isinstance(database, dict)
        database[field] = value

        result = evaluate_trivy(encoded(document), scanner_exit=0)

        assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_scan_report_schema_and_artifact_type_are_fail_closed() -> None:
    for field, value in (("SchemaVersion", 1), ("ArtifactType", "filesystem")):
        document = evidence()
        scan = document["scan"]
        assert isinstance(scan, dict)
        scan[field] = value

        result = evaluate_trivy(encoded(document), scanner_exit=0)

        assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_evidence_envelope_rejects_unknown_top_level_fields() -> None:
    document = evidence()
    document["sarif"] = {"runs": []}

    result = evaluate_trivy(encoded(document), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason is not None
    assert "SARIF" in result.reason


def test_sarif_cannot_be_mistaken_for_gating_json() -> None:
    sarif = {"version": "2.1.0", "runs": []}

    result = evaluate_trivy(encoded(sarif), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_non_utf8_json_encoding_is_rejected() -> None:
    document = json.dumps(evidence()).encode("utf-16")

    result = evaluate_trivy(document, scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "Trivy evidence is not valid UTF-8"


def test_pathologically_nested_json_is_an_invalid_verdict_not_an_exception() -> None:
    document = ("[" * 2000 + "0" + "]" * 2000).encode()

    result = evaluate_trivy(document, scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


@pytest.mark.parametrize("fixed_version", [" ", "\n", "\x00"])
def test_ambiguous_fixed_version_is_invalid_evidence(fixed_version: str) -> None:
    result = evaluate_trivy(
        encoded(evidence(vulnerability(fixed_version=fixed_version))), scanner_exit=0
    )

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
