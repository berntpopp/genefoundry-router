"""Federation Standard v1 §8: community admission CI (#415)."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

from genefoundry_router.registry import BackendDef

ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "community-admission.yml"
SPEC = importlib.util.spec_from_file_location(
    "admission_check", ROOT / "scripts/admission_check.py"
)
assert SPEC is not None and SPEC.loader is not None
adm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adm)

FIXTURE_PIN = ROOT / "tests/fixtures/pins/community/acme.json"
ENTRY: dict[str, Any] = {
    "name": "acme",
    "namespace": "acme",
    "url": "https://mcp.acme.example/mcp",
    "operator": "Acme Lab",
    "contact": "ops@acme.example",
    "ownership": "well-known",
    "server_name": "acme-link",
    "source_name": "Acme DB",
    "data_egress": {"retention": "none", "subprocessors": [], "tools_sending_free_text": []},
}
PUBLIC = ["93.184.215.14"]


def _registry(tmp_path: Path, pin_text: str | None = None) -> Path:
    (tmp_path / "community.yaml").write_text(yaml.safe_dump({"servers": [ENTRY]}))
    (tmp_path / "tombstones.yaml").write_text("tombstones: []\n")
    pins = tmp_path / "pins"
    pins.mkdir()
    (pins / "acme.json").write_text(pin_text or FIXTURE_PIN.read_text())
    return tmp_path


def _static(root: Path) -> list[str]:
    return adm.static_errors(
        root / "community.yaml", ROOT / "servers.yaml", root / "tombstones.yaml", root / "pins"
    )


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


# --- workflow shape ------------------------------------------------------------------------


def test_workflow_uses_pull_request_never_pull_request_target() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    triggers = _workflow()[True]  # YAML 1.1 reads the `on:` key as True
    assert set(triggers) == {"pull_request"}
    assert set(triggers["pull_request"]["paths"]) == {
        "registry/community.yaml",
        "registry/pins/community/**",
    }


def test_network_job_requires_the_protected_environment_and_has_no_secrets() -> None:
    wf = _workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "secrets:" not in text
    assert wf["permissions"] == {"contents": "read"}
    probe = wf["jobs"]["probe"]
    assert probe["environment"] == "community-admission"  # required reviewers, see docs
    assert probe["needs"] == "static"
    assert "permissions" not in probe  # inherits read-only contents
    assert "environment" not in wf["jobs"]["static"]  # static checks never touch the network
    docs = (ROOT / "docs/community-admission.md").read_text(encoding="utf-8")
    assert "community-admission" in docs and "Required reviewers" in docs


def test_workflow_actions_are_sha_pinned_and_checkout_drops_credentials() -> None:
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert len(step["uses"].split("@")[1]) == 40, step["uses"]
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


# --- static checks -------------------------------------------------------------------------


def test_fixture_registry_passes_static_checks(tmp_path: Path) -> None:
    assert _static(_registry(tmp_path)) == []


def test_bidi_character_in_a_description_fails_the_lint(tmp_path: Path) -> None:
    pin = json.loads(FIXTURE_PIN.read_text())
    pin["tools"][0]["description"] = "Look up a record ‮by identifier."
    errors = _static(_registry(tmp_path, json.dumps(pin)))
    assert any("acme_lookup" in e and "U+202E" in e for e in errors), errors


def test_overlong_description_fails(tmp_path: Path) -> None:
    pin = json.loads(FIXTURE_PIN.read_text())
    pin["tools"][0]["description"] = "x" * (adm.TOOL_DESCRIPTION_CHARS + 1)
    assert any("description" in e for e in _static(_registry(tmp_path, json.dumps(pin))))


def test_missing_and_orphan_pins_fail(tmp_path: Path) -> None:
    root = _registry(tmp_path)
    (root / "pins/acme.json").rename(root / "pins/other.json")
    errors = _static(root)
    assert any("acme" in e and "missing" in e for e in errors)
    assert any("other.json" in e for e in errors)


def test_core_namespace_is_not_free(tmp_path: Path) -> None:
    root = _registry(tmp_path)
    (root / "community.yaml").write_text(
        yaml.safe_dump({"servers": [{**ENTRY, "name": "hgnc", "namespace": "hgnc"}]})
    )
    assert any("core" in e for e in _static(root))


def test_changed_namespaces_and_foreign_paths(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text("servers: []\n")
    head = _registry(tmp_path)
    paths = ["registry/community.yaml", "registry/pins/community/acme.json"]
    assert adm.changed_namespaces(base, head / "community.yaml", paths) == ["acme"]
    base.write_text(yaml.safe_dump({"servers": [ENTRY]}))
    assert adm.changed_namespaces(base, head / "community.yaml", paths[:1]) == []
    assert adm.changed_namespaces(base, head / "community.yaml", paths) == ["acme"]
    assert adm.foreign_paths([*paths, "scripts/admission_check.py"]) == [
        "scripts/admission_check.py"
    ]


# --- network checks ------------------------------------------------------------------------


class Net:
    """Fake network: records every request made towards the candidate."""

    def __init__(self, proof: dict[str, Any] | None = None, txt: list[str] | None = None):
        self.calls: list[str] = []
        self.proof = proof
        self.txt = txt or []

    def well_known(self, host: str) -> Any:
        self.calls.append(f"well-known {host}")
        return self.proof

    def txt_records(self, name: str) -> list[str]:
        self.calls.append(f"txt {name}")
        return self.txt

    def conformance(self, backend: Any) -> int:
        self.calls.append("conformance")
        return 0

    def live_pin(self, backend: Any) -> str:
        self.calls.append("live")
        return FIXTURE_PIN.read_text()


def _probe(net: Net, tmp_path: Path, addrs: list[str] = PUBLIC, author: str = "alice") -> Any:
    root = _registry(tmp_path)
    return adm.probe_errors(
        adm.load_entries(root / "community.yaml", root / "tombstones.yaml")[0],
        author,
        root / "pins",
        resolve=lambda host, port: addrs,
        well_known=net.well_known,
        txt_records=net.txt_records,
        conformance=net.conformance,
        live_pin=net.live_pin,
    )


PROOF = {"namespace": "acme", "github_login": "Alice", "probe_consent": True}


def test_valid_proof_conformance_and_matching_pin_pass(tmp_path: Path) -> None:
    net = Net(PROOF)
    assert _probe(net, tmp_path) == []
    assert "conformance" in net.calls and "live" in net.calls


def test_dns_txt_proof_is_accepted(tmp_path: Path) -> None:
    net = Net(txt=["namespace=acme; github_login=alice; probe_consent=true"])
    assert _probe(net, tmp_path) == []
    assert "txt _genefoundry.mcp.acme.example" in net.calls


def test_ownership_proof_naming_a_different_login_fails(tmp_path: Path) -> None:
    net = Net({**PROOF, "github_login": "mallory"})
    errors = _probe(net, tmp_path)
    assert errors and any("alice" in e for e in errors)
    assert "conformance" not in net.calls  # no tool is called without the owner's consent


def test_proof_without_probe_consent_fails(tmp_path: Path) -> None:
    net = Net({**PROOF, "probe_consent": False})
    assert _probe(net, tmp_path)
    assert "conformance" not in net.calls


@pytest.mark.parametrize("addr", ["10.0.0.5", "127.0.0.1", "::ffff:192.168.1.1", "100.64.0.1"])
def test_private_address_fails_before_any_request(tmp_path: Path, addr: str) -> None:
    net = Net(PROOF)
    errors = _probe(net, tmp_path, addrs=[*PUBLIC, addr])
    assert errors and "non-global" in errors[0]
    assert net.calls == []


def test_pin_differing_from_live_definitions_fails_with_a_readable_diff(tmp_path: Path) -> None:
    net = Net(PROOF)
    live = json.loads(FIXTURE_PIN.read_text())
    live["tools"][0]["description"] = "Changed upstream."
    net.live_pin = lambda backend: json.dumps(live, sort_keys=True, indent=2) + "\n"  # type: ignore[method-assign]
    [error] = _probe(net, tmp_path)
    assert "pin file differs from live definitions" in error
    assert '-      "description": "Look up an Acme record by identifier.",' in error
    assert '+      "description": "Changed upstream.",' in error


def test_conformance_failure_fails(tmp_path: Path) -> None:
    net = Net(PROOF)
    net.conformance = lambda backend: 1  # type: ignore[method-assign]
    assert any("conformance" in e for e in _probe(net, tmp_path))


def test_dns_guard_refuses_private_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"good.example": "93.184.215.14", "evil.example": "169.254.169.254"}

    def fake(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with adm.guarded_dns():
        assert socket.getaddrinfo("good.example", 443)
        with pytest.raises(OSError, match=r"169\.254\.169\.254"):
            socket.getaddrinfo("evil.example", 443)
        with pytest.raises(OSError, match="port"):
            socket.getaddrinfo("good.example", 80)
    assert socket.getaddrinfo is fake


def test_ownership_without_author_skips_the_login_check() -> None:
    """The nightly job (#416) re-checks the proof without knowing the PR author."""
    proof = {"namespace": "acme", "github_login": "alice", "probe_consent": True}
    args = ("acme", "mcp.acme.example")
    assert adm.ownership_error(*args, None, lambda h: proof, lambda n: []) is None
    assert adm.ownership_error(*args, None, lambda h: None, lambda n: []) is not None


# --- MCP Registry domain namespace proof (#434) ----------------------------------------------

REG_NAME = "example.acme/acme-link"  # reverse-DNS namespace of acme.example, parent of the host
REG_URL = "https://mcp.acme.example/mcp"


def _reply(name: str = REG_NAME, url: str = REG_URL, **proof: Any) -> dict[str, Any]:
    """Recorded shape of GET /v0/servers/<name>/versions/latest."""
    block = {"namespace": "acme", "github_login": "alice", "probe_consent": True, **proof}
    return {
        "server": {
            "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
            "name": name,
            "version": "1.0.0",
            "remotes": [{"type": "streamable-http", "url": url}],
            "_meta": {
                "io.modelcontextprotocol.registry/publisher-provided": {"genefoundry": block}
            },
        },
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}},
    }


