"""Bounded, caller-data-free detail lifted from a federated tool failure.

The audit record used to carry ``error_type=type(exc).__name__`` and nothing else. Because
the router is a proxy, every backend fault surfaces as the same
``fastmcp.exceptions.ToolError``: 168 of 172 recorded errors read ``"ToolError"``, so the
one field that was kept was the one field with no information in it. Nothing else closed
the gap either — MCP errors travel in-band, so every proxied backend call logged ``200 OK``
and there were zero non-2xx transport records to correlate with.

**Redact the input, not the error.** The docstring's original concern is right: arguments,
results and ``str(exc)`` can carry patient-derived data (variant coordinates, phenotype
text), and none of them may be logged. But the fleet already publishes exactly the bounded
part of the failure this module needs. Response-Envelope Standard v1 gives every error
result a CLOSED ``error_code`` enum plus a ``request_id``, and FastMCP's client puts that
serialized envelope into the ``ToolError`` message (verified against the installed fastmcp:
``client/mixins/tools.py::_parse_call_tool_result`` raises ``ToolError(content[0].text)``,
and ``ToolResult.to_mcp_result()`` serializes ``structured_content`` into that text).

So the extraction takes the closed enum, a bounded upstream HTTP status, and a bounded
correlation token — and leaves the human ``message`` behind. The backends already log in
this shape (``mcp_tool_error tool=… code=… request_id=… exc=…``); this adopts it rather
than inventing one.

**Everything returned here is bounded by construction.** An ``error_code`` is a fixed
literal from :data:`FLEET_ERROR_CODES` or the ``unclassified`` bucket; a status is an int in
the HTTP range; a request id matches a strict opaque-token pattern. That is what keeps a
backend- or caller-influenced string out of an operator's log (injection) and out of a
Prometheus label (cardinality) — the same rule ``observability.safe_log_identity`` already
applies to tool names.

**Nothing here may raise.** These run inside the audit path's ``except`` block; an exception
escaping would replace the backend's real error with the logger's own.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Response-Envelope Standard v1's closed error enum, harmonized across the fleet.
FLEET_ERROR_CODES: frozenset[str] = frozenset(
    {
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    }
)

#: Bucket for anything not in the closed enum: a non-envelope message, a malformed payload,
#: a code the fleet does not define. Deliberately NOT ``internal`` — that is a real fleet
#: code meaning "server-side fault", and conflating "the backend said internal" with "we
#: could not tell" would put a false diagnosis in the audit trail.
UNCLASSIFIED_ERROR_CODE = "unclassified"

#: An upstream correlation id is joined across systems, so it must be an opaque bounded
#: token — hex/uuid shaped — never a free-text field an upstream (or a caller reflected by
#: one) could fill with prose, CRLF, or control characters.
_REQUEST_ID_PATTERN = re.compile(r"\A[0-9a-fA-F][0-9a-fA-F-]{7,63}\Z")

#: Depth limit when walking ``__cause__``/``__context__``; a chain can be cyclic or long.
_CAUSE_CHAIN_LIMIT = 8


def _envelope(exc: BaseException) -> dict[str, Any] | None:
    """Parse the error envelope out of an exception message, or ``None``."""
    try:
        payload = json.loads(str(exc))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def error_code(exc: BaseException) -> str:
    """Return one bounded error code for ``exc``; never caller data, never unbounded.

    Reads the flat ``error_code``/``code`` frame the fleet emits, and the nested
    ``error.code`` variant, and admits a value ONLY if it is a member of the closed enum.
    """
    payload = _envelope(exc)
    if payload is None:
        return UNCLASSIFIED_ERROR_CODE
    candidates = [payload.get("error_code"), payload.get("code")]
    nested = payload.get("error")
    if isinstance(nested, dict):
        candidates.extend([nested.get("error_code"), nested.get("code")])
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in FLEET_ERROR_CODES:
            return candidate
    return UNCLASSIFIED_ERROR_CODE


def upstream_status(exc: BaseException) -> int | None:
    """Return the proxied HTTP status for a transport-level failure, else ``None``.

    ``None`` is the common and correct answer: MCP errors are in-band, so a backend fault
    normally arrives over a ``200``. A status therefore MEANS "the transport itself failed",
    which is precisely the distinction the audit record could not previously make. FastMCP
    re-raises as ``ToolError``, so the httpx error is usually only reachable via the cause
    chain.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(_CAUSE_CHAIN_LIMIT):
        if current is None or id(current) in seen:
            return None
        seen.add(id(current))
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        # bool is an int subclass; exclude it so `status_code = True` cannot render as 1.
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
            return status
        current = current.__cause__ or current.__context__
    return None


def upstream_request_id(exc: BaseException) -> str | None:
    """Return the backend's correlation id when it is a bounded opaque token, else ``None``.

    This is what lets one audit line be joined to the backend's own log for the same call —
    the join the audit trail previously could not make at all.
    """
    payload = _envelope(exc)
    if payload is None:
        return None
    candidate = payload.get("request_id")
    if isinstance(candidate, str) and _REQUEST_ID_PATTERN.match(candidate):
        return candidate
    return None
