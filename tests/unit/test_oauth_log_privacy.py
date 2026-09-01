"""The OAuth privacy filter must redact the VALUE, not the REASON.

``OAuthProxyPrivacyFilter`` replaced the entire ``record.msg`` with one fixed sentence for
every one of its 44 markers. In production that produced 279 identical, information-free
lines. The decisive case: during the 2026-08-26 21:00-23:59Z burst (67 of 114 consecutive
``POST /token`` 401s from one client) the ENTIRE router-side log for the window was

    29 x "OAuth detail omitted (sensitive value redacted)."   proxy.py:1391
     2 x "Bearer token rejected for client"                   jwt.py:506

``proxy.py:1391`` is fastmcp's::

    logger.warning(
        "Refresh token not found for client=%s (token_hash=%s); it was already rotated, "
        "expired, or revoked. Rejecting with invalid_grant, which forces the client to "
        "re-authenticate.",
        client.client_id, token_hash[:8],
    )

Everything the operator needed — *that this was a refresh-token miss and that the client
must re-authorise* — is in the TEMPLATE. The secrets are in the ARGS. The filter deleted
the template and kept nothing.

``notfound_guard`` already learned this lesson (its docstring records that blanking
unrelated diagnostics "cost hours on 2026-08-07") and solved it with arg-token
interpolation. These tests hold the OAuth filter to the same contract, and add the fence
that makes keeping the template SAFE: a ``%``-style logging template is a compile-time
string literal, so it cannot contain a runtime secret — but only if the framework never
mixes an f-string template with lazy args. ``test_fastmcp_oauth_loggers_never_mix_fstring_and_args``
is what keeps that assumption true across a fastmcp upgrade.

These tests drive the REAL entry point against the REAL logging topology, for the reason
``test_log_scrub_rendering`` documents: FastMCP owns non-propagating loggers with their own
handlers, so a plain ``caplog`` assertion can pass while production burns.
"""

from __future__ import annotations

import ast
import io
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from genefoundry_router.observability import (
    _OAUTH_REDACTED_MESSAGE,
    _OAUTH_SENSITIVE_MARKERS,
    OAUTH_PRIVACY_LOGGERS,
    configure_logging,
    install_oauth_proxy_privacy_filter,
)

# A value that must never reach a sink: it stands in for a client_id, a token hash, a
# redirect URI, or an upstream error body.
SENSITIVE_VALUE = "sensitive-c0ffee-value"

# The exact production call site from the 2026-08-26 incident (fastmcp proxy.py:1391).
REFRESH_MISS_TEMPLATE = (
    "Refresh token not found for client=%s (token_hash=%s); it was already rotated, "
    "expired, or revoked. Rejecting with invalid_grant, which forces the client to "
    "re-authenticate."
)


@pytest.fixture
def oauth_logs() -> Iterator[Callable[[], str]]:
    """Rebuild the router's real logging topology and return a reader of what it rendered."""
    buf = io.StringIO()
    root = logging.getLogger()
    # "fastmcp" owns NON-PROPAGATING Rich handlers; without neutralising the parent these
    # records never reach the root buffer at all and every assertion would read "".
    touched = [root, logging.getLogger("fastmcp")]
    touched += [logging.getLogger(name) for name in OAUTH_PRIVACY_LOGGERS]
    saved = [(lg, lg.handlers[:], lg.level, lg.propagate, lg.filters[:]) for lg in touched]

    configure_logging("DEBUG")
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root.addHandler(handler)
    for lg in touched[1:]:
        lg.handlers[:] = []
        lg.propagate = True
        lg.setLevel(logging.DEBUG)

    install_oauth_proxy_privacy_filter()
    try:
        yield buf.getvalue
    finally:
        for lg, handlers, level, propagate, filters in saved:
            lg.handlers[:] = handlers
            lg.setLevel(level)
            lg.propagate = propagate
            lg.filters[:] = filters


def _emit(template: str, *args: object, level: int = logging.WARNING) -> None:
    logging.getLogger("fastmcp.server.auth.oauth_proxy.proxy").log(level, template, *args)


def test_oauth_filter_keeps_template_prose(oauth_logs: Callable[[], str]) -> None:
    """THE regression. The refresh-miss diagnostic must survive redaction and stay readable."""
    _emit(REFRESH_MISS_TEMPLATE, "client-abc", SENSITIVE_VALUE)
    out = oauth_logs()

    # Non-vacuity first: a dropped record would satisfy every "not in" below.
    assert "Refresh token not found" in out
    # The actionable half of the template — what the operator is supposed to DO.
    assert "re-authenticate" in out
    assert "already rotated, expired, or revoked" in out
    # ...and none of the values.
    assert SENSITIVE_VALUE not in out
    assert "client-abc" not in out
    # A redacted record must READ as one, not as a broken formatter.
    assert "%s" not in out


