"""container-security.yml must build the image and run Trivy + Syft on PR and push."""

from pathlib import Path
from typing import Any

import yaml

WF = Path(".github/workflows/container-security.yml")


def _load_steps() -> list[dict[str, Any]]:
    """Return the flattened step list for the first (only) job."""
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    jobs: dict[str, Any] = doc["jobs"]
    first_job = next(iter(jobs.values()))
    return list(first_job["steps"])


def test_workflow_is_valid_yaml_with_a_job() -> None:
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert doc["jobs"], "expected at least one job"


def test_triggers_on_pull_request_and_push() -> None:
    text = WF.read_text(encoding="utf-8")
    assert "pull_request" in text and "push" in text, (
        "scan on PR and push (Container-Hardening v1 §8.27)"
    )


def test_builds_image_and_runs_trivy_and_syft() -> None:
    text = WF.read_text(encoding="utf-8")
    assert "docker build" in text and "docker/Dockerfile" in text, (
        "must build the production image in CI"
    )
    assert "aquasecurity/trivy-action@" in text, "Trivy image scan required"
    assert 'exit-code: "1"' in text, "Trivy must fail the build on findings"
    assert "CRITICAL,HIGH" in text, "gate on HIGH/CRITICAL"
    assert "ignore-unfixed: true" in text, "gate on fixable vulns only"
    assert "anchore/sbom-action@" in text, "SBOM (Syft) required (Container-Hardening v1 §8.29)"


def test_trivy_runs_before_syft() -> None:
    """Trivy must gate the image before the SBOM step can shadow it (Finding 1).

    If Syft runs first and fails for a transient reason, Trivy never executes —
    an admin override could merge an unscanned image.  The fix is to place the
    Trivy scan immediately after the image build and before any Syft steps.
    """
    steps = _load_steps()

    def _uses(step: dict[str, Any], prefix: str) -> bool:
        return str(step.get("uses", "")).startswith(prefix)

    trivy_idx = next(
        (i for i, s in enumerate(steps) if _uses(s, "aquasecurity/trivy-action@")),
        None,
    )
    syft_idx = next(
        (i for i, s in enumerate(steps) if _uses(s, "anchore/sbom-action@")),
        None,
    )

    assert trivy_idx is not None, "Trivy step not found in workflow"
    assert syft_idx is not None, "Syft SBOM step not found in workflow"
    assert trivy_idx < syft_idx, (
        f"Trivy scan (step {trivy_idx}) must come BEFORE Syft SBOM (step {syft_idx}) "
        "so the security gate cannot be shadowed by an SBOM-action failure"
    )


def test_sbom_step_uploads_artifact() -> None:
    """SBOM artifact must be persisted so it survives even when Trivy fails (§8.29)."""
    steps = _load_steps()
    sbom_step = next(
        (s for s in steps if str(s.get("uses", "")).startswith("anchore/sbom-action@")),
        None,
    )
    assert sbom_step is not None, "Syft SBOM step not found"
    with_block: dict[str, Any] = sbom_step.get("with", {})
    assert with_block.get("upload-artifact") is True, (
        "anchore/sbom-action must have upload-artifact: true (Container-Hardening v1 §8.29)"
    )


def test_sarif_upload_runs_on_failure() -> None:
    """SARIF upload step must carry 'if: always()' so findings reach code scanning
    even when the Trivy exit-code gate has already failed the job."""
    steps = _load_steps()
    sarif_step = next(
        (
            s
            for s in steps
            if str(s.get("uses", "")).startswith("github/codeql-action/upload-sarif@")
        ),
        None,
    )
    assert sarif_step is not None, "SARIF upload step not found"
    if_condition: str = str(sarif_step.get("if", ""))
    assert "always()" in if_condition, (
        "SARIF upload step must include 'always()' in its 'if:' condition "
        "so Trivy findings reach code scanning even after the job fails"
    )
