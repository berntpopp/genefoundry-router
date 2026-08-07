"""Read-only CLI surface for the OAuth refresh-rotation decision report."""

from __future__ import annotations

import json
import time

import typer

from genefoundry_router.config import RouterSettings
from genefoundry_router.refresh_observability import (
    CLIENT_CLASSES,
    FAILURE_REASONS,
    SCHEMA_VERSION,
)
from genefoundry_router.refresh_report import RefreshReportUnavailableError, read_refresh_report


def refresh_report_command(
    json_output: bool = typer.Option(False, "--json", help="Emit compact JSON."),
) -> None:
    """Print aggregate refresh-rotation evidence without modifying the ledger."""
    configured = RouterSettings().GF_REFRESH_OBSERVABILITY_DB
    if configured is None:
        typer.echo("refresh observability database is not configured", err=True)
        raise typer.Exit(1)
    try:
        report = read_refresh_report(
            configured,
            now=time.time(),
            schema_version=SCHEMA_VERSION,
            client_classes=CLIENT_CLASSES,
            failure_reasons=FAILURE_REASONS,
        )
    except RefreshReportUnavailableError:
        typer.echo("refresh observability database is unavailable", err=True)
        raise typer.Exit(1) from None
    indent = None if json_output else 2
    typer.echo(json.dumps(report.to_dict(), indent=indent, sort_keys=True))
