"""Stable JSON adapters for repository and reusable-workflow release tooling."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import typer
from pydantic import ValidationError

from genefoundry_router.release.compose import (
    AuxiliaryServiceRule,
    ComposePolicy,
    validate_compose,
)
from genefoundry_router.release.content import inspect_oci_layout
from genefoundry_router.release.data import (
    DataReleaseManifest,
    DataVerificationError,
)
from genefoundry_router.release.data_materialization import (
    materialize_data,
    probe_schema_file,
    rollback_data,
)
from genefoundry_router.release.definitions import (
    canonical_json_bytes,
    capture_definitions,
    load_definition_evidence,
    verify_definition_contract,
)
from genefoundry_router.release.deploy import DeploymentVerificationError, verify_deployment
from genefoundry_router.release.evidence import (
    ApplicationIdentity,
    ReleaseAsset,
    ScannerIdentity,
    assemble_application_release_manifest,
    write_json_atomic,
)
from genefoundry_router.release.models import ApplicationReleaseManifest, ReleaseConfig
from genefoundry_router.release.runtime_identity import (
    DataIdentityAdoption,
    verify_readiness_data_identity,
)
from genefoundry_router.release.smoke import parse_smoke_env, render_smoke_override
from genefoundry_router.release.source import validate_source_release
from genefoundry_router.release.vulnerabilities import ReleaseExitCode, evaluate_trivy

MAX_INPUT_BYTES = 64 * 1024 * 1024
STANDARD_ASSETS = (
    "attestation-bundle.json",
    "image-manifest.json",
    "mcp-capture-context.json",
    "mcp-definitions.json",
    "sbom.spdx.json",
    "trusted-root.json",
    "trivy.json",
    "verification.json",
)

app = typer.Typer(
    add_completion=False,
    help="Validate and assemble GeneFoundry container release evidence.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
)


class _CliResult:
    def __init__(self, payload: dict[str, object], exit_code: ReleaseExitCode) -> None:
        self.payload = payload
        self.exit_code = exit_code


def _read_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError("input is not a regular file")
    try:
        size = path.stat().st_size
        if size <= 0 or size > MAX_INPUT_BYTES:
            raise ValueError("input file size is outside the allowed range")
        return json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("input is not valid UTF-8 JSON") from exc


def _object(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise ValueError("input must be a JSON object")
    return value


def _array(path: Path) -> list[Any]:
    value = _read_json(path)
    if not isinstance(value, list):
        raise ValueError("input must be a JSON array")
    return value


def _verify_file_sha256(path: Path, expected: str) -> None:
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        raise ValueError("expected SHA-256 must be a full lowercase hex digest")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError("input digest does not match trusted identity")


def _emit(command: str, result: _CliResult) -> None:
    payload = {"command": command, **result.payload, "exit_code": int(result.exit_code)}
    typer.echo(canonical_json_bytes(payload).decode("utf-8"))
    if result.exit_code is not ReleaseExitCode.SUCCESS:
        raise typer.Exit(int(result.exit_code))


def _execute(command: str, operation: Callable[[], _CliResult]) -> None:
    try:
        result = operation()
    except DeploymentVerificationError as exc:
        result = _CliResult({"reason": str(exc), "verdict": _verdict(exc.exit_code)}, exc.exit_code)
    except DataVerificationError as exc:
        result = _CliResult(
            {"reason": str(exc), "verdict": "policy_violation"},
            ReleaseExitCode.POLICY_VIOLATION,
        )
    except (OSError, TypeError, ValueError, ValidationError):
        result = _CliResult(
            {"reason": "input validation failed", "verdict": "invalid_evidence"},
            ReleaseExitCode.INVALID_EVIDENCE,
        )
    _emit(command, result)


def _verdict(exit_code: ReleaseExitCode) -> str:
    return {
        ReleaseExitCode.SUCCESS: "pass",
        ReleaseExitCode.POLICY_VIOLATION: "policy_violation",
        ReleaseExitCode.INVALID_EVIDENCE: "invalid_evidence",
        ReleaseExitCode.INFRASTRUCTURE_FAILURE: "infrastructure_failure",
    }[exit_code]


@app.command("validate-config")
def validate_config_command(
    config: Path = typer.Option(..., "--config", help="Container release JSON configuration."),
) -> None:
    """Validate a strict per-repository container release configuration."""

    def operation() -> _CliResult:
        ReleaseConfig.model_validate(_object(config))
        return _CliResult({"config": str(config), "verdict": "pass"}, ReleaseExitCode.SUCCESS)

    _execute("validate-config", operation)


@app.command("validate-source")
def validate_source_command(
    event_name: str = typer.Option(..., "--event-name"),
    event_ref: str = typer.Option(..., "--event-ref"),
    event_sha: str = typer.Option(..., "--event-sha"),
    changelog: Path = typer.Option(..., "--changelog"),
) -> None:
    """Validate the exact protected stable-tag source identity."""

    def operation() -> _CliResult:
        if changelog.is_symlink() or not changelog.is_file():
            raise ValueError("changelog is not a regular file")
        source = validate_source_release(
            event_name=event_name,
            event_ref=event_ref,
            event_sha=event_sha,
            changelog_text=changelog.read_text(encoding="utf-8"),
        )
        return _CliResult({"source": asdict(source), "verdict": "pass"}, ReleaseExitCode.SUCCESS)

    _execute("validate-source", operation)


@app.command("validate-data-manifest")
def validate_data_manifest_command(
    manifest: Path = typer.Option(..., "--manifest", help="Immutable data release manifest."),
    manifest_sha256: str = typer.Option(..., "--manifest-sha256"),
    public: bool = typer.Option(True, "--public/--private"),
) -> None:
    """Validate strict data evidence and gate public redistribution."""

    def operation() -> _CliResult:
        _verify_file_sha256(manifest, manifest_sha256)
        parsed = DataReleaseManifest.model_validate(_object(manifest))
        if public:
            parsed.validate_publication()
        return _CliResult({"manifest": str(manifest), "verdict": "pass"}, ReleaseExitCode.SUCCESS)

    _execute("validate-data-manifest", operation)


@app.command("materialize-data")
def materialize_data_command(
    manifest: Path = typer.Option(..., "--manifest"),
    manifest_sha256: str = typer.Option(..., "--manifest-sha256"),
    artifact: Path = typer.Option(..., "--artifact"),
    data_root: Path = typer.Option(..., "--data-root"),
    schema_version: str = typer.Option(..., "--schema-version"),
    schema_file: str = typer.Option(..., "--schema-file"),
) -> None:
    """Materialize and select one exact verified reference artifact."""

    def operation() -> _CliResult:
        _verify_file_sha256(manifest, manifest_sha256)
        parsed = DataReleaseManifest.model_validate(_object(manifest))
        if schema_version != parsed.schema_identity.actual:
            raise DataVerificationError("expected schema does not match reviewed manifest")
        selected = materialize_data(
            artifact,
            parsed.requirement(),
            data_root,
            schema_probe=lambda root: probe_schema_file(root, schema_file),
        )
        return _CliResult({"selected": str(selected), "verdict": "pass"}, ReleaseExitCode.SUCCESS)

    _execute("materialize-data", operation)


@app.command("rollback-data")
def rollback_data_command(
    data_root: Path = typer.Option(..., "--data-root"),
    digest: str = typer.Option(..., "--digest"),
    schema_minimum: str = typer.Option(..., "--schema-minimum"),
    schema_maximum: str = typer.Option(..., "--schema-maximum"),
) -> None:
    """Atomically select a retained previous-known-good data version."""

    def operation() -> _CliResult:
        selected = rollback_data(data_root, digest, schema_minimum, schema_maximum)
        return _CliResult({"selected": str(selected), "verdict": "pass"}, ReleaseExitCode.SUCCESS)

    _execute("rollback-data", operation)


def _compose_policy(config: Path | None) -> tuple[ComposePolicy | None, str | None]:
    """Build the effective Compose policy, including any declared sidecar roles."""
    if config is None:
        return None, None
    parsed = ReleaseConfig.model_validate(_object(config))
    policy = ComposePolicy(
        approved_networks=frozenset(parsed.service.networks),
        internal_networks=frozenset(parsed.service.internal_networks),
        auxiliary_services=tuple(
            AuxiliaryServiceRule(
                name=auxiliary.name,
                role=auxiliary.role,
                egress=auxiliary.egress,
                writable_targets=frozenset(auxiliary.writable_targets),
                read_only_targets=frozenset(auxiliary.read_only_targets),
                healthcheck_test=auxiliary.healthcheck_test,
                user=auxiliary.user,
            )
            for auxiliary in parsed.service.auxiliary
        ),
    )
    return policy, parsed.service.name


@app.command("validate-compose")
def validate_compose_command(
    rendered: Path = typer.Option(..., "--rendered", help="Rendered Compose JSON."),
    service: str = typer.Option(..., "--service", help="Application service name."),
    config: Path | None = typer.Option(
        None, "--config", help="Container release JSON declaring auxiliary service roles."
    ),
) -> None:
    """Validate the effective production Compose configuration."""

    def operation() -> _CliResult:
        policy, declared = _compose_policy(config)
        if declared is not None and declared != service:
            raise ValueError("requested service does not match the declared application service")
        violations = validate_compose(_object(rendered), service, policy)
        code = ReleaseExitCode.POLICY_VIOLATION if violations else ReleaseExitCode.SUCCESS
        return _CliResult({"verdict": _verdict(code), "violations": list(violations)}, code)

    _execute("validate-compose", operation)


@app.command("render-smoke-override")
def render_smoke_override_command(
    config: Path = typer.Option(..., "--config", help="Container release JSON configuration."),
    image: str = typer.Option(..., "--image", help="Exact local image built once in this run."),
    host_port: int = typer.Option(..., "--host-port", help="Loopback port for the smoke probe."),
    out: Path = typer.Option(..., "--out", help="Compose override to write."),
    env_file: Path | None = typer.Option(
        None, "--env-file", help="KEY=VALUE output of docker/ci-prepare-smoke.sh."
    ),
    servers: Path | None = typer.Option(None, "--servers", help="Router backend registry."),
) -> None:
    """Render the no-build smoke stack for the declared smoke profile."""

    def operation() -> _CliResult:
        parsed = ReleaseConfig.model_validate(_object(config))
        environment: dict[str, str] = {}
        if env_file is not None:
            if env_file.is_symlink() or not env_file.is_file():
                raise ValueError("smoke environment is not a regular file")
            environment = parse_smoke_env(env_file.read_text(encoding="utf-8"))
        url_env_keys: tuple[str, ...] = ()
        if servers is not None and servers.is_file() and not servers.is_symlink():
            url_env_keys = tuple(
                sorted(set(re.findall(r"url_env:\s*([A-Z0-9_]+)", servers.read_text("utf-8"))))
            )
        override = render_smoke_override(
            parsed,
            image=image,
            host_port=host_port,
            environment=environment,
            url_env_keys=url_env_keys,
        )
        out.write_text(override, encoding="utf-8")
        return _CliResult(
            {
                "override": str(out),
                "profile": parsed.smoke.profile,
                "services": [parsed.service.name, *(a.name for a in parsed.service.auxiliary)],
                "verdict": "pass",
            },
            ReleaseExitCode.SUCCESS,
        )

    _execute("render-smoke-override", operation)


@app.command("inspect-oci")
def inspect_oci_command(
    layout: Path = typer.Option(..., "--layout", help="OCI image-layout directory."),
    allowlist: list[str] | None = typer.Option(None, "--allowlist"),
) -> None:
    """Inspect every OCI layer and the image configuration."""

    def operation() -> _CliResult:
        report = inspect_oci_layout(layout, allowlist=tuple(allowlist or ()))
        violated = bool(report.denied_paths or report.denied_config)
        code = ReleaseExitCode.POLICY_VIOLATION if violated else ReleaseExitCode.SUCCESS
        return _CliResult({"report": report.to_dict(), "verdict": _verdict(code)}, code)

    _execute("inspect-oci", operation)


@app.command("evaluate-trivy")
def evaluate_trivy_command(
    report: Path = typer.Option(..., "--report", help="Trivy JSON evidence envelope."),
    scanner_exit: int = typer.Option(..., "--scanner-exit"),
    out: Path = typer.Option(..., "--out", help="Output verdict JSON."),
) -> None:
    """Separate Trivy operational state from vulnerability policy."""

    def operation() -> _CliResult:
        if report.is_symlink() or not report.is_file():
            raise ValueError("Trivy report is not a regular file")
        evaluation = evaluate_trivy(report.read_bytes(), scanner_exit)
        payload = evaluation.to_dict()
        write_json_atomic(out, {"command": "evaluate-trivy", **payload})
        return _CliResult(
            {key: value for key, value in payload.items() if key != "exit_code"},
            evaluation.exit_code,
        )

    _execute("evaluate-trivy", operation)


@app.command("capture-definitions")
def capture_definitions_command(
    tools: list[Path] = typer.Option(..., "--tools", help="Repeat for each tool-list JSON."),
    context: list[Path] = typer.Option(
        ..., "--context", help="Repeat in the same order as --tools."
    ),
    contract: Literal["data-independent", "data-bound"] = typer.Option(..., "--contract"),
    out_definitions: Path = typer.Option(..., "--out-definitions"),
    out_context: Path = typer.Option(..., "--out-context"),
    data_release_tag: str | None = typer.Option(None, "--data-release-tag"),
    data_digest: str | None = typer.Option(None, "--data-digest"),
    observed_identity: Path | None = typer.Option(None, "--observed-identity"),
) -> None:
    """Canonicalize MCP tools and prove their declared definition contract."""

    def operation() -> _CliResult:
        if len(tools) != len(context):
            raise ValueError("tools and context counts differ")
        if observed_identity is not None and (
            data_release_tag is not None or data_digest is not None
        ):
            raise ValueError("observed and legacy data identity inputs are mutually exclusive")
        observed_value: dict[str, Any] | None = None
        adoption: DataIdentityAdoption | None = None
        if observed_identity is not None:
            observed_value = _object(observed_identity)
            adoption = "runtime-v1"
        elif data_release_tag is not None or data_digest is not None:
            adoption = "unadopted"
        captures = tuple(
            capture_definitions(
                _array(tool_path),
                context=_object(context_path),
                observed_identity=observed_value,
                data_release_tag=data_release_tag,
                data_digest=data_digest,
                adoption=adoption,
            )
            for tool_path, context_path in zip(tools, context, strict=True)
        )
        evidence = verify_definition_contract(
            contract,
            captures,
            observed_identity=observed_value,
            data_release_tag=data_release_tag,
            data_digest=data_digest,
            adoption=adoption,
        )
        write_json_atomic(out_definitions, evidence.definitions_document)
        write_json_atomic(out_context, evidence.context_document)
        return _CliResult(
            {
                "capture_context_sha256": evidence.capture_context_sha256,
                "definition_contract": evidence.definition_contract,
                "definitions_sha256": evidence.definitions_sha256,
                "verdict": "pass",
            },
            ReleaseExitCode.SUCCESS,
        )

    _execute("capture-definitions", operation)


@app.command("verify-runtime-data-identity")
def verify_runtime_data_identity_command(
    config: Path = typer.Option(..., "--config", help="Container release JSON configuration."),
    health: Path = typer.Option(..., "--health", help="Readiness JSON captured from the image."),
    out: Path = typer.Option(..., "--out", help="Canonical observed identity JSON."),
) -> None:
    """Verify an adopted data-bound image's independently observed identity."""

    def operation() -> _CliResult:
        parsed = ReleaseConfig.model_validate(_object(config))
        adoption = parsed.data_identity_contract
        if parsed.definitions.contract != "data-bound" or adoption is None:
            raise ValueError("runtime identity verification requires a data-bound release")
        observed = verify_readiness_data_identity(
            _object(health),
            release_tag=getattr(parsed.data, "release_tag", None),
            digest=getattr(parsed.data, "digest", None),
            adoption=adoption,
        )
        if observed is None:
            raise ValueError("unadopted releases do not provide observed runtime identity")
        write_json_atomic(out, observed)
        return _CliResult(
            {"observed_identity": str(out), "verdict": "pass"}, ReleaseExitCode.SUCCESS
        )

    _execute("verify-runtime-data-identity", operation)


