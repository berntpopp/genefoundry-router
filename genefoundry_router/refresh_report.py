"""Aggregate-only report construction for the durable refresh ledger."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DAY_SECONDS = 24 * 60 * 60
MINIMUM_ATTEMPTS = 50
MAX_RESTART_INTERVALS = 100


class RefreshReportUnavailableError(RuntimeError):
    """The configured ledger cannot be inspected safely in read-only mode."""


@dataclass(frozen=True, slots=True)
class RestartInterval:
    """One bounded router availability interval, without its internal boot ID."""

    started_at: float
    ended_at: float | None
    clean_shutdown: bool
    duration_seconds: float
    version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "clean_shutdown": self.clean_shutdown,
            "duration_seconds": self.duration_seconds,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class RefreshReport:
    """Bounded aggregate report; never contains hashes or per-client identifiers."""

    generated_at: float
    schema_version: int
    window_days: int
    sample_status: str
    decision: str
    observation_seconds: float
    consecutive_observation_seconds: float
    attempts: int
    successes: int
    failures: int
    failure_rate: float
    attempts_by_client_class: dict[str, int]
    failures_by_reason: dict[str, int]
    affected_clients: int
    reuse_delay_buckets: dict[str, int]
    restart_intervals: tuple[RestartInterval, ...]
    subsequent_authorizations: int
    forced_reauthorization_clients: int
    material_rotation_failures: int
    material_rotation_rate: float

    def to_dict(self) -> dict[str, object]:
        """Return JSON-ready aggregate data with no row-level fields."""
        return {
            "generated_at": self.generated_at,
            "schema_version": self.schema_version,
            "window_days": self.window_days,
            "sample_status": self.sample_status,
            "decision": self.decision,
            "observation_seconds": self.observation_seconds,
            "consecutive_observation_seconds": self.consecutive_observation_seconds,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "failure_rate": self.failure_rate,
            "attempts_by_client_class": dict(self.attempts_by_client_class),
            "failures_by_reason": dict(self.failures_by_reason),
            "affected_clients": self.affected_clients,
            "reuse_delay_buckets": dict(self.reuse_delay_buckets),
            "restart_intervals": [interval.to_dict() for interval in self.restart_intervals],
            "subsequent_authorizations": self.subsequent_authorizations,
            "forced_reauthorization_clients": self.forced_reauthorization_clients,
            "material_rotation_failures": self.material_rotation_failures,
            "material_rotation_rate": self.material_rotation_rate,
        }


def _lifecycle_intervals(
    connection: sqlite3.Connection, now: float
) -> tuple[tuple[RestartInterval, ...], float]:
    rows = connection.execute(
        "SELECT boot_id, marker, at, version FROM router_lifecycle ORDER BY at, id"
    ).fetchall()
    starts: dict[str, tuple[float, str]] = {}
    shutdowns: dict[str, float] = {}
    order: list[str] = []
    for row in rows:
        boot_id = str(row["boot_id"])
        if row["marker"] == "startup":
            starts[boot_id] = (float(row["at"]), str(row["version"]))
            order.append(boot_id)
        else:
            shutdowns[boot_id] = float(row["at"])

    latest = order[-1] if order else None
    intervals: list[RestartInterval] = []
    consecutive = 0.0
    for boot_id in order[-MAX_RESTART_INTERVALS:]:
        started_at, version = starts[boot_id]
        ended_at = shutdowns.get(boot_id)
        clean = ended_at is not None
        # Only the latest open interval is known to be available through ``now``.
        effective_end = ended_at if ended_at is not None else (now if boot_id == latest else None)
        duration = max(0.0, effective_end - started_at) if effective_end is not None else 0.0
        intervals.append(RestartInterval(started_at, ended_at, clean, duration, version))
        if boot_id == latest and ended_at is None:
            consecutive = duration
    return tuple(intervals), consecutive


def _attempts_since(connection: sqlite3.Connection, cutoff: float, now: float) -> int:
    return int(
        connection.execute(
            """
            SELECT COUNT(*) FROM refresh_events
            WHERE event_type='refresh' AND at >= ? AND at <= ?
            """,
            (cutoff, now),
        ).fetchone()[0]
    )


def _select_window(
    connection: sqlite3.Connection, now: float, consecutive: float
) -> tuple[int, str, float]:
    seven_cutoff = now - 7 * DAY_SECONDS
    seven_attempts = _attempts_since(connection, seven_cutoff, now)
    if consecutive < 7 * DAY_SECONDS:
        return 7, "collecting", seven_cutoff
    if seven_attempts >= MINIMUM_ATTEMPTS:
        return 7, "ready", seven_cutoff

    fourteen_cutoff = now - 14 * DAY_SECONDS
    if consecutive < 14 * DAY_SECONDS:
        return 14, "extended", fourteen_cutoff
    status = (
        "ready"
        if _attempts_since(connection, fourteen_cutoff, now) >= MINIMUM_ATTEMPTS
        else "insufficient_sample"
    )
    return 14, status, fourteen_cutoff


def _observation_seconds(
    intervals: tuple[RestartInterval, ...], cutoff: float, now: float
) -> float:
    observed = 0.0
    for index, interval in enumerate(intervals):
        end = interval.ended_at
        if end is None and index == len(intervals) - 1:
            end = now
        if end is not None:
            observed += max(0.0, min(end, now) - max(interval.started_at, cutoff))
    return observed


def build_refresh_report(
    connection: sqlite3.Connection,
    *,
    now: float,
    schema_version: int,
    client_classes: frozenset[str],
    failure_reasons: frozenset[str],
) -> RefreshReport:
    """Build the exact 7-day/50-attempt, 14-day-fallback decision report."""
    intervals, consecutive = _lifecycle_intervals(connection, now)
    window_days, sample_status, cutoff = _select_window(connection, now, consecutive)
    params = (cutoff, now)
    event_filter = "event_type='refresh' AND at >= ? AND at <= ?"
    rows = connection.execute(
        f"""
        SELECT client_class, outcome, reason, COUNT(*) AS n
        FROM refresh_events WHERE {event_filter}
        GROUP BY client_class, outcome, reason
        """,  # noqa: S608 - fixed internal fragment, parameters remain bound
        params,
    ).fetchall()

    attempts_by_class = dict.fromkeys(sorted(client_classes), 0)
    failures_by_reason = dict.fromkeys(sorted(failure_reasons), 0)
    successes = failures = 0
    for row in rows:
        count = int(row["n"])
        client_class = str(row["client_class"])
        attempts_by_class[client_class] += count
        if row["outcome"] == "success":
            successes += count
        elif row["outcome"] == "failure":
            failures += count
            failures_by_reason[str(row["reason"])] += count
    attempts = successes + failures

    affected_clients = int(
        connection.execute(
            f"""
            SELECT COUNT(DISTINCT client_hmac) FROM refresh_events
            WHERE {event_filter} AND outcome='failure'
            """,  # noqa: S608 - fixed internal fragment
            params,
        ).fetchone()[0]
    )
    delays = connection.execute(
        f"""
        SELECT reuse_delay_seconds FROM refresh_events
        WHERE {event_filter} AND reason='reuse_after_rotation'
          AND reuse_delay_seconds IS NOT NULL
        """,  # noqa: S608 - fixed internal fragment
        params,
    ).fetchall()
    delay_buckets = {
        "under_5s": 0,
        "5s_to_under_60s": 0,
        "1m_to_15m": 0,
        "over_15m": 0,
    }
    for row in delays:
        delay = float(row[0])
        bucket = (
            "under_5s"
            if delay < 5
            else "5s_to_under_60s"
            if delay < 60
            else "1m_to_15m"
            if delay <= 900
            else "over_15m"
        )
        delay_buckets[bucket] += 1

    auth_row = connection.execute(
        """
        SELECT COUNT(*) AS events, COUNT(DISTINCT a.client_hmac) AS clients
        FROM refresh_events AS a
        WHERE a.event_type='authorize' AND a.at >= ? AND a.at <= ?
          AND EXISTS (
            SELECT 1 FROM refresh_events AS f
            WHERE f.event_type='refresh' AND f.outcome='failure'
              AND f.client_hmac=a.client_hmac AND f.at >= ?
              AND f.at <= a.at AND a.at - f.at <= 900
          )
        """,
        (cutoff, now, cutoff),
    ).fetchone()
    subsequent_auth = int(auth_row["events"])
    forced_clients = int(auth_row["clients"])
    material_failures = (
        failures_by_reason["reuse_after_rotation"] + failures_by_reason["upstream_invalid_grant"]
    )
    material_rate = material_failures / attempts if attempts else 0.0
    if sample_status != "ready":
        decision = "collecting" if sample_status == "collecting" else "insufficient_sample"
    elif (material_failures >= 3 and material_rate > 0.01) or forced_clients >= 2:
        decision = "material"
    else:
        decision = "not_material"

    return RefreshReport(
        generated_at=now,
        schema_version=schema_version,
        window_days=window_days,
        sample_status=sample_status,
        decision=decision,
        observation_seconds=_observation_seconds(intervals, cutoff, now),
        consecutive_observation_seconds=consecutive,
        attempts=attempts,
        successes=successes,
        failures=failures,
        failure_rate=failures / attempts if attempts else 0.0,
        attempts_by_client_class=attempts_by_class,
        failures_by_reason=failures_by_reason,
        affected_clients=affected_clients,
        reuse_delay_buckets=delay_buckets,
        restart_intervals=intervals,
        subsequent_authorizations=subsequent_auth,
        forced_reauthorization_clients=forced_clients,
        material_rotation_failures=material_failures,
        material_rotation_rate=material_rate,
    )


def read_refresh_report(
    path: str | Path,
    *,
    now: float,
    schema_version: int,
    client_classes: frozenset[str],
    failure_reasons: frozenset[str],
) -> RefreshReport:
    """Read one aggregate report without creating or modifying the ledger."""
    source = Path(path)
    if not source.is_file():
        raise RefreshReportUnavailableError("configured refresh ledger is unavailable")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        actual_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if actual_version != schema_version:
            raise RefreshReportUnavailableError("refresh ledger schema version is unsupported")
        return build_refresh_report(
            connection,
            now=now,
            schema_version=schema_version,
            client_classes=client_classes,
            failure_reasons=failure_reasons,
        )
    except RefreshReportUnavailableError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise RefreshReportUnavailableError("configured refresh ledger is unavailable") from exc
    finally:
        if connection is not None:
            connection.close()
