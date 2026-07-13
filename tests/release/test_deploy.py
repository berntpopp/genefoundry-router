"""Tests for fail-closed online and offline deployment provenance checks."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from genefoundry_router.release.deploy import (
    MINIMUM_GH_VERSION,
    DeploymentVerificationError,
    verify_deployment,
)
from genefoundry_router.release.models import ApplicationReleaseManifest
from genefoundry_router.release.source import CommandResult
from genefoundry_router.release.vulnerabilities import ReleaseExitCode


@pytest.fixture
def deployment_manifest() -> ApplicationReleaseManifest:
    digest = f"sha256:{'a' * 64}"
    checksum = "b" * 64
    return ApplicationReleaseManifest.model_validate(
        {
            "schema_version": 1,
            "repository": "berntpopp/gnomad-link",
            "version": "1.2.3",
            "source": {"tag": "v1.2.3", "revision": "c" * 40},
            "image": {
                "name": "ghcr.io/berntpopp/gnomad-link",
                "digest": digest,
                "platforms": [{"platform": "linux/amd64", "digest": digest}],
            },
            "workflow": {
                "caller": "berntpopp/gnomad-link/.github/workflows/container-release.yml",
                "standard": (
                    "berntpopp/genefoundry-router/.github/workflows/_container-release.yml"
                ),
                "standard_revision": "d" * 40,
            },
            "mcp": {
                "definitions_sha256": checksum,
                "capture_context_sha256": "e" * 64,
                "definition_contract": "data-independent",
            },
            "security_evidence": {
                "scanner": "trivy",
                "scanner_version": "0.66.0",
                "database_updated_at": "2026-07-13T10:30:00Z",
                "sbom_sha256": "1" * 64,
                "scanner_evidence_sha256": "2" * 64,
                "attestation_bundle_sha256": "3" * 64,
                "trusted_root_sha256": "4" * 64,
                "verification_sha256": "5" * 64,
            },
            "release_assets": {
                "image-manifest.json": digest,
                "sbom.spdx.json": f"sha256:{'1' * 64}",
                "mcp-definitions.json": f"sha256:{checksum}",
                "mcp-capture-context.json": f"sha256:{'e' * 64}",
                "trivy.json": f"sha256:{'2' * 64}",
                "attestation-bundle.json": f"sha256:{'3' * 64}",
                "trusted-root.json": f"sha256:{'4' * 64}",
                "verification.json": f"sha256:{'5' * 64}",
            },
            "data_requirements": {"mode": "none", "schema_compatibility": []},
        }
    )


class RecordingRunner:
    def __init__(
        self,
        *,
        version: str = "2.93.0",
        verification_output: str = '[{"verified":true}]',
    ) -> None:
        self.version = version
        self.verification_output = verification_output
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str]) -> CommandResult:
        call = tuple(args)
        self.calls.append(call)
        if call == ("gh", "--version"):
            return CommandResult(0, f"gh version {self.version} (2026-05-27)\n", "")
        return CommandResult(0, self.verification_output, "")


def _required_policy(manifest: ApplicationReleaseManifest, predicate_type: str) -> tuple[str, ...]:
    # --signer-repo is deliberately absent: `gh attestation verify` rejects it alongside
    # --signer-workflow, which already binds the signing repository.
    return (
        "--repo",
        manifest.repository,
        "--signer-workflow",
        manifest.workflow.standard,
        "--signer-digest",
        manifest.workflow.standard_revision,
        "--source-ref",
        f"refs/tags/{manifest.source.tag}",
        "--source-digest",
        manifest.source.revision,
        "--predicate-type",
        predicate_type,
        "--deny-self-hosted-runners",
        "--format",
        "json",
    )


def test_online_verification_pins_release_and_full_attestation_identity(
    deployment_manifest: ApplicationReleaseManifest,
) -> None:
    runner = RecordingRunner()

    result = verify_deployment(deployment_manifest, runner=runner)

    assert result.exit_code is ReleaseExitCode.SUCCESS
    assert runner.calls == [
        ("gh", "--version"),
        (
            "gh",
            "release",
            "verify",
            deployment_manifest.source.tag,
            "--repo",
            deployment_manifest.repository,
            "--format",
            "json",
        ),
        (
            "gh",
            "attestation",
            "verify",
            f"oci://{deployment_manifest.image.name}@{deployment_manifest.image.digest}",
            *_required_policy(deployment_manifest, "https://slsa.dev/provenance/v1"),
        ),
        (
            "gh",
            "attestation",
            "verify",
            f"oci://{deployment_manifest.image.name}@{deployment_manifest.image.digest}",
            *_required_policy(deployment_manifest, "https://spdx.dev/Document/v2.3"),
        ),
    ]


def test_offline_verification_hashes_local_manifest_and_uses_saved_trust_material(
    tmp_path: Path,
    deployment_manifest: ApplicationReleaseManifest,
) -> None:
    manifest_bytes = b'{"schemaVersion":2,"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
    image_manifest = tmp_path / "image-manifest.json"
    image_manifest.write_bytes(manifest_bytes)
    image_digest = f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}"
    payload = deployment_manifest.model_dump(mode="json")
    payload["image"]["digest"] = image_digest  # type: ignore[index]
    payload["image"]["platforms"][0]["digest"] = image_digest  # type: ignore[index]
    payload["release_assets"]["image-manifest.json"] = image_digest  # type: ignore[index]
    reviewed = ApplicationReleaseManifest.model_validate(payload)
    bundle = tmp_path / "attestation-bundle.json"
    trusted_root = tmp_path / "trusted-root.jsonl"
    bundle.write_text("{}", encoding="utf-8")
    trusted_root.write_text("{}\n", encoding="utf-8")
    payload = reviewed.model_dump(mode="json")
    payload["security_evidence"]["attestation_bundle_sha256"] = hashlib.sha256(  # type: ignore[index]
        bundle.read_bytes()
    ).hexdigest()
    payload["security_evidence"]["trusted_root_sha256"] = hashlib.sha256(  # type: ignore[index]
        trusted_root.read_bytes()
    ).hexdigest()
    payload["release_assets"]["attestation-bundle.json"] = (  # type: ignore[index]
        f"sha256:{hashlib.sha256(bundle.read_bytes()).hexdigest()}"
    )
    payload["release_assets"]["trusted-root.json"] = (  # type: ignore[index]
        f"sha256:{hashlib.sha256(trusted_root.read_bytes()).hexdigest()}"
    )
    reviewed = ApplicationReleaseManifest.model_validate(payload)
    runner = RecordingRunner()

    result = verify_deployment(
        reviewed,
        runner=runner,
        image_manifest=image_manifest,
        bundle=bundle,
        trusted_root=trusted_root,
    )

    assert result.mode == "offline"
    assert runner.calls == [
        ("gh", "--version"),
        (
            "gh",
            "attestation",
            "verify",
            str(image_manifest),
            *_required_policy(reviewed, "https://slsa.dev/provenance/v1"),
            "--bundle",
            str(bundle),
            "--custom-trusted-root",
            str(trusted_root),
        ),
        (
            "gh",
            "attestation",
            "verify",
            str(image_manifest),
            *_required_policy(reviewed, "https://spdx.dev/Document/v2.3"),
            "--bundle",
            str(bundle),
            "--custom-trusted-root",
            str(trusted_root),
        ),
    ]


def test_offline_verification_rejects_manifest_digest_mismatch_before_gh(
    tmp_path: Path,
    deployment_manifest: ApplicationReleaseManifest,
) -> None:
    image_manifest = tmp_path / "image-manifest.json"
    bundle = tmp_path / "bundle.json"
    trusted_root = tmp_path / "root.jsonl"
    image_manifest.write_bytes(b"tampered")
    bundle.write_bytes(b"{}")
    trusted_root.write_bytes(b"{}\n")
    runner = RecordingRunner()

    with pytest.raises(DeploymentVerificationError) as caught:
        verify_deployment(
            deployment_manifest,
            runner=runner,
            image_manifest=image_manifest,
            bundle=bundle,
            trusted_root=trusted_root,
        )

    assert caught.value.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert runner.calls == [("gh", "--version")]


@pytest.mark.parametrize("version", ["2.92.9", "1.99.0", "garbage"])
def test_verifier_requires_minimum_gh_with_release_verification_support(
    deployment_manifest: ApplicationReleaseManifest,
    version: str,
) -> None:
    runner = RecordingRunner(version=version)

    with pytest.raises(DeploymentVerificationError, match="GitHub CLI") as caught:
        verify_deployment(deployment_manifest, runner=runner)

    assert MINIMUM_GH_VERSION == (2, 93, 0)
    assert caught.value.exit_code is ReleaseExitCode.INFRASTRUCTURE_FAILURE
    assert runner.calls == [("gh", "--version")]


def test_command_failure_redacts_subprocess_output(
    deployment_manifest: ApplicationReleaseManifest,
) -> None:
    secret = "ghp_do-not-leak-this"  # noqa: S105 - synthetic redaction canary

    def runner(args: Sequence[str]) -> CommandResult:
        if tuple(args) == ("gh", "--version"):
            return CommandResult(0, "gh version 2.93.0\n", "")
        return CommandResult(1, secret, f"Authorization: Bearer {secret}")

    with pytest.raises(DeploymentVerificationError) as caught:
        verify_deployment(deployment_manifest, runner=runner)

    assert caught.value.exit_code is ReleaseExitCode.POLICY_VIOLATION
    assert secret not in str(caught.value)
    assert "Authorization" not in str(caught.value)


def test_oversized_command_output_is_bounded_and_redacted(
    deployment_manifest: ApplicationReleaseManifest,
) -> None:
    canary = "sensitive-server-output"

    def runner(args: Sequence[str]) -> CommandResult:
        if tuple(args) == ("gh", "--version"):
            return CommandResult(0, "gh version 2.93.0\n", "")
        return CommandResult(0, canary * 100_000, "")

    with pytest.raises(DeploymentVerificationError) as caught:
        verify_deployment(deployment_manifest, runner=runner)

    assert caught.value.exit_code is ReleaseExitCode.INFRASTRUCTURE_FAILURE
    assert canary not in str(caught.value)


@pytest.mark.parametrize("verification_output", ["", "not-json", "[]", "{}", "true", '"ok"'])
def test_successful_gh_command_requires_nonempty_structured_json(
    deployment_manifest: ApplicationReleaseManifest,
    verification_output: str,
) -> None:
    runner = RecordingRunner(verification_output=verification_output)

    with pytest.raises(DeploymentVerificationError, match="structured JSON") as caught:
        verify_deployment(deployment_manifest, runner=runner)

    assert caught.value.exit_code is ReleaseExitCode.INFRASTRUCTURE_FAILURE
    assert runner.calls[:2] == [
        ("gh", "--version"),
        (
            "gh",
            "release",
            "verify",
            deployment_manifest.source.tag,
            "--repo",
            deployment_manifest.repository,
            "--format",
            "json",
        ),
    ]


def test_partial_offline_evidence_is_rejected_before_attestation_command(
    tmp_path: Path,
    deployment_manifest: ApplicationReleaseManifest,
) -> None:
    runner = RecordingRunner()

    with pytest.raises(DeploymentVerificationError, match="requires image manifest") as caught:
        verify_deployment(
            deployment_manifest,
            runner=runner,
            image_manifest=tmp_path / "image-manifest.json",
        )

    assert caught.value.exit_code is ReleaseExitCode.INVALID_EVIDENCE
    assert runner.calls == [("gh", "--version")]
