import asyncio
from types import SimpleNamespace

import pytest

from genefoundry_router.devtools.fakes import BackendSpec, ToolSpec, load_manifest
from scripts.snapshot_fleet import (
    ReleaseCandidateCaptureError,
    _run,
    backend_definitions_digest,
    load_release_candidate_inventory,
    merge_backend,
)


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


def test_release_candidate_inventory_rejects_non_immutable_revision(tmp_path):
    inventory = tmp_path / "candidate.json"
    inventory.write_text(
        '{"identity":"release-1","backends":{"one":{"endpoint":"https://one.example/mcp","revision":"main"}}}'
    )

    with pytest.raises(ReleaseCandidateCaptureError, match="40-character commit SHA"):
        load_release_candidate_inventory(inventory)


def test_release_candidate_inventory_requires_definition_attestation(tmp_path):
    inventory = tmp_path / "candidate.json"
    inventory.write_text(
        '{"identity":"release-1","backends":{"one":{"endpoint":"https://one.example/mcp","revision":"'
        + "a" * 40
        + '"}}}'
    )

    with pytest.raises(ReleaseCandidateCaptureError, match="definitions_sha256"):
        load_release_candidate_inventory(inventory)


def test_candidate_snapshot_uses_inventory_endpoint_and_records_full_provenance(
    monkeypatch, tmp_path
):
    spec = BackendSpec(version="1", tools=[ToolSpec(name="get_one")])
    inventory = {
        "identity": "release-1",
        "backends": {
            "one": {
                "endpoint": "https://candidate.example/mcp",
                "revision": "a" * 40,
                "definitions_sha256": backend_definitions_digest(spec),
            }
        },
    }
    seen: list[str] = []
    seen_tokens: list[str | None] = []

    async def snapshot(url: str, service_token: str | None = None) -> BackendSpec:
        seen.append(url)
        seen_tokens.append(service_token)
        return spec

    monkeypatch.setattr(
        "scripts.snapshot_fleet.load_registry",
        lambda *_args: [
            SimpleNamespace(
                enabled=True,
                namespace="one",
                url="https://wrong.example/mcp",
                service_token="svc-token",  # noqa: S106 - test fixture, not a credential
            )
        ],
    )
    monkeypatch.setattr("scripts.snapshot_fleet._snapshot_backend", snapshot)
    output = tmp_path / "baseline.json"

    asyncio.run(_run("servers.yaml", output, "2026-07-12T00:00:00Z", inventory))

    manifest = load_manifest(output)
    assert seen == ["https://candidate.example/mcp"]
    # A token-protected backend must be probed with the router's service credential,
    # otherwise its /mcp answers 401 and the capture fails it as "unreachable".
    assert seen_tokens == ["svc-token"]
    assert manifest.snapshot_meta.release_candidate == inventory
    assert manifest.backends["one"].tools == [ToolSpec(name="get_one")]


def test_candidate_snapshot_refuses_definition_digest_mismatch(monkeypatch, tmp_path):
    inventory = {
        "identity": "release-1",
        "backends": {
            "one": {
                "endpoint": "https://candidate.example/mcp",
                "revision": "a" * 40,
                "definitions_sha256": "0" * 64,
            }
        },
    }

    async def snapshot(_url: str, _service_token: str | None = None) -> BackendSpec:
        return BackendSpec(version="1", tools=[ToolSpec(name="get_one")])

    monkeypatch.setattr(
        "scripts.snapshot_fleet.load_registry",
        lambda *_args: [
            SimpleNamespace(enabled=True, namespace="one", url=None, service_token=None)
        ],
    )
    monkeypatch.setattr("scripts.snapshot_fleet._snapshot_backend", snapshot)

    with pytest.raises(ReleaseCandidateCaptureError, match="definition attestation mismatch"):
        asyncio.run(
            _run("servers.yaml", tmp_path / "baseline.json", "2026-07-12T00:00:00Z", inventory)
        )
