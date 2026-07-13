"""Evaluate native Trivy JSON without overloading the scanner process exit."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, IntEnum
from typing import Any

MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_RESULTS = 10_000
MAX_FINDINGS = 100_000
POLICY_VERSION = "fixable-high-critical-v1"
_NANOSECONDS_PER_SECOND = 1_000_000_000
_CLOCK_SKEW_SECONDS = 5 * 60
_MAX_REPORT_AGE_SECONDS = 60 * 60
_RFC3339 = re.compile(
    r"^(?P<calendar>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,9}))?(?P<timezone>Z|[+-][0-9]{2}:[0-9]{2})$"
)
_SEVERITIES = frozenset({"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"})
_GATED_SEVERITIES = frozenset({"HIGH", "CRITICAL"})


class ReleaseExitCode(IntEnum):
    """Shared workflow-facing release result contract."""

    SUCCESS = 0
    POLICY_VIOLATION = 1
    INVALID_EVIDENCE = 2
    INFRASTRUCTURE_FAILURE = 3


class VulnerabilityVerdict(str, Enum):
    """Machine-readable meaning of one vulnerability evaluation."""

    PASS = "pass"  # noqa: S105 - policy verdict, not a credential
    POLICY_VIOLATION = "policy_violation"
    INVALID_EVIDENCE = "invalid_evidence"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True)
class VulnerabilityFinding:
    """Bounded fields required to identify and remediate one Trivy finding."""

    target: str
    vulnerability_id: str
    package_name: str
    installed_version: str
    fixed_version: str
    severity: str
    status: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "fixed_version": self.fixed_version,
            "installed_version": self.installed_version,
            "package_name": self.package_name,
            "severity": self.severity,
            "status": self.status,
            "target": self.target,
            "vulnerability_id": self.vulnerability_id,
        }


@dataclass(frozen=True)
class TrivyEvaluation:
    """Stable evidence verdict consumed by workflows and release manifests."""

    verdict: VulnerabilityVerdict
    exit_code: ReleaseExitCode
    reason: str | None
    scanner: str = "trivy"
    scanner_exit: int | None = None
    scanner_version: str | None = None
    database_updated_at: str | None = None
    database_next_update: str | None = None
    database_downloaded_at: str | None = None
    scan_created_at: str | None = None
    fixable_high_critical: tuple[VulnerabilityFinding, ...] = ()
    unfixable_high_critical: tuple[VulnerabilityFinding, ...] = ()
    other_findings_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "database_downloaded_at": self.database_downloaded_at,
            "database_next_update": self.database_next_update,
            "database_updated_at": self.database_updated_at,
            "exit_code": int(self.exit_code),
            "fixable_high_critical": [item.to_dict() for item in self.fixable_high_critical],
            "other_findings_count": self.other_findings_count,
            "policy_version": POLICY_VERSION,
            "reason": self.reason,
            "scan_created_at": self.scan_created_at,
            "scanner": self.scanner,
            "scanner_exit": self.scanner_exit,
            "scanner_version": self.scanner_version,
            "schema_version": 1,
            "unfixable_high_critical": [item.to_dict() for item in self.unfixable_high_critical],
            "verdict": self.verdict.value,
        }


class _InvalidEvidenceError(ValueError):
    """Internal parse failure converted into an invalid-evidence verdict."""


@dataclass(frozen=True)
class _Timestamp:
    """One RFC3339 instant retained at Trivy's native nanosecond precision."""

    raw: str
    nanoseconds: int


