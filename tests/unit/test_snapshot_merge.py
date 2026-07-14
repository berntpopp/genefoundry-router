import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from genefoundry_router.devtools.fakes import BackendSpec, ToolSpec, load_manifest
from scripts.make_release_candidate import _load_application_releases
from scripts.snapshot_fleet import (
    ReleaseCandidateCaptureError,
    _run,
    load_release_candidate_inventory,
    merge_backend,
    release_definitions_digest,
)


def _application_release(
    repository: str = "berntpopp/one-link",
    *,
    version: str = "1.0.0",
    definitions_sha256: str = "b" * 64,
) -> dict[str, object]:
    digest = f"sha256:{'a' * 64}"
    return {
        "schema_version": 1,
        "repository": repository,
        "version": version,
        "source": {"tag": f"v{version}", "revision": "d" * 40},
        "image": {
            "name": f"ghcr.io/{repository}",
            "digest": digest,
            "platforms": [{"platform": "linux/amd64", "digest": digest}],
        },
        "workflow": {
            "caller": f"{repository}/.github/workflows/container-release.yml",
            "standard": "berntpopp/genefoundry-router/.github/workflows/_container-release.yml",
            "standard_revision": "e" * 40,
        },
        "mcp": {
            "definitions_sha256": definitions_sha256,
            "capture_context_sha256": "f" * 64,
            "definition_contract": "data-independent",
        },
        "security_evidence": {
            "scanner": "trivy",
            "scanner_version": "0.66.0",
            "database_updated_at": "2026-07-13T10:30:00Z",
            "sbom_sha256": "1" * 64,
            "scanner_evidence_sha256": "2" * 64,
            "attestation_bundle_sha256": "3" * 64,
            "trusted_root_sha256": "4" * 64,
            "verification_sha256": "5" * 64,
        },
        "release_assets": {
            "image-manifest.json": digest,
            "sbom.spdx.json": f"sha256:{'1' * 64}",
            "mcp-definitions.json": f"sha256:{definitions_sha256}",
            "mcp-capture-context.json": f"sha256:{'f' * 64}",
            "trivy.json": f"sha256:{'2' * 64}",
            "attestation-bundle.json": f"sha256:{'3' * 64}",
            "trusted-root.json": f"sha256:{'4' * 64}",
            "verification.json": f"sha256:{'5' * 64}",
        },
        "data_requirements": {"mode": "none", "schema_compatibility": []},
    }


def _raw_tools() -> list[Any]:
    """The tools as the backend puts them on the wire.

    The release manifest attests the canonicalization of *this* payload, not of the drift
    baseline's ToolSpec projection -- so the capture must compare against this.
    """
    from mcp.types import Tool

    return [
        Tool(
            name="get_one",
            description="",
            inputSchema={"type": "object", "properties": {}},
        )
    ]


def _inventory(spec: BackendSpec) -> dict[str, object]:
    return {
        "identity": "release-1",
        "router": _application_release("berntpopp/genefoundry-router", version="0.6.4"),
        "backends": {
            "one": {
                "endpoint": "https://candidate.example/mcp",
                "application_release": _application_release(
                    definitions_sha256=release_definitions_digest(_raw_tools())
                ),
            }
        },
    }


def test_required_release_candidate_backend_cannot_retain_prior_snapshot():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    with pytest.raises(ReleaseCandidateCaptureError, match="required release-candidate backend"):
        merge_backend(prior, None, release_candidate=True)


def test_merge_prefers_new_when_present():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    fresh = BackendSpec(version="2.0.0", tools=[ToolSpec(name="get_y")])
    merged = merge_backend(prior, fresh, release_candidate=True)
    assert merged.version == "2.0.0"
    assert [t.name for t in merged.tools] == ["get_y"]


def test_release_candidate_inventory_rejects_bare_revision_map(tmp_path):
    inventory = tmp_path / "candidate.json"
    inventory.write_text(
        '{"identity":"release-1","backends":{"one":{"endpoint":"https://one.example/mcp","revision":"main"}}}'
    )

    with pytest.raises(ReleaseCandidateCaptureError, match="router application release"):
        load_release_candidate_inventory(inventory)


def test_release_candidate_inventory_rejects_partial_application_release(tmp_path):
    spec = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_one")])
    payload = _inventory(spec)
    release = payload["backends"]["one"]["application_release"]  # type: ignore[index]
    release.pop("security_evidence")
    inventory = tmp_path / "candidate.json"
    inventory.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ReleaseCandidateCaptureError, match="application release manifest"):
        load_release_candidate_inventory(inventory)


