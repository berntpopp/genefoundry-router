"""Unit-test fixtures.

Structlog isolation: the e2e/integration suites (which pytest collects before
``unit/``) start the server, which calls ``configure_logging()`` with
``cache_logger_on_first_use=True`` and then exercises the module-level
``observability.audit_log`` proxy — caching a bound JSON→stdout logger on it.
Once cached, ``structlog.testing.capture_logs()`` in the unit tests can no longer
intercept that logger, so audit-log assertions saw zero captured events (and the
line leaked to real stdout). Reset structlog and hand back a fresh, uncached proxy
before every unit test so ``capture_logs`` works regardless of prior collection order.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog

import genefoundry_router.observability as observability


@pytest.fixture(autouse=True)
def _isolate_structlog() -> Iterator[None]:
    structlog.reset_defaults()
    observability.audit_log = structlog.get_logger("genefoundry.audit")
    try:
        yield
    finally:
        structlog.reset_defaults()
        observability.audit_log = structlog.get_logger("genefoundry.audit")