def _registry_error(reply: Any, name: str = REG_NAME, author: str | None = "alice") -> str | None:
    seen: list[str] = []

    def registry(n: str) -> Any:
        seen.append(n)
        if isinstance(reply, Exception):
            raise reply
        return reply

    error = adm.ownership_error(
        "acme",
        "mcp.acme.example",
        author,
        lambda h: None,  # no well-known proof
        lambda n: [],  # no DNS TXT proof
        ownership=f"mcp-registry:{name}",
        url=REG_URL,
        registry=registry,
    )
    assert seen in ([], [name])
    return error


def test_registry_proof_with_valid_ownership_passes(tmp_path: Path) -> None:
    assert _registry_error(_reply()) is None
    assert _registry_error(_reply(), author=None) is None  # nightly re-check
    backend = BackendDef.model_validate(
        {**ENTRY, "ownership": f"mcp-registry:{REG_NAME}", "trust": "community"}
    )
    net = Net()
    errors = adm.probe_errors(
        backend,
        "Alice",
        _registry(tmp_path) / "pins",
        resolve=lambda host, port: PUBLIC,
        well_known=net.well_known,
        txt_records=net.txt_records,
        registry=lambda name: _reply(),
        conformance=net.conformance,
        live_pin=net.live_pin,
    )
    assert errors == [] and "conformance" in net.calls


