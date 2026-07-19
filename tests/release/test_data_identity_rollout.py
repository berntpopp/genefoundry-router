"""Coverage and evidence rules for staged runtime data-identity adoption."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rollout_ledger_exactly_covers_current_data_bound_repositories() -> None:
    releases = json.loads((ROOT / "ci/fleet-application-releases.json").read_text())
    expected = {
        value["repository"].split("/", 1)[1]
        for value in releases["backends"].values()
        if value["mcp"]["definition_contract"] == "data-bound"
    }
    rollout = json.loads((ROOT / "ci/data-identity-rollout-v1.json").read_text())

    assert set(rollout) == {"schema_version", "repositories"}
    assert rollout["schema_version"] == 1
    assert set(rollout["repositories"]) == expected


def test_rollout_evidence_exists_only_for_runtime_v1_adopters() -> None:
    rollout = json.loads((ROOT / "ci/data-identity-rollout-v1.json").read_text())

    for repository, row in rollout["repositories"].items():
        assert set(row) == {
            "status",
            "verified_commit",
            "observed_identity_sha256",
            "evidence",
        }
        assert row["status"] in {"unadopted", "runtime-v1"}
        if row["status"] == "unadopted":
            assert row == {
                "status": "unadopted",
                "verified_commit": None,
                "observed_identity_sha256": None,
                "evidence": None,
            }, repository
        else:
            assert re.fullmatch(r"[0-9a-f]{40}", row["verified_commit"])
            assert re.fullmatch(r"[0-9a-f]{64}", row["observed_identity_sha256"])
            assert isinstance(row["evidence"], str) and row["evidence"]
