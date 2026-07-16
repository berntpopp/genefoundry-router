"""Offline release-contract gate for the packaged drift baseline."""

import json
from pathlib import Path

from genefoundry_router.devtools.fakes import load_manifest
from scripts.snapshot_fleet import load_release_candidate_inventory

BASELINE = Path("genefoundry_router/data/fleet-baseline.json")
RELEASE_CANDIDATE = Path("ci/release-candidate-fleet.json")
RELEASE_INVENTORY = Path("ci/release-candidate-inventory.json")


def test_baseline_is_bound_to_an_oci_release_candidate_inventory() -> None:
    """The packaged baseline is bound to an inventory that names an application release.

    This previously asserted the *inverse*: the pre-OCI inventory carried no router
    application release, so loading it raised. The fleet now ships attested GHCR images and
    every entry is bound to a signed release manifest, so the inventory must load cleanly --
    a baseline that cannot name what produced it is exactly what the drift pin exists to stop.
    """
    baseline = load_manifest(BASELINE)
    candidate = load_manifest(RELEASE_CANDIDATE)
    inventory = json.loads(RELEASE_INVENTORY.read_text(encoding="utf-8"))

    loaded = load_release_candidate_inventory(RELEASE_INVENTORY)
    assert loaded["identity"] == inventory["identity"]
    assert inventory["router"]["image"]["digest"].startswith("sha256:")
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
