"""Nightly community job (Federation Standard v1 §8): issue, kill switch, disable PR."""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from genefoundry_router.config import load_community_registry
from genefoundry_router.exceptions import RegistryError
from genefoundry_router.registry import BackendDef
from genefoundry_router.runtime_drift import read_kill_switch

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts"))  # the nightly imports admission_check by name
SPEC = importlib.util.spec_from_file_location(
    "community_nightly", ROOT / "scripts/community_nightly.py"
)
assert SPEC is not None and SPEC.loader is not None
nightly = importlib.util.module_from_spec(SPEC)
sys.modules["community_nightly"] = nightly  # dataclasses resolve their module
SPEC.loader.exec_module(nightly)

DAY = date(2026, 11, 26)
ENTRY: dict[str, Any] = {
    "name": "acme",
    "namespace": "acme",
    "url": "https://mcp.acme.example/mcp",
    "operator": "Acme Lab",
    "contact": "@acme-ops",
    "ownership": "dns-txt",
    "server_name": "acme-link",
    "source_name": "Acme DB",
    "data_egress": {"retention": "none", "subprocessors": [], "tools_sending_free_text": []},
}


class FakeGitHub:
    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: list[tuple[int, str]] = []
        self.prs: list[str] = []

    def find_issue(self, title: str) -> dict[str, Any] | None:
        return next((i for i in self.issues.values() if i["title"] == title and i["open"]), None)

    def open_issue(self, title: str, body: str) -> None:
        number = len(self.issues) + 1
        self.issues[number] = {"number": number, "title": title, "body": body, "open": True}

    def edit_issue(self, number: int, body: str) -> None:
        self.issues[number]["body"] = body

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def close_issue(self, number: int, body: str) -> None:
        self.issues[number]["open"] = False

    def has_pr(self, branch: str) -> bool:
        return branch in self.prs

    def open_disable_pr(self, branch: str, title: str, body: str, edit: Callable[[], Any]) -> None:
        edit()
        self.prs.append(branch)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    community = tmp_path / "community.yaml"
    community.write_text("# header\n" + yaml.safe_dump({"servers": [ENTRY], "disabled": []}))
    tombstones = tmp_path / "tombstones.yaml"
    tombstones.write_text("tombstones: []\n")
    return {
        "gh": FakeGitHub(),
        "kill": tmp_path / "kill-switch.txt",
        "community": community,
        "tombstones": tombstones,
    }


def _run(env: dict[str, Any], outcome: nightly.Outcome, today: date = DAY) -> list[Any]:
    backends = [BackendDef.model_validate({**ENTRY, "trust": "community"})]
    return nightly.run(
        backends,
        gh=env["gh"],
        pins_dir=Path("unused"),
        kill_switch_file=env["kill"],
        community_file=env["community"],
        check=lambda backend, pins_dir: outcome,
        today=today,
    )


FAIL = nightly.Outcome(conformance=["initialize — 500"])
PASS = nightly.Outcome()


def _open_issues(env: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in env["gh"].issues.values() if i["open"]]


def test_one_failure_opens_only_the_issue_and_mentions_the_contact(env: dict[str, Any]) -> None:
    _run(env, FAIL)
    [issue] = _open_issues(env)
    assert "@acme-ops" in issue["body"] and "initialize" in issue["body"]
    assert env["gh"].prs == []
    assert read_kill_switch(env["kill"]) == set()


def test_two_consecutive_failures_kill_switch_and_disable_pr(env: dict[str, Any]) -> None:
    _run(env, FAIL)
    _run(env, FAIL, DAY + timedelta(days=1))
    assert read_kill_switch(env["kill"]) == {"acme"}
    assert env["gh"].prs == ["community/disable-acme"]
    data = yaml.safe_load(env["community"].read_text())
    assert data["servers"] == []
    assert data["disabled"][0]["namespace"] == "acme"
    assert "conformance" in data["disabled"][0]["reason"]
    assert env["community"].read_text().startswith("# header\n")
    assert len(_open_issues(env)) == 1


def test_removed_ownership_record_kills_on_the_next_run(env: dict[str, Any]) -> None:
    _run(env, nightly.Outcome(ownership="no ownership record"))
    assert read_kill_switch(env["kill"]) == {"acme"}
    assert env["gh"].prs == ["community/disable-acme"]


def test_seven_days_unreachable_kills(env: dict[str, Any]) -> None:
    down = nightly.Outcome(unreachable="connect timeout")
    for offset in range(7):
        _run(env, down, DAY + timedelta(days=offset))
    assert read_kill_switch(env["kill"]) == set()
    assert env["gh"].prs == []
    _run(env, down, DAY + timedelta(days=7))
    assert read_kill_switch(env["kill"]) == {"acme"}
    assert env["gh"].prs == ["community/disable-acme"]
    [entry] = yaml.safe_load(env["community"].read_text())["disabled"]
    assert entry["namespace"] == "acme" and "unreachable" in entry["reason"]


