"""Probe live GitHub/GHCR release controls and emit the fleet control ledger.

Every control below is probed against the live API using the installed control-audit
GitHub App's declared permissions ONLY (``metadata: read``, ``administration: read`` —
see ``.github/workflows/control-audit.yml``). A control this App's permissions CAN
reach is probed directly and a row is marked ``unavailable`` (which keeps the release
gate closed) whenever that probe fails to verify. No control is ever auto-passed from
absence of evidence.

**Proven automatically by this App**, per row:
tag ruleset, main-branch ruleset (trusted builder only), release environment,
immutable releases, and anonymous package pull (an unauthenticated GHCR registry
request — this needs no GitHub API permission at all, since it talks to the registry,
not the GitHub REST API).

**NOT provable with this App's current permissions** — four hard controls, each gated
by a GitHub permission the App does not declare:

- **package linkage** (does the package belong to its claimed source repository?) —
  needs ``GET /users/{owner}/packages/container/{name}``, which requires
  ``packages: read``.
- **GITHUB_TOKEN publication** (was the package published by the ambient
  ``GITHUB_TOKEN`` rather than a personal access token?) — the only way to prove this
  is to resolve the artifact's build provenance via the attestations API
  (``GET /repos/{owner}/{repo}/attestations/{subject_digest}``), which is gated by a
  separate ``attestations: read`` permission this App also does not have.
- **standing package PAT absence** — there is no GitHub REST endpoint that enumerates
  which personal access tokens hold package-write scope on a repository. This is not
  a permission gap this App could close by requesting more scope; it has no
  automatable form at all.
- **retention** — the package's version-retention/cleanup policy is read through the
  same packages API as package linkage, so it needs ``packages: read`` too.

A row where every *provable* control above passes is emitted with
``status: "partial"``, not ``unavailable``, naming these four in
``manual_evidence_required``. This is the honest middle ground the previous
unconditional-``unavailable`` design lacked: "unproven by this App" is not the same
claim as "failing right now", and conflating them made the audit fail even when live
controls were exactly as configured. Supplying documented, human-attested manual
evidence for these four (the ``source: "manual"`` evidence type already used
elsewhere in this schema) is what would move a row from ``partial`` to fully
``verified``; `require_compliant_controls` treats ``partial`` as a warning rather than
a release blocker, and ``unavailable`` exactly as before.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from genefoundry_router.release.controls import (
    ControlLedgerError,
    expected_fleet_repositories,
    load_control_ledger,
    require_compliant_controls,
    router_repository,
)

RULESET_NAME = "Protect semantic release tags"
REQUIRED_RULES = frozenset({"creation", "update", "deletion", "non_fast_forward"})
MAIN_RULESET_NAME = "Protect trusted-builder main"
MAIN_BRANCH_REF = "refs/heads/main"
MAIN_BRANCH_RULE_TYPES = frozenset({"deletion", "non_fast_forward", "pull_request"})
MAIN_PULL_REQUEST_PARAMETER_VALUES: dict[str, Any] = {
    "dismiss_stale_reviews_on_push": False,
    "require_code_owner_review": False,
    "require_extra_approval_for_unattributed_changes": True,
    "require_last_push_approval": False,
    "required_review_thread_resolution": False,
}
# GitHub omits these from the rulesets response unless they are set, and an absent key
# means "not enabled" — exactly what this control wants. Demanding them as mandatory keys
# made the probe reject a correctly-configured ruleset outright, so require only that they
# are neutral *if present*. Unknown keys are still rejected: this widens the accepted
# shape, it does not stop pinning it.
MAIN_PULL_REQUEST_OPTIONAL_VALUES: dict[str, Any] = {
    "automatic_copilot_code_review_enabled": False,
}
# Pin the approval count to the fleet's current one-maintainer policy. GitHub forbids
# self-approval, so requiring 1 with no bypass actor would make `main` unmergeable.
MAIN_PULL_REQUEST_APPROVAL_COUNTS = frozenset({0})
MAIN_PULL_REQUEST_PARAMETER_KEYS = frozenset(
    {
        *MAIN_PULL_REQUEST_PARAMETER_VALUES,
        "required_approving_review_count",
        "allowed_merge_methods",
        "required_reviewers",
    }
)
MAIN_PULL_REQUEST_OPTIONAL_KEYS = frozenset(
    {*MAIN_PULL_REQUEST_OPTIONAL_VALUES, "dismissal_restriction"}
)
SUPPORTED_MERGE_METHODS = frozenset({"merge", "squash", "rebase"})
NEUTRAL_DISMISSAL_RESTRICTION: dict[str, Any] = {
    "allowed_actors": [],
    "enabled": False,
}
RELEASE_ENVIRONMENT = "release"
EXACT_TAG_POLICY = "v*.*.*"
BOOTSTRAP_TAG = "control-bootstrap"
MANIFEST_ACCEPT = ",".join(
    (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
)

JsonDict = dict[str, Any]


def _matches_exact_typed_values(actual: JsonDict, expected: JsonDict) -> bool:
    """Return whether a JSON object has exactly the expected keys, types, and values."""
    return actual.keys() == expected.keys() and all(
        type(actual[key]) is type(value) and actual[key] == value for key, value in expected.items()
    )


def _matches_neutral_pull_request_parameters(parameters: JsonDict) -> bool:
    """Accept only GitHub's known, non-restrictive pull-request response fields."""
    keys = frozenset(parameters)
    if not MAIN_PULL_REQUEST_PARAMETER_KEYS <= keys:
        return False
    if keys - MAIN_PULL_REQUEST_PARAMETER_KEYS - MAIN_PULL_REQUEST_OPTIONAL_KEYS:
        return False
    scalar_values = {key: parameters[key] for key in MAIN_PULL_REQUEST_PARAMETER_VALUES}
    if not _matches_exact_typed_values(scalar_values, MAIN_PULL_REQUEST_PARAMETER_VALUES):
        return False
    present_optional = {
        key: parameters[key] for key in MAIN_PULL_REQUEST_OPTIONAL_VALUES if key in parameters
    }
    expected_optional = {key: MAIN_PULL_REQUEST_OPTIONAL_VALUES[key] for key in present_optional}
    if not _matches_exact_typed_values(present_optional, expected_optional):
        return False
    approvals = parameters["required_approving_review_count"]
    if type(approvals) is not int or approvals not in MAIN_PULL_REQUEST_APPROVAL_COUNTS:
        return False
    merge_methods = parameters["allowed_merge_methods"]
    if (
        not isinstance(merge_methods, list)
        or len(merge_methods) != len(SUPPORTED_MERGE_METHODS)
        or any(type(method) is not str for method in merge_methods)
        or set(merge_methods) != SUPPORTED_MERGE_METHODS
    ):
        return False
    required_reviewers = parameters["required_reviewers"]
    if type(required_reviewers) is not list or required_reviewers != []:
        return False
    if "dismissal_restriction" not in parameters:
        return True
    dismissal = parameters["dismissal_restriction"]
    return isinstance(dismissal, dict) and _matches_exact_typed_values(
        dismissal, NEUTRAL_DISMISSAL_RESTRICTION
    )


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gh_api(path: str) -> Any | None:
    """Return a parsed ``gh api`` response, or None when the probe cannot be proven."""
    try:
        completed = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None


