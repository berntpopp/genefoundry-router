"""Static security contract for protected-tag container publication."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
REUSABLE = ROOT / ".github/workflows/_container-release.yml"
CALLER = ROOT / ".github/workflows/container-release.yml"
TRIVY_CACHE_DIR = "${{ github.workspace }}/.cache/trivy"

ACTION_PINS = {
    "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "astral-sh/setup-uv": "fac544c07dec837d0ccb6301d7b5580bf5edae39",
    "docker/setup-buildx-action": "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
    "docker/build-push-action": "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "aquasecurity/trivy-action": "a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8",
    "anchore/sbom-action": "e22c389904149dbc22b58101806040fa8d37a610",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "docker/login-action": "af1e73f918a031802d376d3c8bbc3fe56130a9b0",
    "actions/attest-build-provenance": "43d14bc2b83dec42d39ecae14e916627a18bb661",
    "actions/attest-sbom": "51e74621a501c89df81fc1391c5a8f4cfc9fab2f",
}

READ_JOBS = {"prepare", "build-gate", "capture", "assemble-evidence"}
PRIVILEGED_JOBS = {"publish-attest", "finalize"}


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _on(document: dict[str, Any]) -> dict[str, Any]:
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return steps


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _all_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in _steps(job)]


def _step_index(job: dict[str, Any], fragment: str) -> int:
    return next(
        index
        for index, step in enumerate(_steps(job))
        if fragment in str(step.get("name", "")) + str(step.get("uses", ""))
    )


def test_caller_accepts_tag_push_only_with_release_permission_ceiling() -> None:
    workflow = _load(CALLER)
    trigger = _on(workflow)
    assert set(trigger) == {"push"}
    assert trigger["push"] == {"tags": ["v*.*.*"]}
    assert workflow["permissions"] == {
        "attestations": "write",
        "contents": "write",
        "id-token": "write",
        "packages": "write",
    }
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert "github.repository" in workflow["concurrency"]["group"]
    assert "github.ref" in workflow["concurrency"]["group"]
    assert workflow["jobs"] == {"release": {"uses": "./.github/workflows/_container-release.yml"}}


def test_reusable_has_six_jobs_with_job_scoped_least_privilege() -> None:
    workflow = _load(REUSABLE)
    assert _on(workflow) == {"workflow_call": {}}
    assert workflow["permissions"] == {}
    assert set(workflow["jobs"]) == READ_JOBS | PRIVILEGED_JOBS
    expected = {
        "prepare": {"contents": "read", "packages": "read"},
        "build-gate": {"contents": "read"},
        "capture": {"contents": "read", "packages": "read"},
        "assemble-evidence": {"contents": "read"},
        "publish-attest": {
            "attestations": "write",
            "contents": "read",
            "id-token": "write",
            "packages": "write",
        },
        "finalize": {"contents": "write", "packages": "write"},
    }
    for name, permissions in expected.items():
        assert workflow["jobs"][name]["permissions"] == permissions
    for name in PRIVILEGED_JOBS:
        assert workflow["jobs"][name]["environment"] == "release"


def test_every_action_is_full_sha_pinned_and_required_pins_are_exact() -> None:
    workflow = _load(REUSABLE)
    seen: dict[str, set[str]] = {}
    for step in _all_steps(workflow):
        uses = step.get("uses")
        if not uses:
            continue
        action, separator, revision = str(uses).partition("@")
        assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), uses
        seen.setdefault(action, set()).add(revision)
    for action, revision in ACTION_PINS.items():
        assert seen[action] == {revision}


def test_runtime_revalidates_exact_stable_tag_and_called_workflow_identity() -> None:
    workflow = _load(REUSABLE)
    prepare = workflow["jobs"]["prepare"]
    steps = _steps(prepare)
    identity = next(step for step in steps if step.get("id") == "workflow-identity")
    assert steps.index(identity) < min(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert identity["env"] == {"JOB_CONTEXT": "${{ toJSON(job) }}"}
    text = _run_text(prepare)
    assert 'jq -er ".workflow_repository"' in text
    assert 'jq -er ".workflow_ref"' in text
    assert 'jq -er ".workflow_sha"' in text
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in text
    assert 'GITHUB_EVENT_NAME" = "push' in text
    assert "refs/tags/" in text
    assert "validate-source" in text
    assert "_container-release.yml@" in text
    assert "^[0-9a-f]{40}$" in text


def test_privileged_jobs_never_checkout_or_execute_leaf_code_or_containers() -> None:
    workflow = _load(REUSABLE)
    forbidden = ("scripts/", "docker compose", "docker run", "docker build", "uv run")
    for name in PRIVILEGED_JOBS:
        job = workflow["jobs"][name]
        assert not any(
            str(step.get("uses", "")).startswith("actions/checkout@") for step in _steps(job)
        )
        text = _run_text(job).lower()
        assert all(token not in text for token in forbidden)


def test_prepare_covers_new_recovery_collision_and_completed_states() -> None:
    prepare = _load(REUSABLE)["jobs"]["prepare"]
    assert "build_date" in prepare["outputs"]
    step_names = {str(step.get("name", "")) for step in _steps(prepare)}
    assert "Enforce fleet release controls before publication" in step_names
    text = _run_text(prepare)
    for token in (
        "build_required=true",
        "build_required=false",
        'git show -s --format=%cI "$source_sha"',
        "require_compliant_controls",
        "ci/container-controls.json",
        "source alias collision",
        "completed_release=true",
        "version alias collision",
        "org.opencontainers.image.revision",
        "gh release verify",
        "missing attestation",
    ):
        assert token in text


def test_build_gate_builds_only_when_absent_and_never_uses_release_cache() -> None:
    workflow = _load(REUSABLE)
    job = workflow["jobs"]["build-gate"]
    builds = [
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(builds) == 1
    build = builds[0]
    assert "build_required == 'true'" in build["if"]
    inputs = build["with"]
    assert inputs["platforms"] == "linux/amd64"
    assert inputs["push"] is False
    assert inputs["provenance"] is False
    assert inputs["sbom"] is False
    assert inputs["build-args"].rstrip("\n") == "\n".join(
        [
            "APP_VERSION=${{ needs.prepare.outputs.version }}",
            "VCS_REF=${{ needs.prepare.outputs.source_sha }}",
            "BUILD_DATE=${{ needs.prepare.outputs.build_date }}",
        ]
    )
    assert str(inputs["outputs"]).startswith("type=oci,")
    assert not any(key.startswith("cache-") for key in inputs)
    text = _run_text(job)
    trivy = next(
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
    )
    assert trivy["with"]["cache-dir"] == TRIVY_CACHE_DIR
    evaluate_trivy = next(
        step for step in _steps(job) if step.get("name") == "Evaluate versioned Trivy policy"
    )
    assert evaluate_trivy["env"]["TRIVY_CACHE_DIR"] == TRIVY_CACHE_DIR
    assert "build_required == 'false'" in str(job)
    assert "--to-oci-layout" in text
    assert "inspect-oci" in text
    assert "allowlist_args+=(--allowlist" in text
    assert "--image-allowlist" not in text
    assert "evaluate-trivy" in text
    assert "trivy version --format json" in text
    assert "trivy-native.json" in text
    assert "{schema_version: 1, scan: $scan[0], version: $version[0]}" in text
    assert "sha256sum" in text
    legacy_release_path = "/".join(("", "tmp", "release-build"))
    assert legacy_release_path not in str(job)
    assert "OCI_ARCHIVE=$RUNNER_TEMP/release-build/image.oci.tar" in text
    assert "OCI_LAYOUT=$RUNNER_TEMP/release-build/oci-layout" in text
    assert "steps.evidence-paths.outputs.archive" in str(inputs["outputs"])


def test_pinned_gh_binary_is_checked_for_required_release_and_attestation_commands() -> None:
    workflow = _load(REUSABLE)
    text = "\n".join(
        _run_text(workflow["jobs"][name]) for name in ("prepare", "publish-attest", "finalize")
    )
    for token in (
        "release verify --help",
        "release verify-asset --help",
        "attestation verify --help",
        "attestation download --help",
        "attestation trusted-root --help",
    ):
        assert text.count(token) == 3


def test_publish_verifies_artifact_before_registry_login_or_write() -> None:
    job = _load(REUSABLE)["jobs"]["publish-attest"]
    assert _step_index(job, "Verify immutable OCI evidence") < _step_index(job, "Log in to GHCR")
    assert _step_index(job, "Log in to GHCR") < _step_index(job, "Publish source-SHA alias")
    text = _run_text(job)
    assert "sha256sum -c" in text
    assert "oras cp --from-oci-layout" in text
    assert "source alias digest mismatch" in text
    assert "published_digest=" in text


def test_source_alias_precedes_provenance_and_spdx_attestations() -> None:
    job = _load(REUSABLE)["jobs"]["publish-attest"]
    push = _step_index(job, "Publish source-SHA alias")
    provenance = _step_index(job, "Attest build provenance")
    sbom = _step_index(job, "Attest SPDX SBOM")
    assert push < provenance < sbom
    steps = _steps(job)
    provenance_step = steps[provenance]
    sbom_step = steps[sbom]
    assert provenance_step["with"]["subject-digest"].startswith("sha256:")
    assert provenance_step["with"]["push-to-registry"] is True
    assert sbom_step["with"]["sbom-path"].endswith("sbom.spdx.json")
    assert sbom_step["with"]["push-to-registry"] is True
    text = _run_text(job)
    assert "--predicate-type https://slsa.dev/provenance/v1" in text
    assert "--predicate-type https://spdx.dev/Document/v2.3" in text
    assert "attestation download" in text
    assert "attestation trusted-root" in text
    assert "attestation-bundle.json" in text
    assert "trusted-root.json" in text
    assert "application/vnd.dev.sigstore.trustedroot" not in text


def test_capture_uses_published_digest_and_assemble_is_read_only() -> None:
    workflow = _load(REUSABLE)
    capture = _run_text(workflow["jobs"]["capture"])
    assert "needs.publish-attest.outputs.published_digest" in str(workflow["jobs"]["capture"])
    assert "docker pull" in capture and "@${PUBLISHED_DIGEST}" in capture
    assert 'method":"tools/list"' in capture
    assert "mcp-tools-a.json" in capture
    assert "mcp-tools-b.json" in capture
    assert "jq -er '.result.tools | type == \"array\" and length > 0'" in capture
    assert "printf '[]'" not in capture
    assert "capture-definitions" in capture
    assemble = _run_text(workflow["jobs"]["assemble-evidence"])
    assert "assemble-manifest" in assemble
    assert "sha256sum" in assemble


def test_finalize_handles_draft_recovery_then_aliases_identical_manifest() -> None:
    job = _load(REUSABLE)["jobs"]["finalize"]
    text = _run_text(job)
    for token in (
        "matching draft assets",
        "mismatched draft assets",
        "gh release create",
        "--draft",
        "gh release edit",
        "--draft=false",
        "release-assets",
        "gh release verify",
        "gh release verify-asset",
        "oras cp",
        "version alias digest mismatch",
        "missing version alias",
    ):
        assert token in text
    assert 'cp "$manifest" "$expected"/' in text
    assert 'diff -qr "$expected" "$RUNNER_TEMP/draft"' in text
    assert '"$expected"/*' in text
    assert '"$assets" "$RUNNER_TEMP/draft"' not in text
    assert text.index("gh release edit") < text.index("oras cp")
    all_text = REUSABLE.read_text(encoding="utf-8")
    assert "--clobber" not in all_text
    assert "imagetools create" not in all_text
    assert "docker manifest" not in all_text
    assert "docker save" not in all_text
    assert "docker load" not in all_text
