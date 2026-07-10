"""Runtime enforcement for the reviewed normalized fleet catalog."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal

import structlog
from fastmcp.tools import Tool

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import load_manifest
from genefoundry_router.drift import (
    DriftReport,
    ToolDefinition,
    detect_drift,
    manifest_fingerprints,
    tool_fingerprint,
)
from genefoundry_router.exceptions import StartupError
from genefoundry_router.observability import set_drift_metrics

DriftMode = Literal["off", "warn", "enforce"]
DriftPhase = Literal["startup", "poll"]

log = structlog.get_logger(__name__)


class RuntimeDriftGuard:
    """Compare each harvested catalog with the reviewed definition pin."""

    def __init__(self, pinned: dict[str, str], mode: DriftMode) -> None:
        self.pinned = dict(pinned)
        self.mode = mode
        self.last_report = DriftReport(added=[], removed=[], changed=[])
        self.last_check_timestamp = 0.0
        self.degraded = False
        self.quarantined: frozenset[str] = frozenset()

    def evaluate(
        self,
        current: dict[str, str],
        *,
        phase: DriftPhase,
        unreachable: set[str],
    ) -> DriftReport:
        self.last_check_timestamp = time.time()
        if self.mode == "off":
            self.last_report = DriftReport(added=[], removed=[], changed=[])
            self.degraded = False
            self.quarantined = frozenset()
            set_drift_metrics(changed=0, added=0, removed=0, timestamp=self.last_check_timestamp)
            return self.last_report

        def reachable(definitions: dict[str, str]) -> dict[str, str]:
            return {
                name: digest
                for name, digest in definitions.items()
                if name.split("_", 1)[0] not in unreachable
            }

        report = detect_drift(reachable(current), reachable(self.pinned))
        self.last_report = report
        self.degraded = report.has_drift
        self.quarantined = frozenset(report.added) | (
            frozenset(report.changed) if phase == "poll" else frozenset()
        )
        self._log_report(report, phase)
        set_drift_metrics(
            changed=len(report.changed),
            added=len(report.added),
            removed=len(report.removed),
            timestamp=self.last_check_timestamp,
        )
        if self.mode == "enforce" and phase == "startup" and report.changed:
            names = ", ".join(report.changed)
            raise StartupError(f"changed tool definition: {names}")
        return report

    @staticmethod
    def _log_report(report: DriftReport, phase: DriftPhase) -> None:
        if report.changed:
            log.error("runtime_drift_changed", phase=phase, tools=report.changed)
        if report.added:
            log.warning("runtime_drift_added", phase=phase, tools=report.added)
        if report.removed:
            log.warning("runtime_drift_removed", phase=phase, tools=report.removed)


@contextmanager
def bundled_baseline() -> Iterator[Path]:
    """Yield the reviewed baseline as a real path for source and wheel installs."""
    resource = files("genefoundry_router.data").joinpath("fleet-baseline.json")
    with as_file(resource) as path:
        yield path


def load_runtime_guard(settings: RouterSettings) -> RuntimeDriftGuard:
    """Load an operator override or the reviewed baseline packaged in the wheel."""
    if settings.GF_DRIFT_MODE == "off":
        return RuntimeDriftGuard({}, "off")
    if settings.GF_DRIFT_BASELINE is not None:
        manifest = load_manifest(Path(settings.GF_DRIFT_BASELINE))
    else:
        with bundled_baseline() as path:
            manifest = load_manifest(path)
    return RuntimeDriftGuard(manifest_fingerprints(manifest), settings.GF_DRIFT_MODE)


def _model_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        result: dict[str, Any] = value.model_dump(mode="json", by_alias=True, exclude_none=False)
        return result
    return dict(value)


def definitions_from_tools(tools: Sequence[Tool]) -> dict[str, ToolDefinition]:
    """Capture the exact normalized definitions used for reachability and search."""
    return {
        tool.name: ToolDefinition(
            name=tool.name,
            description=tool.description or "",
            inputSchema=tool.parameters or {},
            outputSchema=tool.output_schema,
            annotations=_model_dict(tool.annotations),
            execution=_model_dict(tool.execution),
        )
        for tool in tools
    }


def fingerprint_definitions(
    definitions: dict[str, ToolDefinition],
) -> dict[str, str]:
    return {name: tool_fingerprint(tool) for name, tool in definitions.items()}
