"""Caller authorization for state-changing federated tools."""

from __future__ import annotations

from typing import Any

import structlog
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

log = structlog.get_logger(__name__)

PUBTATOR_WRITE_TOOLS = frozenset(
    {
        "pubtator_index_review_evidence",
        "pubtator_ground_question",
        "pubtator_record_review_context",
        "pubtator_stage_research_session",
        "pubtator_review_quickstart",
        "pubtator_add_evidence_certainty",
        "pubtator_submit_text_annotation",
        "pubtator_export_review_audit_bundle",
    }
)


class WriteAuthorizationMiddleware(Middleware):
    """Require a backend-specific caller scope before PubTator writes."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        name = getattr(context.message, "name", "")
        if name in PUBTATOR_WRITE_TOOLS:
            token = get_access_token()
            scopes = set(token.scopes) if token is not None else set()
            if "pubtator:write" not in scopes:
                log.warning(
                    "write_authorization_denied",
                    tool=name,
                    required_scope="pubtator:write",
                )
                raise ToolError("This tool requires the pubtator:write scope")
        return await call_next(context)
