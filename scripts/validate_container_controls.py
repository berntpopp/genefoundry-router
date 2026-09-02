"""Validate a live container-control ledger against the exact router fleet."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from genefoundry_router.release.controls import (
    ControlLedgerError,
    expected_fleet_repositories,
    load_control_ledger,
    oldest_evidence_age,
    require_compliant_controls,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path)
    args = parser.parse_args(argv)

    try:
        repositories = expected_fleet_repositories(ROOT / "servers.yaml")
        ledger = load_control_ledger(args.ledger)
        warnings = require_compliant_controls(ledger, repositories)
    except ControlLedgerError as exc:
        print(f"control ledger is not compliant: {exc}", file=sys.stderr)
        return 1

    # `partial` rows (see PartialRepositoryControls) surface here as warnings, not a
    # hard failure: a bounded, honestly-named evidence gap accepted on purpose is not
    # silence, but it also is not a reason to fail a release gate that never claimed to
    # prove those controls in the first place.
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    age_days = oldest_evidence_age(ledger).days
    print(
        f"validated {len(ledger.repositories)} compliant repository controls "
        f"(oldest evidence is {age_days} day(s) old)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