def test_candidate_snapshot_uses_inventory_endpoint_and_records_full_provenance(
    monkeypatch, tmp_path
):
    spec = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_one")])
    inventory = _inventory(spec)
    release = inventory["backends"]["one"]["application_release"]  # type: ignore[index]
    release["version"] = "1.0.0"
    release["source"]["tag"] = "v1.0.0"
    seen: list[str] = []
    seen_tokens: list[str | None] = []

    async def snapshot(url: str, service_token: str | None = None) -> tuple[BackendSpec, list[Any]]:
        seen.append(url)
        seen_tokens.append(service_token)
        return spec, _raw_tools()

    monkeypatch.setattr(
        "scripts.snapshot_fleet.load_registry",
        lambda *_args: [
            SimpleNamespace(
                enabled=True,
                namespace="one",
                repo="berntpopp/one-link",
                url="https://wrong.example/mcp",
                service_token="svc-token",  # noqa: S106 - test fixture, not a credential
            )
        ],
    )
    monkeypatch.setattr("scripts.snapshot_fleet._capture_backend", snapshot)
    output = tmp_path / "baseline.json"

    asyncio.run(_run("servers.yaml", output, "2026-07-12T00:00:00Z", inventory))

    manifest = load_manifest(output)
    assert seen == ["https://candidate.example/mcp"]
    # A token-protected backend must be probed with the router's service credential,
    # otherwise its /mcp answers 401 and the capture fails it as "unreachable".
    assert seen_tokens == ["svc-token"]
    assert manifest.snapshot_meta.release_candidate == inventory
    recorded = manifest.snapshot_meta.release_candidate["backends"]["one"]["application_release"]
    assert recorded["image"]["digest"] == f"sha256:{'a' * 64}"
    assert recorded["workflow"]["standard_revision"] == "e" * 40
    assert recorded["security_evidence"]["sbom_sha256"] == "1" * 64
    assert recorded["data_requirements"]["mode"] == "none"
    assert recorded["mcp"]["capture_context_sha256"] == "f" * 64
    assert manifest.backends["one"].tools == [ToolSpec(name="get_one")]


def test_candidate_snapshot_refuses_definition_digest_mismatch(monkeypatch, tmp_path):
    spec = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_one")])
    inventory = _inventory(spec)
    release = inventory["backends"]["one"]["application_release"]  # type: ignore[index]
    release["mcp"]["definitions_sha256"] = "0" * 64
    release["release_assets"]["mcp-definitions.json"] = f"sha256:{'0' * 64}"

    async def snapshot(
        _url: str, _service_token: str | None = None
    ) -> tuple[BackendSpec, list[Any]]:
        return spec, _raw_tools()

    monkeypatch.setattr(
        "scripts.snapshot_fleet.load_registry",
        lambda *_args: [
            SimpleNamespace(
                enabled=True,
                namespace="one",
                repo="berntpopp/one-link",
                url=None,
                service_token=None,
            )
        ],
    )
    monkeypatch.setattr("scripts.snapshot_fleet._capture_backend", snapshot)

    with pytest.raises(ReleaseCandidateCaptureError, match="definition attestation mismatch"):
        asyncio.run(
            _run("servers.yaml", tmp_path / "baseline.json", "2026-07-12T00:00:00Z", inventory)
        )


def test_candidate_snapshot_rejects_manifest_repository_mismatch(monkeypatch, tmp_path):
    spec = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_one")])
    inventory = _inventory(spec)

    monkeypatch.setattr(
        "scripts.snapshot_fleet.load_registry",
        lambda *_args: [
            SimpleNamespace(
                enabled=True,
                namespace="one",
                repo="berntpopp/different-link",
                url=None,
                service_token=None,
            )
        ],
    )

    with pytest.raises(ReleaseCandidateCaptureError, match="repository mismatch"):
        asyncio.run(
            _run("servers.yaml", tmp_path / "baseline.json", "2026-07-12T00:00:00Z", inventory)
        )


def test_release_candidate_authoring_consumes_complete_release_manifests(tmp_path):
    source = tmp_path / "releases.json"
    source.write_text(
        json.dumps(
            {
                "router": _application_release("berntpopp/genefoundry-router", version="0.6.4"),
                "backends": {"one": _application_release()},
            }
        ),
        encoding="utf-8",
    )

    releases = _load_application_releases(source)

    assert releases["router"]["image"]["digest"] == f"sha256:{'a' * 64}"
    assert releases["backends"]["one"]["security_evidence"]["sbom_sha256"] == "1" * 64


def test_release_candidate_authoring_rejects_partial_manifest_set(tmp_path):
    source = tmp_path / "releases.json"
    source.write_text(json.dumps({"router": _application_release()}), encoding="utf-8")

    with pytest.raises(ReleaseCandidateCaptureError, match="requires backends"):
        _load_application_releases(source)
