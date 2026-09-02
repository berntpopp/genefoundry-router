"""The `validate-deployed-overlay` command: the fleet Compose contract gate.

Registered onto the shared release CLI from `cli.py` rather than defined inside it, so the
CLI module stays within the repository's 600-line-per-module budget. The shared result and
dispatch helpers are passed in, which also keeps the import one-directional.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import typer

from genefoundry_router.release.compose_deployed import (
    DeployedOverlayPolicy,
    DeployedSidecar,
    validate_deployed_file_set,
    validate_deployed_overlay,
)
from genefoundry_router.release.compose_render import (
    ComposeRenderError,
    render_deployed_overlay,
)
from genefoundry_router.release.models import ReleaseConfig
from genefoundry_router.release.vulnerabilities import ReleaseExitCode

__all__ = ["register"]


class _Result(Protocol):
    def __call__(self, payload: dict[str, object], exit_code: ReleaseExitCode) -> Any: ...


def _deployed_policy(parsed: ReleaseConfig) -> DeployedOverlayPolicy:
    return DeployedOverlayPolicy(
        container_port=parsed.service.container_port,
        sidecars=tuple(
            DeployedSidecar(name=sidecar.name, image=sidecar.image)
            for sidecar in parsed.service.deployed_sidecars
        ),
        seed_binds=frozenset(parsed.service.deployed_seed_binds),
    )


def register(
    app: typer.Typer,
    *,
    execute: Callable[[str, Callable[[], Any]], None],
    result: _Result,
    read_object: Callable[[Path], dict[str, Any]],
    verdict: Callable[[ReleaseExitCode], str],
) -> None:
    """Attach the deployed-overlay gate to the shared release CLI."""

    @app.command("validate-deployed-overlay")
    def validate_deployed_overlay_command(
        config: Path = typer.Option(..., "--config", help="Container release JSON."),
        project_dir: Path = typer.Option(
            Path(), "--project-dir", help="Repository root the deployed overlay renders from."
        ),
        compose_file: list[str] = typer.Option(
            None,
            "--compose-file",
            help="Override service.deployed_compose_files (repeatable, in overlay order).",
        ),
    ) -> None:
        """Validate the Compose overlay the fleet controller actually deploys."""

        def operation() -> Any:
            parsed = ReleaseConfig.model_validate(read_object(config))
            files = tuple(compose_file) if compose_file else parsed.service.deployed_compose_files

            def report(violations: tuple[str, ...]) -> Any:
                for violation in violations:
                    typer.echo(violation, err=True)
                code = ReleaseExitCode.POLICY_VIOLATION if violations else ReleaseExitCode.SUCCESS
                return result(
                    {
                        "compose_files": list(files),
                        "verdict": verdict(code),
                        "violations": list(violations),
                    },
                    code,
                )

            sources = {
                name: (project_dir / name).read_text(encoding="utf-8", errors="replace")
                for name in files
                if (project_dir / name).is_file() and not (project_dir / name).is_symlink()
            }
            # Checked before the render: an overlay meant to be layered may not render at
            # all on its own, and "declare deployed_compose_files" is the actionable message.
            declared = validate_deployed_file_set(sources, files)
            if declared:
                return report(declared)
            try:
                rendered, raw = render_deployed_overlay(project_dir, files)
            except ComposeRenderError as exc:
                return result(
                    {
                        "compose_files": list(files),
                        "reason": str(exc),
                        "verdict": "invalid_evidence",
                    },
                    ReleaseExitCode.INVALID_EVIDENCE,
                )
            return report(validate_deployed_overlay(rendered, raw, _deployed_policy(parsed)))

        execute("validate-deployed-overlay", operation)
