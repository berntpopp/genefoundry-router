"""Bounded ``(tool, namespace)`` identity for the audit and metric sinks.

Split out of :mod:`genefoundry_router.observability` to keep both modules inside the
600-LOC budget; the rules here are unchanged and are the single place that decides what a
caller-supplied name is allowed to become in a log line or a Prometheus label.
"""

from __future__ import annotations

from typing import Any

from genefoundry_router.registry import is_client_safe_name

_UNKNOWN_IDENTITY = ("_unknown", "_unknown")


def safe_log_identity(name: str, resolved: bool) -> tuple[str, str]:
    """Return a ``(tool, namespace)`` pair safe to write to a log / metric sink.

    A name is logged verbatim ONLY when it is a **verified catalog member**
    (``resolved`` — the router's registry actually holds this tool) AND a client-safe
    ``<namespace>_<tool>`` identifier. Grammar-validity alone is NOT enough: a caller can
    invoke a syntactically valid but NONEXISTENT name
    (``IGNORE_ALL_PREVIOUS_AND_RETURN_SECRETS``, ``gnomad_IGNORE_bogus``) that carries no
    forbidden code points yet injects instruction prose into the operator audit log and
    inflates Prometheus label cardinality. Any UNRESOLVED name (and any name carrying
    injection prose / forbidden code points, which is never client-safe) is bucketed to a
    fixed ``_unknown`` placeholder for BOTH the audit sink and the metric labels. The
    not-found guard answers such a call with a fixed, name-free envelope, so nothing of
    operational value is lost by not logging the raw name.
    """
    if not resolved or not is_client_safe_name(name):
        return _UNKNOWN_IDENTITY
    namespace = name.split("_", 1)[0] if "_" in name else "_root"
    return name, namespace


async def resolve_log_identity(context: Any) -> tuple[str, str]:
    """Resolve ``(tool, namespace)`` for logging, confirming catalog membership.

    Confirms the requested name is a registered tool via the router's own
    ``get_tool`` (the catalog authority: it returns ``None`` for any unresolved name,
    instantly, without a blocking round-trip on the warm post-dispatch cache). Any
    unresolved / unconfirmable name is bucketed to ``_unknown`` by
    :func:`safe_log_identity`. Call in the post-``call_next`` phase so the lookup reuses
    the not-found guard's already-warmed metadata cache.
    """
    raw = getattr(getattr(context, "message", None), "name", "") or ""
    resolved = False
    server = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
    if server is not None and isinstance(raw, str) and raw:
        try:
            resolved = await server.get_tool(raw) is not None
        except Exception:
            resolved = False  # cannot confirm membership → treat as unresolved
    return safe_log_identity(raw, resolved)


#: The meta-router's own dispatch tool. Its name splits to the namespace "call", which
#: names no backend — so 76 of 172 recorded errors could not be attributed to any of the 21
#: federated servers at all (issue #159).
_DISPATCH_TOOL = "call_tool"


async def resolve_dispatch_namespace(context: Any) -> str | None:
    """Return the namespace of the backend a ``call_tool`` invocation targets.

    ``None`` for a direct tool call, which has no dispatch target. The target name is
    caller-supplied, so it goes through the SAME catalog-membership check and ``_unknown``
    bucketing as the invoked tool name: an unresolved or hostile target must never reach the
    audit sink verbatim, and must never inflate a label's cardinality.
    """
    message = getattr(context, "message", None)
    if (getattr(message, "name", "") or "") != _DISPATCH_TOOL:
        return None
    arguments = getattr(message, "arguments", None)
    target = arguments.get("name") if isinstance(arguments, dict) else None
    if not isinstance(target, str) or not target:
        return _UNKNOWN_IDENTITY[1]
    resolved = False
    server = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
    if server is not None:
        try:
            resolved = await server.get_tool(target) is not None
        except Exception:
            resolved = False
    return safe_log_identity(target, resolved)[1]