def _api_evidence(url: str, reason: str) -> JsonDict:
    return {
        "status": "verified",
        "source": "api",
        "url": url,
        "verified_at": _now(),
        "reason": reason,
    }


def probe_tag_ruleset(repo: str) -> JsonDict | None:
    """Prove an active tag ruleset restricting create/update/delete/non-fast-forward."""
    rulesets = _gh_api(f"repos/{repo}/rulesets")
    if not isinstance(rulesets, list):
        return None
    match = next(
        (
            item
            for item in rulesets
            if item.get("name") == RULESET_NAME and item.get("target") == "tag"
        ),
        None,
    )
    if match is None:
        return None
    detail = _gh_api(f"repos/{repo}/rulesets/{match['id']}")
    if not isinstance(detail, dict):
        return None
    rules = {rule.get("type") for rule in detail.get("rules", [])}
    bypass = [
        f"{actor.get('actor_type')}:{actor.get('actor_id')}"
        for actor in detail.get("bypass_actors", [])
    ]
    if detail.get("enforcement") != "active" or not REQUIRED_RULES <= rules or not bypass:
        return None
    return {
        "active": True,
        "restricts_creation": "creation" in rules,
        "restricts_update": "update" in rules,
        "restricts_deletion": "deletion" in rules,
        "restricts_non_fast_forward": "non_fast_forward" in rules,
        "bypass_actors": bypass,
        "evidence": _api_evidence(
            f"https://github.com/{repo}/settings/rules/{match['id']}",
            "Active tag ruleset probed via the repository rulesets API.",
        ),
    }


