# P0 Trusted-Builder Governance Implementation Plan

> Historical record — this plan records the approved 2026-07-18 implementation sequence. Current
> behavior is defined by implemented controls, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce and continuously prove a one-approval, no-bypass `main` branch rule for the router—the source of the reusable release workflow—without imposing that rule on backend repositories.

**Architecture:** Extend the existing fail-closed container-control ledger with an explicit repository role and a router-only `main_branch_ruleset` control. The control auditor reads GitHub ruleset detail and emits evidence only when the exact branch protection is active. A separate read-only scheduled/manual audit checks live controls against the committed ledger; the release workflow continues to consume the sealed ledger as its publication prerequisite.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, GitHub REST rulesets API through `gh api`, GitHub Actions, `uv`, Ruff, mypy.

---

## File structure

- Modify: `genefoundry_router/release/controls.py` — typed, fail-closed ledger models and role-aware compliance validation.
- Modify: `scripts/audit_container_controls.py` — deterministic `main` branch-ruleset probe and role-aware ledger construction.
- Modify: `tests/release/test_controls.py` — ledger-model and compliance regression tests.
- Modify: `tests/release/test_control_audit.py` — GitHub API fixture tests for the branch probe.
- Create: `.github/workflows/control-audit.yml` — scheduled/manual, read-only live-control check using a dedicated audit token.
- Create: `tests/release/test_control_audit_workflow.py` — static workflow safety contract.
- Modify: `ci/container-controls.json` — generated evidence after the GitHub rule exists and the probe succeeds.

## Preconditions outside the repository

Before changing GitHub settings, establish two active, independently controlled maintainer accounts
with write access to `berntpopp/genefoundry-router`; GitHub does not allow self-approval. Create a
test PR on a non-protected branch and have the other maintainer approve it. Do not enable a no-bypass
rule until this succeeds. The administrator must also provision `CONTROL_AUDIT_TOKEN` as either a
short-lived GitHub App installation token or a fine-grained token restricted to read-only repository
administration; it must not have contents-write or workflow-write access.

### Task 1: Add a role-aware, router-only ledger model

**Files:**

- Modify: `tests/release/test_controls.py:31-166`
- Modify: `genefoundry_router/release/controls.py:50-211`

- [ ] **Step 1: Write failing role and branch-control tests**

Add these helpers and tests to `tests/release/test_controls.py`. Keep existing tag-ruleset fixtures
unchanged: a backend still has its existing tag-rule bypass policy, while only the trusted builder
gets the new empty-bypass branch control.

```python
def _main_rule() -> dict[str, object]:
    return {
        "active": True,
        "targets_main": True,
        "requires_pull_request": True,
        "required_approving_review_count": 1,
        "blocks_force_pushes": True,
        "blocks_deletions": True,
        "bypass_actors": [],
        "evidence": _evidence(),
    }


def test_only_the_trusted_builder_requires_the_main_branch_rule() -> None:
    router = "berntpopp/genefoundry-router"
    backend = "berntpopp/example-link"
    payload = _ledger({router, backend})
    payload["repositories"][router]["role"] = "trusted-builder"  # type: ignore[index]
    payload["repositories"][router]["main_branch_ruleset"] = _main_rule()  # type: ignore[index]
    payload["repositories"][backend]["role"] = "backend"  # type: ignore[index]

    require_compliant_controls(load_control_ledger(payload), {router, backend})


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("main_branch_ruleset", "bypass_actors", ["RepositoryRole:5"]), "main branch"),
        (("main_branch_ruleset", "required_approving_review_count", 2), "main branch"),
        (("main_branch_ruleset", "blocks_force_pushes", False), "main branch"),
    ],
)
def test_trusted_builder_main_branch_control_fails_closed(
    change: tuple[str, str, object], message: str
) -> None:
    router = "berntpopp/genefoundry-router"
    payload = _ledger({router})
    row = payload["repositories"][router]  # type: ignore[index]
    row["role"] = "trusted-builder"
    row["main_branch_ruleset"] = _main_rule()
    row[change[0]][change[1]] = change[2]

    with pytest.raises(ControlLedgerError, match=message):
        require_compliant_controls(load_control_ledger(payload), {router})
```