def _invalid(reason: str, scanner_exit: int | None = None) -> TrivyEvaluation:
    return TrivyEvaluation(
        verdict=VulnerabilityVerdict.INVALID_EVIDENCE,
        exit_code=ReleaseExitCode.INVALID_EVIDENCE,
        reason=reason,
        scanner_exit=scanner_exit,
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidEvidenceError("Trivy evidence contains duplicate JSON object keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _InvalidEvidenceError(f"Trivy evidence contains non-JSON number {value}")


def _decode(document: bytes | str) -> object:
    if isinstance(document, bytes):
        size = len(document)
        try:
            text = document.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise _InvalidEvidenceError("Trivy evidence is not valid UTF-8") from exc
    elif isinstance(document, str):
        try:
            size = len(document.encode("utf-8", "strict"))
        except UnicodeEncodeError as exc:
            raise _InvalidEvidenceError("Trivy evidence is not valid UTF-8") from exc
        text = document
    else:
        raise _InvalidEvidenceError("Trivy evidence must be JSON bytes or text")
    if size == 0:
        raise _InvalidEvidenceError("Trivy evidence is empty")
    if size > MAX_EVIDENCE_BYTES:
        raise _InvalidEvidenceError("Trivy evidence exceeds the byte limit")
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except _InvalidEvidenceError:
        raise
    except (RecursionError, ValueError) as exc:
        raise _InvalidEvidenceError("Trivy evidence is not valid JSON") from exc


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _InvalidEvidenceError(f"{label} must be a JSON object")
    return value


def _string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not value and not allow_empty)
        or (value and not value.strip())
        or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        qualifier = "string" if allow_empty else "nonempty string"
        raise _InvalidEvidenceError(f"{label} must be a bounded {qualifier}")
    return value


def _timestamp(value: object, label: str) -> _Timestamp:
    raw = _string(value, label)
    match = _RFC3339.fullmatch(raw)
    if match is None:
        raise _InvalidEvidenceError(f"{label} must be an aware RFC3339 timestamp")
    timezone = match.group("timezone").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(match.group("calendar") + timezone)
    except ValueError as exc:
        raise _InvalidEvidenceError(f"{label} must be a valid RFC3339 timestamp") from exc
    if parsed.utcoffset() is None:
        raise _InvalidEvidenceError(f"{label} must include an explicit timezone")
    utc = parsed.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = utc - epoch
    whole_seconds = delta.days * 86_400 + delta.seconds
    fraction = (match.group("fraction") or "").ljust(9, "0")
    fraction_nanoseconds = int(fraction) if fraction else 0
    return _Timestamp(
        raw=raw,
        nanoseconds=whole_seconds * _NANOSECONDS_PER_SECOND + fraction_nanoseconds,
    )


def _evaluation_timestamp(evaluated_at: datetime | None) -> _Timestamp:
    value = datetime.now(UTC) if evaluated_at is None else evaluated_at
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise _InvalidEvidenceError("evaluation time must be an aware datetime")
    utc = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = utc - epoch
    whole_seconds = delta.days * 86_400 + delta.seconds
    return _Timestamp(
        raw=utc.isoformat().replace("+00:00", "Z"),
        nanoseconds=(whole_seconds * _NANOSECONDS_PER_SECOND + utc.microsecond * 1_000),
    )


def _validate_scan_freshness(created: _Timestamp, evaluated: _Timestamp) -> None:
    skew = _CLOCK_SKEW_SECONDS * _NANOSECONDS_PER_SECOND
    maximum_age = _MAX_REPORT_AGE_SECONDS * _NANOSECONDS_PER_SECOND
    if created.nanoseconds > evaluated.nanoseconds + skew:
        raise _InvalidEvidenceError("Trivy scan report is future-dated")
    if created.nanoseconds + maximum_age + skew < evaluated.nanoseconds:
        raise _InvalidEvidenceError("Trivy scan report is stale")


def _database_metadata(
    version_document: dict[str, Any], scan_created: _Timestamp, evaluated: _Timestamp
) -> tuple[str, str, str, str]:
    scanner_version = _string(version_document.get("Version"), "Trivy version")
    database = _mapping(version_document.get("VulnerabilityDB"), "VulnerabilityDB metadata")
    if type(database.get("Version")) is not int or database["Version"] < 1:
        raise _InvalidEvidenceError("VulnerabilityDB version must be a positive integer")
    updated = _timestamp(database.get("UpdatedAt"), "database UpdatedAt")
    next_update = _timestamp(database.get("NextUpdate"), "database NextUpdate")
    downloaded = _timestamp(database.get("DownloadedAt"), "database DownloadedAt")
    if not updated.nanoseconds <= downloaded.nanoseconds <= scan_created.nanoseconds:
        raise _InvalidEvidenceError("database timestamps are not ordered before the scan")
    if next_update.nanoseconds <= updated.nanoseconds:
        raise _InvalidEvidenceError("database NextUpdate must be later than UpdatedAt")
    if next_update.nanoseconds <= scan_created.nanoseconds:
        raise _InvalidEvidenceError("vulnerability database was stale when the scan was created")
    skew = _CLOCK_SKEW_SECONDS * _NANOSECONDS_PER_SECOND
    if next_update.nanoseconds + skew < evaluated.nanoseconds:
        raise _InvalidEvidenceError(
            "vulnerability database was stale when the evidence was evaluated"
        )
    return scanner_version, updated.raw, next_update.raw, downloaded.raw


def _finding(raw: object, target: str) -> VulnerabilityFinding:
    item = _mapping(raw, "Trivy vulnerability")
    severity = _string(item.get("Severity"), "vulnerability Severity")
    if severity not in _SEVERITIES:
        raise _InvalidEvidenceError("vulnerability Severity is unsupported")
    status_raw = item.get("Status")
    status = None if status_raw is None else _string(status_raw, "vulnerability Status")
    return VulnerabilityFinding(
        target=target,
        vulnerability_id=_string(item.get("VulnerabilityID"), "VulnerabilityID"),
        package_name=_string(item.get("PkgName"), "vulnerability PkgName"),
        installed_version=_string(item.get("InstalledVersion"), "vulnerability InstalledVersion"),
        fixed_version=_string(
            item.get("FixedVersion", ""), "vulnerability FixedVersion", allow_empty=True
        ),
        severity=severity,
        status=status,
    )


def _scan_findings(
    scan: dict[str, Any],
) -> tuple[_Timestamp, tuple[VulnerabilityFinding, ...], tuple[VulnerabilityFinding, ...], int]:
    if type(scan.get("SchemaVersion")) is not int or scan["SchemaVersion"] != 2:
        raise _InvalidEvidenceError("Trivy scan SchemaVersion must be the integer 2")
    if scan.get("ArtifactType") != "container_image":
        raise _InvalidEvidenceError("Trivy scan must describe a container_image")
    _string(scan.get("ArtifactName"), "Trivy ArtifactName")
    created = _timestamp(scan.get("CreatedAt"), "Trivy CreatedAt")
    results = scan.get("Results")
    if not isinstance(results, list) or not results or len(results) > MAX_RESULTS:
        raise _InvalidEvidenceError("Trivy Results must be a bounded nonempty array")
    findings: list[VulnerabilityFinding] = []
    for raw_result in results:
        result = _mapping(raw_result, "Trivy result")
        target = _string(result.get("Target"), "Trivy result Target")
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise _InvalidEvidenceError("Trivy Vulnerabilities must be an array or null")
        if len(findings) + len(vulnerabilities) > MAX_FINDINGS:
            raise _InvalidEvidenceError("Trivy vulnerability count exceeds the limit")
        findings.extend(_finding(item, target) for item in vulnerabilities)
    ordered = sorted(
        findings,
        key=lambda item: (
            item.vulnerability_id,
            item.package_name,
            item.installed_version,
            item.target,
            item.severity,
        ),
    )
    fixable = tuple(
        item for item in ordered if item.severity in _GATED_SEVERITIES and bool(item.fixed_version)
    )
    unfixable = tuple(
        item for item in ordered if item.severity in _GATED_SEVERITIES and not item.fixed_version
    )
    other_count = len(ordered) - len(fixable) - len(unfixable)
    return created, fixable, unfixable, other_count


def evaluate_trivy(
    document: bytes | str,
    scanner_exit: object,
    *,
    evaluated_at: datetime | None = None,
) -> TrivyEvaluation:
    """Evaluate a scan/version envelope and keep operational failure distinct.

    The envelope contains native JSON from ``trivy image --format json`` under
    ``scan`` and ``trivy version --format json`` under ``version``. The scanner
    process is always configured with exit zero for policy findings; its captured
    process exit is supplied independently here.
    """
    if type(scanner_exit) is not int or not 0 <= scanner_exit <= 255:
        return _invalid("scanner exit capture must be an integer from 0 through 255")
    if scanner_exit != 0:
        return TrivyEvaluation(
            verdict=VulnerabilityVerdict.INFRASTRUCTURE_FAILURE,
            exit_code=ReleaseExitCode.INFRASTRUCTURE_FAILURE,
            reason=f"Trivy process exited non-zero ({scanner_exit})",
            scanner_exit=scanner_exit,
        )
    try:
        evaluated = _evaluation_timestamp(evaluated_at)
        envelope = _mapping(_decode(document), "Trivy evidence")
        if set(envelope) != {"schema_version", "scan", "version"}:
            if "sarif" in {str(key).lower() for key in envelope}:
                raise _InvalidEvidenceError("SARIF is non-gating and is not valid Trivy evidence")
            raise _InvalidEvidenceError("Trivy evidence envelope fields are incomplete or unknown")
        if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
            raise _InvalidEvidenceError("Trivy evidence schema_version must be the integer 1")
        scan = _mapping(envelope["scan"], "Trivy scan")
        version = _mapping(envelope["version"], "Trivy version document")
        created, fixable, unfixable, other_count = _scan_findings(scan)
        _validate_scan_freshness(created, evaluated)
        scanner_version, updated, next_update, downloaded = _database_metadata(
            version, created, evaluated
        )
    except (KeyError, _InvalidEvidenceError) as exc:
        reason = str(exc) or "Trivy evidence is incomplete"
        return _invalid(reason, scanner_exit)
    violated = bool(fixable)
    return TrivyEvaluation(
        verdict=(VulnerabilityVerdict.POLICY_VIOLATION if violated else VulnerabilityVerdict.PASS),
        exit_code=(ReleaseExitCode.POLICY_VIOLATION if violated else ReleaseExitCode.SUCCESS),
        reason=("fixable HIGH/CRITICAL vulnerabilities found" if violated else None),
        scanner_exit=scanner_exit,
        scanner_version=scanner_version,
        database_updated_at=updated,
        database_next_update=next_update,
        database_downloaded_at=downloaded,
        scan_created_at=created.raw,
        fixable_high_critical=fixable,
        unfixable_high_critical=unfixable,
        other_findings_count=other_count,
    )


__all__ = [
    "ReleaseExitCode",
    "TrivyEvaluation",
    "VulnerabilityFinding",
    "VulnerabilityVerdict",
    "evaluate_trivy",
]