@pytest.mark.parametrize("marker", _OAUTH_SENSITIVE_MARKERS)
def test_oauth_filter_redacts_every_arg(marker: str, oauth_logs: Callable[[], str]) -> None:
    """No marker may let an arg value through, whatever the template looks like."""
    _emit(f"{marker} %s", SENSITIVE_VALUE)
    assert SENSITIVE_VALUE not in oauth_logs()


def test_oauth_filter_never_raises_on_unformattable_template(
    oauth_logs: Callable[[], str],
) -> None:
    """A redaction may never raise inside a logging handler. ``%d`` cannot take the string
    redaction token, so such a record degrades to the fixed message instead of erroring."""
    _emit("Refresh token not found for client=%s after %d seconds", SENSITIVE_VALUE, 12)
    out = oauth_logs()

    assert _OAUTH_REDACTED_MESSAGE in out
    assert SENSITIVE_VALUE not in out
    assert "%d" not in out


def test_oauth_filter_replaces_wholesale_when_the_value_is_in_the_message(
    oauth_logs: Callable[[], str],
) -> None:
    """The one shape where the template itself is unsafe: an f-string already interpolated
    the value into ``record.msg`` (args is empty), so clearing args protects nothing and the
    WHOLE message must go. fastmcp does this at proxy.py:2381 ("Forwarding to client
    callback for transaction {tid}")."""
    _emit(f"Forwarding to client callback for transaction {SENSITIVE_VALUE}")
    out = oauth_logs()

    assert _OAUTH_REDACTED_MESSAGE in out
    assert SENSITIVE_VALUE not in out


def test_non_marker_oauth_record_is_untouched(oauth_logs: Callable[[], str]) -> None:
    """The filter stays narrow: a record matching no marker renders its args normally."""
    _emit("upstream token endpoint responded in %d ms", 42)
    assert "upstream token endpoint responded in 42 ms" in oauth_logs()


def _log_call_sites(path: Path) -> Iterator[tuple[int, bool, int]]:
    """Yield ``(lineno, template_is_runtime_formatted, n_args)`` per ``logger.X(...)`` call."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
            continue
        first = node.args[0]
        runtime_formatted = isinstance(first, ast.JoinedStr | ast.BinOp)
        yield node.lineno, runtime_formatted, len(node.args) - 1


def _oauth_module_paths() -> list[Path]:
    import fastmcp

    root = Path(os.path.dirname(fastmcp.__file__)).parent
    paths = []
    for name in OAUTH_PRIVACY_LOGGERS:
        candidate = root / (name.replace(".", "/") + ".py")
        if candidate.is_file():
            paths.append(candidate)
    return paths


def test_fastmcp_oauth_loggers_never_mix_fstring_and_args() -> None:
    """The fence under the whole design.

    Keeping the template is safe because a ``%``-style template is a compile-time literal:
    it CANNOT hold a runtime secret. The filter distinguishes the two shapes at runtime by
    ``record.args`` — non-empty args means a literal template, so interpolate a token and
    keep the prose; empty args means the message may have been f-string-formatted, so drop
    it whole. That inference breaks for exactly one shape: an f-string template passed
    TOGETHER with lazy args. Assert the framework never writes one, so a fastmcp upgrade
    that introduces it fails here instead of silently leaking.
    """
    paths = _oauth_module_paths()
    assert len(paths) >= 4, f"expected to resolve the filtered OAuth modules, got {paths}"

    offenders = [
        f"{path}:{lineno}"
        for path in paths
        for lineno, runtime_formatted, n_args in _log_call_sites(path)
        if runtime_formatted and n_args
    ]
    assert not offenders, (
        "fastmcp now logs a runtime-formatted template together with lazy args; the OAuth "
        f"privacy filter would keep that template verbatim: {offenders}"
    )


def test_marker_templates_are_reachable_in_the_installed_fastmcp() -> None:
    """A marker that no longer matches any call site redacts nothing and is dead weight —
    and, worse, hides that the real message moved and is now unfiltered."""
    sources = "\n".join(path.read_text(encoding="utf-8") for path in _oauth_module_paths())
    missing = [marker for marker in _OAUTH_SENSITIVE_MARKERS if marker not in sources]
    assert not missing, f"markers no longer present in the installed fastmcp: {missing}"