- [ ] **Step 2: Run the focused model test to verify it fails**

Run:

```bash
uv run pytest tests/release/test_controls.py -q
```

Expected: FAIL because `role` and `main_branch_ruleset` are forbidden extra ledger fields.

- [ ] **Step 3: Implement the strict control models and validation**

In `genefoundry_router/release/controls.py`, add these fields and preserve `extra="forbid"` on all
models:

```python
RepositoryRole = Literal["trusted-builder", "backend"]


class MainBranchRulesetControl(_StrictModel):
    active: bool
    targets_main: bool
    requires_pull_request: bool
    required_approving_review_count: Literal[1]
    blocks_force_pushes: bool
    blocks_deletions: bool
    bypass_actors: list[Annotated[str, Field(min_length=1, max_length=100)]]
    evidence: ControlEvidence


class VerifiedRepositoryControls(_StrictModel):
    status: Literal["verified"]
    repository: RepositoryName
    role: RepositoryRole
    tag_ruleset: TagRulesetControl
    main_branch_ruleset: MainBranchRulesetControl | None = None
    release_environment: ReleaseEnvironmentControl
    immutable_releases: ImmutableReleaseControl
    package: PackageControl
    retention: RetentionControl

    @model_validator(mode="after")
    def _role_controls_are_exact(self) -> VerifiedRepositoryControls:
        if self.role == "trusted-builder" and self.main_branch_ruleset is None:
            raise ValueError("trusted builder requires a main branch ruleset")
        if self.role == "backend" and self.main_branch_ruleset is not None:
            raise ValueError("backend must not carry a main branch ruleset")
        return self
```

Extend `_verified_row_errors()` to reject any trusted-builder rule that is inactive, not targeted
to `main`, lacks a PR rule, allows force pushes/deletion, has a non-empty `bypass_actors`, has
unavailable evidence, or does not require exactly one approval. Add
`router_repository(pyproject_file: Path = Path("pyproject.toml")) -> RepositoryName`, loading
`project.urls.Repository` with `tomllib`, requiring the exact `https://github.com/OWNER/REPOSITORY`
shape, and returning the `OWNER/REPOSITORY` repository identity. Use that helper in
`expected_fleet_repositories()` rather than the existing literal. Extend
`require_compliant_controls()` to require exactly one `trusted-builder` role, require that row to
equal `router_repository()`, and require every other expected row to be `backend`.

- [ ] **Step 4: Run the focused test to verify it passes**

Run:

```bash
uv run pytest tests/release/test_controls.py -q
```

Expected: PASS, including existing tag-ruleset, unavailable-evidence, and fleet-coverage tests.

- [ ] **Step 5: Commit the typed ledger contract**

```bash
git add genefoundry_router/release/controls.py tests/release/test_controls.py
git commit -m "feat(release): require trusted builder branch controls"
```

### Task 2: Probe the exact GitHub `main` ruleset

**Files:**

- Modify: `tests/release/test_control_audit.py:12-127`
- Modify: `scripts/audit_container_controls.py:30-280`

- [ ] **Step 1: Write failing API-detail tests**

Add a branch ruleset fixture separate from `RULESET_DETAIL`; it must match GitHub's branch target
and `pull_request` rule shape exactly:

```python
MAIN_RULESET = {
    "id": 2,
    "name": "Protect trusted-builder main",
    "target": "branch",
}
MAIN_RULESET_DETAIL = {
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
    "bypass_actors": [],
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
    ],
}


@pytest.mark.parametrize(
    "detail",
    [
        {**MAIN_RULESET_DETAIL, "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5}]},
        {**MAIN_RULESET_DETAIL, "enforcement": "evaluate"},
        {**MAIN_RULESET_DETAIL, "conditions": {"ref_name": {"include": ["refs/heads/dev"]}}},
        {**MAIN_RULESET_DETAIL, "rules": [{"type": "pull_request", "parameters": {"required_approving_review_count": 2}}]},
    ],
)
def test_main_branch_ruleset_rejects_any_policy_relaxation(
    monkeypatch: pytest.MonkeyPatch, detail: dict[str, Any]
) -> None:
    _install_api(monkeypatch, {f"repos/{REPO}/rulesets/2": detail})

    assert audit.probe_main_branch_ruleset(REPO) is None
```

