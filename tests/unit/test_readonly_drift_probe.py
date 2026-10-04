"""The private drift probe accepts only reviewed runtime inputs and bounds its Docker run."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "readonly_drift_probe.py"
SPEC = importlib.util.spec_from_file_location("readonly_drift_probe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def _environment() -> list[str]:
    urls = [
        line.split("=", 1)
        for line in (ROOT / "ci" / "fleet-urls.env").read_text().splitlines()
        if line.startswith("GF_")
    ]
    return [f"{name}={value}" for name, value in urls] + [
        "GF_OAUTH_CLIENT_SECRET=must-not-be-forwarded",
        "HF_TOKEN=must-not-be-forwarded",
        f"{probe.TOKEN_KEY}=opaque-test-value",
    ]


def _container_inspect(image: str, image_id: str) -> dict[str, object]:
    return {
        "config_image": image,
        "image_id": image_id,
        "config_user": "10001:10001",
        "source": "https://github.com/berntpopp/genefoundry-router",
        "revision": "a" * 40,
    }


def _image_inspect(image_id: str, image: str) -> dict[str, object]:
    return {
        "id": image_id,
        "repo_digests": [image],
        "config_user": "10001:10001",
        "source": "https://github.com/berntpopp/genefoundry-router",
        "revision": "a" * 40,
        "os": "linux",
        "architecture": "amd64",
    }


def test_environment_selects_only_closed_reviewed_urls_and_pubtator_token() -> None:
    selected = probe.select_probe_environment(_environment())

    assert set(selected) == set(probe.EXPECTED_URLS) | {probe.TOKEN_KEY}
    assert selected[probe.TOKEN_KEY] == "opaque-test-value"
    configured_urls = dict(
        line.split("=", 1)
        for line in (ROOT / "ci" / "fleet-urls.env").read_text().splitlines()
        if line.startswith("GF_")
    )
    assert configured_urls == probe.EXPECTED_URLS


def test_environment_rejects_missing_duplicate_or_changed_public_url() -> None:
    values = _environment()
    with pytest.raises(probe.ProbeError):
        probe.select_probe_environment(values[:-1])
    with pytest.raises(probe.ProbeError):
        probe.select_probe_environment([*values, values[0]])
    with pytest.raises(probe.ProbeError):
        probe.select_probe_environment(["GF_GNOMAD_URL=https://attacker.invalid/mcp", *values[1:]])


@pytest.mark.parametrize(
    ("container", "image"),
    [
        (
            _container_inspect("latest", "sha256:" + "a" * 64),
            _image_inspect("sha256:" + "a" * 64, "latest"),
        ),
        (
            _container_inspect("ghcr.io/berntpopp/genefoundry-router:tag", "sha256:" + "a" * 64),
            _image_inspect("sha256:" + "a" * 64, "ghcr.io/berntpopp/genefoundry-router:tag"),
        ),
        (
            _container_inspect(
                "ghcr.io/berntpopp/genefoundry-router@sha256:" + "b" * 64, "sha256:" + "a" * 64
            ),
            _image_inspect(
                "sha256:" + "a" * 64, "ghcr.io/berntpopp/genefoundry-router@sha256:" + "c" * 64
            ),
        ),
    ],
)
def test_image_selection_rejects_mutable_or_mismatched_runtime(container, image) -> None:
    with pytest.raises(probe.ProbeError):
        probe.validate_current_image(container, image)


def test_drift_container_is_digest_pinned_non_root_and_has_no_host_mounts() -> None:
    image_ref = "ghcr.io/berntpopp/genefoundry-router@sha256:" + "a" * 64
    args = probe.drift_container_argv(image_ref)

    assert args[0:2] == ["docker", "run"]
    assert "--pull=never" in args
    assert "--network=npm_default" in args
    assert "--user=10001:10001" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges" in args
    assert "--cpus=0.5" in args
    assert "--memory=512m" in args
    assert "--memory-swap=512m" in args
    assert "--pids-limit=128" in args
    assert f"--name={probe.DRIFT_CONTAINER_NAME}" in args
    assert f"--label={probe.DRIFT_CONTAINER_LABEL_KEY}={probe.DRIFT_CONTAINER_LABEL_VALUE}" in args
    assert "--rm" not in args
    assert not any(arg in {"-v", "--volume", "-p", "--publish", "--mount"} for arg in args)
    assert args[-2:] == [image_ref, "drift"]
    assert not any("opaque-test-value" in arg for arg in args)
    env_names = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--env"]
    assert set(env_names) == set(probe.EXPECTED_URLS) | {probe.TOKEN_KEY}


def test_nonempty_ssh_original_command_is_refused_without_docker(monkeypatch) -> None:
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "docker run anything")

    assert probe.main(run=lambda *args, **kwargs: pytest.fail("docker must not run")) == 2


@pytest.mark.parametrize(
    ("probe_exit", "stdout", "stderr", "expected"),
    [
        (0, "OK no tool-definition drift\n", "", "OK no tool-definition drift"),
        (1, "CHANGED pubtator.search_literature\n", "", "CHANGED pubtator.search_literature"),
        (2, "UNREACHABLE: pubtator\n", "", "UNREACHABLE: pubtator"),
        (
            125,
            "opaque-test-value leaked?\n",
            "stack trace",
            "drift probe completed; details omitted",
        ),
    ],
)
def test_pipeline_selects_exact_image_env_and_exit_code(
    probe_exit, stdout, stderr, expected, tmp_path: Path
) -> None:
    image_ref = "ghcr.io/berntpopp/genefoundry-router@sha256:" + "a" * 64
    image_id = "sha256:" + "b" * 64
    env_entries = _environment()
    observed: list[tuple[list[str], dict[str, str]]] = []
    container_exists = False

    def fake_run(args, *, env, **kwargs):
        nonlocal container_exists
        observed.append((args, env))
        if args[0] == "/usr/bin/docker" and args[2] == "ps":
            assert "--all" in args
            assert f"name=^/{probe.DRIFT_CONTAINER_NAME}$" in args
            if not container_exists:
                return probe.subprocess.CompletedProcess(args, 0, "", "")
            return probe.subprocess.CompletedProcess(
                args,
                0,
                f"{probe.DRIFT_CONTAINER_NAME}\t{probe.DRIFT_CONTAINER_LABEL_VALUE}\n",
                "",
            )
        if args[0] == "/usr/bin/docker" and args[2] == "rm":
            assert args == [
                "/usr/bin/docker",
                "--host=unix:///var/run/docker.sock",
                "rm",
                "--force",
                probe.DRIFT_CONTAINER_NAME,
            ]
            container_exists = False
            return probe.subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "/usr/bin/docker" and "inspect" in args:
            if args[-1] == probe.ROUTER_CONTAINER and args[6] == "{{json .Config.Env}}":
                return probe.subprocess.CompletedProcess(args, 0, json.dumps(env_entries), "")
            if args[-1] == probe.ROUTER_CONTAINER:
                output = "\t".join(
                    [
                        image_ref,
                        image_id,
                        "10001:10001",
                        probe.ROUTER_SOURCE,
                        "a" * 40,
                    ]
                )
            else:
                output = "\t".join(
                    [
                        image_id,
                        json.dumps([image_ref]),
                        "10001:10001",
                        probe.ROUTER_SOURCE,
                        "a" * 40,
                        "linux",
                        "amd64",
                    ]
                )
            return probe.subprocess.CompletedProcess(args, 0, output, "")
        assert args[0:3] == ["/usr/bin/docker", "--host=unix:///var/run/docker.sock", "run"]
        assert args[-2:] == [image_ref, "drift"]
        assert set(env) == {"HOME", "PATH", "LC_ALL", "NO_COLOR", "TERM"} | set(
            probe.EXPECTED_URLS
        ) | {probe.TOKEN_KEY}
        assert env[probe.TOKEN_KEY] == "opaque-test-value"
        assert "HF_TOKEN" not in env
        assert "GF_OAUTH_CLIENT_SECRET" not in env
        container_exists = True
        return probe.subprocess.CompletedProcess(args, probe_exit, stdout, stderr)

    code, output = probe.execute_probe(run=fake_run, lock_path=tmp_path / "drift.lock")

    assert code == probe_exit
    assert output == expected
    assert container_exists is False
    run_args = next(args for args, _ in observed if len(args) > 2 and args[2] == "run")
    assert "--pull=never" in run_args
    assert "--network=npm_default" in run_args
    assert not any(
        value in {"--env-file", "--mount", "--volume", "--publish"} for value in run_args
    )


def test_lock_rejects_overlapping_probe_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "drift.lock"
    first_fd = probe._acquire_probe_lock(lock_path)
    try:
        with pytest.raises(probe.ProbeError, match="already running"):
            probe._acquire_probe_lock(lock_path)
    finally:
        os.close(first_fd)

    second_fd = probe._acquire_probe_lock(lock_path)
    os.close(second_fd)


def test_cleanup_failure_is_not_reported_as_a_completed_probe() -> None:
    def fake_run(args, *, env, **kwargs):
        if args[2] == "ps":
            return probe.subprocess.CompletedProcess(
                args,
                0,
                f"{probe.DRIFT_CONTAINER_NAME}\t{probe.DRIFT_CONTAINER_LABEL_VALUE}\n",
                "",
            )
        assert args[2] == "rm"
        return probe.subprocess.CompletedProcess(args, 1, "", "daemon refused cleanup")

    with pytest.raises(probe.ProbeError, match="cleanup failed"):
        probe._remove_probe_container(fake_run)


def test_existing_unowned_fixed_name_is_not_removed_or_reused(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(args, *, env, **kwargs):
        calls.append(args)
        if args[2] == "ps":
            return probe.subprocess.CompletedProcess(
                args, 0, f"{probe.DRIFT_CONTAINER_NAME}\tother-label\n", ""
            )
        pytest.fail("unowned container collision must stop before inspect/run/remove")

    with pytest.raises(probe.ProbeError, match="name is occupied"):
        probe.execute_probe(run=fake_run, lock_path=tmp_path / "drift.lock")
    assert len(calls) == 1


def test_timeout_still_removes_only_the_named_probe_container(tmp_path: Path) -> None:
    image_ref = "ghcr.io/berntpopp/genefoundry-router@sha256:" + "a" * 64
    image_id = "sha256:" + "b" * 64
    env_entries = _environment()
    container_exists = False
    removed: list[str] = []

    def fake_run(args, *, env, **kwargs):
        nonlocal container_exists
        if args[2] == "ps":
            output = (
                f"{probe.DRIFT_CONTAINER_NAME}\t{probe.DRIFT_CONTAINER_LABEL_VALUE}\n"
                if container_exists
                else ""
            )
            return probe.subprocess.CompletedProcess(args, 0, output, "")
        if args[2] == "rm":
            assert args[-1] == probe.DRIFT_CONTAINER_NAME
            removed.append(args[-1])
            container_exists = False
            return probe.subprocess.CompletedProcess(args, 0, "", "")
        if "inspect" in args:
            if args[-1] == probe.ROUTER_CONTAINER and args[6] == "{{json .Config.Env}}":
                return probe.subprocess.CompletedProcess(args, 0, json.dumps(env_entries), "")
            if args[-1] == probe.ROUTER_CONTAINER:
                output = "\t".join(
                    [image_ref, image_id, "10001:10001", probe.ROUTER_SOURCE, "a" * 40]
                )
            else:
                output = "\t".join(
                    [
                        image_id,
                        json.dumps([image_ref]),
                        "10001:10001",
                        probe.ROUTER_SOURCE,
                        "a" * 40,
                        "linux",
                        "amd64",
                    ]
                )
            return probe.subprocess.CompletedProcess(args, 0, output, "")
        assert args[2] == "run"
        container_exists = True
        raise probe.subprocess.TimeoutExpired(args, probe.PROBE_TIMEOUT_SECONDS)

    with pytest.raises(probe.subprocess.TimeoutExpired):
        probe.execute_probe(run=fake_run, lock_path=tmp_path / "drift.lock")

    assert removed == [probe.DRIFT_CONTAINER_NAME]
    assert container_exists is False


def test_bounded_runner_stops_when_output_limit_is_exceeded() -> None:
    with pytest.raises(probe.ProbeError):
        probe._run_bounded(
            probe.subprocess.run,
            [probe.sys.executable, "-c", "print('x' * 10000)"],
            env=probe._host_environment(),
            timeout=5,
            output_limit=128,
        )


def test_bounded_runner_enforces_timeout() -> None:
    with pytest.raises(probe.subprocess.TimeoutExpired):
        probe._run_bounded(
            probe.subprocess.run,
            [probe.sys.executable, "-c", "import time; time.sleep(2)"],
            env=probe._host_environment(),
            timeout=0,
            output_limit=128,
        )


def test_drift_output_redacts_token_and_discards_unrecognized_lines() -> None:
    output = probe._safe_output(
        "CHANGED pubtator.search_literature\nignored opaque-test-value detail\n",
        "",
        "opaque-test-value",
    )

    assert output == "CHANGED pubtator.search_literature"
    assert "opaque-test-value" not in output


def test_workflow_only_heartbeats_success_and_fails_on_drift_or_unreachable() -> None:
    text = (ROOT / ".github" / "workflows" / "drift.yml").read_text()

    assert "github.ref == 'refs/heads/main'" in text
    assert "DRIFT_SSH_PRIVATE_KEY" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "IdentitiesOnly=yes" in text
    assert "-nT" in text
    assert "ssh" in text and "bernt@217.154.76.71" in text
    assert "if: ${{ steps.drift.outputs.exit_code == '1'" in text
    assert "steps.drift.outputs.exit_code == '0'" in text
    assert "steps.drift.outputs.exit_code != '0'" in text
    assert "always() && env.DRIFT_HEARTBEAT_URL" not in text
    assert "uv run genefoundry-router drift" not in text
