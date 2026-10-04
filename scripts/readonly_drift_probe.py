#!/usr/bin/python3 -I
"""Run the router's pinned drift check in a short-lived container on its private network.

The controller installs this file as a root-owned SSH forced command. It accepts no
arguments or caller-provided commands; runtime configuration is selected from the
currently running router container and never written to a file or printed.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import IO

ROUTER_CONTAINER = "genefoundry_router"
ROUTER_NETWORK = "npm_default"
ROUTER_IMAGE = "ghcr.io/berntpopp/genefoundry-router"
ROUTER_SOURCE = "https://github.com/berntpopp/genefoundry-router"
ROUTER_USER = "10001:10001"
DRIFT_CONTAINER_NAME = "genefoundry-private-drift-probe"
DRIFT_CONTAINER_LABEL_KEY = "org.genefoundry.private-drift-probe"
DRIFT_CONTAINER_LABEL_VALUE = "router-monitor-v1"
PROBE_LOCK_PATH = Path("/home/bernt/.genefoundry-private-drift-probe.lock")
MAX_ENV_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
PROBE_TIMEOUT_SECONDS = 780
INSPECT_TIMEOUT_SECONDS = 10
CLEANUP_TIMEOUT_SECONDS = 10

EXPECTED_URLS: dict[str, str] = {
    "GF_GNOMAD_URL": "https://gnomad-link.genefoundry.org/mcp",
    "GF_GTEX_URL": "https://gtex-link.genefoundry.org/mcp",
    "GF_HGNC_URL": "https://hgnc-link.genefoundry.org/mcp",
    "GF_MGI_URL": "https://mgi-link.genefoundry.org/mcp",
    "GF_UNIPROT_URL": "https://uniprot-link.genefoundry.org/mcp",
    "GF_CLINGEN_URL": "https://clingen-link.genefoundry.org/mcp",
    "GF_GENCC_URL": "https://gencc-link.genefoundry.org/mcp",
    "GF_LITVAR_URL": "https://litvar-link.genefoundry.org/mcp",
    "GF_STRINGDB_URL": "https://stringdb-link.genefoundry.org/mcp",
    "GF_AUTOPVS1_URL": "https://autopvs1-link.genefoundry.org/mcp",
    "GF_SPLICEAI_URL": "https://spliceailookup-link.genefoundry.org/mcp",
    "GF_GENEREVIEWS_URL": "https://genereviews-link.genefoundry.org/mcp",
    "GF_PUBTATOR_URL": "https://pubtator-link.genefoundry.org/mcp",
    "GF_CLINVAR_URL": "https://clinvar-link.genefoundry.org/mcp",
    "GF_VEP_URL": "https://vep-link.genefoundry.org/mcp",
    "GF_PANELAPP_URL": "https://panelapp-link.genefoundry.org/mcp",
    "GF_MONDO_URL": "https://mondo-link.genefoundry.org/mcp",
    "GF_MAVEDB_URL": "https://mavedb-link.genefoundry.org/mcp",
    "GF_HPO_URL": "https://hpo-link.genefoundry.org/mcp",
    "GF_METADOME_URL": "https://metadome-link.genefoundry.org/mcp",
    "GF_ORPHANET_URL": "https://orphanet-link.genefoundry.org/mcp",
    "GF_CLINPGX_URL": "https://clinpgx-link.genefoundry.org/mcp",
}
TOKEN_KEY = "GF_PUBTATOR_TOKEN"  # noqa: S105 - environment variable name, not a credential
_IMAGE_REF = re.compile(rf"^{re.escape(ROUTER_IMAGE)}@sha256:[0-9a-f]{{64}}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SAFE_OUTPUT = re.compile(
    r"^(?:(?:CHANGED|ADDED|REMOVED) [A-Za-z0-9_.:-]{1,256}|"
    r"UNREACHABLE: [A-Za-z0-9_.,: -]{1,512}|"
    r"WARN [A-Za-z0-9_.:-]{1,128}: [A-Za-z0-9_ .():/-]{1,256}|"
    r"OK no tool-definition drift|"
    r"tool-definition drift detected — review before refreshing pin|"
    r"no drift, but some backends were unreachable)$"
)


class ProbeError(RuntimeError):
    """A bounded, secret-free refusal of the private drift probe."""


Run = Callable[..., subprocess.CompletedProcess[str]]


def _host_environment() -> dict[str, str]:
    """Do not inherit Docker contexts, credentials, or unrelated service secrets."""
    return {"HOME": "/home/bernt", "PATH": "/usr/bin:/bin", "LC_ALL": "C"}


def select_probe_environment(entries: Sequence[str]) -> dict[str, str]:
    """Keep only the 22 reviewed registry URLs and the single PubTator service token."""
    allowed = set(EXPECTED_URLS) | {TOKEN_KEY}
    selected: dict[str, str] = {}
    for entry in entries:
        name, separator, value = entry.partition("=")
        if not separator or name not in allowed:
            continue
        if name in selected:
            raise ProbeError("running router has duplicate drift environment entries")
        selected[name] = value
    if set(selected) != allowed:
        raise ProbeError("running router is missing a required drift environment entry")
    if any(selected[name] != expected for name, expected in EXPECTED_URLS.items()):
        raise ProbeError("running router URLs differ from the reviewed fleet registry")
    token = selected[TOKEN_KEY]
    if not token or len(token) > 8192 or any(char in token for char in "\x00\r\n"):
        raise ProbeError("running router PubTator credential is invalid")
    return selected


def validate_current_image(container: Mapping[str, object], image: Mapping[str, object]) -> str:
    """Require a cached, digest-pinned image matching the running non-root container."""
    image_ref = container.get("config_image")
    image_id = container.get("image_id")
    if not isinstance(image_ref, str) or _IMAGE_REF.fullmatch(image_ref) is None:
        raise ProbeError("running router is not configured with the accepted digest image")
    if not isinstance(image_id, str) or _SHA256.fullmatch(image_id) is None:
        raise ProbeError("running router image id is invalid")
    revision = container.get("revision")
    if (
        container.get("config_user") != ROUTER_USER
        or container.get("source") != ROUTER_SOURCE
        or not isinstance(revision, str)
        or _REVISION.fullmatch(revision) is None
    ):
        raise ProbeError("running router source or user metadata is invalid")
    repo_digests = image.get("repo_digests")
    if (
        image.get("id") != image_id
        or image.get("config_user") != ROUTER_USER
        or image.get("source") != ROUTER_SOURCE
        or image.get("revision") != container.get("revision")
        or image.get("os") != "linux"
        or image.get("architecture") != "amd64"
        or not isinstance(repo_digests, list)
        or image_ref not in repo_digests
    ):
        raise ProbeError("running router image is not the matching cached amd64 release")
    return image_ref


def drift_container_argv(image_ref: str) -> list[str]:
    """Build the fixed, no-mount, resource-capped invocation for the drift CLI."""
    if _IMAGE_REF.fullmatch(image_ref) is None:
        raise ProbeError("router image reference is not digest-pinned")
    args = [
        "docker",
        "run",
        "--pull=never",
        f"--name={DRIFT_CONTAINER_NAME}",
        f"--label={DRIFT_CONTAINER_LABEL_KEY}={DRIFT_CONTAINER_LABEL_VALUE}",
        f"--network={ROUTER_NETWORK}",
        f"--user={ROUTER_USER}",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--cpus=0.5",
        "--memory=512m",
        "--memory-swap=512m",
        "--pids-limit=128",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=64m,mode=1777",
    ]
    for name in sorted(set(EXPECTED_URLS) | {TOKEN_KEY}):
        args.extend(["--env", name])
    args.extend(["--entrypoint=genefoundry-router", image_ref, "drift"])
    return args


def _docker_inspect(run: Run, target: str, template: str, *, kind: str) -> str:
    argv = [
        "/usr/bin/docker",
        "--host=unix:///var/run/docker.sock",
        "inspect",
        "--type",
        kind,
        "--format",
        template,
        target,
    ]
    completed = _run_bounded(
        run,
        argv,
        env=_host_environment(),
        timeout=INSPECT_TIMEOUT_SECONDS,
        output_limit=MAX_ENV_BYTES,
    )
    if completed.returncode != 0:
        raise ProbeError("router image inspection failed")
    return completed.stdout.rstrip("\n")


def _acquire_probe_lock(lock_path: Path = PROBE_LOCK_PATH) -> int:
    """Serialize runs and reject symlink, non-file, or foreign-owned lock paths."""
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ProbeError("probe lock is unavailable") from exc
    try:
        metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise ProbeError("probe lock path is unsafe")
        os.fchmod(lock_fd, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProbeError("a private drift probe is already running") from exc
        return lock_fd
    except BaseException:
        os.close(lock_fd)
        raise


def _probe_container_exists(run: Run) -> bool:
    """Find only the exact dedicated name and require its ownership label."""
    template = f'{{{{.Names}}}}\t{{{{.Label "{DRIFT_CONTAINER_LABEL_KEY}"}}}}'
    argv = [
        "/usr/bin/docker",
        "--host=unix:///var/run/docker.sock",
        "ps",
        "--all",
        "--filter",
        f"name=^/{DRIFT_CONTAINER_NAME}$",
        "--format",
        template,
    ]
    completed = _run_bounded(
        run,
        argv,
        env=_host_environment(),
        timeout=INSPECT_TIMEOUT_SECONDS,
        output_limit=4096,
    )
    if completed.returncode != 0:
        raise ProbeError("probe container ownership check failed")
    rows = [line.split("\t") for line in completed.stdout.splitlines() if line]
    if not rows:
        return False
    if rows != [[DRIFT_CONTAINER_NAME, DRIFT_CONTAINER_LABEL_VALUE]]:
        raise ProbeError("probe container name is occupied by an unowned container")
    return True


def _remove_probe_container(run: Run) -> None:
    """Remove only the dedicated labeled probe, and verify daemon cleanup completed."""
    if not _probe_container_exists(run):
        return
    argv = [
        "/usr/bin/docker",
        "--host=unix:///var/run/docker.sock",
        "rm",
        "--force",
        DRIFT_CONTAINER_NAME,
    ]
    completed = _run_bounded(
        run,
        argv,
        env=_host_environment(),
        timeout=CLEANUP_TIMEOUT_SECONDS,
        output_limit=4096,
    )
    if completed.returncode != 0 or _probe_container_exists(run):
        raise ProbeError("probe container cleanup failed")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    """Stop the docker client process group so an over-limit probe is not orphaned."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def _run_bounded(
    run: Run,
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: int,
    output_limit: int,
) -> subprocess.CompletedProcess[str]:
    """Capture a command without allowing unbounded stdout/stderr buffering."""
    if run is not subprocess.run:
        return run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=dict(env),
        )
    process = subprocess.Popen(  # noqa: S603 - fixed executable and validated argv only
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    output_streams: dict[str, IO[bytes]] = {
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    selector = selectors.DefaultSelector()
    selector.register(process.stdout.fileno(), selectors.EVENT_READ, "stdout")
    selector.register(process.stderr.fileno(), selectors.EVENT_READ, "stderr")
    chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                raise subprocess.TimeoutExpired(list(argv), timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fd, min(8192, output_limit - total + 1))
                if not chunk:
                    selector.unregister(key.fd)
                    output_streams[key.data].close()
                    continue
                total += len(chunk)
                if total > output_limit:
                    _terminate_process(process)
                    raise ProbeError("drift probe output exceeded its bound")
                chunks[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process)
            raise subprocess.TimeoutExpired(list(argv), timeout)
        returncode = process.wait(timeout=remaining)
    except BaseException:
        if process.poll() is None:
            _terminate_process(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()
    return subprocess.CompletedProcess(
        list(argv),
        returncode,
        chunks["stdout"].decode("utf-8", errors="replace"),
        chunks["stderr"].decode("utf-8", errors="replace"),
    )


def _inspect_current_router(run: Run) -> str:
    container_template = (
        '{{.Config.Image}}{{"\\t"}}{{.Image}}{{"\\t"}}{{.Config.User}}'
        '{{"\\t"}}{{index .Config.Labels "org.opencontainers.image.source"}}'
        '{{"\\t"}}{{index .Config.Labels "org.opencontainers.image.revision"}}'
    )
    values = _docker_inspect(run, ROUTER_CONTAINER, container_template, kind="container").split(
        "\t"
    )
    if len(values) != 5:
        raise ProbeError("router container metadata is incomplete")
    image_ref, image_id, config_user, source, revision = values
    if _IMAGE_REF.fullmatch(image_ref) is None or _SHA256.fullmatch(image_id) is None:
        raise ProbeError("router container image identity is invalid")
    if (
        config_user != ROUTER_USER
        or source != ROUTER_SOURCE
        or _REVISION.fullmatch(revision) is None
    ):
        raise ProbeError("router container source or user metadata is invalid")
    image_template = (
        '{{.Id}}{{"\\t"}}{{json .RepoDigests}}{{"\\t"}}{{.Config.User}}'
        '{{"\\t"}}{{index .Config.Labels "org.opencontainers.image.source"}}'
        '{{"\\t"}}{{index .Config.Labels "org.opencontainers.image.revision"}}'
        '{{"\\t"}}{{.Os}}{{"\\t"}}{{.Architecture}}'
    )
    image_values = _docker_inspect(run, image_ref, image_template, kind="image").split("\t")
    if len(image_values) != 7:
        raise ProbeError("cached router image metadata is incomplete")
    try:
        repo_digests = json.loads(image_values[1])
    except json.JSONDecodeError as exc:
        raise ProbeError("cached router digest metadata is invalid") from exc
    container = {
        "config_image": image_ref,
        "image_id": image_id,
        "config_user": config_user,
        "source": source,
        "revision": revision,
    }
    image = {
        "id": image_values[0],
        "repo_digests": repo_digests,
        "config_user": image_values[2],
        "source": image_values[3],
        "revision": image_values[4],
        "os": image_values[5],
        "architecture": image_values[6],
    }
    return validate_current_image(container, image)


def _inspect_runtime_environment(run: Run) -> dict[str, str]:
    # Docker renders only the container's environment vector, never its full config.
    # Values are parsed in memory, selected immediately, and never logged or persisted.
    raw = _docker_inspect(run, ROUTER_CONTAINER, "{{json .Config.Env}}", kind="container")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError("router environment metadata is invalid") from exc
    if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
        raise ProbeError("router environment metadata is invalid")
    return select_probe_environment(entries)


def _safe_output(stdout: str, stderr: str, token: str) -> str:
    combined = (stdout + "\n" + stderr).replace(token, "[redacted]")
    if len(combined.encode()) > MAX_OUTPUT_BYTES:
        raise ProbeError("drift probe output exceeded its bound")
    safe = []
    for raw_line in combined.splitlines():
        line = _ANSI.sub("", raw_line).strip()
        if _SAFE_OUTPUT.fullmatch(line):
            safe.append(line)
    return "\n".join(safe) or "drift probe completed; details omitted"


def execute_probe(
    run: Run = subprocess.run, *, lock_path: Path = PROBE_LOCK_PATH
) -> tuple[int, str]:
    """Inspect current runtime, then execute its exact cached image with bounded resources."""
    lock_fd = _acquire_probe_lock(lock_path)
    cleanup_required = False
    try:
        _remove_probe_container(run)
        image_ref = _inspect_current_router(run)
        environment = _inspect_runtime_environment(run)
        cleanup_required = True
        completed = _run_bounded(
            run,
            [
                "/usr/bin/docker",
                "--host=unix:///var/run/docker.sock",
                *drift_container_argv(image_ref)[1:],
            ],
            env={**_host_environment(), **environment, "NO_COLOR": "1", "TERM": "dumb"},
            timeout=PROBE_TIMEOUT_SECONDS,
            output_limit=MAX_OUTPUT_BYTES,
        )
        output = _safe_output(completed.stdout, completed.stderr, environment[TOKEN_KEY])
        return completed.returncode, output
    finally:
        try:
            if cleanup_required:
                _remove_probe_container(run)
        finally:
            os.close(lock_fd)


def main(run: Run = subprocess.run, argv: Sequence[str] | None = None) -> int:
    """Fixed SSH forced-command entry point. Errors are generic and secret-free."""
    if (argv if argv is not None else sys.argv[1:]) or os.environ.get("SSH_ORIGINAL_COMMAND", ""):
        print("private drift probe refuses caller commands", file=sys.stderr)
        return 2
    try:
        code, output = execute_probe(run)
    except subprocess.TimeoutExpired:
        print("private drift probe timed out", file=sys.stderr)
        return 124
    except (OSError, ProbeError, json.JSONDecodeError):
        print("private drift probe preflight failed", file=sys.stderr)
        return 2
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
