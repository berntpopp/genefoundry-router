"""Static security contract for protected-tag container publication."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REUSABLE = ROOT / ".github/workflows/_container-release.yml"
CALLER = ROOT / ".github/workflows/container-release.yml"
TRIVY_CACHE_DIR = "${{ github.workspace }}/.cache/trivy"

ACTION_PINS = {
    "actions/checkout": "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/setup-python": "ece7cb06caefa5fff74198d8649806c4678c61a1",
    "astral-sh/setup-uv": "11f9893b081a58869d3b5fccaea48c9e9e46f990",
    "docker/setup-buildx-action": "bb05f3f5519dd87d3ba754cc423b652a5edd6d2c",
    "docker/build-push-action": "53b7df96c91f9c12dcc8a07bcb9ccacbed38856a",
    "aquasecurity/trivy-action": "ed142fd0673e97e23eac54620cfb913e5ce36c25",
    "anchore/sbom-action": "e22c389904149dbc22b58101806040fa8d37a610",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "docker/login-action": "af1e73f918a031802d376d3c8bbc3fe56130a9b0",
    "actions/attest-build-provenance": "977bb373ede98d70efdf65b84cb5f73e068dcc2a",
    "actions/attest-sbom": "4651f806c01d8637787e274ac3bdf724ef169f34",
}

ACTION_PIN_VERSIONS = {
    "astral-sh/setup-uv": "v8.3.2",
    "aquasecurity/trivy-action": "v0.36.0",
    "actions/attest-build-provenance": "v3.0.0",
    "actions/attest-sbom": "v3.0.0",
}

READ_JOBS = {"prepare", "build-gate", "capture", "assemble-evidence"}
PRIVILEGED_JOBS = {"publish-attest", "finalize"}


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _on(document: dict[str, Any]) -> dict[str, Any]:
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return steps


def _run_text(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _executable_shell(shell: str) -> str:
    """Remove lines that cannot execute before inspecting shell contracts."""
    executable: list[str] = []
    continued = False
    for line in shell.splitlines():
        stripped = line.strip()
        if not stripped or line.lstrip().startswith("#"):
            assert not continued, "comment or blank line interrupts a shell continuation"
            continue
        executable.append(line)
        trailing_backslashes = len(stripped) - len(stripped.rstrip("\\"))
        continued = trailing_backslashes % 2 == 1
    return "\n".join(executable)


def _commands(job: dict[str, Any]) -> str:
    """The executed shell of a job, with comment lines removed."""
    return _executable_shell(_run_text(job))


def _all_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in _steps(job)]


def _step_index(job: dict[str, Any], fragment: str) -> int:
    return next(
        index
        for index, step in enumerate(_steps(job))
        if fragment in str(step.get("name", "")) + str(step.get("uses", ""))
    )


def _step_run(job: dict[str, Any], fragment: str) -> str:
    """Return the one run step containing a stable workflow fragment."""
    matching = [
        str(step.get("run", "")) for step in _steps(job) if fragment in str(step.get("run", ""))
    ]
    assert len(matching) == 1
    return matching[0]


RUNTIME_V1_CONDITION = (
    '[ "$contract" = "data-bound" ] && [ "$data_identity_contract" = "runtime-v1" ]'
)
UNADOPTED_CONDITION = (
    '[ "$contract" = "data-bound" ] && [ "$data_identity_contract" = "unadopted" ]'
)
DATA_INDEPENDENT_CONDITION = (
    '[ "$contract" = "data-independent" ] && [ "$data_identity_contract" = "none" ]'
)
RUNTIME_IDENTITY_BOUNDARIES = (
    (
        "build-gate",
        "release-gate-health.json",
        "$RUNNER_TEMP/release-gate-health.json",
        "$RUNNER_TEMP/smoke-observed-data-identity.json",
    ),
    (
        "capture",
        "capture_tools()",
        "$RUNNER_TEMP/capture/a-health.json",
        "$RUNNER_TEMP/capture/observed-data-identity.json",
    ),
)
RUNTIME_IDENTITY_PRODUCERS = (
    (
        "curl -fsS -H 'Host: localhost' \\",
        '"http://127.0.0.1:18000$health_path" > "$RUNNER_TEMP/release-gate-health.json"',
    ),
    (
        'capture_tools a 18000 "$RUNNER_TEMP/capture/mcp-tools-a.json" \\',
        '"$RUNNER_TEMP/capture/context-a.json"',
    ),
)
CAPTURE_FUNCTION_HEALTH_PRODUCER = (
    "curl -fsS -H 'Host: localhost' \\",
    '"http://127.0.0.1:${host_port}${health_path}" \\',
    '> "$RUNNER_TEMP/capture/${label}-health.json"',
)


def _branch_body(shell: str, start_marker: str, next_marker: str) -> str:
    """Return lines between two unique exact executable branch markers."""
    lines = _normalized_executable_lines(shell)
    starts = [index for index, line in enumerate(lines) if line == start_marker]
    assert len(starts) == 1
    start = starts[0]
    ends = [
        index for index, line in enumerate(lines[start + 1 :], start + 1) if line == next_marker
    ]
    assert len(ends) == 1
    return "\n".join(lines[start + 1 : ends[0]])


def _if_branch(shell: str, condition: str, next_marker: str) -> str:
    """Return one exact shell branch, excluding later adoption states."""
    return _branch_body(shell, f"if {condition}; then", next_marker)


def _elif_branch(shell: str, condition: str, next_marker: str) -> str:
    """Return one exact elif branch, excluding later adoption states."""
    return _branch_body(shell, f"elif {condition}; then", next_marker)


def _normalized_executable_lines(shell: str) -> list[str]:
    return [line.strip() for line in _executable_shell(shell).splitlines()]


def _exact_sequence_index(shell: str, expected: tuple[str, ...]) -> int:
    """Find one exact consecutive executable-line sequence."""
    lines = _normalized_executable_lines(shell)
    matches = [
        index
        for index in range(len(lines) - len(expected) + 1)
        if tuple(lines[index : index + len(expected)]) == expected
    ]
    assert len(matches) == 1
    return matches[0]


def _verifier_command(health: str, output: str) -> tuple[str, ...]:
    return (
        "uv run --project .container-release-tools \\",
        "python .container-release-tools/scripts/container_release.py \\",
        "verify-runtime-data-identity \\",
        "--config container-release.json \\",
        f'--health "{health}" \\',
        f'--out "{output}"',
    )


def _runtime_v1_branch_body(health: str, output: str) -> tuple[str, ...]:
    body = _verifier_command(health, output)
    if output == "$RUNNER_TEMP/capture/observed-data-identity.json":
        body += (
            "data_identity_args=(",
            '--observed-identity "$RUNNER_TEMP/capture/observed-data-identity.json"',
            ")",
        )
    return body


def _adoption_assignments() -> tuple[str, ...]:
    return (
        "contract=\"$(jq -er '.definitions.contract' container-release.json)\"",
        'data_identity_contract="$(jq -er \\',
        "'if .data_identity_contract == null then \"none\" else .data_identity_contract end' \\",
        'container-release.json)"',
    )


def _adoption_conditional(boundary: int, health: str, output: str) -> tuple[str, ...]:
    body = (
        f"if {RUNTIME_V1_CONDITION}; then",
        *_runtime_v1_branch_body(health, output),
        f"elif {UNADOPTED_CONDITION}; then",
    )
    if boundary == 0:
        body += (": # Explicit legacy path during staged adoption.",)
    else:
        body += (
            "data_identity_args=(",
            "--data-release-tag \"$(jq -er '.data.release_tag' container-release.json)\"",
            "--data-digest \"$(jq -er '.data.digest' container-release.json)\"",
            ")",
        )
    body += (f"elif {DATA_INDEPENDENT_CONDITION}; then",)
    if boundary == 0:
        body += (": # Data-independent releases have no runtime data identity.",)
    else:
        body += (
            'capture_tools b 18001 "$RUNNER_TEMP/capture/mcp-tools-b.json" \\',
            '"$RUNNER_TEMP/capture/context-b.json"',
            "capture_args+=(",
            '--tools "$RUNNER_TEMP/capture/mcp-tools-b.json"',
            '--context "$RUNNER_TEMP/capture/context-b.json"',
            ")",
        )
    return (
        *body,
        "else",
        'echo "unsupported data identity contract state" >&2',
        "exit 1",
        "fi",
    )


def _critical_boundary_slice(boundary: int, health: str, output: str) -> tuple[str, ...]:
    conditional = _adoption_conditional(boundary, health, output)
    if boundary == 0:
        return (
            "mcp_path=\"$(jq -er '.service.mcp_path' container-release.json)\"",
            *RUNTIME_IDENTITY_PRODUCERS[boundary],
            *_adoption_assignments(),
            *conditional,
            'curl -fsS -D "$RUNNER_TEMP/mcp.headers" -o "$RUNNER_TEMP/mcp-init.json" \\',
        )
    return (
        *_adoption_assignments(),
        *RUNTIME_IDENTITY_PRODUCERS[boundary],
        "capture_args=(",
        '--tools "$RUNNER_TEMP/capture/mcp-tools-a.json"',
        '--context "$RUNNER_TEMP/capture/context-a.json"',
        ")",
        "data_identity_args=()",
        *conditional,
        "uv run --project .container-release-tools \\",
    )


def _capture_tools_body(shell: str) -> tuple[str, ...]:
    lines = _normalized_executable_lines(shell)
    starts = [index for index, line in enumerate(lines) if line == "capture_tools() {"]
    assert len(starts) == 1
    start = starts[0]
    ends = [index for index, line in enumerate(lines[start + 1 :], start + 1) if line == "}"]
    assert len(ends) == 1
    return tuple(lines[start : ends[0] + 1])


def _assert_capture_health_source(shell: str) -> None:
    function = _capture_tools_body(shell)
    _exact_sequence_index("\n".join(function), CAPTURE_FUNCTION_HEALTH_PRODUCER)
    target = '"$RUNNER_TEMP/capture/${label}-health.json"'
    assert sum(target in line for line in function) == 1


def _assert_top_level_critical_slice(shell: str, critical_index: int) -> None:
    lines = _normalized_executable_lines(shell)
    assert lines[0] == "set -euo pipefail"
    assert not any(
        re.fullmatch(r"set \+(?:[A-Za-z]*e[A-Za-z]*|o\s+errexit)", line) for line in lines[1:]
    )
    assert not any("<<" in line for line in lines)
    depth = 0
    for index, line in enumerate(lines):
        if index == critical_index:
            assert depth == 0
        if line == "fi" or line == "}" or line == "esac" or line == ")" or line.startswith("done"):
            depth -= 1
            assert depth >= 0
        if (
            (line.startswith(("if ", "while ", "for ")) and line.endswith(("; then", "; do")))
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\(\) \{", line)
            or line.endswith("&& {")
            or line.endswith("(")
        ):
            depth += 1
    assert depth == 0


def _assert_verifier_branch(shell: str, health: str, output: str) -> None:
    runtime_branch = _if_branch(
        shell,
        RUNTIME_V1_CONDITION,
        f"elif {UNADOPTED_CONDITION}; then",
    )
    assert tuple(_normalized_executable_lines(runtime_branch)) == _runtime_v1_branch_body(
        health, output
    )
    assert "--data-release-tag" not in runtime_branch
    assert "--data-digest" not in runtime_branch


def _assert_runtime_identity_boundary(
    shell: str,
    boundary: int,
    health: str,
    output: str,
) -> None:
    if boundary == 1:
        _assert_capture_health_source(shell)
    critical_index = _exact_sequence_index(
        shell, _critical_boundary_slice(boundary, health, output)
    )
    _assert_top_level_critical_slice(shell, critical_index)
    _assert_verifier_branch(shell, health, output)


def _boundary_step(workflow: dict[str, Any], boundary: int) -> dict[str, Any]:
    job_name, fragment, _health, _output = RUNTIME_IDENTITY_BOUNDARIES[boundary]
    return next(
        step for step in _steps(workflow["jobs"][job_name]) if fragment in str(step.get("run", ""))
    )


def _mutate_verifier_block(shell: str, output: str, mutation: str) -> str:
    """Comment or remove the verifier command ending at one exact output path."""
    lines = shell.splitlines(keepends=True)
    verify_index = next(
        index for index, line in enumerate(lines) if "verify-runtime-data-identity" in line
    )
    start = verify_index
    while "uv run --project .container-release-tools" not in lines[start]:
        start -= 1
    end = verify_index
    while f'--out "{output}"' not in lines[end]:
        end += 1
    return _replace_line_range(shell, start, end, mutation)


def _wrap_verifier_block(shell: str, output: str, wrapper: str) -> str:
    """Put an unchanged verifier sequence behind a non-executing shell construct."""
    lines = shell.splitlines(keepends=True)
    verify_index = next(
        index for index, line in enumerate(lines) if "verify-runtime-data-identity" in line
    )
    start = verify_index
    while "uv run --project .container-release-tools" not in lines[start]:
        start -= 1
    end = verify_index
    while f'--out "{output}"' not in lines[end]:
        end += 1
    return _wrap_line_range(shell, start, end, wrapper)


def _mutate_workflow_verifiers(
    workflow: dict[str, Any], boundaries: tuple[int, ...], mutation: str
) -> None:
    for boundary in boundaries:
        step = _boundary_step(workflow, boundary)
        output = RUNTIME_IDENTITY_BOUNDARIES[boundary][3]
        if mutation in {"if-false", "heredoc"}:
            step["run"] = _wrap_verifier_block(str(step["run"]), output, mutation)
        else:
            step["run"] = _mutate_verifier_block(str(step["run"]), output, mutation)


def _replace_producer_with_inline_noops(shell: str, expected: tuple[str, ...]) -> str:
    lines = shell.splitlines(keepends=True)
    start, end = _line_range_for_sequence(lines, expected)
    return _replace_line_range(shell, start, end, "inline-noop")


def _line_range_for_sequence(lines: list[str], expected: tuple[str, ...]) -> tuple[int, int]:
    normalized = [line.strip() for line in lines]
    starts = [
        index
        for index in range(len(lines) - len(expected) + 1)
        if tuple(normalized[index : index + len(expected)]) == expected
    ]
    assert len(starts) == 1
    return starts[0], starts[0] + len(expected) - 1


def _replace_line_range(shell: str, start: int, end: int, mutation: str) -> str:
    lines = shell.splitlines(keepends=True)
    if mutation == "remove":
        replacement: list[str] = []
    else:
        prefix = ": # " if mutation == "inline-noop" else "# "
        assert mutation in {"inline-noop", "comment"}
        replacement = [
            f"{line[: len(line) - len(line.lstrip())]}{prefix}{line.lstrip()}"
            for line in lines[start : end + 1]
        ]
    return "".join([*lines[:start], *replacement, *lines[end + 1 :]])


def _adoption_conditional_range(lines: list[str]) -> tuple[int, int]:
    start_marker = f"if {RUNTIME_V1_CONDITION}; then"
    starts = [index for index, line in enumerate(lines) if line.strip() == start_marker]
    assert len(starts) == 1
    start = starts[0]
    end = next(index for index in range(start + 1, len(lines)) if lines[index].strip() == "fi")
    return start, end


def _wrap_line_range(shell: str, start: int, end: int, wrapper: str) -> str:
    lines = shell.splitlines(keepends=True)
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    if wrapper == "if-false":
        before = [f"{indent}if false; then\n"]
        after = [f"{indent}fi\n"]
    elif wrapper == "heredoc":
        before = [f"{indent}: <<'DISABLED_BOUNDARY'\n"]
        after = ["DISABLED_BOUNDARY\n"]
    elif wrapper == "function":
        before = [f"{indent}disabled_boundary() {{\n"]
        after = [f"{indent}}}\n"]
    elif wrapper == "true-or-subshell":
        before = [f"{indent}true || (\n"]
        after = [f"{indent})\n"]
    else:
        assert wrapper == "false-and"
        before = [f"{indent}false && {{\n"]
        after = [f"{indent}}}\n"]
    return "".join([*lines[:start], *before, *lines[start : end + 1], *after, *lines[end + 1 :]])


def _wrap_workflow_boundary_part(
    workflow: dict[str, Any], boundary: int, part: str, wrapper: str
) -> None:
    step = _boundary_step(workflow, boundary)
    shell = str(step["run"])
    lines = shell.splitlines(keepends=True)
    if part == "producer":
        start, end = _line_range_for_sequence(lines, RUNTIME_IDENTITY_PRODUCERS[boundary])
    else:
        assert part == "conditional"
        start, end = _adoption_conditional_range(lines)
    step["run"] = _wrap_line_range(shell, start, end, wrapper)


def _insert_health_rewrite(workflow: dict[str, Any], boundary: int, rewrite: str) -> None:
    health = RUNTIME_IDENTITY_BOUNDARIES[boundary][2]
    step = _boundary_step(workflow, boundary)
    shell = str(step["run"])
    lines = shell.splitlines(keepends=True)
    _start, end = _line_range_for_sequence(lines, RUNTIME_IDENTITY_PRODUCERS[boundary])
    indent = lines[end][: len(lines[end]) - len(lines[end].lstrip())]
    if rewrite == "overwrite":
        inserted = [f"{indent}printf '{{}}' > \"{health}\"\n"]
    else:
        assert rewrite == "reproduce"
        inserted = [
            f'{indent}jq -c . "{health}" > "{health}.tmp"\n',
            f'{indent}mv "{health}.tmp" "{health}"\n',
        ]
    step["run"] = "".join([*lines[: end + 1], *inserted, *lines[end + 1 :]])


def _mutate_capture_function_health(workflow: dict[str, Any], mutation: str) -> None:
    step = next(
        step
        for step in _steps(workflow["jobs"]["capture"])
        if "capture_tools()" in str(step.get("run", ""))
    )
    shell = str(step["run"])
    if mutation == "inline-noop":
        step["run"] = _replace_producer_with_inline_noops(shell, CAPTURE_FUNCTION_HEALTH_PRODUCER)
        return
    assert mutation == "config-overwrite"
    lines = shell.splitlines(keepends=True)
    _start, end = _line_range_for_sequence(lines, CAPTURE_FUNCTION_HEALTH_PRODUCER)
    indent = lines[end][: len(lines[end]) - len(lines[end].lstrip())]
    inserted = [
        f"{indent}jq -n --arg release_tag \"$(jq -er '.data.release_tag' "
        'container-release.json)" \\\n',
        f"{indent}'{{release_identity:{{data_identity:{{actual:{{release_tag:$release_tag}}}}}}}}' "
        '> "$RUNNER_TEMP/capture/${label}-health.json"\n',
    ]
    step["run"] = "".join([*lines[: end + 1], *inserted, *lines[end + 1 :]])


def _insert_comment_after_continuation(
    workflow: dict[str, Any], boundary: int, sequence: tuple[str, ...]
) -> None:
    step = _boundary_step(workflow, boundary)
    lines = str(step["run"]).splitlines(keepends=True)
    start, _end = _line_range_for_sequence(lines, sequence)
    assert lines[start].rstrip().endswith("\\")
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    lines.insert(start + 1, f"{indent}# continuation bypass\n")
    step["run"] = "".join(lines)


def _mutate_step_context(workflow: dict[str, Any], boundary: int, mutation: str) -> None:
    step = _boundary_step(workflow, boundary)
    if mutation == "if-metadata":
        step["if"] = "${{ false }}"
        return
    if mutation == "continue-on-error":
        step["continue-on-error"] = True
        return
    if mutation == "shell-suffix":
        step["shell"] = "bash {0} || true"
        return
    shell = str(step["run"])
    lines = shell.splitlines(keepends=True)
    strict = next(index for index, line in enumerate(lines) if line.strip() == "set -euo pipefail")
    if mutation == "if-false-wrapper":
        step["run"] = _wrap_line_range(shell, strict + 1, len(lines) - 1, "if-false")
        return
    if mutation == "subshell-wrapper":
        step["run"] = _wrap_line_range(shell, strict + 1, len(lines) - 1, "true-or-subshell")
        return
    assert mutation in {"set +e", "set +o errexit"}
    indent = lines[strict][: len(lines[strict]) - len(lines[strict].lstrip())]
    lines.insert(strict + 1, f"{indent}{mutation}\n")
    step["run"] = "".join(lines)


def _mutate_workflow_producers(workflow: dict[str, Any], boundaries: tuple[int, ...]) -> None:
    for boundary in boundaries:
        step = _boundary_step(workflow, boundary)
        step["run"] = _replace_producer_with_inline_noops(
            str(step["run"]), RUNTIME_IDENTITY_PRODUCERS[boundary]
        )


def _assert_workflow_verifiers(workflow: dict[str, Any]) -> None:
    for boundary, (_job_name, _fragment, health, output) in enumerate(RUNTIME_IDENTITY_BOUNDARIES):
        step = _boundary_step(workflow, boundary)
        assert "if" not in step
        assert "continue-on-error" not in step
        assert step.get("shell") == "bash"
        _assert_runtime_identity_boundary(
            str(step["run"]),
            boundary,
            health,
            output,
        )


def test_caller_accepts_tag_push_only_with_release_permission_ceiling() -> None:
    workflow = _load(CALLER)
    trigger = _on(workflow)
    assert set(trigger) == {"push"}
    assert trigger["push"] == {"tags": ["v*.*.*"]}
    assert workflow["permissions"] == {
        "attestations": "write",
        "contents": "write",
        "id-token": "write",
        "packages": "write",
    }
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert "github.repository" in workflow["concurrency"]["group"]
    assert "github.ref" in workflow["concurrency"]["group"]
    assert workflow["jobs"] == {"release": {"uses": "./.github/workflows/_container-release.yml"}}


def test_reusable_has_six_jobs_with_job_scoped_least_privilege() -> None:
    workflow = _load(REUSABLE)
    assert _on(workflow) == {"workflow_call": {}}
    assert workflow["permissions"] == {}
    assert set(workflow["jobs"]) == READ_JOBS | PRIVILEGED_JOBS
    expected = {
        "prepare": {"contents": "read", "packages": "read"},
        "build-gate": {"contents": "read"},
        "capture": {"contents": "read", "packages": "read"},
        "assemble-evidence": {"contents": "read"},
        "publish-attest": {
            "attestations": "write",
            "contents": "read",
            "id-token": "write",
            "packages": "write",
        },
        "finalize": {"contents": "write", "packages": "write"},
    }
    for name, permissions in expected.items():
        assert workflow["jobs"][name]["permissions"] == permissions
    for name in PRIVILEGED_JOBS:
        assert workflow["jobs"][name]["environment"] == "release"


def test_every_action_is_full_sha_pinned_and_required_pins_are_exact() -> None:
    workflow = _load(REUSABLE)
    seen: dict[str, set[str]] = {}
    for step in _all_steps(workflow):
        uses = step.get("uses")
        if not uses:
            continue
        action, separator, revision = str(uses).partition("@")
        assert separator and re.fullmatch(r"[0-9a-f]{40}", revision), uses
        seen.setdefault(action, set()).add(revision)
    for action, revision in ACTION_PINS.items():
        assert seen[action] == {revision}


def test_required_action_version_comments_match_pinned_revisions() -> None:
    text = REUSABLE.read_text(encoding="utf-8")
    for action, version in ACTION_PIN_VERSIONS.items():
        assert f"uses: {action}@{ACTION_PINS[action]} # {version}" in text


def test_runtime_revalidates_exact_stable_tag_and_called_workflow_identity() -> None:
    workflow = _load(REUSABLE)
    prepare = workflow["jobs"]["prepare"]
    steps = _steps(prepare)
    identity = next(step for step in steps if step.get("id") == "workflow-identity")
    assert steps.index(identity) < min(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert identity["env"] == {"JOB_CONTEXT": "${{ toJSON(job) }}"}
    text = _run_text(prepare)
    assert 'jq -er ".workflow_repository"' in text
    assert 'jq -er ".workflow_ref"' in text
    assert 'jq -er ".workflow_sha"' in text
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in text
    assert 'GITHUB_EVENT_NAME" = "push' in text
    assert "refs/tags/" in text
    assert "validate-source" in text
    assert "_container-release.yml@" in text
    assert "^[0-9a-f]{40}$" in text


def test_privileged_jobs_never_checkout_or_execute_leaf_code_or_containers() -> None:
    workflow = _load(REUSABLE)
    forbidden = ("scripts/", "docker compose", "docker run", "docker build", "uv run")
    for name in PRIVILEGED_JOBS:
        job = workflow["jobs"][name]
        assert not any(
            str(step.get("uses", "")).startswith("actions/checkout@") for step in _steps(job)
        )
        text = _run_text(job).lower()
        assert all(token not in text for token in forbidden)


def test_prepare_covers_new_recovery_collision_and_completed_states() -> None:
    prepare = _load(REUSABLE)["jobs"]["prepare"]
    assert "build_date" in prepare["outputs"]
    step_names = {str(step.get("name", "")) for step in _steps(prepare)}
    assert "Enforce fleet release controls before publication" in step_names
    text = _run_text(prepare)
    for token in (
        "build_required=true",
        "build_required=false",
        'git show -s --format=%cI "$source_sha"',
        "require_compliant_controls",
        "ci/container-controls.json",
        "source alias collision",
        "completed_release=true",
        "version alias collision",
        "org.opencontainers.image.revision",
        "gh release verify",
        "missing attestation",
    ):
        assert token in text


def test_build_gate_builds_only_when_absent_and_never_uses_release_cache() -> None:
    workflow = _load(REUSABLE)
    job = workflow["jobs"]["build-gate"]
    builds = [
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(builds) == 1
    build = builds[0]
    assert "build_required == 'true'" in build["if"]
    inputs = build["with"]
    assert inputs["platforms"] == "linux/amd64"
    assert inputs["push"] is False
    assert inputs["provenance"] is False
    assert inputs["sbom"] is False
    assert inputs["build-args"].rstrip("\n") == "\n".join(
        [
            "APP_VERSION=${{ needs.prepare.outputs.version }}",
            "VCS_REF=${{ needs.prepare.outputs.source_sha }}",
            "BUILD_DATE=${{ needs.prepare.outputs.build_date }}",
        ]
    )
    assert str(inputs["outputs"]).startswith("type=oci,")
    assert not any(key.startswith("cache-") for key in inputs)
    text = _run_text(job)
    trivy = next(
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("aquasecurity/trivy-action@")
    )
    assert trivy["with"]["cache-dir"] == TRIVY_CACHE_DIR
    evaluate_trivy = next(
        step for step in _steps(job) if step.get("name") == "Evaluate versioned Trivy policy"
    )
    assert evaluate_trivy["env"]["TRIVY_CACHE_DIR"] == TRIVY_CACHE_DIR
    assert "build_required == 'false'" in str(job)
    assert "--to-oci-layout" in text
    assert "inspect-oci" in text
    assert "allowlist_args+=(--allowlist" in text
    assert "--image-allowlist" not in text
    assert "evaluate-trivy" in text
    assert "trivy version --format json" in text
    assert "trivy-native.json" in text
    assert "{schema_version: 1, scan: $scan[0], version: $version[0]}" in text
    assert "sha256sum" in text
    legacy_release_path = "/".join(("", "tmp", "release-build"))
    assert legacy_release_path not in str(job)
    assert "OCI_ARCHIVE=$RUNNER_TEMP/release-build/image.oci.tar" in text
    assert "OCI_LAYOUT=$RUNNER_TEMP/release-build/oci-layout" in text
    assert "steps.evidence-paths.outputs.archive" in str(inputs["outputs"])


def test_pinned_gh_binary_is_checked_for_required_release_and_attestation_commands() -> None:
    workflow = _load(REUSABLE)
    text = "\n".join(
        _run_text(workflow["jobs"][name]) for name in ("prepare", "publish-attest", "finalize")
    )
    for token in (
        "release verify --help",
        "release verify-asset --help",
        "attestation verify --help",
        "attestation download --help",
        "attestation trusted-root --help",
    ):
        assert text.count(token) == 3


def test_publish_verifies_artifact_before_registry_login_or_write() -> None:
    job = _load(REUSABLE)["jobs"]["publish-attest"]
    assert _step_index(job, "Verify immutable OCI evidence") < _step_index(job, "Log in to GHCR")
    assert _step_index(job, "Log in to GHCR") < _step_index(job, "Publish source-SHA alias")
    text = _run_text(job)
    assert "sha256sum -c" in text
    assert "oras cp --from-oci-layout" in text
    assert "source alias digest mismatch" in text
    assert "published_digest=" in text


def test_source_alias_precedes_provenance_and_spdx_attestations() -> None:
    job = _load(REUSABLE)["jobs"]["publish-attest"]
    push = _step_index(job, "Publish source-SHA alias")
    provenance = _step_index(job, "Attest build provenance")
    sbom = _step_index(job, "Attest SPDX SBOM")
    assert push < provenance < sbom
    steps = _steps(job)
    provenance_step = steps[provenance]
    sbom_step = steps[sbom]
    assert provenance_step["with"]["subject-digest"].startswith("sha256:")
    assert provenance_step["with"]["push-to-registry"] is True
    assert sbom_step["with"]["sbom-path"].endswith("sbom.spdx.json")
    assert sbom_step["with"]["push-to-registry"] is True
    text = _run_text(job)
    assert "--predicate-type https://slsa.dev/provenance/v1" in text
    assert "--predicate-type https://spdx.dev/Document/v2.3" in text
    assert "attestation download" in text
    assert "attestation trusted-root" in text
    assert "attestation-bundle.json" in text
    assert "trusted-root.json" in text
    assert "application/vnd.dev.sigstore.trustedroot" not in text


def test_capture_uses_published_digest_and_assemble_is_read_only() -> None:
    workflow = _load(REUSABLE)
    capture = _run_text(workflow["jobs"]["capture"])
    assert "needs.publish-attest.outputs.published_digest" in str(workflow["jobs"]["capture"])
    assert "docker pull" in capture and "@${PUBLISHED_DIGEST}" in capture
    assert 'method":"tools/list"' in capture
    assert "mcp-tools-a.json" in capture
    assert "mcp-tools-b.json" in capture
    assert "jq -er '.result.tools | type == \"array\" and length > 0'" in capture
    assert "printf '[]'" not in capture
    assert "capture-definitions" in capture
    assemble = _run_text(workflow["jobs"]["assemble-evidence"])
    assert "assemble-manifest" in assemble
    assert "sha256sum" in assemble


def test_finalize_handles_draft_recovery_then_aliases_identical_manifest() -> None:
    job = _load(REUSABLE)["jobs"]["finalize"]
    text = _run_text(job)
    for token in (
        "matching draft assets",
        "mismatched draft assets",
        "gh release create",
        "--draft",
        "gh release edit",
        "--draft=false",
        "release-assets",
        "gh release verify",
        "gh release verify-asset",
        "oras cp",
        "version alias digest mismatch",
        "missing version alias",
    ):
        assert token in text
    assert 'cp "$manifest" "$expected"/' in text
    assert 'diff -qr "$expected" "$RUNNER_TEMP/draft"' in text
    assert '"$expected"/*' in text
    assert '"$assets" "$RUNNER_TEMP/draft"' not in text
    assert text.index("gh release edit") < text.index("oras cp")
    all_text = REUSABLE.read_text(encoding="utf-8")
    assert "--clobber" not in all_text
    assert "imagetools create" not in all_text
    assert "docker manifest" not in all_text
    assert "docker save" not in all_text
    assert "docker load" not in all_text


def test_gate_containers_receive_the_declared_smoke_environment() -> None:
    """Both gate containers must apply the repo's declared smoke environment.

    The router refuses to bind a non-loopback address without an explicit auth and
    allowed-hosts configuration, so a gate that runs the image bare can never reach
    /health or /mcp. The environment is read from the caller's container-release.json
    rather than hardcoded, because backends declare none. Now that both gates start the
    stack through Compose, `render-smoke-override` is what carries the declared
    environment onto the application service; it must therefore be handed the config.
    """
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        render = next(
            step
            for step in _steps(workflow["jobs"][job_name])
            if "render-smoke-override" in str(step.get("run", ""))
        )
        assert "--config container-release.json" in str(render["run"]), job_name


def test_release_gates_bring_up_the_declared_sidecar_bearing_smoke_stack() -> None:
    """Both release gates must start the same Compose stack `_container-ci.yml` proves.

    A bare `docker run` starts the application alone: no PostgreSQL sidecar, no data-init
    sidecar, no populated volume. Every data-bearing backend would pass its PR checks and
    then fail the release gate, and release tags are immutable, so each failure burns a
    version. Compose starts the declared sidecars through the app's `depends_on` graph and
    honours `service_completed_successfully` / `service_healthy` before the app runs.
    """
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        job = workflow["jobs"][job_name]
        run_text = _run_text(job)
        assert ".preparation" in run_text, job_name
        assert 'test "$preparation" = "docker/ci-prepare-smoke.sh"' in run_text, job_name
        assert "render-smoke-override" in run_text, job_name
        assert "--host-port" in run_text, job_name
        assert ".service.compose_files[0]" in run_text, job_name
        assert ".service.startup_timeout_seconds" in run_text, job_name
        assert "up --detach --no-build --wait --wait-timeout" in run_text, job_name
        assert "fixture-manifest.sha256" in run_text, job_name
        # The application image is never started outside the composed stack.
        assert "docker run" not in _commands(job), job_name


def test_release_gates_smoke_the_exact_image_under_release() -> None:
    """The gated stack must run the exact image, never one rebuilt by Compose.

    `build-gate` smokes the local tag imported from the gated OCI layout; `capture` smokes
    the published digest. `--no-build` and the override's `pull_policy: never` keep Compose
    from substituting anything else.
    """
    workflow = _load(REUSABLE)
    build_gate = _run_text(workflow["jobs"]["build-gate"])
    capture = _run_text(workflow["jobs"]["capture"])

    assert '--image "$CI_IMAGE"' in build_gate
    assert '--image "$IMAGE@$PUBLISHED_DIGEST"' in capture
    assert "docker compose" in build_gate and "--no-build" in build_gate
    assert "docker compose" in capture and "--no-build" in capture


def test_build_gate_asserts_hardening_on_the_composed_application_container() -> None:
    """The hardening and MCP assertions must survive the move onto Compose.

    They are now made against the application container Compose started, resolved with
    `docker compose ps -q`, rather than against a container id returned by `docker run`.
    """
    run_text = _run_text(_load(REUSABLE)["jobs"]["build-gate"])

    assert 'ps -q "$service"' in run_text
    assert "docker inspect" in run_text
    assert '.[0].Config.User != "" and .[0].Config.User != "0" and .[0].Config.User != "root"' in (
        run_text
    )
    assert ".[0].HostConfig.ReadonlyRootfs" in run_text
    assert '.[0].HostConfig.CapDrop | index("ALL") != null' in run_text
    assert "no-new-privileges" in run_text
    assert '"method":"initialize"' in run_text
    assert "grep -Fq '\"result\"'" in run_text


def test_capture_keeps_two_isolated_contexts_on_distinct_ports_and_projects() -> None:
    """The two capture contexts prove definition stability and must not collide.

    Each context now brings up a whole Compose stack with its own named volumes and
    sidecars, so each needs its own project name as well as its own loopback port.
    """
    capture = _run_text(_load(REUSABLE)["jobs"]["capture"])

    assert "capture_tools a " in capture
    assert "capture_tools b " in capture
    assert "18000" in capture and "18001" in capture
    assert "--project-name" in capture
    assert "mcp-tools-a.json" in capture and "mcp-tools-b.json" in capture


def test_release_gates_always_tear_down_their_smoke_stacks() -> None:
    """A failed gate must not leak containers, networks, or populated volumes."""
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        teardown = next(
            step for step in _steps(workflow["jobs"][job_name]) if step.get("id") == "teardown"
        )
        assert teardown["if"] == "${{ always() }}", job_name
        assert "docker compose" in str(teardown["run"]), job_name
        assert " down " in str(teardown["run"]), job_name
        # The scanner and SBOM still need the gated image after the stack is gone.
        assert "docker image rm" not in str(teardown["run"]), job_name


def test_publish_addresses_the_oci_layout_by_digest_not_ref_name() -> None:
    """Publication must copy the exact gated digest out of the layout.

    A fresh buildx `type=oci` export normalizes a bare tag and annotates the manifest
    `org.opencontainers.image.ref.name: latest`, so addressing the layout by the source
    alias resolves only on the recovery path and fails on every real build. The digest
    is identical on both paths and is already asserted against the layout index.
    """
    publish = _run_text(_load(REUSABLE)["jobs"]["publish-attest"])

    assert 'oci-layout@$EXPECTED_DIGEST"' in publish
    assert 'oci-layout:$SOURCE_ALIAS"' not in publish


def test_attestation_verify_never_pairs_mutually_exclusive_signer_flags() -> None:
    """`gh attestation verify` rejects --signer-repo together with --signer-workflow.

    They belong to one mutually exclusive identity group. --signer-workflow is the
    stronger binding and already names the repository, so it is the one we keep; it
    must be fully qualified as [host/]<owner>/<repo>/<path>/<to>/<workflow>.
    """
    workflow = _load(REUSABLE)
    text = "\n".join(_run_text(job) for job in workflow["jobs"].values())

    assert "--signer-repo" not in text
    assert (
        "--signer-workflow berntpopp/genefoundry-router/.github/workflows/"
        "_container-release.yml" in text
    )


def test_scanner_identity_is_read_from_trivy_version_not_the_scan_report() -> None:
    """Scanner evidence must come from `trivy version`, not the scan report.

    The scan report of an OCI archive carries no ArtifactName or Metadata.DB, so reading
    them yielded null and sealed the literal string "null" as the database timestamp,
    which is not RFC3339 and failed manifest validation after the image was already
    published. `version` is the scanner's version, not the scanned artifact's name.
    """
    assemble = _run_text(_load(REUSABLE)["jobs"]["assemble-evidence"])

    assert "trivy-version.json" in assemble
    assert ".ArtifactName" not in assemble
    assert ".Metadata.DB.UpdatedAt" not in assemble


def test_finalize_names_the_repository_without_a_working_tree() -> None:
    """finalize is privileged and never checks out source, so `gh` cannot infer the repo.

    Without GH_REPO every `gh release` call fails with "not a git repository". The fix is
    to name the repository, not to hand a privileged job a working tree.
    """
    finalize = _load(REUSABLE)["jobs"]["finalize"]

    assert finalize["env"]["GH_REPO"] == "${{ github.repository }}"
    assert not any("checkout" in str(step.get("uses", "")) for step in _steps(finalize))


def test_release_verification_tolerates_asynchronous_attestation() -> None:
    """GitHub mints the immutable-release attestation asynchronously after publication.

    Verifying immediately races it and fails with "no attestations for tag", after the
    image is already published and the evidence sealed. Both the recovery probe and the
    finalize gate must retry rather than fail on the first miss.
    """
    workflow = _load(REUSABLE)

    for job_name in ("prepare", "finalize"):
        run_text = _run_text(workflow["jobs"][job_name])
        assert "release attestation not yet published; retry" in run_text, job_name


def test_every_job_that_pushes_to_ghcr_authenticates_first() -> None:
    """A job with packages: write that pushes must log in to GHCR.

    finalize held packages: write and pushed the version alias with `oras cp`, but never
    authenticated, so the final step of the final job failed with "denied" after the image,
    release, and attestations had all published. Credentials are acquired only after the
    sealed evidence is verified, which is why the login sits immediately before the push.
    """
    workflow = _load(REUSABLE)

    for name, job in workflow["jobs"].items():
        pushes = "oras cp" in _run_text(job) or "docker push" in _run_text(job)
        if not pushes:
            continue
        logs_in = any("docker/login-action" in str(step.get("uses", "")) for step in _steps(job))
        assert logs_in, f"{name} pushes to GHCR without authenticating"


def test_release_gates_probe_the_declared_paths() -> None:
    """The release gates must probe `.service.health_path` / `.mcp_path`, not fixed paths.

    _container-ci.yml already reads them, but the release gate hardcoded /health and /mcp.
    stringdb declares /api/health and only passed because its app happens to serve both; a
    backend serving only its declared path would exhaust the 90s wait loop and fail.
    """
    workflow = _load(REUSABLE)

    for job_name in ("build-gate", "capture"):
        run_text = _run_text(workflow["jobs"][job_name])
        assert ".service.health_path" in run_text, job_name
        assert ".service.mcp_path" in run_text, job_name
        assert "18000/health" not in run_text, job_name
        assert "${host_port}/health" not in run_text, job_name


def test_no_job_level_env_uses_the_runner_context() -> None:
    """`runner` is not a valid context in `jobs.<job_id>.env`.

    Only github, needs, strategy, matrix, vars, secrets and inputs are. Referencing
    runner.temp there is an invalid-context error that kills the workflow before any job
    starts: the run ends with zero jobs and "This run likely failed because of a workflow
    file issue". This silently disabled Container CI across the whole fleet — every
    backend's required gate reported failure while never executing a single job, and the
    release runs kept passing because the release workflow happened not to have it. Use
    the $RUNNER_TEMP shell variable, or a step-level env, instead.
    """
    for path in (REUSABLE, ROOT / ".github/workflows/_container-ci.yml"):
        workflow = _load(path)
        for name, job in workflow["jobs"].items():
            for key, value in (job.get("env") or {}).items():
                assert "runner." not in str(value), f"{path.name}:{name}.env.{key} = {value}"


def test_release_evidence_states_the_declared_data_contract() -> None:
    """Signed evidence must state the data binding the repository actually declares.

    The workflow hardcoded `--contract data-independent` and a fixed {"mode":"none"}
    data_requirements, so every data-bearing backend published a manifest claiming it binds
    to no data at all while pinned to an immutable bundle.
    """
    capture = _run_text(_load(REUSABLE)["jobs"]["capture"])

    assert "--contract data-independent" not in capture
    assert ".definitions.contract" in capture
    assert "--data-release-tag" in capture and "--data-digest" in capture


def test_local_smoke_verifies_adopted_runtime_identity_after_health_capture() -> None:
    """An adopted release must prove readiness identity before publication."""
    build_gate = _executable_shell(
        _step_run(_load(REUSABLE)["jobs"]["build-gate"], "release-gate-health.json")
    )
    _assert_runtime_identity_boundary(
        build_gate,
        0,
        "$RUNNER_TEMP/release-gate-health.json",
        "$RUNNER_TEMP/smoke-observed-data-identity.json",
    )


def test_published_capture_uses_observed_identity_only_for_runtime_v1() -> None:
    """Published evidence separates observed adoption from the explicit legacy path."""
    capture = _executable_shell(_step_run(_load(REUSABLE)["jobs"]["capture"], "capture_tools()"))
    _assert_runtime_identity_boundary(
        capture,
        1,
        "$RUNNER_TEMP/capture/a-health.json",
        "$RUNNER_TEMP/capture/observed-data-identity.json",
    )
    runtime_branch = _if_branch(
        capture,
        RUNTIME_V1_CONDITION,
        f"elif {UNADOPTED_CONDITION}; then",
    )
    unadopted_branch = _elif_branch(
        capture,
        UNADOPTED_CONDITION,
        f"elif {DATA_INDEPENDENT_CONDITION}; then",
    )

    assert '--observed-identity "$RUNNER_TEMP/capture/observed-data-identity.json"' in (
        runtime_branch
    )

    assert "verify-runtime-data-identity" not in unadopted_branch
    assert "--observed-identity" not in unadopted_branch
    assert "--data-release-tag" in unadopted_branch
    assert "--data-digest" in unadopted_branch


def test_release_identity_shell_branches_are_quoted_and_fail_closed() -> None:
    """Only the three valid contract/adoption pairings may reach publication evidence."""
    workflow = _load(REUSABLE)
    for job_name, fragment in (
        ("build-gate", "release-gate-health.json"),
        ("capture", "capture_tools()"),
    ):
        shell = _executable_shell(_step_run(workflow["jobs"][job_name], fragment))
        assert "set -euo pipefail" in shell
        assert f"if {RUNTIME_V1_CONDITION}; then" in shell
        assert f"elif {UNADOPTED_CONDITION}; then" in shell
        assert f"elif {DATA_INDEPENDENT_CONDITION}; then" in shell
        assert "unsupported data identity contract state" in shell
        assert "exit 1" in shell


def test_runtime_verification_never_logs_complete_health_payloads() -> None:
    workflow = _load(REUSABLE)
    paths = (
        "$RUNNER_TEMP/release-gate-health.json",
        "$RUNNER_TEMP/capture/a-health.json",
    )
    shell = _executable_shell(
        "\n".join(
            (
                _step_run(workflow["jobs"]["build-gate"], "release-gate-health.json"),
                _step_run(workflow["jobs"]["capture"], "capture_tools()"),
            )
        )
    )

    for path in paths:
        assert f'cat "{path}"' not in shell
        assert f'jq . "{path}"' not in shell
        assert f'echo "$(<"{path}")"' not in shell


@pytest.mark.parametrize(
    ("boundaries", "mutation"),
    (
        *(((boundary,), mutation) for boundary in (0, 1) for mutation in ("comment", "remove")),
        *(((0, 1), mutation) for mutation in ("comment", "remove")),
        *(
            ((boundary,), mutation)
            for boundary in (0, 1)
            for mutation in ("if-false", "heredoc", "inline-noop")
        ),
    ),
)
def test_runtime_identity_contract_rejects_disabled_verifier(
    boundaries: tuple[int, ...], mutation: str
) -> None:
    workflow = _load(REUSABLE)
    _mutate_workflow_verifiers(workflow, boundaries, mutation)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


@pytest.mark.parametrize("boundaries", ((0,), (1,), (0, 1)))
def test_runtime_identity_contract_rejects_inline_comment_producer_noop(
    boundaries: tuple[int, ...],
) -> None:
    workflow = _load(REUSABLE)
    _mutate_workflow_producers(workflow, boundaries)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


@pytest.mark.parametrize("boundary", (0, 1))
@pytest.mark.parametrize("part", ("producer", "conditional"))
@pytest.mark.parametrize("wrapper", ("if-false", "heredoc", "function", "false-and"))
def test_runtime_identity_contract_rejects_outer_nonexecuting_context(
    boundary: int, part: str, wrapper: str
) -> None:
    workflow = _load(REUSABLE)
    _wrap_workflow_boundary_part(workflow, boundary, part, wrapper)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


@pytest.mark.parametrize("boundary", (0, 1))
@pytest.mark.parametrize("rewrite", ("overwrite", "reproduce"))
def test_runtime_identity_contract_rejects_health_rewrite_before_verification(
    boundary: int, rewrite: str
) -> None:
    workflow = _load(REUSABLE)
    _insert_health_rewrite(workflow, boundary, rewrite)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


@pytest.mark.parametrize("mutation", ("config-overwrite", "inline-noop"))
def test_runtime_identity_contract_rejects_untrusted_capture_function_health(
    mutation: str,
) -> None:
    workflow = _load(REUSABLE)
    _mutate_capture_function_health(workflow, mutation)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


@pytest.mark.parametrize(
    ("boundary", "sequence"),
    (
        (
            0,
            _verifier_command(
                "$RUNNER_TEMP/release-gate-health.json",
                "$RUNNER_TEMP/smoke-observed-data-identity.json",
            ),
        ),
        (
            1,
            _verifier_command(
                "$RUNNER_TEMP/capture/a-health.json",
                "$RUNNER_TEMP/capture/observed-data-identity.json",
            ),
        ),
        (0, RUNTIME_IDENTITY_PRODUCERS[0]),
        (1, CAPTURE_FUNCTION_HEALTH_PRODUCER),
    ),
)
def test_runtime_identity_contract_rejects_comment_after_continuation(
    boundary: int, sequence: tuple[str, ...]
) -> None:
    workflow = _load(REUSABLE)
    _insert_comment_after_continuation(workflow, boundary, sequence)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


def test_executable_shell_ignores_ordinary_comments() -> None:
    assert _executable_shell("echo before\n# ordinary comment\necho after\n") == (
        "echo before\necho after"
    )


@pytest.mark.parametrize("boundary", (0, 1))
@pytest.mark.parametrize(
    "mutation",
    (
        "if-metadata",
        "continue-on-error",
        "shell-suffix",
        "if-false-wrapper",
        "subshell-wrapper",
        "set +e",
        "set +o errexit",
    ),
)
def test_runtime_identity_contract_rejects_weakened_step_context(
    boundary: int, mutation: str
) -> None:
    workflow = _load(REUSABLE)
    _mutate_step_context(workflow, boundary, mutation)

    with pytest.raises(AssertionError):
        _assert_workflow_verifiers(workflow)


def test_capture_projects_top_level_adoption_into_sealed_data_requirements() -> None:
    capture = _executable_shell(_step_run(_load(REUSABLE)["jobs"]["capture"], "capture_tools()"))

    assert ". as $release" in capture
    assert "{data_identity_contract: $release.data_identity_contract}" in capture
    assert 'container-release.json > "$RUNNER_TEMP/capture/data-requirements.json"' in capture


def test_capture_takes_the_context_count_its_contract_requires() -> None:
    """data-independent needs exactly two capture contexts; data-bound exactly one.

    The library enforces both counts. The workflow captured two unconditionally, so a
    data-bound release died in `capture` -- which runs AFTER publish-attest, meaning the
    image and attestation were already pushed and the immutable version tag burned.
    """
    capture = _executable_shell(_step_run(_load(REUSABLE)["jobs"]["capture"], "capture_tools()"))
    assert "capture_args=(" in capture
    runtime_branch = _if_branch(
        capture,
        RUNTIME_V1_CONDITION,
        f"elif {UNADOPTED_CONDITION}; then",
    )
    unadopted_branch = _elif_branch(
        capture,
        UNADOPTED_CONDITION,
        f"elif {DATA_INDEPENDENT_CONDITION}; then",
    )
    independent_branch = _elif_branch(capture, DATA_INDEPENDENT_CONDITION, "else")

    assert "capture_tools b" not in runtime_branch
    assert "capture_tools b" not in unadopted_branch
    assert "capture_tools b" in independent_branch


def test_assemble_evidence_consumes_only_sealed_artifacts() -> None:
    """assemble-evidence checks out no caller source, so it must read no caller file.

    Reading container-release.json there made `jq` exit 2 under `set -euo pipefail` and
    killed every release -- after publish-attest had pushed the image and burned the tag.
    The data contract must reach this job as sealed evidence from `capture`, which is the
    last job that still holds the caller source.
    """
    job = _load(REUSABLE)["jobs"]["assemble-evidence"]
    caller_checkout = any(
        "actions/checkout" in str(step.get("uses", "")) and not (step.get("with") or {}).get("path")
        for step in _steps(job)
    )

    assert not caller_checkout, "assemble-evidence must not check out the caller source"
    assert "container-release.json" not in _run_text(job)
