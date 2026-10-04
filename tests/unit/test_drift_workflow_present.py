"""The production drift workflow is key-scoped, main-only, and fail-closed."""

import re
from pathlib import Path

WF = Path(".github/workflows/drift.yml")


def test_drift_workflow_present_and_gated():
    text = WF.read_text(encoding="utf-8")
    assert "schedule:" in text
    assert "workflow_dispatch:" in text
    assert "DRIFT_ENABLED" in text  # opt-in gate
    assert "DRIFT_HEARTBEAT_URL" in text  # heartbeat
    assert "tool-drift" in text  # dedup label


def test_permissions_are_least_privilege():
    text = WF.read_text(encoding="utf-8")
    assert "contents: read" in text
    assert "issues: write" in text
    # No broad grants.
    assert "write-all" not in text
    assert "contents: write" not in text


def test_all_external_actions_are_sha_pinned():
    refs = re.findall(r"uses:\s*(\S+)", WF.read_text(encoding="utf-8"))
    assert refs, "expected at least one external action"
    for ref in refs:
        assert re.search(r"@[0-9a-f]{40}$", ref), f"action not SHA-pinned: {ref}"


def test_heartbeat_is_fail_safe():
    text = WF.read_text(encoding="utf-8")
    # A clean native probe proves the monitor ran; failed/uncertain probes never heartbeat.
    assert "steps.drift.outputs.exit_code == '0'" in text
    assert "always() && env.DRIFT_HEARTBEAT_URL" not in text


def test_private_ssh_is_main_only_and_has_no_remote_command():
    text = WF.read_text(encoding="utf-8")
    assert "github.ref == 'refs/heads/main'" in text
    assert "DRIFT_SSH_PRIVATE_KEY" in text
    assert "environment: drift-probe" in text
    assert "GF_PUBTATOR_TOKEN" not in text
    assert "ci/fleet-urls.env" not in text
    assert "StrictHostKeyChecking=yes" in text
    assert 'UserKnownHostsFile="$GITHUB_WORKSPACE/ci/drift_known_hosts"' in text
    assert "IdentitiesOnly=yes" in text
    assert "ClearAllForwardings=yes" in text
    assert "ssh -nT" in text
    assert "bernt@217.154.76.71" in text
    ssh_line = next(line.strip() for line in text.splitlines() if "bernt@217.154.76.71" in line)
    assert ssh_line == "bernt@217.154.76.71 > drift_output.txt 2>&1"
    assert "trap 'rm -f \"$key\"' EXIT" in text
    assert "uv run genefoundry-router drift" not in text


def test_nonclean_probe_fails_without_success_heartbeat():
    text = WF.read_text(encoding="utf-8")
    assert "steps.drift.outputs.exit_code == '1'" in text
    assert "steps.drift.outputs.exit_code == '2'" in text
    assert "steps.drift.outputs.exit_code != '0'" in text


def test_host_key_file_is_a_single_pinned_ed25519_entry():
    line = Path("ci/drift_known_hosts").read_text(encoding="utf-8").strip()
    fields = line.split()
    assert fields[0] == "[217.154.76.71]:2323"
    assert fields[1] == "ssh-ed25519"
    assert len(fields[2]) >= 50
