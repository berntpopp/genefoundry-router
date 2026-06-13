"""Logging, health, and metrics for the router."""

from __future__ import annotations

import logging

import structlog
from fastapi import FastAPI

from genefoundry_router.registry import BackendDef

_LOG_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to emit JSON to stdout. Safe to call repeatedly."""
    global _LOG_CONFIGURED
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _LOG_CONFIGURED = True


def register_health(app: FastAPI, backends: list[BackendDef]) -> None:
    """Attach GET /health returning liveness + a per-backend summary."""
    enabled = [b for b in backends if b.enabled]

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "service": "genefoundry",
            "backends": {
                "total": len(backends),
                "enabled": len(enabled),
                "namespaces": [b.namespace for b in enabled],
            },
        }
