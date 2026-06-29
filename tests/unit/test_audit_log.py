"""PII-safe audit logging: record who/what/when, never the query arguments (GDPR Art. 30/32)."""

import structlog

from genefoundry_router.observability import AuditLogMiddleware


class _Msg:
    def __init__(self, name: str, arguments: dict | None = None) -> None:
        self.name = name
        self.arguments = arguments or {}


class _Ctx:
    def __init__(self, name: str, arguments: dict | None = None) -> None:
        self.message = _Msg(name, arguments)


async def test_audit_log_records_tool_and_namespace_without_args() -> None:
    mw = AuditLogMiddleware()

    async def _ok(_ctx):
        return "result"

    with structlog.testing.capture_logs() as logs:
        await mw.on_call_tool(_Ctx("gnomad_search_genes", {"gene_symbol": "BRCA1"}), _ok)

    events = [entry for entry in logs if entry.get("event") == "tool_call"]
    assert events, logs
    e = events[0]
    assert e["tool"] == "gnomad_search_genes"
    assert e["namespace"] == "gnomad"
    assert e["outcome"] == "ok"
    # PII safety: no argument key, and the value never appears anywhere in the entry.
    assert "arguments" not in e and "args" not in e
    assert "BRCA1" not in str(e)


async def test_audit_log_marks_errors_and_reraises_without_leaking_message() -> None:
    mw = AuditLogMiddleware()

    async def _boom(_ctx):
        raise RuntimeError("variant 17-43000000-A-G not found")  # message could echo PII

    raised = False
    with structlog.testing.capture_logs() as logs:
        try:
            await mw.on_call_tool(_Ctx("vep_annotate_variant"), _boom)
        except RuntimeError:
            raised = True

    assert raised  # must not swallow the error
    events = [entry for entry in logs if entry.get("event") == "tool_call"]
    assert events and events[0]["outcome"] == "error"
    assert events[0]["error_type"] == "RuntimeError"
    assert "17-43000000-A-G" not in str(events[0])  # never log the message body
