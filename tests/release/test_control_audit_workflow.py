"""Static contract tests for the short-lived GitHub App control audit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/control-audit.yml"
CHECKOUT_ACTION = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_UV_ACTION = "astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d"
APP_TOKEN_ACTION = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"  # noqa: S105 - action identifier
UPLOAD_ACTION = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
SHELL_LEDGER = "$RUNNER_TEMP/container-controls.json"
MAIN_REF = "refs/heads/main"
ARTIFACT_LEDGER = "${{ runner.temp }}/container-controls.json"
REPOSITORIES = (
    "autopvs1-link",
    "clingen-link",
    "clinvar-link",
    "gencc-link",
    "genefoundry-router",
    "genereviews-link",
    "gnomad-link",
    "gtex-link",
    "hgnc-link",
    "hpo-link",
    "litvar-link",
    "mavedb-link",
    "metadome-link",
    "mgi-link",
    "mondo-link",
    "orphanet-link",
    "panelapp-link",
    "pubtator-link",
    "spliceailookup-link",
    "stringdb-link",
    "uniprot-link",
    "vep-link",
)


def _load(path: Path = WORKFLOW) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _on(document: dict[str, Any]) -> dict[str, Any]:
    # PyYAML 1.1 treats the unquoted key ``on`` as boolean true.
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _context(*, event_name: str, ref: str = MAIN_REF, enabled: str = "") -> dict[str, str]:
    # An unset repository variable renders as the empty string in a GitHub expression.
    return {
        "github.event_name": event_name,
        "github.ref": ref,
        "vars.CONTROL_AUDIT_ENABLED": enabled,
    }


def _atom(expression: str, context: dict[str, str]) -> bool:
    left, equality, right = expression.strip().partition("==")
    assert equality, f"unsupported expression atom: {expression!r}"
    name = left.strip()
    assert name in context, f"unmodelled context reference: {name!r}"
    return context[name] == right.strip().strip("'")


def _evaluate(condition: str, context: dict[str, str]) -> bool:
    """Evaluate the job's ``if`` expression for one trigger context.

    Only the flat ``==`` / ``&&`` / ``||`` shape this workflow uses is supported, and every
    atom is evaluated (no short-circuit) so a rewrite into some other shape fails loudly
    instead of quietly degrading into a test that always passes.
    """
    condition = condition.strip()
    if condition.startswith("${{"):
        condition = condition.removeprefix("${{").removesuffix("}}").strip()
    disjuncts: list[bool] = []
    for clause in condition.split("||"):
        conjunction = clause.strip().removeprefix("(").removesuffix(")")
        atoms = [_atom(atom, context) for atom in conjunction.split("&&")]
        disjuncts.append(all(atoms))
    return any(disjuncts)


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
    condition = job["if"]
    assert _evaluate(condition, _context(event_name="workflow_dispatch"))
    assert not _evaluate(
        condition, _context(event_name="workflow_dispatch", ref="refs/heads/not-main")
    )


def test_scheduled_audit_stays_opt_in_until_the_control_audit_app_exists() -> None:
    # The control-audit GitHub App is a deferred admin prerequisite, so the daily schedule must
    # stay parked until CONTROL_AUDIT_ENABLED is set; enabling it must then restore the run.
    condition = _load()["jobs"]["audit"]["if"]

    assert not _evaluate(condition, _context(event_name="schedule"))
    assert not _evaluate(condition, _context(event_name="schedule", enabled="false"))
    assert _evaluate(condition, _context(event_name="schedule", enabled="true"))


def test_app_token_is_exactly_scoped_to_the_fleet_and_two_read_permissions() -> None:
    steps = _load()["jobs"]["audit"]["steps"]
    token = next(step for step in steps if step.get("uses") == APP_TOKEN_ACTION)

    assert token["id"] == "app-token"
    assert token["with"] == {
        "client-id": "${{ secrets.CONTROL_AUDIT_APP_CLIENT_ID }}",
        "private-key": "${{ secrets.CONTROL_AUDIT_APP_PRIVATE_KEY }}",
        "owner": "berntpopp",
        "repositories": "\n".join(REPOSITORIES) + "\n",
        "permission-metadata": "read",
        "permission-administration": "read",
    }


def test_live_probe_is_strictly_validated_before_success_only_upload() -> None:
    steps = _load()["jobs"]["audit"]["steps"]
    assert steps[:3] == [
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
    ]

    probe_index = next(
        index for index, step in enumerate(steps) if step.get("name") == "Probe live controls"
    )
    validate_index = next(
        index for index, step in enumerate(steps) if step.get("name") == "Validate live ledger"
    )
    upload_index = next(
        index for index, step in enumerate(steps) if step.get("uses") == UPLOAD_ACTION
    )
    assert probe_index < validate_index < upload_index
    assert steps[probe_index] == {
        "name": "Probe live controls",
        "env": {"GH_TOKEN": "${{ steps.app-token.outputs.token }}"},
        "run": f'uv run python scripts/audit_container_controls.py --ledger "{SHELL_LEDGER}"',
    }
    assert steps[validate_index] == {
        "name": "Validate live ledger",
        "run": f'uv run python scripts/validate_container_controls.py "{SHELL_LEDGER}"',
    }
    assert steps[upload_index] == {
        "name": "Upload verified live ledger",
        "if": "success()",
        "uses": UPLOAD_ACTION,
        "with": {
            "name": "container-controls-live",
            "path": ARTIFACT_LEDGER,
            "if-no-files-found": "error",
            "retention-days": 30,
        },
    }


def test_workflow_has_no_long_lived_or_failure_suppressing_escape_hatches() -> None:
    workflow = _load()
    text = WORKFLOW.read_text(encoding="utf-8")
    run_text = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["audit"]["steps"])

    for forbidden in (
        "CONTROL_AUDIT_TOKEN",
        "persist-credentials: true",
        "personal access token",
        "PAT fallback",
        "skip-token-revoke",
        "continue-on-error",
        "|| true",
        "|| :",
        "set +e",
    ):
        assert forbidden.lower() not in text.lower()
    assert "GH_TOKEN" not in str(workflow["jobs"]["audit"].get("env", {}))
    validator = next(
        step
        for step in workflow["jobs"]["audit"]["steps"]
        if step.get("name") == "Validate live ledger"
    )
    assert "GH_TOKEN" not in str(validator.get("env", {}))
    assert "--check" not in run_text


def test_reviewed_action_version_comments_match_pins() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert f"uses: {SETUP_UV_ACTION} # v10.0.1" in text
    assert f"uses: {APP_TOKEN_ACTION} # v3.2.0" in text
    assert f"uses: {UPLOAD_ACTION} # v7.0.1" in text


def test_every_workflow_yaml_parses() -> None:
    paths = sorted((ROOT / ".github/workflows").glob("*.y*ml"))
    assert paths
    for path in paths:
        _load(path)