Add a passing test that asserts the returned row has `bypass_actors == []`,
`required_approving_review_count == 1`, and all boolean safeguards true.

- [ ] **Step 2: Run the auditor test to verify it fails**

Run:

```bash
uv run pytest tests/release/test_control_audit.py -q
```

Expected: FAIL because `probe_main_branch_ruleset` does not exist.

- [ ] **Step 3: Implement the fail-closed branch probe and role assignment**

In `scripts/audit_container_controls.py`:

```python
MAIN_RULESET_NAME = "Protect trusted-builder main"
MAIN_BRANCH_REF = "refs/heads/main"


def probe_main_branch_ruleset(repo: str) -> JsonDict | None:
    rulesets = _gh_api(f"repos/{repo}/rulesets")
    if not isinstance(rulesets, list):
        return None
    match = next(
        (item for item in rulesets if item.get("name") == MAIN_RULESET_NAME and item.get("target") == "branch"),
        None,
    )
    if match is None:
        return None
    detail = _gh_api(f"repos/{repo}/rulesets/{match['id']}")
    if not isinstance(detail, dict) or detail.get("enforcement") != "active":
        return None
    include = detail.get("conditions", {}).get("ref_name", {}).get("include")
    if include != [MAIN_BRANCH_REF] or detail.get("bypass_actors") != []:
        return None
    rules = {rule.get("type"): rule.get("parameters", {}) for rule in detail.get("rules", [])}
    pull_request = rules.get("pull_request")
    if not isinstance(pull_request, dict) or pull_request.get("required_approving_review_count") != 1:
        return None
    if not {"deletion", "non_fast_forward"} <= set(rules):
        return None
    return {
        "active": True,
        "targets_main": True,
        "requires_pull_request": True,
        "required_approving_review_count": 1,
        "blocks_force_pushes": True,
        "blocks_deletions": True,
        "bypass_actors": [],
        "evidence": _api_evidence(
            f"https://github.com/{repo}/settings/rules/{match['id']}",
            "Active no-bypass main ruleset probed via the repository rulesets API.",
        ),
    }
```

Make `build_row()` take `role: Literal["trusted-builder", "backend"]`; only the trusted-builder
probe set includes `main_branch_ruleset`, and a missing branch probe adds `main_branch_ruleset` to
the unavailable reason. Make `build_ledger()` obtain its one trusted-builder from
`router_repository()` and pass `backend` for every registered `*-link` repository. Do not change
tag ruleset bypass behavior.

- [ ] **Step 4: Run audit and complete release tests**

Run:

```bash
uv run pytest tests/release/test_control_audit.py tests/release/test_controls.py -q
```

Expected: PASS. A missing GitHub response, an unknown rule shape, or any bypass actor must make the
row unavailable rather than produce a partially verified field.

- [ ] **Step 5: Commit the live-control probe**

```bash
git add scripts/audit_container_controls.py tests/release/test_control_audit.py
git commit -m "feat(release): audit trusted builder main ruleset"
```

### Task 3: Add the independent read-only audit workflow

**Files:**

- Create: `tests/release/test_control_audit_workflow.py`
- Create: `.github/workflows/control-audit.yml`

- [ ] **Step 1: Write the workflow safety test**

Create `tests/release/test_control_audit_workflow.py`:

