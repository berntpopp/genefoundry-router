"""Static contract tests for the read-only reusable container CI workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from genefoundry_router.release.models import ReleaseConfig

ROOT = Path(__file__).resolve().parents[2]
REUSABLE = ROOT / ".github/workflows/_container-ci.yml"
CALLER = ROOT / ".github/workflows/container-ci.yml"
CONFIG = ROOT / "container-release.json"
OLD_WORKFLOW = ROOT / ".github/workflows/container-security.yml"

ACTION_PINS = {
    "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "docker/setup-buildx-action": "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
    "docker/build-push-action": "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "aquasecurity/trivy-action": "a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8",
    "anchore/sbom-action": "e22c389904149dbc22b58101806040fa8d37a610",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}

ROUTER_IMAGE_ALLOWLIST = {
    "build/.venv/lib/python3.14/site-packages/genefoundry_router/data/__init__.py",
    "build/.venv/lib/python3.14/site-packages/genefoundry_router/data/application-release-manifest.schema.json",
    "build/.venv/lib/python3.14/site-packages/genefoundry_router/data/container-release.schema.json",
    "build/.venv/lib/python3.14/site-packages/genefoundry_router/data/fleet-baseline.json",
    "build/.venv/lib/python3.14/site-packages/genefoundry_router/data/image-content-policy-v1.json",
}


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _on(document: dict[str, Any]) -> dict[str, Any]:
    # PyYAML 1.1 treats the unquoted key ``on`` as boolean true.
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _steps(document: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in document["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def _run_text(document: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(document))


def test_reusable_is_workflow_call_only_and_permissions_are_job_scoped() -> None:
    workflow = _load(REUSABLE)
    assert _on(workflow) == {"workflow_call": {}}
    assert workflow["permissions"] == {}
    assert set(workflow["jobs"]) == {"container-ci"}
    assert workflow["jobs"]["container-ci"]["permissions"] == {"contents": "read"}


def test_caller_filters_application_container_lock_and_workflow_paths() -> None:
    workflow = _load(CALLER)
    trigger = _on(workflow)
    expected_paths = {
        "genefoundry_router/**",
        "scripts/container_release.py",
        "docker/**",
        "pyproject.toml",
        "uv.lock",
        "container-release.json",
        ".github/workflows/_container-ci.yml",
        ".github/workflows/container-ci.yml",
    }
    assert set(trigger) == {"pull_request", "push"}
    assert set(trigger["pull_request"]["paths"]) == expected_paths
    assert trigger["push"]["branches"] == ["main"]
    assert set(trigger["push"]["paths"]) == expected_paths
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"] == {
        "container-ci": {
            "uses": "./.github/workflows/_container-ci.yml",
        }
    }


def test_every_action_is_full_sha_pinned_and_required_pins_are_exact() -> None:
    workflow = _load(REUSABLE)
    seen: dict[str, list[str]] = {}
    for step in _steps(workflow):
        uses = step.get("uses")
        if not uses:
            continue
        action, separator, revision = str(uses).partition("@")
        assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), uses
        seen.setdefault(action, []).append(revision)
    for action, revision in ACTION_PINS.items():
        assert seen[action] and set(seen[action]) == {revision}


def test_called_workflow_identity_is_checked_before_any_checkout() -> None:
    steps = _steps(_load(REUSABLE))
    identity_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "workflow-identity"
    )
    checkout_indexes = [
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkout_indexes and identity_index < min(checkout_indexes)
    step = steps[identity_index]
    env = step["env"]
    assert env == {"JOB_CONTEXT": "${{ toJSON(job) }}"}
    command = step["run"]
    assert ".workflow_repository" in command
    assert ".workflow_ref" in command
    assert ".workflow_sha" in command
    assert "berntpopp/genefoundry-router" in command
    assert ".github/workflows/_container-ci.yml@" in command
    assert "^[0-9a-f]{40}$" in command
    central_checkout = next(
        item for item in steps if item.get("with", {}).get("path") == ".container-release-tools"
    )
    assert central_checkout["with"]["repository"] == (
        "${{ steps.workflow-identity.outputs.repository }}"
    )
    assert central_checkout["with"]["ref"] == "${{ steps.workflow-identity.outputs.sha }}"


def test_exactly_one_build_exports_one_non_published_amd64_oci_layout() -> None:
    steps = _steps(_load(REUSABLE))
    builds = [
        step for step in steps if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(builds) == 1
    inputs = builds[0]["with"]
    assert inputs["platforms"] == "linux/amd64"
    assert inputs["push"] is False
    assert inputs["provenance"] is False
    assert inputs["sbom"] is False
    assert str(inputs["outputs"]).startswith("type=oci,")
    assert "${{ env.OCI_ARCHIVE }}" in inputs["outputs"]
    workflow_env = _load(REUSABLE)["jobs"]["container-ci"]["env"]
    assert workflow_env["OCI_ARCHIVE"] == "image.oci.tar"


def test_ci_cache_scope_binds_all_build_inputs_and_is_bounded() -> None:
    workflow = _load(REUSABLE)
    text = REUSABLE.read_text(encoding="utf-8")
    run_text = _run_text(workflow)
    assert "github.repository" in text
    assert "docker/Dockerfile" in run_text
    assert "linux/amd64" in run_text
    assert "docker buildx version" in run_text
    assert "sha256sum uv.lock" in run_text
    build = next(
        step
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    )["with"]
    assert "type=gha,scope=${{ steps.cache-scope.outputs.scope }}" in build["cache-from"]
    assert "mode=min" in build["cache-to"]
    assert "ignore-error=true" in build["cache-to"]


def test_gate_covers_layout_runtime_scanner_sbom_and_always_tears_down() -> None:
    workflow = _load(REUSABLE)
    steps = _steps(workflow)
    run_text = _run_text(workflow)
    assert "inspect-oci" in run_text
    inspect_step = next(step for step in steps if "inspect-oci" in str(step.get("run", "")))
    inspect_command = inspect_step["run"]
    assert "--config" not in inspect_command
    assert "--out" not in inspect_command
    assert "image_allowlist" in inspect_command
    assert '"${allowlist_args[@]}"' in inspect_command
    assert "validate-compose" in run_text
    assert ".service.compose_files[]" in run_text
    assert ".service.name" in run_text
    assert "container_port" in run_text
    assert "compose-production.json" in run_text
    assert "image_template" in run_text
    assert "--no-interpolate" in run_text
    assert "config --variables" in run_text
    assert "tmpfs: !override" in run_text
    assert "cap_drop: !override" in run_text
    assert "security_opt: !override" in run_text
    assert "--no-build" in run_text
    assert ".service.health_path" in run_text
    assert ".service.mcp_path" in run_text
    assert "initialize" in run_text and "tools/list" in run_text
    assert "Mcp-Session-Id" in run_text
    assert 'if [ -n "$session" ]' in run_text
    assert 'session_args=(-H "Mcp-Session-Id: $session")' in run_text
    assert '"${session_args[@]}"' in run_text
    assert "docker inspect" in run_text
    assert "no-new-privileges" in run_text
    trivy = next(
        step for step in steps if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
    )
    assert trivy["with"]["format"] == "json"
    assert trivy["with"]["output"] == "trivy-native.json"
    assert trivy["with"]["exit-code"] == "0"
    assert "trivy version --format json" in run_text
    assert "--slurpfile scan trivy-native.json" in run_text
    assert "--slurpfile version trivy-version.json" in run_text
    assert "{schema_version: 1, scan: $scan[0], version: $version[0]}" in run_text
    assert "evaluate-trivy" in run_text
    assert '--scanner-exit "$scanner_exit"' in run_text
    sbom = next(
        step for step in steps if str(step.get("uses", "")).startswith("anchore/sbom-action@")
    )
    assert sbom["with"]["format"] == "spdx-json"
    assert sbom["with"]["output-file"] == "sbom.spdx.json"
    teardown = next(step for step in steps if step.get("id") == "teardown")
    assert teardown["if"] == "${{ always() }}"
    assert "docker compose" in teardown["run"] and " down " in teardown["run"]


def test_router_release_configuration_is_strict_and_data_independent() -> None:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    config = ReleaseConfig.model_validate(raw)
    assert config.service.name == "genefoundry-router"
    assert config.service.compose_files == (
        "docker/docker-compose.yml",
        "docker/docker-compose.prod.yml",
    )
    assert config.service.container_port == 8000
    assert config.data.mode == "none"
    assert set(config.data.image_allowlist) == ROUTER_IMAGE_ALLOWLIST
    assert config.definitions.contract == "data-independent"


def test_old_duplicate_container_security_workflow_is_removed() -> None:
    assert not OLD_WORKFLOW.exists()
