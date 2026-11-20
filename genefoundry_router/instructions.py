"""Build the router's server-``instructions`` string.

The MCP ``instructions`` field (returned in the ``initialize`` result) is the
spec-native channel a server uses to teach a host's model how to drive it — clients
MAY inject it into the system prompt (it is "up to the implementer", per the MCP
spec, so this is best-effort and the same guidance is duplicated into the
``search_tools``/``call_tool`` descriptions for defense in depth).

This text exists to fix issue #3 ("discoverability is confusing"): the router is a
two-layer meta-router whose top-level listing shows only ``search_tools``,
``call_tool``, and two pinned resolvers. A model that searched its *client-side*
tool list for "spliceai", saw nothing, and concluded the capability was absent — and
one that read "Unknown tool: call_tool" (a host eviction symptom) as router
flakiness — both needed orientation this string now provides. Per MCP guidance,
instructions focus on the cross-tool *workflow*, not on repeating per-tool docs.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from genefoundry_router.pinned_mode import PinPolicy, request_mode
from genefoundry_router.registry import BackendDef, qualified_name


def _entrypoints_block(registry: list[BackendDef], listed: Collection[str] | None) -> str:
    """One line per enabled backend with a LISTED entry point, naming the namespaced tools
    so the model can call them directly — the always-read complement to pinning that
    survives even when a host drops the server instructions."""
    lines = []
    for b in registry:
        names = [qualified_name(b.namespace, e) for e in b.entrypoints if b.enabled]
        names = [n for n in names if listed is None or n in listed]
        if names:
            lines.append(f"  - {b.namespace}: " + ", ".join(names))
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "\n\nCOMMON ENTRY POINTS — the front-door tool for each domain (a free-text->ID "
        "resolver and/or the primary query). These are pinned (always listed) AND callable "
        "directly without a search; start here, then `search_tools` for follow-on tools:\n"
        f"{body}"
    )


def build_instructions(registry: list[BackendDef], listed: Collection[str] | None = None) -> str:
    """Return the orientation text for the ``genefoundry`` MCP server.

    Lists the enabled backends' namespaces so the model knows the breadth of the
    federated catalog without a ``search_tools`` round-trip; disabled/url-less
    backends are mounted nowhere, so they are omitted. ``listed`` is the pinned set the
    server lists (``GF_PINNED_MODE``, #320); ``None`` means every entry point.
    """
    pinned = _entrypoints_block(registry, listed)
    if pinned:
        listing = (
            "Only `search_tools`, `call_tool`, and a few pinned canonical resolvers "
            "(see COMMON ENTRY POINTS) appear in the top-level tool list."
        )
        direct = "Only the pinned entry points may be called directly."
    else:
        listing = "Only `search_tools` and `call_tool` appear in the top-level tool list."
        direct = "Invoke every fleet tool through `call_tool`."
    namespaces = sorted(b.namespace for b in registry if b.enabled)
    breadth = (
        f"{len(namespaces)} domain backends ({', '.join(namespaces)})"
        if namespaces
        else "its federated fleet of domain backends"
    )
    catalog = ", ".join(namespaces) if namespaces else "every backend"
    return f"""\
genefoundry is a META-ROUTER (a gateway), not a data server. It federates the \
GeneFoundry "-link" fleet — {breadth} — behind ONE MCP endpoint and exposes a \
SEARCH SURFACE instead of listing its whole ~200-tool catalog at once. Research \
workflow skills (step order, stop rules, output template) are `skill://` resources \
and also show up as `search_tools` hits; read one before a multi-step job.

WHAT IS LISTED vs WHAT EXISTS. {listing} \
Every other capability — across {catalog} — is present and callable, but it is \
reached THROUGH search, not shown up front. If a capability (splicing prediction, \
VEP consequence, ClinVar significance, disease ontology, gene-disease curation, \
expression, literature, …) is absent from your client's tool list, that does NOT \
mean it is missing — it means you have not searched yet. Call `search_tools` before \
concluding a tool does not exist.{pinned}

WORKFLOW.
  1. `search_tools(query="<natural language>")` — search over the federated catalog \
of every backend; returns matching tool definitions (name, inputSchema, one-line \
`returns`) as DATA. Pass `limit` (e.g. `limit=2`) to get fewer hits and save context. \
Hits flagged `low_confidence` (or none) mean no clear match: rephrase in domain terms. \
Search ONE intent per call: a multi-part question (e.g. a disease's genes AND where those \
genes sit on the chromosome) needs one search per part, chained on the first result.
  2. Read a hit's `name`; it is always `<namespace>_<tool>` \
(e.g. `spliceai_predict_splicing`, `vep_annotate_variant`).
  3. `call_tool(name="<namespace>_<tool>", arguments={{…}})` to invoke it — each hit's \
`call` field is that call, ready to fill. A tool that is NOT in your client's tool list \
is invoked ONLY through `call_tool`: calling its name directly fails in clients that \
check names against the listed tools. {direct}

DO NOT DEFEAT YOURSELF. `search_tools` returns data; it does not load anything into \
your client, and you do NOT need to re-run any client/host-side tool search to \
invoke a hit — just call `call_tool` next. `search_tools` and `call_tool` are always \
available on this server, and tools you discover persist for the rest of the \
session. If you ever see "Unknown tool: call_tool" (or "Unknown tool: <name>"), your \
client evicted it from its loaded set — re-run `search_tools` to rediscover it and \
continue; that is recoverable, not a router failure. Do not interleave a \
client-side tool-search reload between discovering a tool and calling it.

PROVENANCE & SAFETY. The router namespaces and shapes the surface but never rewrites \
a backend's data. Each backend keeps its own response envelope, citation contract, \
and disclaimers — follow the instructions of the backend whose tool you call and \
paste any `recommended_citation` verbatim. Research use only; not clinical decision \
support."""


class PinnedModeInstructions(Middleware):
    """Answer ``initialize`` / ``server/discover`` with the request's ``X-GF-Pinned-Mode``
    instructions (#320), so a ``none``-mode client is not told about pins it cannot see.

    Only the returned text changes; auth and routing never read it.
    """

    def __init__(
        self,
        registry: list[BackendDef],
        policy: PinPolicy,
        unavailable: Callable[[], Collection[str]],
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._unavailable = unavailable

    def _override(self) -> str | None:
        mode = request_mode(self._policy.default)
        if mode == self._policy.default:
            return None  # server.instructions already matches
        down = self._unavailable()
        live = [b for b in self._registry if b.namespace not in down]
        return build_instructions(live, self._policy.listed(mode))

    async def on_initialize(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        result = await call_next(context)
        text = self._override()
        if result is None or text is None:
            return result
        return result.model_copy(update={"instructions": text})

    async def on_discover(self, context: MiddlewareContext[Any], call_next: CallNext) -> Any:
        result = await call_next(context)
        text = self._override()
        if text is None:
            return result
        if isinstance(result, dict):
            return {**result, "instructions": text}
        return result.model_copy(update={"instructions": text})
