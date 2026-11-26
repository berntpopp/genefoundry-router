"""Suite-wide fixtures.

Structlog isolation: a test that starts the server calls ``configure_logging()`` with
``cache_logger_on_first_use=True`` and then exercises the module-level
``observability.audit_log`` proxy — caching a bound JSON→stdout logger on it.
Once cached, ``structlog.testing.capture_logs()`` in a later test can no longer
intercept that logger, so audit-log assertions saw zero captured events (and the
line leaked to real stdout). Reset structlog and hand back a fresh, uncached proxy
before every test so ``capture_logs`` works regardless of prior collection order.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import structlog

import genefoundry_router.observability as observability


@pytest.fixture(autouse=True)
def _reset_community_breakers() -> Iterator[None]:
    from genefoundry_router import community_breaker

    community_breaker._BREAKERS.clear()
    yield
    community_breaker._BREAKERS.clear()


@pytest.fixture(autouse=True)
def _isolate_structlog() -> Iterator[None]:
    structlog.reset_defaults()
    observability.audit_log = structlog.get_logger("genefoundry.audit")
    try:
        yield
    finally:
        structlog.reset_defaults()
        observability.audit_log = structlog.get_logger("genefoundry.audit")
