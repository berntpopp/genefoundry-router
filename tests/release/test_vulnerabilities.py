"""Tests for deterministic Trivy evidence and vulnerability policy verdicts."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from genefoundry_router.release.vulnerabilities import (
    ReleaseExitCode,
    TrivyEvaluation,
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


EVALUATED_AT = datetime(2026, 7, 13, 10, 35, tzinfo=UTC)


def evaluate(
    document: bytes | str,
    scanner_exit: object,
    *,
    evaluated_at: datetime = EVALUATED_AT,
) -> TrivyEvaluation:
    """Evaluate deterministic fixture evidence at a fixed wall-clock time."""
    return evaluate_trivy(document, scanner_exit, evaluated_at=evaluated_at)


def test_shared_exit_code_contract_is_stable() -> None:
    assert {member.name: member.value for member in ReleaseExitCode} == {
        "SUCCESS": 0,
        "POLICY_VIOLATION": 1,
        "INVALID_EVIDENCE": 2,
        "INFRASTRUCTURE_FAILURE": 3,
    }


def test_valid_clean_report_passes_and_records_scanner_metadata() -> None:
    result = evaluate(encoded(evidence()), scanner_exit=0)

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

    result = evaluate(encoded(evidence(finding)), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.POLICY_VIOLATION
    assert result.exit_code is ReleaseExitCode.POLICY_VIOLATION
    assert [item.vulnerability_id for item in result.fixable_high_critical] == ["CVE-2026-1234"]
    assert result.fixable_high_critical[0].fixed_version == "1.2.4"


def test_unfixable_high_or_critical_is_retained_but_does_not_gate() -> None:
    result = evaluate(
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

    result = evaluate(encoded(evidence(finding)), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.SUCCESS
    assert result.unfixable_high_critical[0].fixed_version == ""


def test_lower_severity_findings_are_retained_outside_the_gate() -> None:
    result = evaluate(
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

    result = evaluate(encoded(document), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "vulnerability database was stale when the scan was created"


def test_database_expired_before_evaluation_is_invalid_evidence() -> None:
    document = evidence()
    version = document["version"]
    assert isinstance(version, dict)
    database = version["VulnerabilityDB"]
    assert isinstance(database, dict)
    database["NextUpdate"] = "2026-07-13T10:40:00Z"

    result = evaluate(
        encoded(document),
        scanner_exit=0,
        evaluated_at=datetime(2026, 7, 13, 10, 46, tzinfo=UTC),
    )

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "vulnerability database was stale when the evidence was evaluated"


def test_old_scan_report_is_invalid_even_if_database_claims_a_later_expiry() -> None:
    result = evaluate(
        encoded(evidence()),
        scanner_exit=0,
        evaluated_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
    )

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "Trivy scan report is stale"


@pytest.mark.parametrize(
    ("evaluated_at", "expected"),
    [
        (datetime(2026, 7, 13, 11, 35, tzinfo=UTC), ReleaseExitCode.SUCCESS),
        (datetime(2026, 7, 13, 11, 35, 1, tzinfo=UTC), ReleaseExitCode.INVALID_EVIDENCE),
    ],
)
def test_report_age_is_bounded_with_clock_skew(
    evaluated_at: datetime, expected: ReleaseExitCode
) -> None:
    result = evaluate(encoded(evidence()), scanner_exit=0, evaluated_at=evaluated_at)

    assert result.exit_code is expected


@pytest.mark.parametrize(
    ("evaluated_at", "expected"),
    [
        (datetime(2026, 7, 13, 10, 45, tzinfo=UTC), ReleaseExitCode.SUCCESS),
        (datetime(2026, 7, 13, 10, 45, 1, tzinfo=UTC), ReleaseExitCode.INVALID_EVIDENCE),
    ],
)
def test_database_expiry_is_bounded_with_clock_skew(
    evaluated_at: datetime, expected: ReleaseExitCode
) -> None:
    document = evidence()
    version = document["version"]
    assert isinstance(version, dict)
    database = version["VulnerabilityDB"]
    assert isinstance(database, dict)
    database["NextUpdate"] = "2026-07-13T10:40:00Z"

    result = evaluate(encoded(document), scanner_exit=0, evaluated_at=evaluated_at)

    assert result.exit_code is expected


def test_default_current_time_rejects_historical_report() -> None:
    document = evidence()
    scan = document["scan"]
    version = document["version"]
    assert isinstance(scan, dict)
    assert isinstance(version, dict)
    database = version["VulnerabilityDB"]
    assert isinstance(database, dict)
    scan["CreatedAt"] = "2020-01-01T00:30:00Z"
    database.update(
        {
            "UpdatedAt": "2020-01-01T00:00:00Z",
            "DownloadedAt": "2020-01-01T00:10:00Z",
            "NextUpdate": "2099-01-01T00:00:00Z",
        }
    )

    result = evaluate_trivy(encoded(document), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "Trivy scan report is stale"


@pytest.mark.parametrize(
    ("evaluated_at", "expected"),
    [
        (datetime(2026, 7, 13, 10, 24, 59, tzinfo=UTC), ReleaseExitCode.INVALID_EVIDENCE),
        (datetime(2026, 7, 13, 10, 25, tzinfo=UTC), ReleaseExitCode.SUCCESS),
    ],
)
def test_future_scan_is_allowed_only_within_bounded_clock_skew(
    evaluated_at: datetime, expected: ReleaseExitCode
) -> None:
    result = evaluate(encoded(evidence()), scanner_exit=0, evaluated_at=evaluated_at)

    assert result.exit_code is expected


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
    result = evaluate(document, scanner_exit=0)

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

    result = evaluate(encoded(document), scanner_exit=0)

    assert result.verdict is VulnerabilityVerdict.INVALID_EVIDENCE
    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_nonzero_scanner_process_result_is_infrastructure_failure() -> None:
    result = evaluate(b"partial output is deliberately ignored", scanner_exit=2)

    assert result.verdict is VulnerabilityVerdict.INFRASTRUCTURE_FAILURE
    assert result.exit_code is ReleaseExitCode.INFRASTRUCTURE_FAILURE
    assert result.reason == "Trivy process exited non-zero (2)"
    assert result.scanner_exit == 2
    assert result.scanner_version is None


@pytest.mark.parametrize("scanner_exit", [-1, 256, True])
def test_invalid_scanner_exit_capture_is_invalid_evidence(scanner_exit: object) -> None:
    result = evaluate(encoded(evidence()), scanner_exit=scanner_exit)

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

    result = evaluate(encoded(evidence(finding)), scanner_exit=0)

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

        result = evaluate(encoded(document), scanner_exit=0)

        assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_scan_report_schema_and_artifact_type_are_fail_closed() -> None:
    for field, value in (("SchemaVersion", 1), ("ArtifactType", "filesystem")):
        document = evidence()
        scan = document["scan"]
        assert isinstance(scan, dict)
        scan[field] = value

        result = evaluate(encoded(document), scanner_exit=0)

        assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_evidence_envelope_rejects_unknown_top_level_fields() -> None:
    document = evidence()
    document["sarif"] = {"runs": []}

    result = evaluate(encoded(document), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason is not None
    assert "SARIF" in result.reason


def test_sarif_cannot_be_mistaken_for_gating_json() -> None:
    sarif = {"version": "2.1.0", "runs": []}

    result = evaluate(encoded(sarif), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_non_utf8_json_encoding_is_rejected() -> None:
    document = json.dumps(evidence()).encode("utf-16")

    result = evaluate(document, scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "Trivy evidence is not valid UTF-8"


def test_pathologically_nested_json_is_an_invalid_verdict_not_an_exception() -> None:
    document = ("[" * 10_000 + "0" + "]" * 10_000).encode()

    result = evaluate(document, scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


def test_oversized_integer_is_an_invalid_verdict_not_an_exception() -> None:
    document = b'{"schema_version":' + (b"9" * 5_000) + b',"scan":{},"version":{}}'

    result = evaluate(document, scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE


@pytest.mark.parametrize(
    ("updated_at", "downloaded_at", "created_at"),
    [
        (
            "2026-07-13T10:00:00.000000200Z",
            "2026-07-13T10:00:00.000000100Z",
            "2026-07-13T10:00:01Z",
        ),
        (
            "2026-07-13T09:00:00Z",
            "2026-07-13T10:00:00.000000200Z",
            "2026-07-13T10:00:00.000000100Z",
        ),
    ],
)
def test_nanosecond_timestamp_misordering_is_rejected_exactly(
    updated_at: str, downloaded_at: str, created_at: str
) -> None:
    document = evidence()
    scan = document["scan"]
    version = document["version"]
    assert isinstance(scan, dict)
    assert isinstance(version, dict)
    database = version["VulnerabilityDB"]
    assert isinstance(database, dict)
    scan["CreatedAt"] = created_at
    database["UpdatedAt"] = updated_at
    database["DownloadedAt"] = downloaded_at

    result = evaluate(encoded(document), scanner_exit=0, evaluated_at=EVALUATED_AT)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert result.reason == "database timestamps are not ordered before the scan"


@pytest.mark.parametrize("fixed_version", [" ", "\n", "\x00"])
def test_ambiguous_fixed_version_is_invalid_evidence(fixed_version: str) -> None:
    result = evaluate(encoded(evidence(vulnerability(fixed_version=fixed_version))), scanner_exit=0)

    assert result.exit_code is ReleaseExitCode.INVALID_EVIDENCE
