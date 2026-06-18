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

from genefoundry_router.registry import BackendDef, qualified_name


def _entrypoints_block(registry: list[BackendDef]) -> str:
    """One line per enabled backend that declares canonical resolvers, naming the
    namespaced tools so the model can call them directly — the always-read complement
    to pinning that survives even when a host drops the server instructions."""
    lines = [
        f"  - {b.namespace}: " + ", ".join(qualified_name(b.namespace, e) for e in b.entrypoints)
        for b in registry
        if b.enabled and b.entrypoints
    ]
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "\n\nCOMMON ENTRY POINTS — canonical resolvers (free text -> stable ID). These "
        "are pinned (always listed) AND callable directly without a search; start here "
        "to turn a user's gene/variant/disease text into the IDs other tools need:\n"
        f"{body}"
    )


def build_instructions(registry: list[BackendDef]) -> str:
    """Return the orientation text for the ``genefoundry`` MCP server.

    Lists the enabled backends' namespaces so the model knows the breadth of the
    federated catalog without a ``search_tools`` round-trip; disabled/url-less
    backends are mounted nowhere, so they are omitted.
    """
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
SEARCH SURFACE instead of listing its whole ~200-tool catalog at once.

WHAT IS LISTED vs WHAT EXISTS. Only `search_tools`, `call_tool`, and a few pinned \
canonical resolvers (see COMMON ENTRY POINTS) appear in the top-level tool list. \
Every other capability — across {catalog} — is present and callable, but it is \
reached THROUGH search, not shown up front. If a capability (splicing prediction, \
VEP consequence, ClinVar significance, disease ontology, gene-disease curation, \
expression, literature, …) is absent from your client's tool list, that does NOT \
mean it is missing — it means you have not searched yet. Call `search_tools` before \
concluding a tool does not exist.{_entrypoints_block(registry)}

WORKFLOW.
  1. `search_tools(query="<natural language>")` — BM25 search over the whole \
federated catalog; returns matching tool definitions (name, inputSchema, one-line \
`returns`) as DATA.
  2. Read a hit's `name`; it is always `<namespace>_<tool>` \
(e.g. `spliceai_predict_splicing`, `vep_annotate_variant`).
  3. `call_tool(name="<namespace>_<tool>", arguments={{…}})` to invoke it. A tool's \
full namespaced name is also directly callable if you already know it.

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
