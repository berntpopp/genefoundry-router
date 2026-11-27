"""Nightly community job (Federation Standard v1 §8).

For every community entry: ownership proof, conformance probe and pin comparison. A failure
opens or updates one issue per namespace (mentioning ``contact`` when it is a ``@login``).
Two consecutive conformance failures, a lost ownership proof or 7 days unreachable append
the namespace to the kill-switch file at once and open one PR that moves the entry to
``disabled:``. The kill switch is append-only: recovery never removes an entry. Per-backend
state lives in a marker in the open issue, so the job keeps no state of its own, and a
same-day rerun changes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import admission_check as adm  # scripts/ is on sys.path when this file runs
import yaml

from genefoundry_router.registry import BackendDef
from genefoundry_router.runtime_drift import read_kill_switch, write_atomic

LABEL = "community-nightly"
UNREACHABLE_DAYS = 7
_STATE_RE = re.compile(r"<!-- gf-nightly-state (\{.*?\}) -->")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


@dataclass
class Outcome:
    ownership: str | None = None  # why the ownership proof is lost
    conformance: list[str] = field(default_factory=list)
    pin: list[str] = field(default_factory=list)  # unified diff, pin file -> live
    unreachable: str | None = None

    @property
    def failures(self) -> list[str]:
        return [
            *([f"ownership: {self.ownership}"] if self.ownership else []),
            *(f"conformance: {f}" for f in self.conformance),
            *(["pin: live definitions differ from the pin file"] if self.pin else []),
            *([f"unreachable: {self.unreachable}"] if self.unreachable else []),
        ]


def check_backend(backend: BackendDef, pins_dir: Path) -> Outcome:
    """The §8 checks (shared with admission); an unreachable host is not a failed proof."""
    out = Outcome()
    url = backend.url or ""
    host = urlsplit(url).hostname or ""
    try:
        adm.check_community_url(url)
        with adm.guarded_dns(), socket.create_connection((host, 443), timeout=10):
            pass
    except ValueError as exc:
        if not isinstance(exc.__cause__, OSError):
            out.conformance = [str(exc)]  # resolves to a non-global address
            return out
        out.unreachable = str(exc)
        return out
    except OSError as exc:
        out.unreachable = f"{host}:443: {type(exc).__name__}: {exc}"
        return out
    out.ownership = adm.ownership_error(
        backend.namespace,
        host,
        None,
        adm.well_known,
        adm.txt_records,
        ownership=backend.ownership,
        url=url,
    )
    if out.ownership:
        return out  # no tool is called without the owner's consent
    if adm.conformance(backend) != 0:
        out.conformance = ["GTS core conformance probe failed (see the run log)"]
        return out
    try:
        out.pin = adm.pin_diff(backend, pins_dir)
    except Exception as exc:  # any harvest failure is reported, never fatal to the job
        out.pin = [f"harvest failed: {type(exc).__name__}: {exc}"]
    return out


def advance(state: dict[str, Any], out: Outcome, today: date) -> tuple[dict[str, Any], str | None]:
    """New per-backend state (unchanged on a same-day rerun) and the kill reason, if any."""
    if state.get("day") != today.isoformat():
        since = state.get("unreachable_since") or today.isoformat()
        state = {
            "day": today.isoformat(),
            "conformance_failures": state.get("conformance_failures", 0) + 1
            if out.conformance
            else 0,
            "unreachable_since": since if out.unreachable else None,
        }
    since = state.get("unreachable_since")
    if out.ownership:
        return state, f"lost ownership proof: {out.ownership}"
    if state["conformance_failures"] >= 2:
        return state, "two consecutive conformance failures"
    if since and (today - date.fromisoformat(since)).days >= UNREACHABLE_DAYS:
        return state, f"unreachable for {UNREACHABLE_DAYS} days"
    return state, None


class GitHub(Protocol):
    def find_issue(self, title: str) -> dict[str, Any] | None: ...
    def open_issue(self, title: str, body: str) -> None: ...
    def edit_issue(self, number: int, body: str) -> None: ...
    def comment(self, number: int, body: str) -> None: ...
    def close_issue(self, number: int, body: str) -> None: ...
    def has_pr(self, branch: str) -> bool: ...
    def open_disable_pr(
        self, branch: str, title: str, body: str, edit: Callable[[], Any]
    ) -> None: ...


def _mention(contact: str | None) -> str:
    return contact if contact and _HANDLE_RE.match(contact) else ""


def _issue_body(
    backend: BackendDef, out: Outcome, state: dict[str, Any], reason: str | None
) -> str:
    lines = [
        f"Nightly check of community backend `{backend.namespace}` ({backend.url}) failed.",
        f"Contact: {_mention(backend.contact) or 'see the registry entry'}",
        "",
        *(f"- {f}" for f in out.failures),
    ]
    if out.pin:
        lines += ["", "```diff", *out.pin[:200], "```"]
    if reason:
        lines += ["", f"**Kill switch:** {reason}. The entry is being moved to `disabled:`."]
    lines += ["", f"<!-- gf-nightly-state {json.dumps(state, sort_keys=True)} -->"]
    return "\n".join(lines)


def add_to_kill_switch(path: Path, namespace: str) -> None:
    """Append ``namespace`` to the kill-switch file (the format ``GF_KILL_SWITCH_FILE`` reads)."""
    if namespace in read_kill_switch(path):
        return
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    write_atomic(path, text + ("" if not text or text.endswith("\n") else "\n") + namespace + "\n")


def _rewrite_yaml(path: Path, data: dict[str, Any]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    header = "".join(
        line
        for line in lines[
            : next((i for i, line in enumerate(lines) if not line.startswith("#")), len(lines))
        ]
    )
    write_atomic(path, header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def disable_entry(community_file: Path, namespace: str, reason: str) -> None:
    """Move a community entry from ``servers:`` to ``disabled:`` with its reason."""
    data = yaml.safe_load(community_file.read_text(encoding="utf-8")) or {}
    servers = data.get("servers") or []
    entry = next((e for e in servers if e.get("namespace") == namespace), None)
    if entry is None:
        return
    servers.remove(entry)
    data["servers"] = servers
    data["disabled"] = [*(data.get("disabled") or []), {**entry, "reason": reason}]
    _rewrite_yaml(community_file, data)


def delist(
    community_file: Path, tombstones_file: Path, namespace: str, reason: str, today: date
) -> None:
    """Final removal: drop a disabled entry and tombstone its name and namespace forever."""
    data = yaml.safe_load(community_file.read_text(encoding="utf-8")) or {}
    disabled = data.get("disabled") or []
    entry = next((e for e in disabled if e.get("namespace") == namespace), None)
    if entry is None:
        raise ValueError(f"{namespace!r} is not under disabled:; disable it first")
    data["disabled"] = [e for e in disabled if e is not entry]
    _rewrite_yaml(community_file, data)
    tombs = yaml.safe_load(tombstones_file.read_text(encoding="utf-8")) or {}
    tombs["tombstones"] = [
        *(tombs.get("tombstones") or []),
        {
            "namespace": namespace,
            "name": entry["name"],
            "reason": reason,
            "date": today.isoformat(),
        },
    ]
    _rewrite_yaml(tombstones_file, tombs)


def run(
    backends: Sequence[BackendDef],
    *,
    gh: GitHub,
    pins_dir: Path,
    kill_switch_file: Path,
    community_file: Path,
    check: Callable[[BackendDef, Path], Outcome] = check_backend,
    today: date | None = None,
) -> list[tuple[str, str]]:
    """Check every backend and act on the results; returns ``(namespace, status)`` rows."""
    today = today or datetime.now(UTC).date()
    rows: list[tuple[str, str]] = []
    for backend in backends:
        ns = backend.namespace
        out = check(backend, pins_dir)
        title = f"Community backend {ns}: nightly check failed"
        issue = gh.find_issue(title)
        match = _STATE_RE.search(issue["body"]) if issue else None
        previous = json.loads(match.group(1)) if match else {}
        state, reason = advance(previous, out, today)
        if not out.failures:
            if issue:
                gh.close_issue(
                    issue["number"], "All checks pass again. The kill switch is not cleared."
                )
            rows.append((ns, "pass"))
            continue
        body = _issue_body(backend, out, state, reason)
        if issue is None:
            gh.open_issue(title, body)
        else:
            gh.edit_issue(issue["number"], body)
            if previous.get("day") != state["day"]:
                gh.comment(issue["number"], f"{_mention(backend.contact)} still failing.".strip())
        if reason:
            add_to_kill_switch(kill_switch_file, ns)
            branch = f"community/disable-{ns}"
            if not gh.has_pr(branch):
                gh.open_disable_pr(
                    branch,
                    f"Disable community backend {ns}: {reason}",
                    f"Federation Standard v1 §8: {reason}.\n\n{body}",
                    partial(disable_entry, community_file, ns, reason),
                )
        rows.append((ns, f"kill switch: {reason}" if reason else "; ".join(out.failures)))
    return rows


def status_table(rows: Sequence[tuple[str, str]]) -> str:
    """Markdown status table, one row per community backend."""
    body = "".join(f"| {ns} | {status.replace('|', '/')} |\n" for ns, status in rows)
    return "| Backend | Status |\n|---|---|\n" + body


def _sh(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout  # noqa: S603


class GhCli:
    """GitHub through the ``gh`` CLI (``GH_TOKEN``) and ``git`` in the checkout."""

    def find_issue(self, title: str) -> dict[str, Any] | None:
        issues = json.loads(
            _sh(
                "gh",
                "issue",
                "list",
                "--label",
                LABEL,
                "--state",
                "open",
                "--limit",
                "200",
                "--json",
                "number,title,body",
            )
        )
        return next((i for i in issues if i["title"] == title), None)

    def open_issue(self, title: str, body: str) -> None:
        _sh("gh", "label", "create", LABEL, "--force", "--color", "B60205")
        _sh("gh", "issue", "create", "--title", title, "--body", body, "--label", LABEL)

    def edit_issue(self, number: int, body: str) -> None:
        _sh("gh", "issue", "edit", str(number), "--body", body)

    def comment(self, number: int, body: str) -> None:
        _sh("gh", "issue", "comment", str(number), "--body", body)

    def close_issue(self, number: int, body: str) -> None:
        _sh("gh", "issue", "close", str(number), "--comment", body)

    def has_pr(self, branch: str) -> bool:
        return bool(
            json.loads(
                _sh("gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number")
            )
        )

    def open_disable_pr(self, branch: str, title: str, body: str, edit: Callable[[], Any]) -> None:
        start = _sh("git", "rev-parse", "--abbrev-ref", "HEAD").strip()
        _sh("git", "switch", "-C", branch)
        try:
            edit()
            _sh("git", "commit", "-am", title)
            _sh("git", "push", "--force", "origin", branch)
            _sh("gh", "pr", "create", "--head", branch, "--title", title, "--body", body)
        finally:
            _sh("git", "switch", start)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="check every community backend and act on failures")
    gone = sub.add_parser("delist", help="final removal of a disabled entry (tombstoned)")
    gone.add_argument("namespace")
    gone.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.command == "delist":
        delist(adm.COMMUNITY, adm.TOMBSTONES, args.namespace, args.reason, datetime.now(UTC).date())
        return 0
    kill_switch = os.environ.get("GF_KILL_SWITCH_FILE")
    if not kill_switch:
        print("GF_KILL_SWITCH_FILE is not set", file=sys.stderr)
        return 2
    rows = run(
        adm.load_entries(adm.COMMUNITY, adm.TOMBSTONES),
        gh=GhCli(),
        pins_dir=adm.PINS,
        kill_switch_file=Path(kill_switch),
        community_file=adm.COMMUNITY,
    )
    table = status_table(rows)
    print(table)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Community backends\n\n" + table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
