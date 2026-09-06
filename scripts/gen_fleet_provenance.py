#!/usr/bin/env python
"""Generate the router's canonical fleet provenance artifact.

Aggregates backend definitions (servers.yaml), repository metadata (fleet-metadata.yaml),
application release manifests (ci/fleet-application-releases.json), reviewed baseline tools
(genefoundry_router/data/fleet-baseline.json), and database volume/snapshot attestations into
a single machine-readable inventory of real, up-to-date provenance across all 22 backends
and 285 tools.

Outputs:
  - ci/fleet-provenance.json
  - genefoundry_router/data/fleet-provenance.json

Usage:
  python scripts/gen_fleet_provenance.py          # regenerate provenance files
  python scripts/gen_fleet_provenance.py --check  # assert checked-in files match
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from genefoundry_router.config import load_registry
from genefoundry_router.registry import qualified_name

ROOT = Path(__file__).resolve().parent.parent
SERVERS = ROOT / "servers.yaml"
METADATA = ROOT / "fleet-metadata.yaml"
APP_RELEASES = ROOT / "ci/fleet-application-releases.json"
BASELINE = ROOT / "genefoundry_router/data/fleet-baseline.json"
CI_TARGET = ROOT / "ci/fleet-provenance.json"
PKG_TARGET = ROOT / "genefoundry_router/data/fleet-provenance.json"


def derive_database_provenance(release_entry: dict[str, Any]) -> dict[str, Any]:
    """Derive database provenance strictly from the attested release manifest."""
    data_req = release_entry.get("data_requirements", {})
    mode = data_req.get("mode", "none")
    release_tag = data_req.get("release_tag")
    digest = data_req.get("digest")
    schema_compat = data_req.get("schema_compatibility", [])
    contract = data_req.get("data_identity_contract")
    updated_at = release_entry.get("security_evidence", {}).get("database_updated_at")

    if mode in ("external-reference", "restored-database"):
        return {
            "mode": mode,
            "status": "attested-reference" if digest else "unattested",
            "release_tag": release_tag,
            "asset_sha256": digest,
            "schema_compatibility": schema_compat,
            "data_identity_contract": contract,
            "updated_at": updated_at,
        }
    if mode == "upstream-live":
        return {
            "mode": "upstream-live",
            "status": "live-upstream",
            "release_tag": release_tag,
            "asset_sha256": digest,
            "schema_compatibility": schema_compat,
            "updated_at": updated_at,
        }
    return {
        "mode": "none",
        "status": "unhosted",
        "release_tag": None,
        "asset_sha256": None,
        "schema_compatibility": [],
        "updated_at": None,
    }


def render() -> dict[str, Any]:
    metadata = yaml.safe_load(METADATA.read_text(encoding="utf-8"))
    backends = [b for b in load_registry(SERVERS, os.environ) if b.enabled]
    baseline_backends = json.loads(BASELINE.read_text(encoding="utf-8"))["backends"]
    app_releases = json.loads(APP_RELEASES.read_text(encoding="utf-8"))["backends"]

    backend_entries: list[dict[str, Any]] = []
    total_tools = 0

    for b in sorted(backends, key=lambda x: x.namespace):
        baseline_entry = baseline_backends.get(b.namespace)
        if baseline_entry is None:
            raise SystemExit(
                f"error: backend {b.namespace!r} missing from baseline {BASELINE.name}"
            )

        release_entry = app_releases.get(b.namespace, {})
        data_prov = derive_database_provenance(release_entry)

        raw_tools = baseline_entry.get("tools", [])
        total_tools += len(raw_tools)

        processed_tools = []
        for t in raw_tools:
            leaf_name = t["name"]
            fed_name = qualified_name(b.namespace, leaf_name)
            tool_entry: dict[str, Any] = {
                "name": leaf_name,
                "federated_name": fed_name,
                "description": t.get("description", ""),
                "inputSchema": t.get("inputSchema") or {},
                "tags": t.get("tags", []),
                "annotations": t.get("annotations", {}),
            }
            if "outputSchema" in t:
                tool_entry["outputSchema"] = t["outputSchema"]
            processed_tools.append(tool_entry)

        source_info = release_entry.get("source", {})
        image_info = release_entry.get("image", {})
        mcp_info = release_entry.get("mcp", {})

        backend_entries.append(
            {
                "namespace": b.namespace,
                "name": b.name,
                "domain": b.description,
                "source_name": b.source_name,
                "source_url": b.source_url,
                "repository": b.repo,
                "repository_url": f"https://github.com/{b.repo}" if b.repo else None,
                "tags": b.tags,
                "entrypoints": b.entrypoints,
                "tools_count": len(processed_tools),
                "release": {
                    "version": release_entry.get("version"),
                    "tag": source_info.get("tag"),
                    "commit": source_info.get("revision"),
                    "image": (
                        f"{image_info.get('name')}@{image_info.get('digest')}"
                        if image_info.get("name") and image_info.get("digest")
                        else None
                    ),
                    "definitions_sha256": mcp_info.get("definitions_sha256"),
                },
                "database_provenance": data_prov,
                "tools": processed_tools,
            }
        )

    total_repos = len(metadata.get("backends", {})) + 1
    return {
        "schema_version": 1,
        "fleet": {
            "total_repositories": total_repos,
            "total_backends": len(backend_entries),
            "total_tools": total_tools,
            "homepage": metadata.get("homepage", "https://genefoundry.org"),
            "universal_topics": metadata.get("universal_topics", []),
        },
        "router": {
            "repository": "berntpopp/genefoundry-router",
            "repository_url": "https://github.com/berntpopp/genefoundry-router",
            "mcp_endpoint": "https://genefoundry.org/mcp",
            "health_endpoint": "https://genefoundry.org/health",
            "provenance_endpoint": "https://genefoundry.org/provenance",
        },
        "backends": backend_entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail if generated provenance is stale"
    )
    args = parser.parse_args()

    payload = render()
    rendered_json = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    if args.check:
        for target in (CI_TARGET, PKG_TARGET):
            if not target.exists():
                print(f"error: {target} does not exist. Run `make provenance`.", file=sys.stderr)
                return 1
            if target.read_text(encoding="utf-8") != rendered_json:
                print(f"error: {target} is stale. Run `make provenance`.", file=sys.stderr)
                return 1
        print("fleet provenance: current")
        return 0

    CI_TARGET.write_text(rendered_json, encoding="utf-8")
    PKG_TARGET.write_text(rendered_json, encoding="utf-8")
    print(f"wrote {CI_TARGET.relative_to(ROOT)} and {PKG_TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
