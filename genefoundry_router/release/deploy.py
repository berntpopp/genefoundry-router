"""Fail-closed online and offline deployment provenance verification."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from genefoundry_router.release.evidence import sha256_file
from genefoundry_router.release.models import ApplicationReleaseManifest
from genefoundry_router.release.source import CommandResult
from genefoundry_router.release.vulnerabilities import ReleaseExitCode

MINIMUM_GH_VERSION = (2, 93, 0)
COMMAND_TIMEOUT_SECONDS = 60
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
SLSA_PROVENANCE_V1 = "https://slsa.dev/provenance/v1"
SPDX_DOCUMENT_V2_3 = "https://spdx.dev/Document/v2.3"
_GH_VERSION = re.compile(r"^gh version ([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+][^\s]+)?(?:\s|$)")
_ENV_ALLOWLIST = frozenset(
    {
        "GH_ENTERPRISE_TOKEN",
        "GH_HOST",
        "GH_TOKEN",
        "HOME",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "XDG_CONFIG_HOME",
    }
)


class DeploymentCommandRunner(Protocol):
    """Narrow no-shell command boundary for deployment verification."""

    def __call__(self, args: Sequence[str]) -> CommandResult: ...


class DeploymentVerificationError(ValueError):
    """Deployment evidence or its verification failed closed."""

    def __init__(self, message: str, exit_code: ReleaseExitCode) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class DeploymentVerification:
    """Machine-readable success result safe to persist as deployment evidence."""

    mode: Literal["online", "offline"]
    repository: str
    source_tag: str
    source_revision: str
    image: str
    signer_workflow: str
    signer_revision: str
    gh_version: str
    exit_code: ReleaseExitCode = ReleaseExitCode.SUCCESS

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": int(self.exit_code),
            "gh_version": self.gh_version,
            "image": self.image,
            "mode": self.mode,
            "repository": self.repository,
            "signer_revision": self.signer_revision,
            "signer_workflow": self.signer_workflow,
            "source_revision": self.source_revision,
            "source_tag": self.source_tag,
            "verdict": "pass",
        }


def run_command(args: Sequence[str]) -> CommandResult:
    """Execute a bounded, noninteractive argument array without a shell."""
    environment = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
    environment.update({"GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"})
    try:
        completed = subprocess.run(  # noqa: S603
            list(args),
            check=False,
            capture_output=True,
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(125, "", "")
    if (
        len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr) > MAX_COMMAND_OUTPUT_BYTES
    ):
        return CommandResult(125, "", "")
    try:
        stdout = completed.stdout.decode("utf-8", "strict")
        stderr = completed.stderr.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return CommandResult(125, "", "")
    return CommandResult(completed.returncode, stdout, stderr)


def _bounded_output(result: CommandResult, label: str) -> None:
    try:
        stdout_size = len(result.stdout.encode("utf-8", "strict"))
        stderr_size = len(result.stderr.encode("utf-8", "strict"))
    except UnicodeEncodeError as exc:
        raise DeploymentVerificationError(
            f"{label} returned invalid output", ReleaseExitCode.INFRASTRUCTURE_FAILURE
        ) from exc
    if stdout_size > MAX_COMMAND_OUTPUT_BYTES or stderr_size > MAX_COMMAND_OUTPUT_BYTES:
        raise DeploymentVerificationError(
            f"{label} exceeded the output limit", ReleaseExitCode.INFRASTRUCTURE_FAILURE
        )


def _gh_version(runner: DeploymentCommandRunner) -> str:
    result = runner(("gh", "--version"))
    _bounded_output(result, "GitHub CLI version check")
    if result.returncode != 0:
        raise DeploymentVerificationError(
            "GitHub CLI version check failed", ReleaseExitCode.INFRASTRUCTURE_FAILURE
        )
    match = _GH_VERSION.match(result.stdout)
    if match is None:
        raise DeploymentVerificationError(
            "GitHub CLI returned an invalid version", ReleaseExitCode.INFRASTRUCTURE_FAILURE
        )
    version = tuple(int(part) for part in match.groups())
    if version < MINIMUM_GH_VERSION:
        required = ".".join(str(part) for part in MINIMUM_GH_VERSION)
        raise DeploymentVerificationError(
            f"GitHub CLI {required} or newer is required",
            ReleaseExitCode.INFRASTRUCTURE_FAILURE,
        )
    return ".".join(match.groups())


def _require_command(args: tuple[str, ...], runner: DeploymentCommandRunner, label: str) -> None:
    result = runner(args)
    _bounded_output(result, label)
    if result.returncode != 0:
        # Command output may contain credentials, URLs, or server-controlled text.
        raise DeploymentVerificationError(f"{label} failed", ReleaseExitCode.POLICY_VIOLATION)
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeploymentVerificationError(
            f"{label} did not return structured JSON",
            ReleaseExitCode.INFRASTRUCTURE_FAILURE,
        ) from exc
    if not isinstance(output, (dict, list)) or not output:
        raise DeploymentVerificationError(
            f"{label} did not return nonempty structured JSON",
            ReleaseExitCode.INFRASTRUCTURE_FAILURE,
        )


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise DeploymentVerificationError(
            f"{label} is not a regular file", ReleaseExitCode.INVALID_EVIDENCE
        )


def _verify_file_digest(path: Path, expected: str, label: str) -> None:
    _regular_file(path, label)
    try:
        actual = f"sha256:{sha256_file(path)}"
    except (OSError, ValueError) as exc:
        raise DeploymentVerificationError(
            f"unable to hash {label}", ReleaseExitCode.INVALID_EVIDENCE
        ) from exc
    if actual != expected:
        raise DeploymentVerificationError(
            f"{label} digest does not match the reviewed release manifest",
            ReleaseExitCode.INVALID_EVIDENCE,
        )


def _attestation_policy(
    manifest: ApplicationReleaseManifest, predicate_type: str
) -> tuple[str, ...]:
    signer_repo = manifest.workflow.standard.split("/.github/workflows/", maxsplit=1)[0]
    return (
        "--repo",
        manifest.repository,
        "--signer-repo",
        signer_repo,
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


def verify_deployment(
    manifest: ApplicationReleaseManifest,
    *,
    runner: DeploymentCommandRunner = run_command,
    image_manifest: Path | None = None,
    bundle: Path | None = None,
    trusted_root: Path | None = None,
) -> DeploymentVerification:
    """Verify one reviewed release online or from a complete offline evidence set."""
    gh_version = _gh_version(runner)
    offline_values = (image_manifest, bundle, trusted_root)
    offline = all(value is not None for value in offline_values)
    if any(value is not None for value in offline_values) and not offline:
        raise DeploymentVerificationError(
            "offline verification requires image manifest, bundle, and trusted root",
            ReleaseExitCode.INVALID_EVIDENCE,
        )
    if offline:
        assert image_manifest is not None and bundle is not None and trusted_root is not None
        _verify_file_digest(image_manifest, manifest.image.digest, "image manifest")
        _verify_file_digest(
            bundle,
            f"sha256:{manifest.security_evidence.attestation_bundle_sha256}",
            "attestation bundle",
        )
        _verify_file_digest(
            trusted_root,
            f"sha256:{manifest.security_evidence.trusted_root_sha256}",
            "trusted root",
        )
        for predicate_type, label in (
            (SLSA_PROVENANCE_V1, "offline GitHub provenance attestation verification"),
            (SPDX_DOCUMENT_V2_3, "offline GitHub SPDX SBOM attestation verification"),
        ):
            _require_command(
                (
                    "gh",
                    "attestation",
                    "verify",
                    str(image_manifest),
                    *_attestation_policy(manifest, predicate_type),
                    "--bundle",
                    str(bundle),
                    "--custom-trusted-root",
                    str(trusted_root),
                ),
                runner,
                label,
            )
        mode: Literal["online", "offline"] = "offline"
    else:
        _require_command(
            (
                "gh",
                "release",
                "verify",
                manifest.source.tag,
                "--repo",
                manifest.repository,
                "--format",
                "json",
            ),
            runner,
            "GitHub release verification",
        )
        for predicate_type, label in (
            (SLSA_PROVENANCE_V1, "GitHub provenance attestation verification"),
            (SPDX_DOCUMENT_V2_3, "GitHub SPDX SBOM attestation verification"),
        ):
            _require_command(
                (
                    "gh",
                    "attestation",
                    "verify",
                    f"oci://{manifest.image.name}@{manifest.image.digest}",
                    *_attestation_policy(manifest, predicate_type),
                ),
                runner,
                label,
            )
        mode = "online"
    return DeploymentVerification(
        mode=mode,
        repository=manifest.repository,
        source_tag=manifest.source.tag,
        source_revision=manifest.source.revision,
        image=f"{manifest.image.name}@{manifest.image.digest}",
        signer_workflow=manifest.workflow.standard,
        signer_revision=manifest.workflow.standard_revision,
        gh_version=gh_version,
    )


__all__ = [
    "MINIMUM_GH_VERSION",
    "DeploymentVerification",
    "DeploymentVerificationError",
    "run_command",
    "verify_deployment",
]
