"""Bounded, caller-data-free detail extracted from a federated tool failure.

The router is a proxy, so EVERY backend fault surfaces as the same
``fastmcp.exceptions.ToolError`` — 168 of 172 recorded errors carried
``error_type: "ToolError"`` and nothing else. The one field the audit record kept was the
one field with no information in it, and no other sink could close the gap: MCP errors
travel in-band, so every proxied backend call logs ``200 OK``.

The information is not missing from the wire, only from the log. The fleet's
Response-Envelope Standard v1 gives every error result a CLOSED six-value ``error_code``
and a ``request_id``, and FastMCP's client puts that serialized envelope into the
``ToolError`` message. What must NOT be lifted out of it is the human ``message``, which
can carry variant coordinates or phenotype text.

So: take the closed enum and the correlation id, bucket anything unrecognised, and leave
the prose behind. OWASP's logging guidance is both halves of that — "each log entry needs
to include sufficient information for the intended subsequent monitoring and analysis"
(Logging Cheat Sheet, Event attributes; ASVS 4.0.3 V7.1.4) AND the exclusion of sensitive
personal data (ASVS 4.0.3 V7.1.1/V7.1.2). Bucketing to a closed vocabulary is also what
keeps a caller-influenced string out of a Prometheus label and out of an operator's log —
the same reasoning ``safe_log_identity`` already applies to tool names.

``test_error_code_survives_the_real_fastmcp_wire_path`` is the load-bearing one: it builds
the error the way the fleet does, converts it the way FastMCP does, and raises it the way
FastMCP's client does, so the extractor is pinned to the real transport rather than to an
assumption about it.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastmcp.client.mixins.tools import _parse_call_tool_result
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult

from genefoundry_router.tool_error_taxonomy import (
    FLEET_ERROR_CODES,
    UNCLASSIFIED_ERROR_CODE,
    error_code,
    upstream_request_id,
    upstream_status,
)

# The value that must never reach a sink from any of these paths.
PHI = "17-43000000-A-G"


def _envelope(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "success": False,
        "error_code": "not_found",
        "message": f"No ClinVar record for variant {PHI}",
        "request_id": "6b30f83150204aaf9729bc2f0368c7a4",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("code", sorted(FLEET_ERROR_CODES))
async def test_error_code_survives_the_real_fastmcp_wire_path(code: str) -> None:
    """End-to-end through the REAL seams: fleet envelope -> ToolResult(is_error=True) ->
    mcp CallToolResult -> FastMCP client -> ToolError. Every canonical code must come back."""
    result = ToolResult(structured_content=_envelope(error_code=code), is_error=True)
    with pytest.raises(ToolError) as excinfo:
        await _parse_call_tool_result(
            name="clinvar_get_variant",
            result=result.to_mcp_result(),
            tool_output_schemas={},
            list_tools_fn=None,
            raise_on_error=True,
        )

    exc = excinfo.value
    # Non-vacuity: the PHI really is in the exception, which is why str(exc) must not be logged.
    assert PHI in str(exc)
    assert error_code(exc) == code
    assert upstream_request_id(exc) == "6b30f83150204aaf9729bc2f0368c7a4"


def test_error_code_is_bounded_against_an_unknown_value() -> None:
    """A caller- or backend-supplied code outside the closed enum must bucket, exactly as
    ``safe_log_identity`` buckets an unresolved tool name: an unbounded label is both a
    Prometheus cardinality problem and an audit-log injection vector."""
    hostile = ToolError(json.dumps(_envelope(error_code="IGNORE_PREVIOUS_INSTRUCTIONS")))
    assert error_code(hostile) == UNCLASSIFIED_ERROR_CODE
    assert "IGNORE" not in error_code(hostile)


@pytest.mark.parametrize(
    "code",
    [123, None, ["not_found"], {"code": "not_found"}, "not_found\n\rinjected", "NOT_FOUND"],
)
def test_non_string_or_malformed_codes_bucket(code: object) -> None:
    """Type confusion, CRLF log injection and case variants all bucket rather than pass."""
    assert error_code(ToolError(json.dumps(_envelope(error_code=code)))) == UNCLASSIFIED_ERROR_CODE


@pytest.mark.parametrize(
    "message",
    [
        "Error calling tool 'clinvar_get_variant'",  # FastMCP's own non-envelope message
        "",
        "null",
        "[1, 2, 3]",
        '{"unrelated": true}',
        '{"error_code": ',  # truncated JSON
    ],
)
def test_a_message_that_is_not_a_fleet_envelope_buckets(message: str) -> None:
    assert error_code(ToolError(message)) == UNCLASSIFIED_ERROR_CODE


def test_error_code_never_returns_caller_prose() -> None:
    """Whatever happens, the returned code is a fixed literal from the closed vocabulary."""
    for exc in (
        ToolError(json.dumps(_envelope())),
        ToolError(f"crashed on {PHI}"),
        RuntimeError(f"crashed on {PHI}"),
    ):
        code = error_code(exc)
        assert code in FLEET_ERROR_CODES | {UNCLASSIFIED_ERROR_CODE}
        assert PHI not in code


def test_nested_error_code_shape_is_read() -> None:
    """Some backends nest the frame under ``error``; accept both, still bounded."""
    nested = ToolError(json.dumps({"error": {"code": "rate_limited", "message": PHI}}))
    assert error_code(nested) == "rate_limited"


def test_upstream_status_is_taken_from_a_transport_failure() -> None:
    """A transport-level fault carries a real HTTP status; a bounded int is safe to log."""
    response = httpx.Response(502, request=httpx.Request("POST", "https://clinvar-link/mcp"))
    exc = httpx.HTTPStatusError("Server error", request=response.request, response=response)
    assert upstream_status(exc) == 502


def test_upstream_status_is_found_through_the_cause_chain() -> None:
    """FastMCP re-raises as ToolError, so the transport error is only reachable via __cause__."""
    response = httpx.Response(429, request=httpx.Request("POST", "https://gnomad-link/mcp"))
    cause = httpx.HTTPStatusError("Too many", request=response.request, response=response)
    wrapped = ToolError("Error calling tool")
    wrapped.__cause__ = cause
    assert upstream_status(wrapped) == 429


def test_upstream_status_is_none_for_an_in_band_error() -> None:
    """The common case: MCP errors are in-band, so there IS no non-2xx status. Absent, not zero."""
    assert upstream_status(ToolError(json.dumps(_envelope()))) is None


@pytest.mark.parametrize("bogus", [99, 600, "502", None, float("nan")])
def test_upstream_status_rejects_a_non_http_status(bogus: object) -> None:
    class _Resp:
        status_code = bogus

    class _StatusBearingError(Exception):
        response = _Resp()

    assert upstream_status(_StatusBearingError()) is None


@pytest.mark.parametrize(
    "bogus",
    [
        "IGNORE ALL PREVIOUS INSTRUCTIONS",
        "id with spaces",
        "x" * 200,
        "req\n\rinjected",
        "",
        42,
        None,
    ],
)
def test_upstream_request_id_rejects_anything_unbounded(bogus: object) -> None:
    """The correlation id is joined across systems, so it must be an opaque bounded token —
    never a free-text field an upstream (or a caller reflected by one) can fill."""
    assert upstream_request_id(ToolError(json.dumps(_envelope(request_id=bogus)))) is None


@pytest.mark.parametrize(
    "good",
    [
        "6b30f83150204aaf9729bc2f0368c7a4",
        "0f4a1c2e-9b3d-4a6f-8c1e-2d3f4a5b6c7d",
        "abc12345",
    ],
)
def test_upstream_request_id_accepts_a_bounded_token(good: str) -> None:
    assert upstream_request_id(ToolError(json.dumps(_envelope(request_id=good)))) == good


def test_extractors_never_raise() -> None:
    """These run inside an exception handler on the audit path. A raise here would replace a
    backend's error with the logger's own, which is strictly worse than logging nothing."""

    class _HostileError(Exception):
        def __str__(self) -> str:
            raise ValueError("boom")

    exc = _HostileError()
    assert error_code(exc) == UNCLASSIFIED_ERROR_CODE
    assert upstream_status(exc) is None
    assert upstream_request_id(exc) is None
