"""Render the deployed Compose overlay the way the fleet controller deploys it.

The deployed overlay names its image through a required `${<PROJECT>_IMAGE:?...}`
variable, so it cannot be rendered at all without values. This module supplies inert,
shape-correct placeholders for every required variable, and returns both the interpolated
model (what the rules are checked against) and the `--no-interpolate` model (the only
place the image *template* survives).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

__all__ = ["ComposeRenderError", "placeholder_value", "render_deployed_overlay"]

_TIMEOUT_SECONDS = 120
_MAX_OUTPUT_BYTES = 32 * 1024 * 1024
_PLACEHOLDER_HEX = "a" * 64
_PLACEHOLDER_IMAGE = f"ghcr.io/example/placeholder@sha256:{_PLACEHOLDER_HEX}"
_ENV_ALLOWLIST = frozenset({"HOME", "PATH", "DOCKER_CONFIG", "DOCKER_HOST", "XDG_RUNTIME_DIR"})


class ComposeRenderError(RuntimeError):
    """The deployed overlay could not be rendered by `docker compose config`."""


def placeholder_value(variable: str) -> str:
    """Return an inert value shaped like what `variable` is required to carry."""
    if variable.endswith("_IMAGE") or variable.endswith("_IMAGE_REF"):
        return _PLACEHOLDER_IMAGE
    if "EXPANDED_SHA256" in variable or variable.endswith("SHA256"):
        return _PLACEHOLDER_HEX
    if "DIGEST" in variable:
        return f"sha256:{_PLACEHOLDER_HEX}"
    if variable.endswith("_URL") or variable.endswith("_URI"):
        return "https://example.test/placeholder"
    return "example.test"


def _run(project_dir: Path, args: Sequence[str], extra_env: Mapping[str, str] | None = None) -> str:
    environment = {key: value for key, value in os.environ.items() if key in _ENV_ALLOWLIST}
    environment.update({"NO_COLOR": "1", "COMPOSE_PROGRESS": "plain"})
    environment.update(extra_env or {})
    try:
        completed = subprocess.run(  # noqa: S603
            ["docker", "compose", *args],  # noqa: S607 -- the CI runner PATH is trusted
            check=False,
            capture_output=True,
            cwd=project_dir,
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ComposeRenderError("docker compose could not be executed") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ComposeRenderError(detail[-1] if detail else "docker compose config failed")
    if len(completed.stdout) > _MAX_OUTPUT_BYTES:
        raise ComposeRenderError("rendered Compose model is implausibly large")
    return completed.stdout.decode("utf-8", "strict")


def _file_args(compose_files: Sequence[str]) -> list[str]:
    args: list[str] = []
    for name in compose_files:
        args.extend(["-f", name])
    return args


def required_variables(project_dir: Path, compose_files: Sequence[str]) -> tuple[str, ...]:
    """Return every variable the overlay declares as required (`${X:?...}`)."""
    listing = _run(project_dir, [*_file_args(compose_files), "config", "--variables"])
    required: list[str] = []
    for line in listing.splitlines()[1:]:
        columns = line.split()
        if len(columns) >= 2 and columns[1] == "true":
            required.append(columns[0])
    return tuple(dict.fromkeys(required))


def _load(document: str) -> Mapping[str, object]:
    value = json.loads(document)
    if not isinstance(value, dict):
        raise ComposeRenderError("rendered Compose model is not a JSON object")
    return value


def render_deployed_overlay(
    project_dir: Path, compose_files: Sequence[str]
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Return the (interpolated, non-interpolated) rendered deployed Compose models."""
    for name in compose_files:
        candidate = project_dir / name
        if candidate.is_symlink() or not candidate.is_file():
            raise ComposeRenderError(f"{name} is not a regular file in this repository")
    files = _file_args(compose_files)
    raw = _load(_run(project_dir, [*files, "config", "--no-interpolate", "--format", "json"]))
    placeholders = {
        variable: placeholder_value(variable)
        for variable in required_variables(project_dir, compose_files)
    }
    rendered = _load(_run(project_dir, [*files, "config", "--format", "json"], placeholders))
    return rendered, raw
