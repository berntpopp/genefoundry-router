"""Federation Standard v1 §8: community admission CI (#415)."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from typing import Any

import pytest
import yaml

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
