"""Offline release-contract gate for the packaged drift baseline."""

import hashlib
import json
from pathlib import Path

from genefoundry_router.devtools.fakes import load_manifest
from scripts.make_release_candidate import _load_application_releases
from scripts.snapshot_fleet import load_release_candidate_inventory

BASELINE = Path("genefoundry_router/data/fleet-baseline.json")
RELEASE_CANDIDATE = Path("ci/release-candidate-fleet.json")
RELEASE_INVENTORY = Path("ci/release-candidate-inventory.json")
APPLICATION_RELEASES = Path("ci/fleet-application-releases.json")

# fleet-2026-08-07-remediation. Updating these four hashes is the act of adopting a new reviewed fleet;
# they must only ever move in the same commit that regenerates the evidence set from a live
# fleet, never to make a failing check pass.
HISTORICAL_EVIDENCE_SHA256 = {
    APPLICATION_RELEASES: "a77df0bdf651861425c6cbd822bdc49a9d22b0750e8b9b417583aa03f46fbb26",
    RELEASE_INVENTORY: "496edf227550af9d17de3eb15dd2de4b6124e603948dd92fe097997b377445a1",
    RELEASE_CANDIDATE: "72f6bd99058e6083f804e55321466fc0dfedc818c281713d51850f26fe12448f",
    BASELINE: "72f6bd99058e6083f804e55321466fc0dfedc818c281713d51850f26fe12448f",
}


def test_historical_release_evidence_bytes_are_immutable() -> None:
    for path, expected in HISTORICAL_EVIDENCE_SHA256.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_historical_application_release_reader_preserves_semantic_objects() -> None:
    releases = json.loads(APPLICATION_RELEASES.read_text(encoding="utf-8"))

    assert _load_application_releases(APPLICATION_RELEASES) == releases


def test_baseline_is_bound_to_an_oci_release_candidate_inventory() -> None:
    """The packaged baseline is bound to an inventory that names backend releases.

    Every backend entry is bound to a signed release manifest, so the inventory must load
    cleanly. Router provenance is verified separately through its protected release manifest and
    Strato lock/runtime attestation, rather than recursively duplicated in candidate data.
    """
    baseline = load_manifest(BASELINE)
    candidate = load_manifest(RELEASE_CANDIDATE)
    inventory = json.loads(RELEASE_INVENTORY.read_text(encoding="utf-8"))

    loaded = load_release_candidate_inventory(RELEASE_INVENTORY)
    assert loaded == inventory
    assert set(inventory) == {"identity", "backends"}
    assert all(
        entry["application_release"]["image"]["digest"].startswith("sha256:")
        for entry in inventory["backends"].values()
    )

    assert candidate.snapshot_meta.source == "release-candidate"
    assert candidate.snapshot_meta.release_candidate == inventory
    assert set(inventory["backends"]) == set(candidate.backends)
    assert all(entry["endpoint"].startswith("https://") for entry in inventory["backends"].values())
    assert all(
        len(entry["application_release"]["source"]["revision"]) == 40
        for entry in inventory["backends"].values()
    )
    # The inventory attests the canonical raw wire catalog. The candidate and baseline
    # intentionally store the post-proxy catalog RuntimeDriftGuard hashes instead, so
    # compare only the provenance that is shared across those representations here.
    assert all(
        entry["application_release"]["version"] == candidate.backends[namespace].version
        for namespace, entry in inventory["backends"].items()
    )
    assert baseline.backends == candidate.backends
    assert baseline.snapshot_meta.release_candidate == inventory


def test_release_candidate_baseline_has_reviewed_tool_metadata() -> None:
    manifest = load_manifest(BASELINE)

    assert all(tool.annotations is not None for tool in manifest.backends["litvar"].tools)
    # LitVar v6 intentionally suppresses its optional output schemas under Tool-Surface
    # Budget v1 B3. The backend returns a dict response envelope, so FastMCP still emits
    # structuredContent; requiring an outputSchema here would contradict the fleet policy.
    assert all(tool.outputSchema is None for tool in manifest.backends["litvar"].tools)
    assert all(
        tool.annotations is not None and tool.annotations["readOnlyHint"] is True
        for tool in manifest.backends["vep"].tools
    )
    request = next(
        tool
        for tool in manifest.backends["metadome"].tools
        if tool.name == "request_tolerance_landscape"
    )
    assert request.annotations is not None
    assert {
        key: request.annotations[key]
        for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
    } == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
