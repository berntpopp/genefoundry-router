"""Regression guard: every committed GitHub Actions workflow must be valid YAML.

F-01 (2026-07-12): `fleet-probe.yml` shipped an unquoted plain `run:` scalar containing
`fleet-probe: ` (colon-space), which made the workflow invalid YAML — GitHub created
zero-job failures and the workflow could not execute. This test fails on that class of
defect across every workflow file (both `.yml` and `.yaml`, which Actions both load).
"""

from pathlib import Path

import yaml

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def test_all_workflows_are_valid_yaml() -> None:
    files = sorted([*_WORKFLOWS.glob("*.yml"), *_WORKFLOWS.glob("*.yaml")])
    assert files, f"no workflow files found under {_WORKFLOWS}"
    for f in files:
        try:
            yaml.safe_load(f.read_text())
        except yaml.YAMLError as exc:  # pragma: no cover - failure path is the point
            raise AssertionError(f"{f.name} is not valid YAML: {exc}") from exc