def probe_main_branch_ruleset(repo: str) -> JsonDict | None:
    """Prove the exact active main-branch policy for the trusted builder."""
    rulesets = _gh_api(f"repos/{repo}/rulesets")
    if not isinstance(rulesets, list):
        return None
    matches = [
        item
        for item in rulesets
        if isinstance(item, dict)
        and item.get("name") == MAIN_RULESET_NAME
        and item.get("target") == "branch"
    ]
    if len(matches) != 1:
        return None
    ruleset_id = matches[0].get("id")
    if type(ruleset_id) is not int:
        return None
    detail = _gh_api(f"repos/{repo}/rulesets/{ruleset_id}")
    if not isinstance(detail, dict) or detail.get("enforcement") != "active":
        return None
    conditions = detail.get("conditions")
    if not isinstance(conditions, dict) or conditions.keys() != {"ref_name"}:
        return None
    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, dict) or ref_name.keys() != {"include", "exclude"}:
        return None
    if ref_name.get("include") != [MAIN_BRANCH_REF] or ref_name.get("exclude") != []:
        return None
    if detail.get("bypass_actors") != []:
        return None
    rules = detail.get("rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        return None
    rule_types: list[str] = []
    for rule in rules:
        rule_type = rule.get("type")
        if type(rule_type) is not str:
            return None
        rule_types.append(rule_type)
    if len(rule_types) != len(MAIN_BRANCH_RULE_TYPES) or set(rule_types) != MAIN_BRANCH_RULE_TYPES:
        return None
    deletion = [rule for rule in rules if rule.get("type") == "deletion"]
    non_fast_forward = [rule for rule in rules if rule.get("type") == "non_fast_forward"]
    pull_requests = [rule for rule in rules if rule.get("type") == "pull_request"]
    if len(deletion) != 1 or len(non_fast_forward) != 1 or len(pull_requests) != 1:
        return None
    if deletion[0].keys() != {"type"} or non_fast_forward[0].keys() != {"type"}:
        return None
    if pull_requests[0].keys() != {"type", "parameters"}:
        return None
    parameters = pull_requests[0].get("parameters")
    if not isinstance(parameters, dict) or not _matches_neutral_pull_request_parameters(parameters):
        return None
    return {
        "active": True,
        "targets_main": True,
        "requires_pull_request": True,
        "required_approving_review_count": parameters["required_approving_review_count"],
        "blocks_force_pushes": True,
        "blocks_deletions": True,
        "bypass_actors": [],
        "evidence": _api_evidence(
            f"https://github.com/{repo}/settings/rules/{ruleset_id}",
            "Exact active main branch ruleset probed via the repository rulesets API.",
        ),
    }


def probe_release_environment(repo: str) -> JsonDict | None:
    """Prove a protected release environment restricted to exact semantic tags."""
    environment = _gh_api(f"repos/{repo}/environments/{RELEASE_ENVIRONMENT}")
    if not isinstance(environment, dict):
        return None
    reviewers: list[str] = []
    for rule in environment.get("protection_rules", []):
        if rule.get("type") != "required_reviewers":
            continue
        for entry in rule.get("reviewers", []):
            login = entry.get("reviewer", {}).get("login")
            if login:
                reviewers.append(login)
    policies = _gh_api(
        f"repos/{repo}/environments/{RELEASE_ENVIRONMENT}/deployment-branch-policies"
    )
    if not isinstance(policies, dict):
        return None
    entries = policies.get("branch_policies", [])
    exact_tag_only = bool(entries) and all(
        entry.get("type") == "tag" and entry.get("name") == EXACT_TAG_POLICY for entry in entries
    )
    if not reviewers or not exact_tag_only:
        return None
    return {
        "protected": True,
        "exact_tag_only": True,
        "required_reviewers": reviewers,
        "evidence": _api_evidence(
            f"https://github.com/{repo}/settings/environments",
            "Required reviewers and an exact tag deployment policy probed via the environments API.",
        ),
    }


def probe_immutable_releases(repo: str) -> JsonDict | None:
    """Prove immutable releases are enabled."""
    payload = _gh_api(f"repos/{repo}/immutable-releases")
    if not isinstance(payload, dict) or payload.get("enabled") is not True:
        return None
    return {
        "enabled": True,
        "evidence": _api_evidence(
            f"https://github.com/{repo}/settings",
            "Immutable releases probed via the repository immutable-releases API.",
        ),
    }


def _anonymous_manifest_status(repo: str) -> int:
    """Return the HTTP status of an unauthenticated GHCR manifest read."""
    token_url = f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull"
    try:
        with urllib.request.urlopen(token_url, timeout=30) as response:
            token = json.loads(response.read()).get("token")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return 0
    if not token:
        return 0
    request = urllib.request.Request(
        f"https://ghcr.io/v2/{repo}/manifests/{BOOTSTRAP_TAG}",
        headers={"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0


# The four hard controls this App's permissions (`metadata: read`, `administration:
# read`) genuinely cannot prove -- see the module docstring for exactly why each one
# needs a permission this App does not declare, or (for the standing-PAT check) has no
# automatable form at all. A row that verifies every OTHER control names these as
# `manual_evidence_required` rather than being marked `unavailable` for them.
MANUAL_EVIDENCE_REQUIRED: tuple[str, ...] = (
    "package linkage",
    "GITHUB_TOKEN publication",
    "standing package PAT absence",
    "retention",
)


def build_row(repo: str, role: Literal["trusted-builder", "backend"]) -> JsonDict:
    """Probe every control this App can prove; return ``partial`` or ``unavailable``.

    ``unavailable`` when any *provable* control (tag ruleset, main-branch ruleset for
    a trusted builder, release environment, immutable releases, anonymous package
    pull) fails to verify -- these stay hard fail-closed conditions, unchanged.
    ``partial`` when every provable control passes but the four package/retention
    controls named in ``MANUAL_EVIDENCE_REQUIRED`` remain unproven by this App's
    permissions (see the module docstring): they are named honestly rather than
    silently dropped or falsely claimed as either passing or failing.
    """
    probes = {
        "tag_ruleset": probe_tag_ruleset(repo),
        "release_environment": probe_release_environment(repo),
        "immutable_releases": probe_immutable_releases(repo),
    }
    if role == "trusted-builder":
        probes["main_branch_ruleset"] = probe_main_branch_ruleset(repo)
    missing = sorted(name for name, value in probes.items() if value is None)
    anonymous_pull_verified = _anonymous_manifest_status(repo) == 200
    if not anonymous_pull_verified:
        missing.append("anonymous package pull")

    if missing:
        reason = f"unproven hard controls: {', '.join(missing)}"
        return {
            "status": "unavailable",
            "repository": repo,
            "reason": reason,
            "evidence": {
                "status": "unavailable",
                "source": "api",
                "url": f"https://github.com/{repo}/pkgs/container/{repo.rsplit('/', 1)[-1]}",
                "verified_at": _now(),
                "reason": reason,
            },
        }

    row: JsonDict = {
        "status": "partial",
        "repository": repo,
        "role": role,
        "tag_ruleset": probes["tag_ruleset"],
        "release_environment": probes["release_environment"],
        "immutable_releases": probes["immutable_releases"],
        "anonymous_pull": anonymous_pull_verified,
        "manual_evidence_required": list(MANUAL_EVIDENCE_REQUIRED),
    }
    if role == "trusted-builder":
        row["main_branch_ruleset"] = probes["main_branch_ruleset"]
    return row


def build_ledger(repositories: set[str]) -> JsonDict:
    router = router_repository()
    return {
        "schema_version": 1,
        "reviewed_at": _now(),
        "repositories": {
            repo: build_row(
                repo,
                role="trusted-builder" if repo == router else "backend",
            )
            for repo in sorted(repositories)
        },
    }


def _report(ledger: JsonDict) -> list[str]:
    """Print a summary and return the rows that should fail the audit.

    Only ``unavailable`` rows are blockers: a control this App can observe failed to
    verify. ``partial`` rows are reported too, but are not blockers -- they name a
    permanent, known evidence gap (see ``MANUAL_EVIDENCE_REQUIRED``), not a live
    regression, and treating them as failures is exactly the bug this function used to
    have (every row was unconditionally unavailable; see issue #165).
    """
    rows = ledger["repositories"]
    blockers = [
        f"{repo}: {row['reason']}"
        for repo, row in sorted(rows.items())
        if row["status"] == "unavailable"
    ]
    partial = [
        f"{repo}: manual evidence required: {', '.join(row['manual_evidence_required'])}"
        for repo, row in sorted(rows.items())
        if row["status"] == "partial"
    ]
    verified = sum(1 for row in rows.values() if row["status"] == "verified")
    print(
        f"verified rows: {verified}/{len(rows)} "
        f"({len(partial)} partial, {len(blockers)} unavailable)"
    )
    for note in partial:
        print(f"  PARTIAL {note}")
    for blocker in blockers:
        print(f"  BLOCKER {blocker}")
    return blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="probe live controls and fail unless the checked-in ledger is compliant",
    )
    parser.add_argument("--servers", type=Path, default=Path("servers.yaml"))
    parser.add_argument("--ledger", type=Path, default=Path("ci/container-controls.json"))
    args = parser.parse_args(argv)

    repositories = expected_fleet_repositories(args.servers)

    if args.check:
        try:
            ledger = load_control_ledger(args.ledger)
            checked_in_warnings = require_compliant_controls(ledger, repositories)
        except ControlLedgerError as exc:
            print(f"control ledger is not release-ready: {exc}", file=sys.stderr)
            return 1
        for warning in checked_in_warnings:
            print(f"WARNING: checked-in ledger: {warning}", file=sys.stderr)
        live = build_ledger(repositories)
        blockers = _report(live)
        if blockers:
            print(
                "checked-in ledger claims compliance the live controls do not support",
                file=sys.stderr,
            )
            return 1
        print("live controls match the checked-in compliant ledger")
        return 0

    live = build_ledger(repositories)
    args.ledger.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {args.ledger}")
    blockers = _report(live)
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
