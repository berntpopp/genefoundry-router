"""Re-run and recovery contract for the shared container-release workflow.

Observed on mitomap-link v0.1.2 (run 43409703573): a fully published release stayed red
because (1) GHCR had not yet served the just-copied version tag, (2) "re-run failed jobs"
looked up evidence under the new attempt's artifact name, and (3) a full re-run's recovery
probe verified attestations with the caller repo's default signer identity, which can never
match an attestation signed by this reusable workflow.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
REUSABLE = ROOT / ".github/workflows/_container-release.yml"
SIGNER = "--signer-workflow berntpopp/genefoundry-router/.github/workflows/_container-release.yml"
PREDICATES = ("https://slsa.dev/provenance/v1", "https://spdx.dev/Document/v2.3")
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
BASH = shutil.which("bash") or "/bin/bash"


def _workflow() -> dict[str, Any]:
    document = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _logical_lines(shell: str) -> list[str]:
    """Executable shell lines with backslash continuations joined."""
    lines: list[str] = []
    pending = ""
    for raw in shell.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
            continue
        lines.append(pending + stripped)
        pending = ""
    return lines


def _verify_commands(job: dict[str, Any]) -> list[str]:
    shell = "\n".join(str(step.get("run", "")) for step in job["steps"])
    return [line for line in _logical_lines(shell) if 'attestation verify "oci://' in line]


def test_every_image_attestation_verify_pins_the_release_identity() -> None:
    """`--repo` alone pins the signer to the CALLER's workflows; ours signs them all."""
    commands = [cmd for job in _workflow()["jobs"].values() for cmd in _verify_commands(job)]
    assert commands
    for command in commands:
        for flag in (
            SIGNER,
            "--repo ",
            "--signer-digest ",
            "--source-ref ",
            "--source-digest ",
            "--predicate-type ",
            "--deny-self-hosted-runners",
        ):
            assert flag in command, (flag, command)


def test_recovery_probe_verifies_both_predicates_against_this_run_identity() -> None:
    prepare = _workflow()["jobs"]["prepare"]
    commands = _verify_commands(prepare)
    assert len(commands) == 1
    command = commands[0]
    assert '--signer-digest "$STANDARD_SHA"' in command
    assert '--source-ref "$EVENT_REF"' in command
    assert '--source-digest "$source_sha"' in command
    assert '"oci://$image@$source_digest"' in command
    # The probe must not swallow the verifier's reason for failing.
    assert "2>&1" not in command
    state = next(s for s in prepare["steps"] if s.get("id") == "state")["run"]
    for predicate in PREDICATES:
        assert predicate in state


def test_cross_job_artifacts_are_named_by_the_producing_jobs_outputs() -> None:
    """Re-running only failed jobs keeps upstream outputs; a fresh run_attempt name does not."""
    jobs = _workflow()["jobs"]
    downloads = 0
    for job_name, job in jobs.items():
        for step in job.get("steps", []):
            uses = str(step.get("uses", ""))
            if uses.startswith("actions/upload-artifact@"):
                name = step["with"]["name"]
                if name.startswith("release-"):
                    assert job["outputs"]["artifact_name"] == name, job_name
            if uses.startswith("actions/download-artifact@"):
                downloads += 1
                match = re.fullmatch(
                    r"\$\{\{ needs\.([a-z-]+)\.outputs\.artifact_name \}\}", step["with"]["name"]
                )
                assert match, (job_name, step["with"]["name"])
                needs = job["needs"]
                assert match.group(1) in (needs if isinstance(needs, list) else [needs])
    assert downloads == 5


# --- script-level: the finalize alias step under GHCR read-after-write lag ---------------

STUB_ORAS = """#!/usr/bin/env bash
# Stub ORAS: the source alias resolves to $SRC_DIGEST; the version tag answers from the
# space-separated $VERSION_PLAN queue (one entry per fetch): ok, notfound, denied, other.
set -euo pipefail
state="$RUNNER_TEMP/plan"
[ -f "$state" ] || echo "$VERSION_PLAN" > "$state"
case "$1 $2" in
  "cp "*) echo "Copied"; exit 0 ;;
esac
ref="${@: -1}"
if [[ "$ref" == *":$SOURCE_ALIAS" ]]; then
  printf '{"digest":"%s"}' "$SRC_DIGEST"; exit 0
fi
read -r head rest < "$state" || true
echo "${rest:-$head}" > "$state"
case "$head" in
  ok) printf '{"digest":"%s"}' "$VERSION_DIGEST" ;;
  notfound) echo "Error response from registry: failed to find \\"$ref\\": $ref: not found" >&2; exit 4 ;;
  denied) echo "Error response from registry: unauthorized: authentication required" >&2; exit 1 ;;
esac
"""


def _alias_script() -> str:
    finalize = _workflow()["jobs"]["finalize"]
    return next(s["run"] for s in finalize["steps"] if "version alias" in str(s.get("name")))


def _run_alias_step(tmp_path: Path, plan: str, version_digest: str = DIGEST) -> tuple[int, str]:
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    oras = runner_temp / "oras"
    oras.write_text(STUB_ORAS, encoding="utf-8")
    oras.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RUNNER_TEMP": str(runner_temp),
        "IMAGE": "ghcr.io/berntpopp/example-link",
        "SOURCE_ALIAS": "sha-" + "c" * 40,
        "VERSION": "1.2.3",
        "PUBLISHED_DIGEST": DIGEST,
        "SRC_DIGEST": DIGEST,
        "VERSION_DIGEST": version_digest,
        "VERSION_PLAN": plan,
    }
    result = subprocess.run(  # noqa: S603 -- fixed bash running the workflow's own step
        [BASH, "-c", _alias_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return result.returncode, result.stdout + result.stderr


def test_alias_step_retries_while_ghcr_has_not_yet_served_the_new_tag(tmp_path: Path) -> None:
    # 1st fetch: tag absent before copy; then two read-after-write misses; then visible.
    code, output = _run_alias_step(tmp_path, "notfound notfound notfound ok")
    assert code == 0, output
    assert "missing version alias" in output
    assert output.count("version alias not yet visible") == 2


def test_alias_step_still_fails_on_a_real_digest_mismatch(tmp_path: Path) -> None:
    code, output = _run_alias_step(tmp_path, "notfound notfound ok", version_digest=OTHER_DIGEST)
    assert code != 0
    assert "version alias digest mismatch" in output


def test_alias_step_does_not_retry_errors_other_than_not_found(tmp_path: Path) -> None:
    code, output = _run_alias_step(tmp_path, "notfound denied ok")
    assert code != 0
    assert "unauthorized" in output
    assert "version alias not yet visible" not in output


def test_alias_step_retry_is_bounded(tmp_path: Path) -> None:
    code, output = _run_alias_step(tmp_path, "notfound")
    assert code != 0
    assert "never became visible" in output
    assert 1 <= output.count("version alias not yet visible") <= 10


def test_alias_step_accepts_an_existing_identical_alias_without_copying(tmp_path: Path) -> None:
    code, output = _run_alias_step(tmp_path, "ok")
    assert code == 0, output
    assert "missing version alias" not in output
