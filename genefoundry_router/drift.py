"""Tool-definition drift detection — a rug-pull / tool-poisoning tripwire.

The MCP spec covers auth/transport but does NOT mandate tool-definition integrity, so a
gateway must do it: a backend that changes a tool's description or schema *after* it was
reviewed is the canonical "rug pull" (and the channel for tool-poisoning instructions).
This module fingerprints each tool's security-relevant definition (name + description +
inputSchema) and diffs a live snapshot against a reviewed, pinned manifest
(``scripts/snapshot_fleet.py`` → ``tests/fixtures/fleet_manifest.json``). Surface any
drift loudly; treat ``changed`` as the highest-signal event.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from genefoundry_router.devtools.fakes import Manifest


def tool_fingerprint(
    name: str, description: str | None, input_schema: dict[str, Any] | None
) -> str:
    """Stable SHA-256 over the security-relevant parts of a tool definition."""
    payload = json.dumps(
        {"name": name, "description": description or "", "inputSchema": input_schema or {}},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DriftReport:
    """Tools present-but-new (``added``), gone (``removed``), or redefined (``changed``)."""

    added: list[str]
    removed: list[str]
    changed: list[str]

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def detect_drift(current: dict[str, str], pinned: dict[str, str]) -> DriftReport:
    """Diff two maps of ``tool_key -> fingerprint``."""
    cur_keys, pin_keys = set(current), set(pinned)
    added = sorted(cur_keys - pin_keys)
    removed = sorted(pin_keys - cur_keys)
    changed = sorted(k for k in (cur_keys & pin_keys) if current[k] != pinned[k])
    return DriftReport(added=added, removed=removed, changed=changed)


def manifest_fingerprints(manifest: Manifest) -> dict[str, str]:
    """Map ``"<namespace>/<leaf>" -> fingerprint`` for every tool in a fleet manifest."""
    out: dict[str, str] = {}
    for namespace, backend in manifest.backends.items():
        for tool in backend.tools:
            out[f"{namespace}/{tool.name}"] = tool_fingerprint(
                tool.name, tool.description, tool.inputSchema
            )
    return out


def diff_manifests(pinned: Manifest, live: Manifest) -> DriftReport:
    """Detect drift between a reviewed pinned manifest and a freshly-snapshotted live one."""
    return detect_drift(manifest_fingerprints(live), manifest_fingerprints(pinned))
