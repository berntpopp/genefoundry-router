"""container-security.yml must build the image and run Trivy + Syft on PR and push."""

from pathlib import Path

import yaml

WF = Path(".github/workflows/container-security.yml")


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
