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

# fleet-2026-11-26: strict capture of 30 verified signed backend releases (all 30 re-released for
# Response-Envelope v1.2; metadome and pubtator tool renames). Previous: fleet-2026-11-22b (ucsc,
# gtex, stringdb, clinvar, spliceai, gnomad, vep, mondo re-released); fleet-2026-11-22 (autopvs1, civic,
# clingen, clinpgx, clinvar, genereviews, gnomad, litvar, mavedb, moalmanac, pubtator, spliceai,
# vep re-released); fleet-2026-11-20b (clinvar, vep, mondo, hpo, ucsc re-released; civic,
# cbioportal, opentargets, moalmanac added); fleet-2026-11-20
# (nine re-released for
# foreign-identifier handling and batch pointers: hgnc, hpo, clingen, orphanet, uniprot, gnomad,
# litvar, stringdb, genereviews).
# Updating these four hashes adopts the reviewed fleet atomically with its captured evidence;
# they must never move independently merely to make a failing check pass.
HISTORICAL_EVIDENCE_SHA256 = {
    APPLICATION_RELEASES: "dac0ca9d501f8c911d588fe797dba0c2b625a178690e39789f2de910fd427769",
    RELEASE_INVENTORY: "471dbf000e3a5dac9a2552aae4d3b96bf2c87c5c62e6dc09c9f18c0ad31ae68a",
    RELEASE_CANDIDATE: "9ea78f6adf0c1df81931496717a9d25399d464f1d8ecc5108732ed8868ffaed9",
    BASELINE: "9ea78f6adf0c1df81931496717a9d25399d464f1d8ecc5108732ed8868ffaed9",
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
        if tool.name == "submit_tolerance_landscape"
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
