"""Tool-definition drift detection — a rug-pull / tool-poisoning tripwire.

The MCP spec covers auth/transport but does NOT mandate tool-definition integrity, so a
gateway must do it: a backend that changes a tool's description or schema *after* it was
reviewed is the canonical "rug pull" (and the channel for tool-poisoning instructions).
This module fingerprints each complete security-relevant tool definition and diffs a live
snapshot against the reviewed baseline packaged with the router. Surface any drift loudly;
the runtime/CI pin is ``genefoundry_router/data/fleet-baseline.json`` while
``tests/fixtures/fleet_manifest.json`` remains the offline fake-fleet fixture. Treat
``changed`` as the highest-signal event.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from genefoundry_router.devtools.fakes import Manifest


class ToolDefinition(BaseModel):
    """Complete MCP definition whose mutation can alter model or execution behavior."""

    name: str
    description: str = ""
    inputSchema: dict[str, Any] = Field(default_factory=dict)  # noqa: N815
    outputSchema: dict[str, Any] | None = None  # noqa: N815
    annotations: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None


def _canonical_json_schema(value: Any) -> Any:
    """Canonicalize a JSON Schema's ``required`` so equivalent schemas hash alike.

    ``required`` is an unordered set of property names, so neither its order nor an empty
    list carries meaning -- but SHA-256 sees both. The two sides of the tripwire read a
    tool from different places and disagree on exactly this key:

    * FastMCP's server-side ``Tool.parameters`` emits ``"required": []`` for a tool with
      no required arguments and orders the names as declared; the MCP wire schema a client
      sees -- what ``snapshot_fleet`` captures into the reviewed baseline -- omits the
      empty key and can order the names differently.

    Without this, every no-required-argument tool and every multi-required-argument tool
    fingerprinted differently at runtime than in the pin, so ``GF_DRIFT_MODE=enforce``
    could never start. CI stayed green throughout because ``drift`` compares a wire
    snapshot against a wire baseline and never sees the server-side representation.

    Representation-only: dropping an *empty* list and sorting a set of names cannot mask a
    real change, since gaining or losing a required argument still changes the sorted list.
    """
    if isinstance(value, dict):
        canonical: dict[str, Any] = {}
        for key, item in value.items():
            if key == "required" and isinstance(item, list):
                if not item:
                    continue  # absent == empty, per JSON Schema
                if all(isinstance(name, str) for name in item):
                    canonical[key] = sorted(item)
                    continue
            canonical[key] = _canonical_json_schema(item)
        return canonical
    if isinstance(value, list):
        return [_canonical_json_schema(item) for item in value]
    return value


def tool_fingerprint(tool: ToolDefinition) -> str:
    """Stable SHA-256 over a complete security-relevant tool definition."""
    payload = tool.model_dump(mode="json", by_alias=True, exclude_none=False)
    for schema_key in ("inputSchema", "outputSchema"):
        payload[schema_key] = _canonical_json_schema(payload[schema_key])
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
    """Map qualified normalized names to reviewed definition fingerprints."""
    return {
        f"{namespace}_{tool.name}": tool_fingerprint(
            ToolDefinition(
                name=f"{namespace}_{tool.name}",
                description=tool.description,
                inputSchema=tool.inputSchema,
                outputSchema=tool.outputSchema,
                annotations=tool.annotations,
                execution=tool.execution,
            )
        )
        for namespace, backend in manifest.backends.items()
        for tool in backend.tools
    }


def diff_manifests(pinned: Manifest, live: Manifest) -> DriftReport:
    """Detect drift between a reviewed pinned manifest and a freshly-snapshotted live one."""
    return detect_drift(manifest_fingerprints(live), manifest_fingerprints(pinned))
