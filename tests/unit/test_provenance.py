"""Tests for the canonical fleet provenance loader and HTTP endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from genefoundry_router.provenance import load_fleet_provenance, register_provenance

ROOT = Path(__file__).resolve().parent.parent.parent
APP_RELEASES = ROOT / "ci/fleet-application-releases.json"


def test_load_fleet_provenance_structure() -> None:
    data = load_fleet_provenance()
    assert data["schema_version"] == 1
    fleet = data["fleet"]
    assert fleet["total_backends"] == 22
    assert fleet["total_repositories"] == 23
    assert fleet["total_tools"] == 285

    router = data["router"]
    assert router["repository"] == "berntpopp/genefoundry-router"
    assert "https://genefoundry.org/provenance" in router["provenance_endpoint"]

    backends = {b["namespace"]: b for b in data["backends"]}
    assert len(backends) == 22

    # Check clinpgx entry
    assert "clinpgx" in backends
    clinpgx = backends["clinpgx"]
    assert clinpgx["tools_count"] == 13
    assert len(clinpgx["tools"]) == 13
    assert clinpgx["release"]["tag"] == "v0.1.0"
    assert clinpgx["database_provenance"]["mode"] == "none"

    # Cross-verify EVERY backend against ci/fleet-application-releases.json
    manifest = json.loads(APP_RELEASES.read_text(encoding="utf-8"))["backends"]
    for ns, b in backends.items():
        assert ns in manifest, f"Backend {ns} missing from release manifest"
        m_entry = manifest[ns]
        m_req = m_entry.get("data_requirements", {})
        prov = b["database_provenance"]

        assert prov["mode"] == m_req.get("mode", "none")
        assert prov["release_tag"] == m_req.get("release_tag")
        assert prov["asset_sha256"] == m_req.get("digest")
        assert prov["schema_compatibility"] == m_req.get("schema_compatibility", [])

        # Check tools have valid qualified federated names
        for t in b["tools"]:
            assert t["federated_name"] == f"{ns}_{t['name']}"
            assert len(t["federated_name"]) <= 64


def test_provenance_immutability() -> None:
    data1 = load_fleet_provenance()
    data1["fleet"]["total_backends"] = 9999
    data1["backends"].clear()

    data2 = load_fleet_provenance()
    assert data2["fleet"]["total_backends"] == 22
    assert len(data2["backends"]) == 22


def test_provenance_http_endpoints() -> None:
    app = FastAPI()
    register_provenance(app)
    client = TestClient(app)

    for path in ("/provenance", "/api/fleet/provenance"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert resp.headers["x-genefoundry-fleet-backends"] == "22"
        assert resp.headers["x-genefoundry-fleet-tools"] == "285"
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "ETag" in resp.headers
        etag = resp.headers["etag"]

        body = resp.json()
        assert body["fleet"]["total_backends"] == 22
        assert body["fleet"]["total_tools"] == 285

        # Test HEAD request
        head_resp = client.head(path)
        assert head_resp.status_code == 200
        assert head_resp.headers["etag"] == etag
        assert len(head_resp.content) == 0

        # Test If-None-Match conditional request
        cond_resp = client.get(path, headers={"If-None-Match": etag})
        assert cond_resp.status_code == 304
        assert cond_resp.headers["etag"] == etag
        assert len(cond_resp.content) == 0
