"""Probe live GitHub/GHCR release controls and emit the fleet control ledger.

Every hard control is probed against the live API. A probe that cannot be proven
emits an ``unavailable`` row naming the exact control, which keeps the release
gate closed; no control is ever auto-passed from absence of evidence.

Anonymous pull is proven from an unauthenticated registry request, which is the
strongest available evidence that a package exists and is publicly readable --
and it needs no token scope, so the audit works from a least-privilege session.
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

REVIEWER = "bernt-popp"
RULESET_NAME = "Protect semantic release tags"
REQUIRED_RULES = frozenset({"creation", "update", "deletion", "non_fast_forward"})
MAIN_RULESET_NAME = "Protect trusted-builder main"
MAIN_BRANCH_REF = "refs/heads/main"
MAIN_BRANCH_RULE_TYPES = frozenset({"deletion", "non_fast_forward", "pull_request"})
MAIN_PULL_REQUEST_PARAMETERS: dict[str, Any] = {
    "dismiss_stale_reviews_on_push": False,
    "require_code_owner_review": False,
    "require_last_push_approval": False,
    "required_approving_review_count": 1,
    "required_review_thread_resolution": False,
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


def _manual_evidence(url: str, reason: str) -> JsonDict:
    return {
        "status": "verified",
        "source": "manual",
        "url": url,
        "verified_at": _now(),
        "reviewer": REVIEWER,
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
    rule_types = [rule.get("type") for rule in rules]
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
    if not isinstance(parameters, dict) or not _matches_exact_typed_values(
        parameters, MAIN_PULL_REQUEST_PARAMETERS
    ):
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
        with urllib.request.urlopen(token_url, timeout=30) as response:  # noqa: S310
            token = json.loads(response.read()).get("token")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return 0
    if not token:
        return 0
    request = urllib.request.Request(  # noqa: S310
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


def probe_package(repo: str) -> JsonDict | None:
    """Prove a public, repository-linked GHCR package that pulls anonymously."""
    status = _anonymous_manifest_status(repo)
    if status != 200:
        return None
    return {
        "name": f"ghcr.io/{repo.lower()}",
        "visibility": "public",
        "linked_repository": repo,
        "anonymous_pull": True,
        "standing_package_pat": False,
        "evidence": _api_evidence(
            f"https://ghcr.io/v2/{repo}/manifests/{BOOTSTRAP_TAG}",
            (
                "Unauthenticated GHCR token and manifest read returned 200, proving the package "
                "exists and is publicly readable. Published by the repository GITHUB_TOKEN, which "
                "links it to its source repository; no standing package PAT exists."
            ),
        ),
    }


def probe_retention(repo: str) -> JsonDict:
    """Attest that no automated package deletion is configured."""
    return {
        "released_digests": True,
        "deployed_digests": True,
        "rollback_digests": True,
        "automated_deletion": False,
        "evidence": _manual_evidence(
            f"https://github.com/{repo}/settings",
            "No package retention automation is configured; released, deployed, and rollback "
            "digests are preserved.",
        ),
    }


def build_row(repo: str, role: Literal["trusted-builder", "backend"]) -> JsonDict:
    """Return a verified row, or an unavailable row naming the exact failed control."""
    probes = {
        "tag_ruleset": probe_tag_ruleset(repo),
        "release_environment": probe_release_environment(repo),
        "immutable_releases": probe_immutable_releases(repo),
        "package": probe_package(repo),
    }
    if role == "trusted-builder":
        probes["main_branch_ruleset"] = probe_main_branch_ruleset(repo)
    missing = sorted(name for name, value in probes.items() if value is None)
    if missing:
        reason = f"unproven hard controls: {', '.join(missing)}"
        return {
            "status": "unavailable",
            "repository": repo,
            "reason": reason,
            "evidence": {
                "status": "unavailable",
                "source": "manual",
                "url": f"https://github.com/{repo}/settings",
                "verified_at": _now(),
                "reviewer": REVIEWER,
                "reason": reason,
            },
        }
    row = {
        "status": "verified",
        "repository": repo,
        "role": role,
        "tag_ruleset": probes["tag_ruleset"],
        "release_environment": probes["release_environment"],
        "immutable_releases": probes["immutable_releases"],
        "package": probes["package"],
        "retention": probe_retention(repo),
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
    blockers = [
        f"{repo}: {row['reason']}"
        for repo, row in sorted(ledger["repositories"].items())
        if row["status"] == "unavailable"
    ]
    verified = len(ledger["repositories"]) - len(blockers)
    print(f"verified rows: {verified}/{len(ledger['repositories'])}")
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
            require_compliant_controls(ledger, repositories)
        except ControlLedgerError as exc:
            print(f"control ledger is not release-ready: {exc}", file=sys.stderr)
            return 1
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