def test_recovery_does_not_clear_the_kill_switch(env: dict[str, Any]) -> None:
    _run(env, FAIL)
    _run(env, FAIL, DAY + timedelta(days=1))
    _run(env, PASS, DAY + timedelta(days=2))
    assert read_kill_switch(env["kill"]) == {"acme"}
    assert _open_issues(env) == []


def test_rerun_is_idempotent(env: dict[str, Any]) -> None:
    for _ in range(3):
        _run(env, FAIL)
    assert len(env["gh"].issues) == 1 and env["gh"].comments == []
    assert read_kill_switch(env["kill"]) == set()  # a same-day rerun is not a second failure
    for _ in range(3):
        _run(env, FAIL, DAY + timedelta(days=1))
    assert len(env["gh"].issues) == 1 and len(env["gh"].comments) == 1
    assert env["gh"].prs == ["community/disable-acme"]
    assert env["kill"].read_text().count("acme") == 1


def test_state_marker_round_trips(env: dict[str, Any]) -> None:
    _run(env, FAIL)
    [issue] = _open_issues(env)
    assert re.search(r'"conformance_failures": 1', issue["body"])


def test_status_rows(env: dict[str, Any]) -> None:
    assert _run(env, PASS) == [("acme", "pass")]
    table = nightly.status_table([("acme", "pass")])
    assert "| acme | pass |" in table


def test_tombstoned_namespace_cannot_be_re_registered(env: dict[str, Any]) -> None:
    nightly.disable_entry(env["community"], "acme", "lost ownership proof")
    nightly.delist(env["community"], env["tombstones"], "acme", "final removal", DAY)
    data = yaml.safe_load(env["community"].read_text())
    assert data["disabled"] == []
    tombs = yaml.safe_load(env["tombstones"].read_text())["tombstones"]
    assert tombs == [
        {"namespace": "acme", "name": "acme", "reason": "final removal", "date": "2026-11-26"}
    ]
    env["community"].write_text(yaml.safe_dump({"servers": [ENTRY], "disabled": []}))
    with pytest.raises(RegistryError, match="tombstoned"):
        load_community_registry(env["community"], [], env["tombstones"])


def test_workflow_is_scheduled_with_minimal_permissions() -> None:
    text = Path(".github/workflows/community-nightly.yml").read_text(encoding="utf-8")
    assert "schedule:" in text and "workflow_dispatch:" in text
    assert "pull_request_target" not in text and "write-all" not in text
    secrets = set(re.findall(r"secrets\.(\w+)", text))
    assert secrets <= {"GITHUB_TOKEN"}
    wf = yaml.safe_load(text)
    assert wf["permissions"] == {"contents": "read"}
    [job] = wf["jobs"].values()
    assert job["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }
    for ref in re.findall(r"uses:\s*(\S+)", text):
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"


def _reachable(monkeypatch: pytest.MonkeyPatch, *, ok: bool = True) -> None:
    class _Conn:
        def __enter__(self) -> None: ...
        def __exit__(self, *exc: object) -> None: ...

    def connect(addr: Any, timeout: float) -> _Conn:
        if not ok:
            raise ConnectionRefusedError("refused")
        return _Conn()

    monkeypatch.setattr(nightly.adm, "check_community_url", lambda url: None)
    monkeypatch.setattr(nightly.socket, "create_connection", connect)


def _backend() -> BackendDef:
    return BackendDef.model_validate({**ENTRY, "trust": "community"})


def test_check_unreachable_host_is_not_a_lost_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    _reachable(monkeypatch, ok=False)
    monkeypatch.setattr(nightly.adm, "ownership_error", lambda *a: pytest.fail("not reached"))
    out = nightly.check_backend(_backend(), Path("unused"))
    assert out.unreachable and out.ownership is None and out.conformance == []


def test_check_lost_proof_calls_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    _reachable(monkeypatch)
    seen: list[Any] = []
    monkeypatch.setattr(
        nightly.adm, "ownership_error", lambda *a: seen.append(a) or "acme: no ownership proof"
    )
    monkeypatch.setattr(nightly.adm, "conformance", lambda b: pytest.fail("no consent"))
    out = nightly.check_backend(_backend(), Path("unused"))
    assert out.ownership == "acme: no ownership proof"
    assert seen[0][2] is None  # nightly does not know the admitting PR author


def test_check_conformance_and_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    _reachable(monkeypatch)
    monkeypatch.setattr(nightly.adm, "ownership_error", lambda *a: None)
    monkeypatch.setattr(nightly.adm, "conformance", lambda b: 1)
    assert nightly.check_backend(_backend(), Path("unused")).conformance
    monkeypatch.setattr(nightly.adm, "conformance", lambda b: 0)
    monkeypatch.setattr(nightly.adm, "pin_diff", lambda b, p: ["-old", "+new"])
    out = nightly.check_backend(_backend(), Path("unused"))
    assert out.pin == ["-old", "+new"] and out.failures