```python
from __future__ import annotations

from pathlib import Path

import yaml


def test_control_audit_workflow_is_read_only_and_uses_the_dedicated_token() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/control-audit.yml").read_text())

    assert workflow[True]["workflow_dispatch"] == {}
    assert workflow[True]["schedule"]
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["audit"]
    run_steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert "scripts/audit_container_controls.py --check" in run_steps
    assert "CONTROL_AUDIT_TOKEN" in run_steps
    assert "contents: write" not in Path(".github/workflows/control-audit.yml").read_text()
```

- [ ] **Step 2: Run the new workflow test to verify it fails**

Run:

```bash
uv run pytest tests/release/test_control_audit_workflow.py -q
```

Expected: FAIL because the workflow file does not exist.

- [ ] **Step 3: Add the scheduled/manual audit workflow**

Create `.github/workflows/control-audit.yml` with a daily schedule and a manual trigger. Pin the
same checkout and uv setup actions used by existing workflows. The audit step must run under the
dedicated secret and must not write the ledger:

```yaml
permissions:
  contents: read

jobs:
  audit:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990 # v8.3.2
      - run: uv sync --group dev --frozen
      - name: Verify live trusted-builder controls
        env:
          GH_TOKEN: ${{ secrets.CONTROL_AUDIT_TOKEN }}
        run: uv run python scripts/audit_container_controls.py --check
```

The job must only report drift. Ledger refresh remains a reviewed local change so the control being
audited cannot self-update its evidence.

- [ ] **Step 4: Run workflow syntax and safety tests**

Run:

```bash
uv run pytest tests/release/test_control_audit_workflow.py tests/unit/test_workflows_parse.py -q
make lint-actions
```

Expected: PASS; locally unavailable `actionlint` may emit the repository's documented skip message,
while the YAML parser test still passes.

- [ ] **Step 5: Commit the audit workflow**

```bash
git add .github/workflows/control-audit.yml tests/release/test_control_audit_workflow.py
git commit -m "ci: audit trusted builder controls"
```

### Task 4: Configure GitHub, seal evidence, and verify the release gate

**Files:**

- Modify: `ci/container-controls.json`

- [ ] **Step 1: Configure the ruleset in GitHub after the two-maintainer precondition is met**

In the router repository's Rulesets settings, create an active branch ruleset named `Protect
trusted-builder main` targeting exactly `refs/heads/main`. Enable required pull requests with one
approving review, deletion prevention, and non-fast-forward prevention. Leave required status
checks, CODEOWNERS, merge queue, and additional review requirements unset. Configure no bypass
actors, including no administrator bypass.

- [ ] **Step 2: Prove the live setting before modifying evidence**

Run with the dedicated read-only audit token available only in the terminal environment:

```bash
GH_TOKEN="$CONTROL_AUDIT_TOKEN" uv run python scripts/audit_container_controls.py --check
```

Expected: FAIL only because the checked-in ledger predates the new schema; its live probe output
must show no blockers for `berntpopp/genefoundry-router` once the settings are correct.

- [ ] **Step 3: Regenerate the ledger and inspect the router row**

Run:

```bash
GH_TOKEN="$CONTROL_AUDIT_TOKEN" uv run python scripts/audit_container_controls.py
jq '.repositories["berntpopp/genefoundry-router"] | {role, main_branch_ruleset}' ci/container-controls.json
```

Expected: the router row has `role: "trusted-builder"`, an active `main_branch_ruleset`, exactly
one approval, and an empty `bypass_actors` array. Every backend row has `role: "backend"` and no
`main_branch_ruleset` field.

- [ ] **Step 4: Run the full required verification**

Run:

```bash
GH_TOKEN="$CONTROL_AUDIT_TOKEN" uv run python scripts/audit_container_controls.py --check
make ci-local
```

Expected: both commands pass. The first proves live API state agrees with the checked-in evidence;
the second proves the release gate can parse and require it.

- [ ] **Step 5: Commit evidence through the newly protected branch**

```bash
git add ci/container-controls.json
git commit -m "chore(release): record trusted builder branch controls"
```

Open a PR and obtain exactly one approval from the second maintainer. This is the first protected
`main` merge and validates the operational policy as well as the static model.
