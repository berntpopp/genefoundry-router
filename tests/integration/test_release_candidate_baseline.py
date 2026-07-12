"""Offline release-contract gate for the packaged drift baseline."""

from pathlib import Path

from genefoundry_router.devtools.fakes import load_manifest

BASELINE = Path("genefoundry_router/data/fleet-baseline.json")
RELEASE_CANDIDATE = Path("ci/release-candidate-fleet.json")


def test_packaged_baseline_matches_reviewed_release_candidate_full_definitions() -> None:
    baseline = load_manifest(BASELINE)
    candidate = load_manifest(RELEASE_CANDIDATE)

    assert candidate.snapshot_meta.source == "release-candidate"
    assert candidate.snapshot_meta.release_candidate
    assert baseline.backends == candidate.backends


def test_release_candidate_baseline_has_corrected_tool_metadata() -> None:
    manifest = load_manifest(BASELINE)

    assert all(tool.annotations is not None for tool in manifest.backends["litvar"].tools)
    assert all(tool.outputSchema is not None for tool in manifest.backends["litvar"].tools)
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