@app.command("assemble-manifest")
def assemble_manifest_command(
    identity: Path = typer.Option(..., "--identity"),
    definitions: Path = typer.Option(..., "--definitions"),
    context: Path = typer.Option(..., "--context"),
    scanner: Path = typer.Option(..., "--scanner"),
    data: Path = typer.Option(..., "--data"),
    asset_dir: Path = typer.Option(..., "--asset-dir"),
    out: Path = typer.Option(..., "--out"),
) -> None:
    """Assemble the immutable application release evidence manifest."""

    def operation() -> _CliResult:
        identity_value = _object(identity)
        scanner_value = _object(scanner)
        application_identity = ApplicationIdentity(**identity_value)
        scanner_identity = ScannerIdentity(**scanner_value)
        definition_evidence = load_definition_evidence(_object(definitions), _object(context))
        assets = tuple(ReleaseAsset(name=name, path=asset_dir / name) for name in STANDARD_ASSETS)
        manifest = assemble_application_release_manifest(
            identity=application_identity,
            definitions=definition_evidence,
            scanner=scanner_identity,
            data_requirements=_object(data),
            assets=assets,
        )
        write_json_atomic(out, manifest.model_dump(mode="json"))
        return _CliResult(
            {
                "image_digest": manifest.image.digest,
                "manifest": str(out),
                "verdict": "pass",
            },
            ReleaseExitCode.SUCCESS,
        )

    _execute("assemble-manifest", operation)


@app.command("verify-deployment")
def verify_deployment_command(
    manifest: Path = typer.Option(..., "--manifest"),
    image_manifest: Path | None = typer.Option(None, "--image-manifest"),
    bundle: Path | None = typer.Option(None, "--bundle"),
    trusted_root: Path | None = typer.Option(None, "--trusted-root"),
) -> None:
    """Verify online provenance or a complete saved offline evidence set."""

    def operation() -> _CliResult:
        reviewed = ApplicationReleaseManifest.model_validate(_object(manifest))
        verification = verify_deployment(
            reviewed,
            image_manifest=image_manifest,
            bundle=bundle,
            trusted_root=trusted_root,
        )
        payload = verification.to_dict()
        return _CliResult(
            {key: value for key, value in payload.items() if key != "exit_code"},
            verification.exit_code,
        )

    _execute("verify-deployment", operation)


__all__ = ["app"]
