"""security.yml dependency-review must be a hard gate, not advisory."""

from pathlib import Path

import yaml


def _dep_review_step() -> dict:
    doc = yaml.safe_load(Path(".github/workflows/security.yml").read_text(encoding="utf-8"))
    for step in doc["jobs"]["dependency-review"]["steps"]:
        if "dependency-review-action" in str(step.get("uses", "")):
            return step
    raise AssertionError("dependency-review-action step not found")


def test_dependency_review_is_a_gate() -> None:
    step = _dep_review_step()
    assert "continue-on-error" not in step, "dependency-review must not be advisory-only"
    assert step.get("with", {}).get("fail-on-severity") == "high", "gate on HIGH+ severity"
