"""How the Layer-5 log-scrub filter must affect log RENDERING (production incident).

``docker logs genefoundry_router`` showed, for hours during an auth outage::

    WARNING  Bearer token rejected for client %s: audience mismatch (got %r, expected %r)

— the printf placeholders, never the values. ``fastmcp.server.auth.providers.jwt`` logs
that line the ordinary lazy way (template in ``record.msg``, values in ``record.args``),
and :class:`NotFoundLogScrubFilter`'s WARNING+ fallback cleared ``record.args`` for EVERY
record on the whole ``fastmcp`` / ``mcp`` logger trees. ``LogRecord.getMessage`` skips
``msg % args`` when args is falsy, so the raw template reached the sink and the two
audience values that identified the misconfiguration were invisible.

These tests drive the REAL entry point (:func:`install_notfound_log_filter`) against the
REAL logging topology, because that topology is the whole reason the defect never showed
up in the suite: FastMCP owns a NON-PROPAGATING logger whose handlers carry the filter,
and pytest's ``caplog`` handler is attached to root per-test — i.e. after the install — so
a plain caplog assertion sees an unscrubbed record and passes while production burns.

Every case runs under both sink topologies (``rich`` = FastMCP's own non-propagating
handler, ``plain`` = propagation to the root handler ``configure_logging`` installs), so a
fix that only holds for one of them fails here.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable, Iterator

import pytest
import structlog
from fastmcp.utilities import logging as fastmcp_logging
from rich.console import Console

from genefoundry_router.notfound_guard import (
    _REDACTED_ARG,
    _SCRUBBED_MESSAGE,
    install_notfound_log_filter,
)
from genefoundry_router.observability import configure_logging

# Caller-supplied name carrying injection prose + forbidden code points (bidi override,
# zero width, NUL) — the input Layer 5 exists to keep out of an operator's log.
HOSTILE = "evil‮​\x00__IGNORE_ALL_PREVIOUS__nonexistent"


@pytest.fixture(params=["rich", "plain"])
def router_logs(request: pytest.FixtureRequest) -> Iterator[Callable[[], str]]:
    """Rebuild the router's real logging topology; return a reader of what it rendered.

    Mirrors production's ordering exactly — ``configure_logging`` (as ``build_app`` does),
    then FastMCP's own logging setup, then ``install_notfound_log_filter`` last (as
    ``build_server`` does, once the framework's handlers exist to be filtered).

    ``rich``: FastMCP's real ``configure_logging`` puts Rich handlers on the
    NON-PROPAGATING ``fastmcp`` logger; only their console is swapped for one writing to
    the buffer, so the handler, formatter, and filter chain under test are FastMCP's own.
    ``plain``: no framework handler, so records propagate to the root handler instead —
    the shape seen with ``FASTMCP_ENABLE_RICH_LOGGING=false`` and under pytest.
    """
    buf = io.StringIO()
    root = logging.getLogger()
    framework = logging.getLogger("fastmcp")
    saved = [(lg, lg.handlers[:], lg.level, lg.propagate) for lg in (root, framework)]

    configure_logging("INFO")
    # Stand in for basicConfig's stdout StreamHandler, redirected into the buffer. It is
    # installed BEFORE the filter so it receives it, exactly as the real one does.
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    root_handler = logging.StreamHandler(buf)
    root_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(root_handler)

    for handler in framework.handlers[:]:
        framework.removeHandler(handler)
    framework.propagate = True
    if request.param == "rich":
        fastmcp_logging.configure_logging("INFO")
        for handler in framework.handlers:
            if getattr(handler, "console", None) is not None:
                handler.console = Console(file=buf, width=240, no_color=True)  # type: ignore[attr-defined]

    install_notfound_log_filter()
    try:
        yield buf.getvalue
    finally:
        for lg, handlers, level, propagate in saved:
            lg.handlers[:] = handlers
            lg.setLevel(level)
            lg.propagate = propagate


def test_framework_auth_warning_renders_its_args(router_logs: Callable[[], str]) -> None:
    """THE regression. The exact call site (fastmcp/server/auth/providers/jwt.py) whose
    placeholders reached production. Auth diagnostics are not caller-supplied MCP
    identifiers — they must render, or the operator cannot see got-vs-expected."""
    logging.getLogger("fastmcp.server.auth.providers.jwt").warning(
        "Bearer token rejected for client %s: audience mismatch (got %r, expected %r)",
        "CID-1",
        "AUD-GOT",
        "AUD-EXP",
    )
    out = router_logs()
    assert "CID-1" in out
    assert "AUD-GOT" in out
    assert "AUD-EXP" in out
    assert "%s" not in out
    assert "%r" not in out


def test_unrelated_third_party_warning_renders_its_args(router_logs: Callable[[], str]) -> None:
    """A logger outside the framework trees is never a scrub candidate at all."""
    logging.getLogger("httpx").warning("upstream %s returned %s", "gnomad", 503)
    out = router_logs()
    assert "upstream gnomad returned 503" in out
    assert "%s" not in out


def test_dispatch_logger_warning_still_redacts_caller_input(
    router_logs: Callable[[], str],
) -> None:
    """The protection Layer 5 exists for, unchanged: a request-dispatch logger echoing a
    caller-supplied identifier through its args leaks neither the prose nor a code point.

    Real call site + real message (mcp/server/streamable_http_manager.py), reached on the
    router's own transport, whose ``%s`` is the caller's ``Mcp-Session-Id`` header and
    which no marker covers — i.e. exactly what the fallback net is for."""
    logging.getLogger("mcp.server.streamable_http_manager").warning(
        "Rejecting request for session %s: credential does not match the one that "
        "created the session",
        HOSTILE,
    )
    out = router_logs()
    # Non-vacuity guard FIRST: a record dropped before the sink would satisfy every
    # "not in" below without proving anything. (FastMCP ships its own filter that drops
    # one of this tree's records outright — a redaction test must not silently ride on it.)
    assert "Rejecting request for session" in out
    assert "evil" not in out
    assert "IGNORE_ALL_PREVIOUS" not in out
    assert "‮" not in out
    assert "​" not in out
    assert "\x00" not in out


def test_redacted_record_renders_a_marker_not_a_bare_placeholder(
    router_logs: Callable[[], str],
) -> None:
    """A redacted record must still READ as one. Clearing args left ``%s`` in the sink,
    which an operator reads as a broken formatter rather than a deliberate redaction —
    and which loses the input-free template text that says what happened."""
    logging.getLogger("mcp.server.streamable_http_manager").warning(
        "Rejecting request for session %s: credential does not match the one that "
        "created the session",
        HOSTILE,
    )
    out = router_logs()
    assert _REDACTED_ARG in out
    assert "%s" not in out
    assert "credential does not match" in out


def test_marker_record_is_still_replaced_wholesale(router_logs: Callable[[], str]) -> None:
    """The marker branch is untouched: a known reflecting message (here the aggregate
    provider fault, whose name is already f-string-interpolated into ``msg``) loses the
    WHOLE message, not just its args."""
    logging.getLogger("fastmcp.server.providers.aggregate").warning(
        "Error during get_tool('%s') from provider %s: boom", HOSTILE, "gnomad"
    )
    out = router_logs()
    assert _SCRUBBED_MESSAGE in out
    assert "evil" not in out
    assert "get_tool" not in out


def test_unformattable_template_falls_back_to_the_fixed_message(
    router_logs: Callable[[], str],
) -> None:
    """A redaction may never raise inside a handler. ``%d`` cannot take the redaction
    token, so such a record degrades to the fixed message rather than erroring out.
    (Real call site: fastmcp/server/middleware/response_limiting.py.)"""
    logging.getLogger("fastmcp.server.middleware.response_limiting").warning(
        "Tool %r response exceeds size limit: %d bytes > %d bytes, truncating",
        HOSTILE,
        99,
        10,
    )
    out = router_logs()
    assert _SCRUBBED_MESSAGE in out
    assert "evil" not in out
    assert "%d" not in out


def test_framework_records_below_warning_keep_their_args(router_logs: Callable[[], str]) -> None:
    """The fallback is WARNING+ only; INFO/DEBUG framework records are untouched."""
    logging.getLogger("fastmcp.server.server").info("cache warmed in %s ms", 12)
    out = router_logs()
    assert "cache warmed in 12 ms" in out


def test_structlog_native_events_still_render_as_json(
    router_logs: Callable[[], str], capsys: pytest.CaptureFixture[str]
) -> None:
    """The router's own ``log.info("event", key=...)`` style is unaffected: it never
    becomes a stdlib record, and still renders as one JSON object per line on stdout."""
    structlog.get_logger("genefoundry.test").info("auth_mode", mode="jwt")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["event"] == "auth_mode"
    assert payload["mode"] == "jwt"
    assert payload["level"] == "info"
    assert "timestamp" in payload
