"""scripts/community_egress_check.py: a reachable target fails the check (#417)."""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "community_egress_check", ROOT / "scripts/community_egress_check.py"
)
assert SPEC is not None and SPEC.loader is not None
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_listener_and_refusal_count_as_reachable_unresolvable_as_blocked() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        assert check.reachable(f"127.0.0.1:{port}", 1.0)[0] is True
    assert check.reachable(f"127.0.0.1:{port}", 1.0)[0] is True  # refused: host answered
    assert check.reachable("no-such-host.invalid:80", 1.0) == (False, "name does not resolve")


def test_host_gateway_is_parsed_from_the_route_table(tmp_path: Path) -> None:
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\n"
        "eth0\t0011A8C0\t00000000\t0001\n"
        "eth0\t00000000\t0100A8C0\t0003\n"
    )
    assert check.host_gateway(str(route)) == "192.168.0.1"
    assert check.host_gateway(str(tmp_path / "missing")) is None


def test_main_fails_when_any_blocked_target_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check, "host_gateway", lambda: "192.0.2.1")
    answers = {"core:8000": (True, "connected")}
    monkeypatch.setattr(check, "reachable", lambda t, _: answers.get(t, (False, "timed out")))
    assert check.main(["--blocked", "core:8000"]) == 1
    assert check.main(["--blocked", "idp:8080"]) == 0
    assert check.main(["--open", "egress.example:443"]) == 1  # no egress is a failure too
