"""Namespace-aware rewriting of embedded tool references in backend responses.

Finding 1 (the one correctness bug in Claude's usage reports): the fleet invests in
self-healing hints — an error or empty result embeds ``fallback_tool`` and
``next_commands[].tool`` so an agent can recover. Each backend emits its own **bare**
leaf names (``search_genes``), because un-federated those are correct. The router
namespaces tool *names* at mount time but not tool *references inside payloads*, so an
agent that follows a hint via ``call_tool`` hits "Unknown tool" — the router only knows
``clingen_search_genes``.

This middleware closes that gap: it rewrites the known tool-reference fields to the same
``<namespace>_<leaf>`` form the tool itself was given, so the hint the agent reads is
already correct and the existing ``call_tool`` path works unchanged. It is convention-
coupled (the agentic-hints field names), not backend-coupled, and is removable on the
same lifecycle as the ``servers.yaml`` transform blocks.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any

import structlog
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.middleware import CallNext, MiddlewareContext
from fastmcp.tools.base import ToolResult

log = structlog.get_logger(__name__)

_SYNTHETIC = {"search_tools", "call_tool"}
# Keys whose string value is a tool reference (GeneFoundry agentic-hints convention).
# Captures top-level ``fallback_tool`` and each ``next_commands[].tool`` without guessing
# at free-text fields.
TOOL_REF_KEYS = {"tool", "fallback_tool", "tool_name", "next_tool"}
_BARE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Prose fields whose text names sibling tools ("Try search_genes.") — issue #299. A key is
# prose when it IS one of these words or ENDS in ``_<word>`` (gnomad's ``recovery``,
# ``build_note``; ``usage_notes``, ``warnings``). Everything else — identifiers, enums such as
# ``recovery_action``/``reason``, and backend data records — is never regex-rewritten; a
# string value or a list of strings under a prose key is.
PROSE_KEYS = {
    "message", "hint", "hints", "note", "notes", "recovery", "guidance", "suggestion",
    "warning", "warnings", "caveat", "help",
}  # fmt: skip
# A bare tool name in prose starts with a Tool-Naming v1.1 verb (mirror of cli.py's tiers).
# ponytail: verb-shaped, not catalog-checked; a verb_noun word that is no tool gets prefixed too.
_PROSE_TOOL_RE = re.compile(
    r"\b(?:get|search|list|resolve|find|compare|compute|map|predict|annotate|recode|liftover"
    r"|analyze|score|submit|export|generate|download)_[a-z0-9_]+\b"
)


def _is_rewritable(value: str, namespaces: set[str]) -> bool:
    """True when ``value`` is a bare leaf name not already carrying a known namespace."""
    if not _BARE_NAME_RE.match(value):
        return False
    return value.split("_", 1)[0] not in namespaces  # already-namespaced -> leave as-is


def _is_prose_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower().rsplit("_", 1)[-1] in PROSE_KEYS


def _rewrite_prose(text: str, ns: str, pinned: Collection[str]) -> str:
    """Name each bare sibling tool in prose the way it must be called (#299)."""

    def ref(match: re.Match[str]) -> str:
        name = f"{ns}_{match.group(0)}"
        return name if name in pinned else f'call_tool(name="{name}")'

    return _PROSE_TOOL_RE.sub(ref, text)


def rewrite_tool_refs(obj: Any, ns: str, namespaces: set[str], pinned: Collection[str] = ()) -> int:
    """Prefix bare tool-reference values with ``<ns>_`` in place; return the rewrite count.

    Prose fields (``PROSE_KEYS``) name the call form for tools not in ``pinned``. Idempotent:
    a value already prefixed with any known namespace is left untouched.
    """
    count = 0
    if isinstance(obj, dict):
        if obj.get("kind") == "untrusted_text":
            return 0
        for key, value in obj.items():
            if (
                key in TOOL_REF_KEYS
                and isinstance(value, str)
                and _is_rewritable(value, namespaces)
            ):
                obj[key] = f"{ns}_{value}"
                count += 1
            elif _is_prose_key(key) and isinstance(value, str):
                new = _rewrite_prose(value, ns, pinned)
                if new != value:
                    obj[key] = new
                    count += 1
            elif _is_prose_key(key) and isinstance(value, list):
                for i, item in enumerate(value):  # e.g. ``warnings: [str, ...]``
                    if isinstance(item, str) and (new := _rewrite_prose(item, ns, pinned)) != item:
                        value[i] = new
                        count += 1
                    elif not isinstance(item, str):
                        count += rewrite_tool_refs(item, ns, namespaces, pinned)
            else:
                count += rewrite_tool_refs(value, ns, namespaces, pinned)
    elif isinstance(obj, list):
        for item in obj:
            count += rewrite_tool_refs(item, ns, namespaces, pinned)
    return count


def _rewrite_block(block: Any, ns: str, namespaces: set[str], pinned: Collection[str] = ()) -> Any:
    """Rewrite tool refs inside a JSON text content block (the channel clients display)."""
    text = getattr(block, "text", None)
    if not isinstance(text, str):
        return block
    try:
        parsed = json.loads(text)
    except ValueError, TypeError:
        return block  # plain prose — never regex-rewrite free text
    if rewrite_tool_refs(parsed, ns, namespaces, pinned):
        return block.model_copy(update={"text": json.dumps(parsed)})
    return block


def _rewrite_result(
    result: ToolResult, ns: str, namespaces: set[str], pinned: Collection[str]
) -> ToolResult:
    total = 0
    if isinstance(result.structured_content, dict):
        total += rewrite_tool_refs(result.structured_content, ns, namespaces, pinned)
    new_content = [_rewrite_block(b, ns, namespaces, pinned) for b in result.content]
    if total:
        log.debug("hints_rewritten", namespace=ns, count=total)
    return result.model_copy(update={"content": new_content})


class NamespaceHintMiddleware(Middleware):
    """Rewrite bare tool references in tool results to their namespaced form."""

    def __init__(self, namespaces: set[str], pinned: Collection[str] = ()) -> None:
        self._namespaces = set(namespaces)
        self._pinned = frozenset(pinned)

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        name = getattr(context.message, "name", "") or ""
        result = await call_next(context)
        # The synthetic call_tool/search_tools re-enter the middleware chain for the real
        # target (FastMCP calls self.call_tool(..., run_middleware=True)), so the inner
        # pass carries the namespaced name — skip the synthetic outer pass.
        if name in _SYNTHETIC or not isinstance(result, ToolResult):
            return result
        ns = name.split("_", 1)[0]
        if ns not in self._namespaces:
            return result
        return _rewrite_result(result, ns, self._namespaces, self._pinned)