def test_registry_is_not_asked_without_a_registry_ownership() -> None:
    assert adm.ownership_error(
        "acme",
        "mcp.acme.example",
        "alice",
        lambda h: None,
        lambda n: [],
        ownership="well-known",
        registry=lambda n: pytest.fail("registry asked"),
    )


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("example.other/acme-link", REG_URL),  # namespace of another domain
        ("example.acme.mcp.deep/acme-link", REG_URL),  # child of the host, not a parent
        ("io.github.alice/acme-link", REG_URL),  # a GitHub login covers only alice.github.io
        (REG_NAME, "https://evil.example/mcp"),  # remotes do not list the entry URL
    ],
)
def test_registry_proof_with_mismatched_host_fails(name: str, url: str) -> None:
    error = _registry_error(_reply(name=name, url=url), name=name)
    assert error and "MCP Registry" in error


def test_io_github_namespace_covers_its_pages_host_and_binds_the_login() -> None:
    name, url = "io.github.Alice/acme-link", "https://alice.github.io/mcp"
    args: tuple[Any, ...] = ("acme", "alice.github.io", "alice", lambda h: None, lambda n: [])
    ok = adm.ownership_error(
        *args,
        ownership=f"mcp-registry:{name}",
        url=url,
        registry=lambda n: _reply(name=name, url=url),
    )
    assert ok is None
    other = adm.ownership_error(
        *args,
        ownership=f"mcp-registry:{name}",
        url=url,
        registry=lambda n: _reply(name=name, url=url, github_login="mallory"),
    )
    assert other and "mallory" in other


