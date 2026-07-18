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
    "astral-sh/setup-uv": "11f9893b081a58869d3b5fccaea48c9e9e46f990",
    "docker/setup-buildx-action": "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
    "docker/build-push-action": "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "aquasecurity/trivy-action": "ed142fd0673e97e23eac54620cfb913e5ce36c25",
    "anchore/sbom-action": "e22c389904149dbc22b58101806040fa8d37a610",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "docker/login-action": "af1e73f918a031802d376d3c8bbc3fe56130a9b0",
    "actions/attest-build-provenance": "977bb373ede98d70efdf65b84cb5f73e068dcc2a",
    "actions/attest-sbom": "4651f806c01d8637787e274ac3bdf724ef169f34",
}

ACTION_PIN_VERSIONS = {
    "astral-sh/setup-uv": "v8.3.2",
    "aquasecurity/trivy-action": "v0.36.0",
    "actions/attest-build-provenance": "v3.0.0",
    "actions/attest-sbom": "v3.0.0",
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


def _commands(job: dict[str, Any]) -> str:
    """The executed shell of a job, with comment lines removed."""
    return "\n".join(
        line for line in _run_text(job).splitlines() if not line.lstrip().startswith("#")
    )


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


def test_required_action_version_comments_match_pinned_revisions() -> None:
    text = REUSABLE.read_text(encoding="utf-8")
    for action, version in ACTION_PIN_VERSIONS.items():
        assert f"uses: {action}@{ACTION_PINS[action]} # {version}" in text


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


def test_gate_containers_receive_the_declared_smoke_environment() -> None:
    """Both gate containers must apply the repo's declared smoke environment.

    The router refuses to bind a non-loopback address without an explicit auth and
    allowed-hosts configuration, so a gate that runs the image bare can never reach
    /health or /mcp. The environment is read from the caller's container-release.json
    rather than hardcoded, because backends declare none. Now that both gates start the
    stack through Compose, `render-smoke-override` is what carries the declared
    environment onto the application service; it must therefore be handed the config.
    """
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        render = next(
            step
            for step in _steps(workflow["jobs"][job_name])
            if "render-smoke-override" in str(step.get("run", ""))
        )
        assert "--config container-release.json" in str(render["run"]), job_name


def test_release_gates_bring_up_the_declared_sidecar_bearing_smoke_stack() -> None:
    """Both release gates must start the same Compose stack `_container-ci.yml` proves.

    A bare `docker run` starts the application alone: no PostgreSQL sidecar, no data-init
    sidecar, no populated volume. Every data-bearing backend would pass its PR checks and
    then fail the release gate, and release tags are immutable, so each failure burns a
    version. Compose starts the declared sidecars through the app's `depends_on` graph and
    honours `service_completed_successfully` / `service_healthy` before the app runs.
    """
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        job = workflow["jobs"][job_name]
        run_text = _run_text(job)
        assert ".preparation" in run_text, job_name
        assert 'test "$preparation" = "docker/ci-prepare-smoke.sh"' in run_text, job_name
        assert "render-smoke-override" in run_text, job_name
        assert "--host-port" in run_text, job_name
        assert ".service.compose_files[0]" in run_text, job_name
        assert ".service.startup_timeout_seconds" in run_text, job_name
        assert "up --detach --no-build --wait --wait-timeout" in run_text, job_name
        assert "fixture-manifest.sha256" in run_text, job_name
        # The application image is never started outside the composed stack.
        assert "docker run" not in _commands(job), job_name


def test_release_gates_smoke_the_exact_image_under_release() -> None:
    """The gated stack must run the exact image, never one rebuilt by Compose.

    `build-gate` smokes the local tag imported from the gated OCI layout; `capture` smokes
    the published digest. `--no-build` and the override's `pull_policy: never` keep Compose
    from substituting anything else.
    """
    workflow = _load(REUSABLE)
    build_gate = _run_text(workflow["jobs"]["build-gate"])
    capture = _run_text(workflow["jobs"]["capture"])

    assert '--image "$CI_IMAGE"' in build_gate
    assert '--image "$IMAGE@$PUBLISHED_DIGEST"' in capture
    assert "docker compose" in build_gate and "--no-build" in build_gate
    assert "docker compose" in capture and "--no-build" in capture


def test_build_gate_asserts_hardening_on_the_composed_application_container() -> None:
    """The hardening and MCP assertions must survive the move onto Compose.

    They are now made against the application container Compose started, resolved with
    `docker compose ps -q`, rather than against a container id returned by `docker run`.
    """
    run_text = _run_text(_load(REUSABLE)["jobs"]["build-gate"])

    assert 'ps -q "$service"' in run_text
    assert "docker inspect" in run_text
    assert '.[0].Config.User != "" and .[0].Config.User != "0" and .[0].Config.User != "root"' in (
        run_text
    )
    assert ".[0].HostConfig.ReadonlyRootfs" in run_text
    assert '.[0].HostConfig.CapDrop | index("ALL") != null' in run_text
    assert "no-new-privileges" in run_text
    assert '"method":"initialize"' in run_text
    assert "grep -Fq '\"result\"'" in run_text


def test_capture_keeps_two_isolated_contexts_on_distinct_ports_and_projects() -> None:
    """The two capture contexts prove definition stability and must not collide.

    Each context now brings up a whole Compose stack with its own named volumes and
    sidecars, so each needs its own project name as well as its own loopback port.
    """
    capture = _run_text(_load(REUSABLE)["jobs"]["capture"])

    assert "capture_tools a " in capture
    assert "capture_tools b " in capture
    assert "18000" in capture and "18001" in capture
    assert "--project-name" in capture
    assert "mcp-tools-a.json" in capture and "mcp-tools-b.json" in capture


def test_release_gates_always_tear_down_their_smoke_stacks() -> None:
    """A failed gate must not leak containers, networks, or populated volumes."""
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        teardown = next(
            step for step in _steps(workflow["jobs"][job_name]) if step.get("id") == "teardown"
        )
        assert teardown["if"] == "${{ always() }}", job_name
        assert "docker compose" in str(teardown["run"]), job_name
        assert " down " in str(teardown["run"]), job_name
        # The scanner and SBOM still need the gated image after the stack is gone.
        assert "docker image rm" not in str(teardown["run"]), job_name


def test_publish_addresses_the_oci_layout_by_digest_not_ref_name() -> None:
    """Publication must copy the exact gated digest out of the layout.

    A fresh buildx `type=oci` export normalizes a bare tag and annotates the manifest
    `org.opencontainers.image.ref.name: latest`, so addressing the layout by the source
    alias resolves only on the recovery path and fails on every real build. The digest
    is identical on both paths and is already asserted against the layout index.
    """
    publish = _run_text(_load(REUSABLE)["jobs"]["publish-attest"])

    assert 'oci-layout@$EXPECTED_DIGEST"' in publish
    assert 'oci-layout:$SOURCE_ALIAS"' not in publish


def test_attestation_verify_never_pairs_mutually_exclusive_signer_flags() -> None:
    """`gh attestation verify` rejects --signer-repo together with --signer-workflow.

    They belong to one mutually exclusive identity group. --signer-workflow is the
    stronger binding and already names the repository, so it is the one we keep; it
    must be fully qualified as [host/]<owner>/<repo>/<path>/<to>/<workflow>.
    """
    workflow = _load(REUSABLE)
    text = "\n".join(_run_text(job) for job in workflow["jobs"].values())

    assert "--signer-repo" not in text
    assert (
        "--signer-workflow berntpopp/genefoundry-router/.github/workflows/"
        "_container-release.yml" in text
    )


def test_scanner_identity_is_read_from_trivy_version_not_the_scan_report() -> None:
    """Scanner evidence must come from `trivy version`, not the scan report.

    The scan report of an OCI archive carries no ArtifactName or Metadata.DB, so reading
    them yielded null and sealed the literal string "null" as the database timestamp,
    which is not RFC3339 and failed manifest validation after the image was already
    published. `version` is the scanner's version, not the scanned artifact's name.
    """
    assemble = _run_text(_load(REUSABLE)["jobs"]["assemble-evidence"])

    assert "trivy-version.json" in assemble
    assert ".ArtifactName" not in assemble
    assert ".Metadata.DB.UpdatedAt" not in assemble


def test_finalize_names_the_repository_without_a_working_tree() -> None:
    """finalize is privileged and never checks out source, so `gh` cannot infer the repo.

    Without GH_REPO every `gh release` call fails with "not a git repository". The fix is
    to name the repository, not to hand a privileged job a working tree.
    """
    finalize = _load(REUSABLE)["jobs"]["finalize"]

    assert finalize["env"]["GH_REPO"] == "${{ github.repository }}"
    assert not any("checkout" in str(step.get("uses", "")) for step in _steps(finalize))


def test_release_verification_tolerates_asynchronous_attestation() -> None:
    """GitHub mints the immutable-release attestation asynchronously after publication.

    Verifying immediately races it and fails with "no attestations for tag", after the
    image is already published and the evidence sealed. Both the recovery probe and the
    finalize gate must retry rather than fail on the first miss.
    """
    workflow = _load(REUSABLE)

    for job_name in ("prepare", "finalize"):
        run_text = _run_text(workflow["jobs"][job_name])
        assert "release attestation not yet published; retry" in run_text, job_name


def test_every_job_that_pushes_to_ghcr_authenticates_first() -> None:
    """A job with packages: write that pushes must log in to GHCR.

    finalize held packages: write and pushed the version alias with `oras cp`, but never
    authenticated, so the final step of the final job failed with "denied" after the image,
    release, and attestations had all published. Credentials are acquired only after the
    sealed evidence is verified, which is why the login sits immediately before the push.
    """
    workflow = _load(REUSABLE)

    for name, job in workflow["jobs"].items():
        pushes = "oras cp" in _run_text(job) or "docker push" in _run_text(job)
        if not pushes:
            continue
        logs_in = any("docker/login-action" in str(step.get("uses", "")) for step in _steps(job))
        assert logs_in, f"{name} pushes to GHCR without authenticating"


def test_release_gates_probe_the_declared_paths() -> None:
    """The release gates must probe `.service.health_path` / `.mcp_path`, not fixed paths.

    _container-ci.yml already reads them, but the release gate hardcoded /health and /mcp.
    stringdb declares /api/health and only passed because its app happens to serve both; a
    backend serving only its declared path would exhaust the 90s wait loop and fail.
    """
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        run_text = _run_text(workflow["jobs"][job_name])
        assert ".service.health_path" in run_text, job_name
        assert ".service.mcp_path" in run_text, job_name
        assert "18000/health" not in run_text, job_name
        assert "${host_port}/health" not in run_text, job_name


def test_no_job_level_env_uses_the_runner_context() -> None:
    """`runner` is not a valid context in `jobs.<job_id>.env`.

    Only github, needs, strategy, matrix, vars, secrets and inputs are. Referencing
    runner.temp there is an invalid-context error that kills the workflow before any job
    starts: the run ends with zero jobs and "This run likely failed because of a workflow
    file issue". This silently disabled Container CI across the whole fleet — every
    backend's required gate reported failure while never executing a single job, and the
    release runs kept passing because the release workflow happened not to have it. Use
    the $RUNNER_TEMP shell variable, or a step-level env, instead.
    """
    for path in (REUSABLE, ROOT / ".github/workflows/_container-ci.yml"):
        workflow = _load(path)
        for name, job in workflow["jobs"].items():
            for key, value in (job.get("env") or {}).items():
                assert "runner." not in str(value), f"{path.name}:{name}.env.{key} = {value}"


def test_release_evidence_states_the_declared_data_contract() -> None:
    """Signed evidence must state the data binding the repository actually declares.

    The workflow hardcoded `--contract data-independent` and a fixed {"mode":"none"}
    data_requirements, so every data-bearing backend published a manifest claiming it binds
    to no data at all while pinned to an immutable bundle.
    """
    capture = _run_text(_load(REUSABLE)["jobs"]["capture"])

    assert "--contract data-independent" not in capture
    assert ".definitions.contract" in capture
    assert "--data-release-tag" in capture and "--data-digest" in capture


def test_capture_takes_the_context_count_its_contract_requires() -> None:
    """data-independent needs exactly two capture contexts; data-bound exactly one.

    The library enforces both counts. The workflow captured two unconditionally, so a
    data-bound release died in `capture` -- which runs AFTER publish-attest, meaning the
    image and attestation were already pushed and the immutable version tag burned.
    """
    capture = _run_text(_load(REUSABLE)["jobs"]["capture"])

    assert 'if [ "$contract" = "data-bound" ]' in capture
    assert "capture_args=(" in capture
    # The second context is captured only on the data-independent branch.
    body = capture.split('if [ "$contract" = "data-bound" ]', 1)[1]
    assert "capture_tools b" in body.split("else", 1)[1]


def test_assemble_evidence_consumes_only_sealed_artifacts() -> None:
    """assemble-evidence checks out no caller source, so it must read no caller file.

    Reading container-release.json there made `jq` exit 2 under `set -euo pipefail` and
    killed every release -- after publish-attest had pushed the image and burned the tag.
    The data contract must reach this job as sealed evidence from `capture`, which is the
    last job that still holds the caller source.
    """
    job = _load(REUSABLE)["jobs"]["assemble-evidence"]
    caller_checkout = any(
        "actions/checkout" in str(step.get("uses", "")) and not (step.get("with") or {}).get("path")
        for step in _steps(job)
    )

    assert not caller_checkout, "assemble-evidence must not check out the caller source"
    assert "container-release.json" not in _run_text(job)
