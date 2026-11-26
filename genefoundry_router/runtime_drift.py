"""Runtime enforcement for the reviewed normalized fleet catalog."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal

import structlog
from fastmcp.server.providers.proxy import ClientFactoryT, ProxyProvider
from fastmcp.server.transforms import GetToolNext, Transform, VersionSpec
from fastmcp.tools import Tool

from genefoundry_router.config import RouterSettings
from genefoundry_router.devtools.fakes import load_manifest
from genefoundry_router.drift import (
    DriftReport,
    ToolDefinition,
    canonical_json_schema,
    detect_drift,
    manifest_fingerprints,
    tool_fingerprint,
)
from genefoundry_router.exceptions import RegistryError, StartupError
from genefoundry_router.observability import COMMUNITY_QUARANTINES, set_drift_metrics
from genefoundry_router.registry import BackendDef

DriftMode = Literal["off", "warn", "enforce"]
DriftPhase = Literal["startup", "poll"]

log = structlog.get_logger(__name__)

# Federation v1 §4: the kill-switch and latch files are re-read at most this often on calls.
CALL_PATH_RELOAD_SECONDS = 5.0
_active: RuntimeDriftGuard | None = None


def _namespace(name: str) -> str:
    return name.split("_", 1)[0]


class RuntimeDriftGuard(Transform):
    """Compare each harvested catalog with the reviewed definition pin."""

    def __init__(self, pinned: dict[str, str], mode: DriftMode) -> None:
        self.pinned = dict(pinned)
        self.mode = mode
        self.last_report = DriftReport(added=[], removed=[], changed=[])
        self.last_check_timestamp = 0.0
        self.degraded = False
        self.quarantined: set[str] = set()
        # Community backends (Federation v1 §4): namespace -> pinned fingerprints, the pin's
        # digest, and the latched whole-backend quarantines (namespace -> entry).
        self.community: dict[str, dict[str, str]] = {}
        self.pin_digest: dict[str, str] = {}
        self.latched: dict[str, dict[str, Any]] = {}
        # Released backends stay blocked until a harvest matches their pin again.
        self.unverified: set[str] = set()
        self.kill_switch_file: Path | None = None
        self.state_file: Path | None = None
        self._reloaded_at = 0.0

    def _blocked(self, name: str) -> bool:
        ns = _namespace(name)
        return name in self.quarantined or ns in self.latched or ns in self.unverified

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Filter out tools currently quarantined from public listing."""
        self._reload()
        return [tool for tool in tools if not self._blocked(tool.name)]

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        """Reject quarantined tools immediately with zero upstream RPCs."""
        self._reload()
        if self._blocked(name):
            return None
        tool = await call_next(name, version=version)
        return None if self._blocked(name) else tool

    def bind_community(
        self,
        backends: Sequence[BackendDef],
        *,
        pins_dir: str | Path,
        kill_switch_file: str | Path | None = None,
        state_file: str | Path | None = None,
    ) -> None:
        """Load each community backend's pin file and make this the active guard."""
        global _active
        _active = self
        for backend in backends:
            if backend.trust != "community":
                continue
            path = Path(pins_dir) / f"{backend.namespace}.json"
            self.community[backend.namespace] = load_pin(path, backend.namespace)
            self.pin_digest[backend.namespace] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.kill_switch_file = Path(kill_switch_file) if kill_switch_file else None
        self.state_file = Path(state_file) if state_file else None
        if self.community and self.state_file is None:
            log.warning("community_quarantine_not_persisted", reason="GF_QUARANTINE_FILE unset")
        self._reload(force=True)

    def latch(self, namespace: str, reason: str) -> None:
        """Quarantine a whole community backend until a re-pin or an explicit release."""
        if namespace not in self.community or namespace in self.latched:
            return
        self.latched[namespace] = {
            "reason": reason,
            "pin": self.pin_digest[namespace],
            "at": time.time(),
        }
        log.critical("community_backend_quarantined", namespace=namespace, reason=reason)
        COMMUNITY_QUARANTINES.labels(namespace=namespace, reason=reason).inc()
        if self.state_file is not None:
            try:
                write_atomic(self.state_file, json.dumps(self.latched, sort_keys=True, indent=2))
            except OSError as exc:
                log.error("community_quarantine_write_failed", error_type=type(exc).__name__)

    def check_harvest(self, namespace: str, tools: Sequence[Tool]) -> None:
        """Latch a community backend whose upstream definitions differ from its pin."""
        self._reload(force=True)
        if namespace not in self.community or namespace in self.latched or not tools:
            return  # an empty harvest is an outage, not drift
        live = fingerprint_definitions(upstream_definitions(namespace, tools))
        report = detect_drift(live, self.community[namespace])
        if report.has_drift:
            log.critical(
                "community_definition_drift",
                namespace=namespace,
                added=report.added,
                removed=report.removed,
                changed=report.changed,
            )
            self.latch(namespace, "definition_drift")
        else:
            self.unverified.discard(namespace)

    def _reload(self, *, force: bool = False) -> None:
        """Re-read the latch state (``release`` edits it) and the kill switch."""
        if not self.community:
            return
        now = time.monotonic()
        if not force and now - self._reloaded_at < CALL_PATH_RELOAD_SECONDS:
            return
        self._reloaded_at = now
        if self.state_file is not None:
            try:
                stored = json.loads(self.state_file.read_text(encoding="utf-8"))
            except FileNotFoundError:
                stored = {}
            except (OSError, ValueError) as exc:  # fail closed: keep the current latches
                log.error("community_quarantine_read_failed", error_type=type(exc).__name__)
                stored = self.latched
            # A changed pin file (merged re-pin) clears that backend's latch.
            before = set(self.latched)
            self.latched = {
                ns: entry
                for ns, entry in stored.items()
                if ns in self.community and entry.get("pin") == self.pin_digest[ns]
            }
            self.unverified |= before - set(self.latched)
        for namespace in read_kill_switch(self.kill_switch_file):
            self.latch(namespace, "kill_switch")

    def evaluate(
        self,
        current: dict[str, str],
        *,
        phase: DriftPhase,
        unreachable: set[str],
    ) -> DriftReport:
        self.last_check_timestamp = time.time()
        self._reload(force=True)
        # Community backends are checked per harvest against their own pins (check_harvest).
        current = {n: d for n, d in current.items() if _namespace(n) not in self.community}
        if self.mode == "off":
            self.last_report = DriftReport(added=[], removed=[], changed=[])
            self.degraded = False
            self.quarantined.clear()
            set_drift_metrics(changed=0, added=0, removed=0, timestamp=self.last_check_timestamp)
            return self.last_report

        def reachable(definitions: dict[str, str]) -> dict[str, str]:
            return {
                name: digest
                for name, digest in definitions.items()
                if name.split("_", 1)[0] not in unreachable
            }

        reachable_current = reachable(current)

        # Recover pinned tools whose current digest matches, and unpinned tools that were
        # rolled back/removed from a reachable backend.
        for name in list(self.quarantined):
            if name.split("_", 1)[0] in unreachable:
                continue
            if name in self.pinned:
                if reachable_current.get(name) == self.pinned[name]:
                    self.quarantined.discard(name)
            elif name not in reachable_current:
                self.quarantined.discard(name)

        report = detect_drift(reachable_current, reachable(self.pinned))
        self.last_report = report

        for name in report.added:
            self.quarantined.add(name)
        if phase == "poll":
            for name in report.changed:
                self.quarantined.add(name)

        self.degraded = report.has_drift or bool(self.quarantined) or bool(self.latched)

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


