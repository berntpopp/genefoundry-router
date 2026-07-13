"""End-to-end contract for the router reference application image."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import tomllib
import urllib.request
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml

from genefoundry_router.release.content import inspect_oci_layout
from genefoundry_router.release.deploy import verify_deployment
from genefoundry_router.release.models import ApplicationReleaseManifest
from genefoundry_router.release.source import CommandResult

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "Dockerfile"
DOCKER = Path(shutil.which("docker") or "/usr/bin/docker")
IMAGE = "genefoundry-router:task11-integration"
REVISION = "1" * 40
CREATED = "2026-07-13T00:00:00Z"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
REQUIRED_LABELS = {
    "org.opencontainers.image.title": "GeneFoundry Router",
    "org.opencontainers.image.description": (
        "FastMCP gateway for the GeneFoundry research MCP fleet"
    ),
    "org.opencontainers.image.source": "https://github.com/berntpopp/genefoundry-router",
    "org.opencontainers.image.url": "https://github.com/berntpopp/genefoundry-router",
    "org.opencontainers.image.documentation": (
        "https://github.com/berntpopp/genefoundry-router#container-release-and-deployment"
    ),
    "org.opencontainers.image.version": VERSION,
    "org.opencontainers.image.revision": REVISION,
    "org.opencontainers.image.created": CREATED,
    "org.opencontainers.image.licenses": "MIT",
    "org.opencontainers.image.vendor": "GeneFoundry",
    "org.genefoundry.research-use-only": "true",
    "org.genefoundry.data-policy": "code-only",
}
PACKAGED_CODE_ASSETS = tuple(
    f"build/.venv/lib/python3.14/site-packages/genefoundry_router/data/{name}"
    for name in (
        "__init__.py",
        "application-release-manifest.schema.json",
        "container-release.schema.json",
        "fleet-baseline.json",
        "image-content-policy-v1.json",
    )
)
TMPFS = "/tmp:rw,noexec,nosuid,size=64m,mode=1777"  # noqa: S108 - container mount


def _run(
    args: Sequence[str], *, timeout: int = 120, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        list(args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _require_docker() -> None:
    if not DOCKER.is_file():
        pytest.skip("Docker is required for the router image integration contract")
    try:
        _run((str(DOCKER), "info", "--format", "{{.ServerVersion}}"), timeout=15)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("a reachable Docker daemon is required")


@pytest.fixture(scope="module")
def built_image(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    """Build one image once, exporting both OCI evidence and a daemon-loadable archive."""
    _require_docker()
    output = tmp_path_factory.mktemp("router-image")
    oci_archive = output / "image.oci.tar"
    docker_archive = output / "image.docker.tar"
    _run(
        (
            str(DOCKER),
            "buildx",
            "build",
            "--file",
            "docker/Dockerfile",
            "--target",
            "production",
            "--platform",
            "linux/amd64",
            "--build-arg",
            f"APP_VERSION={VERSION}",
            "--build-arg",
            f"VCS_REF={REVISION}",
            "--build-arg",
            f"BUILD_DATE={CREATED}",
            "--tag",
            IMAGE,
            "--output",
            f"type=oci,dest={oci_archive}",
            "--output",
            f"type=docker,dest={docker_archive}",
            ".",
        ),
        timeout=900,
    )
    _run((str(DOCKER), "load", "--input", str(docker_archive)), timeout=180)
    layout = output / "oci"
    layout.mkdir()
    with tarfile.open(oci_archive, "r") as archive:
        archive.extractall(layout, filter="data")
    image_config = json.loads(_run((str(DOCKER), "image", "inspect", IMAGE)).stdout)[0]
    yield layout, image_config
    subprocess.run(  # noqa: S603
        [str(DOCKER), "image", "rm", "--force", IMAGE],
        cwd=ROOT,
        check=False,
        capture_output=True,
        timeout=60,
    )


def test_dockerfile_declares_complete_code_only_oci_identity() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    for key in REQUIRED_LABELS:
        assert f"{key}=" in text
    assert "ARG APP_VERSION" in text
    assert "ARG VCS_REF" in text
    assert "ARG BUILD_DATE" in text
    assert "COPY . ." not in text


def test_reference_runbook_documents_release_and_operational_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    standard = (ROOT / "docs" / "CONTAINER-HARDENING-STANDARD-v1.md").read_text(encoding="utf-8")
    required = (
        "bootstrap",
        "public package",
        "anonymous pull",
        "protected tag",
        "digest",
        "rollback tuple",
        "incident",
        "Research use only",
        "AMD64",
        "ARM64",
    )
    combined = f"{readme}\n{standard}".lower()
    for phrase in required:
        assert phrase.lower() in combined


def test_built_image_has_exact_required_labels(
    built_image: tuple[Path, dict[str, object]],
) -> None:
    _, config = built_image
    labels = config["Config"]["Labels"]
    assert labels == REQUIRED_LABELS


def test_built_image_is_code_only_in_every_oci_layer(
    built_image: tuple[Path, dict[str, object]],
) -> None:
    layout, _ = built_image
    report = inspect_oci_layout(layout, allowlist=PACKAGED_CODE_ASSETS)
    assert report.denied_paths == ()
    assert report.denied_config == ()
    assert report.allowlisted_paths == PACKAGED_CODE_ASSETS


def _backend_environment() -> list[str]:
    document = yaml.safe_load((ROOT / "servers.yaml").read_text(encoding="utf-8"))
    servers = document["servers"]
    keys = sorted({server["url_env"] for server in servers if "url_env" in server})
    return [item for key in keys for item in ("--env", f"{key}=http://127.0.0.1:9/mcp")]


def _json_request(
    url: str, payload: dict[str, object] | None = None, session: str | None = None
) -> tuple[dict[str, object], str | None]:
    headers = {"Host": "localhost", "Accept": "application/json, text/event-stream"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    if session is not None:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(  # noqa: S310 - URL is a loopback test fixture
        url, data=data, headers=headers, method="POST" if data else "GET"
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        body = response.read().decode()
        session_id = response.headers.get("Mcp-Session-Id")
    if body.startswith("event:"):
        body = next(line[6:] for line in body.splitlines() if line.startswith("data: "))
    return json.loads(body), session_id


def test_live_image_is_hardened_and_serves_health_and_mcp(
    built_image: tuple[Path, dict[str, object]],
) -> None:
    _, config = built_image
    name = f"genefoundry-router-test-{os.getpid()}"
    args = [
        str(DOCKER),
        "run",
        "--detach",
        "--name",
        name,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        TMPFS,
        "--tmpfs",
        "/data:rw,noexec,nosuid,size=16m",
        "--publish",
        "127.0.0.1::8000",
        "--env",
        "GF_AUTH_MODE=none",
        "--env",
        "GF_ALLOW_INSECURE=true",
        "--env",
        "GF_ALLOWED_HOSTS=localhost,127.0.0.1",
        "--env",
        "GF_HEALTHCHECK_HOST=localhost",
        *_backend_environment(),
        IMAGE,
    ]
    _run(args)
    try:
        inspect = json.loads(_run((str(DOCKER), "inspect", name)).stdout)[0]
        host_port = inspect["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        url = f"http://127.0.0.1:{host_port}"
        deadline = time.monotonic() + 90
        while True:
            try:
                health, _ = _json_request(f"{url}/health")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        initialize, session = _json_request(
            f"{url}/mcp",
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "router-image-test", "version": "1"},
                },
            },
        )
        tools, _ = _json_request(
            f"{url}/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            session,
        )
        # Unreachable deterministic backend URLs intentionally make aggregate health
        # degraded; the router endpoint itself must still be live and well-formed.
        assert health["status"] in {"healthy", "ok", "degraded"}
        assert initialize["result"]["serverInfo"]["name"] == "genefoundry"
        assert isinstance(tools["result"]["tools"], list)
        assert config["Config"]["User"] == "10001:10001"
        assert inspect["HostConfig"]["ReadonlyRootfs"] is True
        assert "ALL" in inspect["HostConfig"]["CapDrop"]
        assert "no-new-privileges" in inspect["HostConfig"]["SecurityOpt"]
    finally:
        subprocess.run(  # noqa: S603
            [str(DOCKER), "rm", "--force", name],
            cwd=ROOT,
            check=False,
            capture_output=True,
            timeout=60,
        )


def test_effective_production_and_proxy_compose_are_digest_only() -> None:
    _require_docker()
    image = f"ghcr.io/berntpopp/genefoundry-router@sha256:{'a' * 64}"
    environment = {
        **os.environ,
        "GENEFOUNDRY_IMAGE": image,
        "GF_ALLOWED_HOSTS": "genefoundry.example",
        "GF_HEALTHCHECK_HOST": "genefoundry.example",
        "NPM_NETWORK_NAME": "npm_default",
    }
    for files in (
        ("docker/docker-compose.yml", "docker/docker-compose.prod.yml"),
        (
            "docker/docker-compose.yml",
            "docker/docker-compose.prod.yml",
            "docker/docker-compose.npm.yml",
        ),
    ):
        arguments = [str(DOCKER), "compose"]
        for path in files:
            arguments.extend(("--file", path))
        arguments.extend(("config", "--format", "json"))
        rendered = json.loads(_run(arguments, env=environment).stdout)
        service = rendered["services"]["genefoundry-router"]
        assert "build" not in service
        assert service["image"] == image
        assert service.get("ports", []) == []


class _VerifierRunner:
    def __call__(self, args: Sequence[str]) -> CommandResult:
        if tuple(args) == ("gh", "--version"):
            return CommandResult(0, "gh version 2.93.0\n", "")
        return CommandResult(0, '{"verified":true}\n', "")


def test_router_deployment_fixture_verifies_only_a_reviewed_digest() -> None:
    digest = f"sha256:{'a' * 64}"
    checksum = "b" * 64
    manifest = ApplicationReleaseManifest.model_validate(
        {
            "schema_version": 1,
            "repository": "berntpopp/genefoundry-router",
            "version": VERSION,
            "source": {"tag": f"v{VERSION}", "revision": REVISION},
            "image": {
                "name": "ghcr.io/berntpopp/genefoundry-router",
                "digest": digest,
                "platforms": [{"platform": "linux/amd64", "digest": digest}],
            },
            "workflow": {
                "caller": ("berntpopp/genefoundry-router/.github/workflows/container-release.yml"),
                "standard": (
                    "berntpopp/genefoundry-router/.github/workflows/_container-release.yml"
                ),
                "standard_revision": REVISION,
            },
            "mcp": {
                "definitions_sha256": checksum,
                "capture_context_sha256": checksum,
                "definition_contract": "data-independent",
            },
            "security_evidence": {
                "scanner": "trivy",
                "scanner_version": "1.0.0",
                "database_updated_at": "2026-07-13T00:00:00Z",
                "sbom_sha256": checksum,
                "scanner_evidence_sha256": checksum,
                "attestation_bundle_sha256": checksum,
                "trusted_root_sha256": checksum,
                "verification_sha256": checksum,
            },
            "release_assets": {
                "image-manifest.json": digest,
                **dict.fromkeys(
                    (
                        "sbom.spdx.json",
                        "mcp-definitions.json",
                        "mcp-capture-context.json",
                        "trivy.json",
                        "attestation-bundle.json",
                        "trusted-root.json",
                        "verification.json",
                    ),
                    f"sha256:{checksum}",
                ),
            },
            "data_requirements": {"mode": "none", "schema_compatibility": []},
        }
    )
    result = verify_deployment(manifest, runner=_VerifierRunner())
    assert result.image == f"ghcr.io/berntpopp/genefoundry-router@{digest}"
    assert result.source_revision == REVISION
    assert hashlib.sha256(result.image.encode()).hexdigest()
