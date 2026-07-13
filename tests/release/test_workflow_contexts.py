"""No workflow may reference an unavailable context in a job-level ``env:`` block.

The ``runner`` context does not exist when GitHub evaluates ``jobs.<id>.env``: only
``github``, ``needs``, ``strategy``, ``matrix``, ``vars``, ``secrets`` and ``inputs`` do.
A ``${{ runner.temp }}`` there is an invalid-context error that kills the whole run before
a single job starts — the run ends with zero jobs and "This run likely failed because of a
workflow file issue", which reads like infrastructure flake rather than a broken gate.

That is not hypothetical: one such line in ``_container-ci.yml`` silently disabled the
container gate across the whole fleet, and every existing test still passed because the
workflow's *text* was correct. This asserts the context, not the text.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.yml"))

#: Contexts GitHub makes available when it evaluates a job-level ``env:`` block.
JOB_ENV_CONTEXTS = frozenset({"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"})
CONTEXT_REFERENCE = re.compile(r"\$\{\{\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*\.")


def _jobs(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = document.get("jobs") or {}
    assert isinstance(jobs, dict)
    return jobs


def test_workflows_are_discovered() -> None:
    assert WORKFLOWS
    names = {path.name for path in WORKFLOWS}
    assert {"_container-ci.yml", "_container-release.yml"} <= names


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_job_level_env_never_references_an_unavailable_context(path: Path) -> None:
    for job_name, job in _jobs(path).items():
        for key, value in (job.get("env") or {}).items():
            contexts = set(CONTEXT_REFERENCE.findall(str(value)))
            unavailable = contexts - JOB_ENV_CONTEXTS
            assert not unavailable, (
                f"{path.name}: jobs.{job_name}.env.{key} references {sorted(unavailable)}, "
                "which GitHub cannot evaluate in a job-level env block; the whole run fails "
                "to start. Use $RUNNER_TEMP inside the step, or a step-level env."
            )