def upstream_definitions(namespace: str, tools: Sequence[Tool]) -> dict[str, ToolDefinition]:
    """Qualified definitions of a backend's raw upstream tools (before router transforms)."""
    return {
        f"{namespace}_{name}": definition.model_copy(update={"name": f"{namespace}_{name}"})
        for name, definition in definitions_from_tools(tools).items()
    }


async def harvest_upstream(client_factory: ClientFactoryT) -> list[Tool]:
    """One backend's ``tools/list``, exactly as the router's proxy provider receives it."""
    return list(await ProxyProvider(client_factory, cache_ttl=0)._list_tools())


def pin_json(namespace: str, tools: Sequence[Tool]) -> str:
    """Canonical pin file text: the full normalised upstream definitions of one backend."""
    definitions = upstream_definitions(namespace, tools)
    payload = {
        "namespace": namespace,
        "tools": [
            {
                **definitions[name].model_dump(mode="json"),
                "inputSchema": canonical_json_schema(definitions[name].inputSchema),
                "outputSchema": canonical_json_schema(definitions[name].outputSchema),
            }
            for name in sorted(definitions)
        ],
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def load_pin(path: Path, namespace: str) -> dict[str, str]:
    """Fingerprints derived from a pin file that may hold only its own namespace."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tools = [ToolDefinition.model_validate(tool) for tool in data["tools"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RegistryError(f"community pin {path}: {exc}") from exc
    if data.get("namespace") != namespace or any(
        not tool.name.startswith(f"{namespace}_") for tool in tools
    ):
        raise RegistryError(f"community pin {path} holds a namespace other than {namespace!r}")
    return {tool.name: tool_fingerprint(tool) for tool in tools}


def write_atomic(path: str | Path, text: str) -> None:
    """Replace ``path`` with ``text`` so a reader never sees a partial file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_kill_switch(path: Path | None) -> set[str]:
    """Namespaces listed in ``GF_KILL_SWITCH_FILE`` (one per line, ``#`` comments)."""
    if path is None:
        return set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    return {line.split("#", 1)[0].strip() for line in lines} - {""}


def release_backend(state_file: str | Path, namespace: str) -> bool:
    """Maintainer release: drop one latch from the state file. True if one was dropped."""
    path = Path(state_file)
    try:
        latched = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    if latched.pop(namespace, None) is None:
        return False
    write_atomic(path, json.dumps(latched, sort_keys=True, indent=2))
    return True


def quarantine_backend(namespace: str, reason: str) -> None:
    """Latch a community backend on the active guard (no-op for core namespaces)."""
    if _active is not None:
        _active.latch(namespace, reason)


def check_community_harvest(namespace: str, tools: Sequence[Tool]) -> None:
    """Called by the community proxy provider on every upstream ``tools/list``."""
    if _active is not None:
        _active.check_harvest(namespace, tools)
