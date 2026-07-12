"""The packaged fleet baseline is the live manifest runtime and CI pin against.

Kept in lockstep with servers.yaml: it must be a *live* snapshot (not the local test stub),
cover exactly the enabled backends, and carry tools for each — so a partial or stale baseline
can't silently ship and read as drift/unreachable on the first scheduled run.
"""

import os
from pathlib import Path

from genefoundry_router.config import load_registry
from genefoundry_router.devtools.fakes import load_manifest

BASELINE = Path("genefoundry_router/data/fleet-baseline.json")


def test_baseline_is_a_reviewed_release_candidate_snapshot():
    manifest = load_manifest(BASELINE)
    assert manifest.snapshot_meta.source == "release-candidate", (
        "drift baseline must be a reviewed release-candidate snapshot "
        "(make snapshot-baseline RELEASE_CANDIDATE=<identity>), not the local fake-fleet stub"
    )
    assert manifest.snapshot_meta.release_candidate


def test_baseline_covers_exactly_the_enabled_backends():
    enabled = {b.namespace for b in load_registry("servers.yaml", os.environ) if b.enabled}
    pinned = set(load_manifest(BASELINE).backends)

    # Lockstep contract: exactly the enabled fleet — no more, no less. A missing backend means
    # an undeployed/unreachable one slipped into the pin (would read as exit-2 forever); an extra
    # means a disabled/removed backend lingers in the pin (would read as REMOVED drift).
    missing = enabled - pinned
    extra = pinned - enabled
    assert not missing, (
        f"baseline missing enabled backends — deploy + `make snapshot-baseline`: {sorted(missing)}"
    )
    assert not extra, (
        f"baseline pins backends not enabled in servers.yaml — re-pin: {sorted(extra)}"
    )


def test_baseline_backends_each_have_tools():
    manifest = load_manifest(BASELINE)
    empty = sorted(ns for ns, spec in manifest.backends.items() if not spec.tools)
    assert not empty, f"baseline backends snapshotted with zero tools (capture error): {empty}"
