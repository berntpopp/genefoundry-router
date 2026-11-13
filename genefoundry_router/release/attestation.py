"""Owner attestation that closes the control-audit evidence gap (issue #155).

The control-audit App cannot observe package linkage, the publishing credential, the
absence of a standing package PAT, retention, or ruleset bypass actors (see
``scripts/audit_container_controls.py``). A reviewed, dated owner attestation committed
to ``ci/container-controls-attestation.json`` supplies those as ``source: "manual"``
evidence. It can only promote a row the audit already emitted as ``partial`` — every
observable control passed — so it never masks a control the App can see failing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from genefoundry_router.release.controls import (
    _MAX_CONTROL_EVIDENCE_SKEW,
    BYPASS_ACTORS_GAP,
    MAX_CONTROL_EVIDENCE_AGE,
    ControlLedgerError,
)
from genefoundry_router.release.models import Rfc3339Timestamp

_Actor = Annotated[str, Field(min_length=1, max_length=100)]


class RulesetBypassActors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # The tag ruleset must keep a bypass actor (release automation); main must have none.
    tag: Annotated[tuple[_Actor, ...], Field(min_length=1)]
    main: tuple[()]


class OwnerAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    # False until the owner has personally checked every statement; never promotes a row.
    confirmed: bool
    attested_by: Annotated[str, Field(min_length=1, max_length=100)]
    attested_at: Rfc3339Timestamp
    url: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    # manual-evidence item (exactly as the audit names it) -> what the owner asserts.
    controls: dict[
        Annotated[str, Field(min_length=1, max_length=200)],
        Annotated[str, Field(min_length=1, max_length=2000)],
    ]
    ruleset_bypass_actors: RulesetBypassActors


def load_attestation(path: Path) -> OwnerAttestation | None:
    """Return the committed attestation, or None when there is none."""
    if not path.exists():
        return None
    try:
        return OwnerAttestation.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise ControlLedgerError(f"invalid owner attestation {path}: {exc}") from exc


def attestation_is_current(attestation: OwnerAttestation, now: datetime | None = None) -> bool:
    """Confirmed, not post-dated, and inside the same 90-day bound as all control evidence."""
    age = (now or datetime.now(UTC)) - attestation.attested_at
    return (
        attestation.confirmed
        and age >= -_MAX_CONTROL_EVIDENCE_SKEW
        and age <= MAX_CONTROL_EVIDENCE_AGE
    )


def promote_partial_row(
    row: dict[str, Any], attestation: OwnerAttestation | None, now: datetime | None = None
) -> dict[str, Any]:
    """Turn an audit ``partial`` row into ``verified`` when the attestation covers its gap.

    Anything else — an ``unavailable`` row, an unconfirmed/stale attestation, or one that
    does not name every gap item — is returned unchanged, so the gap stays visible.
    """
    if (
        row.get("status") != "partial"
        or attestation is None
        or not attestation_is_current(attestation, now)
        or not set(row["manual_evidence_required"]) <= set(attestation.controls)
    ):
        return row
    evidence = {
        "status": "verified",
        "source": "manual",
        "url": attestation.url,
        "verified_at": attestation.attested_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reviewer": attestation.attested_by,
        "reason": "Owner attestation (ci/container-controls-attestation.json, issue #155).",
    }
    promoted = {
        key: value
        for key, value in row.items()
        if key not in {"anonymous_pull", "manual_evidence_required"}
    }
    promoted["status"] = "verified"
    repository = row["repository"]
    promoted["package"] = {
        "name": f"ghcr.io/{repository.lower()}",
        "visibility": "public",
        "linked_repository": repository,
        "anonymous_pull": row["anonymous_pull"],  # observed by the audit, never attested
        "standing_package_pat": False,
        "github_token_publication": True,
        "evidence": evidence,
    }
    promoted["retention"] = {
        "released_digests": True,
        "deployed_digests": True,
        "rollback_digests": True,
        "automated_deletion": False,
        "evidence": evidence,
    }
    if BYPASS_ACTORS_GAP in row["manual_evidence_required"]:
        attested = {"tag_ruleset": attestation.ruleset_bypass_actors.tag, "main_branch_ruleset": ()}
        for key, actors in attested.items():
            rule = promoted.get(key)
            # Only fill what the App could not see; an observed value is never overwritten.
            if rule is not None and rule["bypass_actors"] is None:
                rule = {**rule, "bypass_actors": list(actors)}
                rule["evidence"] = {
                    **rule["evidence"],
                    "reason": f"{rule['evidence'].get('reason', '')} Bypass actors: "
                    f"owner attestation {attestation.url}".strip()[:500],
                }
                promoted[key] = rule
    return promoted


__all__ = [
    "OwnerAttestation",
    "attestation_is_current",
    "load_attestation",
    "promote_partial_row",
]