def test_registry_proof_with_mismatched_login_or_no_consent_fails() -> None:
    assert "mallory" in (_registry_error(_reply(github_login="mallory")) or "")
    assert _registry_error(_reply(probe_consent=False))
    assert _registry_error(_reply(namespace="other"))


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        "not json",
        {"server": {"name": REG_NAME}},  # no official status
        {**_reply(), "_meta": {"io.modelcontextprotocol.registry/official": {"status": "deleted"}}},
        {**_reply(), "_meta": []},
        {"server": {**_reply()["server"], "remotes": "https://mcp.acme.example/mcp"}},
        {"server": {**_reply()["server"], "_meta": {}}, "_meta": _reply()["_meta"]},  # no block
        {
            "server": {**_reply()["server"], "name": "example.acme/other"},
            "_meta": _reply()["_meta"],
        },
    ],
)
def test_malformed_registry_reply_fails_closed(reply: Any) -> None:
    assert _registry_error(reply)


@pytest.mark.parametrize("name", ["acme", "example.acme/../x", "example.acme/a/b", "/x", "a/b c"])
def test_invalid_registry_name_fails_before_any_request(name: str) -> None:
    assert _registry_error(_reply(), name=name)  # _registry_error asserts no fetch for bad names


def _mock(handler: Any) -> Any:
    import httpx2

    return lambda name: adm.fetch_json(f"{adm.MCP_REGISTRY}/x", httpx2.MockTransport(handler))


def test_oversize_registry_reply_fails_closed() -> None:
    import httpx2

    big = json.dumps({**_reply(), "pad": "x" * adm.MAX_PROOF_BYTES}).encode()
    error = adm.ownership_error(
        "acme",
        "mcp.acme.example",
        "alice",
        lambda h: None,
        lambda n: [],
        ownership=f"mcp-registry:{REG_NAME}",
        url=REG_URL,
        registry=_mock(lambda request: httpx2.Response(200, content=big)),
    )
    assert error and f"larger than {adm.MAX_PROOF_BYTES} bytes" in error


@pytest.mark.parametrize("status", [404, 500, 503, 302])
def test_registry_down_or_redirecting_fails_closed(status: int) -> None:
    import httpx2

    headers = {"location": "http://169.254.169.254/"} if status == 302 else {}
    error = adm.ownership_error(
        "acme",
        "mcp.acme.example",
        "alice",
        lambda h: None,
        lambda n: [],
        ownership=f"mcp-registry:{REG_NAME}",
        url=REG_URL,
        registry=_mock(lambda request: httpx2.Response(status, headers=headers)),
    )
    assert error and "MCP Registry" in error


def test_registry_unreachable_fails_closed() -> None:
    assert _registry_error(OSError("connection refused"))
    assert _registry_error(TimeoutError("timed out"))


def test_registry_reply_url_is_the_registry_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(adm, "fetch_json", lambda url, transport=None: seen.append(url))
    adm.registry_reply("io.github.alice/x")
    assert seen == [
        "https://registry.modelcontextprotocol.io/v0/servers/io.github.alice%2Fx/versions/latest"
    ]
