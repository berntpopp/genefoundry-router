"""Static contract tests for the read-only trusted-builder control audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/control-audit.yml"
AUDIT_COMMAND = "uv run python scripts/audit_container_controls.py --check"
CONTROL_AUDIT_SECRET_REF = "${{ secrets.CONTROL_AUDIT_TOKEN }}"  # noqa: S105
SECRET_EXPRESSION_PREFIX = "${{ secrets."  # noqa: S105
CHECKOUT_ACTION = "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
SETUP_UV_ACTION = "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990"


def _load() -> dict[str, Any]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _on(document: dict[str, Any]) -> dict[str, Any]:
    # PyYAML 1.1 treats the unquoted key ``on`` as boolean true.
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    return trigger


def test_manual_audit_is_confined_to_main_and_protected_environment() -> None:
    workflow = _load()
    trigger = _on(workflow)

    assert set(trigger) == {"schedule", "workflow_dispatch"}
    assert trigger["workflow_dispatch"] == {}
    assert trigger["schedule"]
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"audit"}

    job = workflow["jobs"]["audit"]
    job_permissions = job.get("permissions", {})
    assert isinstance(job_permissions, dict)
    assert all(access != "write" for access in job_permissions.values())
    assert job["environment"] == "control-audit"
    assert job["if"] == "github.event_name == 'schedule' || github.ref == 'refs/heads/main'"
    assert "contents: write" not in WORKFLOW.read_text(encoding="utf-8").lower()


def test_audit_job_has_an_exact_read_only_step_and_secret_allowlist() -> None:
    job = _load()["jobs"]["audit"]
    steps = job["steps"]
    expected_steps = [
        {
            "name": "Checkout",
            "uses": CHECKOUT_ACTION,
            "with": {"persist-credentials": False},
        },
        {
            "name": "Set up uv",
            "uses": SETUP_UV_ACTION,
            "with": {"version": "0.8.7"},
        },
        {
            "name": "Install dependencies",
            "run": "uv sync --group dev --frozen",
        },
        {
            "name": "Verify trusted-builder controls",
            "env": {"GH_TOKEN": CONTROL_AUDIT_SECRET_REF},
            "run": AUDIT_COMMAND,
        },
    ]
    assert steps == expected_steps

    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    assert workflow_text.count(SECRET_EXPRESSION_PREFIX) == 1
    assert workflow_text.count("CONTROL_AUDIT_TOKEN") == 1

    inherited_envs = [job.get("env", {}), job.get("container", {}).get("env", {})]
    inherited_envs.extend(service.get("env", {}) for service in job.get("services", {}).values())
    assert all(SECRET_EXPRESSION_PREFIX not in str(env) for env in inherited_envs)

    run_text = "\n".join(str(step.get("run", "")) for step in steps).lower()
    for mutation in ("--write", "--update", "git commit", "git push", "gh api", "gh release"):
        assert mutation not in run_text
