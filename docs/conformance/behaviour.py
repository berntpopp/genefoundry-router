"""Behaviour Conformance v1 — the contract an agent relies on, tested against a live server.

Self-contained (httpx only). Vendored byte-identical into every -link repo's tests/conformance/
and run against that repo's own container in CI:

    python -m genefoundry_router.behaviour http://127.0.0.1:8000 --name gtex-link

Exit code: 0 conformant, 1 non-conformant, 2 transport/probe error.

WHAT THIS GATES, AND WHY IT IS SCHEMA-DERIVED
---------------------------------------------
The fleet's confirmed defects are not 82 unrelated bugs. They are three bugs repeated, and all
three are already forbidden — by the fleet's own Response-Envelope Standard or by the MCP spec.
Nothing enforced them. This does.

  1. THE SILENTLY-EMPTY FILTER. An unrecognised filter value matches nothing and returns
     `success:true, total_count:0` — indistinguishable from "the data genuinely has none".
     Forbidden by Response-Envelope v1.1: "silent omission is not compliant."
  2. THE LYING total/truncated. `total` set to the page size, `truncated:false`, while the
     server itself knows there are more.
  3. THE ERROR AN LLM CANNOT ACT ON. A missing argument answered with `not_found` /
     "The requested tool is not available" — telling the model the tool does not exist.

The probes are derived from the server's OWN advertised schema. There is no per-repo config file
to write, and none to forget: every declared enum is probed, so a tool is gated the day it ships.
That is deliberate. A hand-maintained list of probes is the same bug one level up — whoever
forgets to add a row ships an ungated tool while the suite still reports PASS.

The coupling to the Tool-Schema Documentation Standard is the point. This gate builds a valid
call out of each parameter's `examples`, so the artifact that teaches a model how to call the
tool is the same artifact that proves the tool rejects a bad call. A tool without examples cannot
be probed — and is reported as UNGATED, never as passing. Under-documentation shows up as lost
coverage, never as a green tick.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

PROTOCOL = "2025-06-18"
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# Response-Envelope Standard v1: "error_code is a closed enum, harmonized with codes already
# used in the fleet". Anything outside this set is a violation, however sensible it reads.
ERROR_CODES = {
    "invalid_input",
    "not_found",
    "ambiguous_query",
    "upstream_unavailable",
    "rate_limited",
    "internal",
}

# A value no upstream vocabulary can legitimately contain.
SENTINEL = "__gf_conformance_no_such_value__"
BOGUS_ARG = "__gf_conformance_no_such_arg__"

# Errors that mean "the world is broken", not "the server is wrong". A probe that hits one of
# these is skipped, never passed — an unreachable upstream must not silently buy a green tick.
INCONCLUSIVE = {"upstream_unavailable", "rate_limited", "internal"}

# Tools that orient a client rather than query data (Tool-Naming Standard ops/meta carve-out).
META_TOOLS = ("capabilit", "health", "diagnost", "warmup", "help", "quickstart")

# Free-text inputs, where a nonsense value returning zero rows is HONEST, not a defect. The
# silent-empty probe below cannot distinguish "a filter over a closed vocabulary" from "a search
# box" by type — both are optional strings — so it declines to judge these by name.
#
# This deliberately trades coverage for precision. A false accusation is far more costly than a
# missed one here: it sends a maintainer chasing a phantom and teaches them to distrust the gate.
# The failure mode of this list is a LOUD one (a free-text param not listed here produces a visible
# FAIL that a human can dismiss), never a silent pass.
FREE_TEXT = (
    "query",
    "text",
    "search",
    "keyword",
    "question",
    "topic",
    "contains",
    "term",
    "filters",
)


@dataclass
class Report:
    base_url: str
    name: str
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    ungated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed.append(label)
        else:
            self.failed.append(f"{label} — {detail}")
        return ok

    def ungate(self, label: str, why: str) -> None:
        """A tool this gate could not verify. Counts AGAINST conformance (B7).

        The earlier version of this probe recorded these as skips and still certified the server
        CONFORMANT. That made its central promise a lie: `gtex-link`, with 8 of its 9 tools
        unprobed for want of `examples`, would have passed while both of its confirmed HIGH
        defects went untested. A server that does not document its inputs cannot be verified, and
        must not be told it passed.
        """
        self.ungated.append(f"{label} — {why}")

    def skip(self, label: str, why: str) -> None:
        """Genuinely inconclusive — an unreachable upstream. Does not count against conformance."""
        self.skipped.append(f"{label} — {why}")

    @property
    def conformant(self) -> bool:
        return not self.failed and not self.ungated


class Probe:
    """A minimal Streamable-HTTP MCP client. No SDK, so the gate tests the wire, not a wrapper."""

    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        self.url = base_url.rstrip("/") + "/mcp"
        self.client = httpx.Client(timeout=timeout, follow_redirects=False)
        self.session: str | None = None
        self.server_info: dict[str, Any] = {}
        self._id = 0

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        headers = dict(HEADERS)
        if self.session:
            headers["mcp-session-id"] = self.session
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        resp = self.client.post(self.url, json=body, headers=headers)
        if not self.session:
            self.session = resp.headers.get("mcp-session-id")
        text = resp.text.strip()
        if "text/event-stream" in resp.headers.get("content-type", ""):
            frames = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
            text = frames[-1] if frames else "{}"
        message = json.loads(text or "{}")
        if "error" in message:
            raise ProtocolError(message["error"])
        return dict(message.get("result") or {})

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "gf-behaviour-probe", "version": "1.0.0"},
            },
        )
        self.server_info = dict(result.get("serverInfo") or {})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return self._rpc("tools/call", {"name": tool, "arguments": args})


class ProtocolError(RuntimeError):
    """A JSON-RPC error frame — i.e. the server chose NOT to answer with a tool result."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.error = error
        super().__init__(json.dumps(error)[:200])


class WrongServerError(RuntimeError):
    """The server at this URL is not the one we were asked to gate. Never report; always abort."""


# --------------------------------------------------------------------------- envelope reading


def envelope(result: dict[str, Any]) -> dict[str, Any]:
    """The fleet's flat envelope, from structuredContent or its TextContent mirror."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text") or "")
            except (TypeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def rows(env: dict[str, Any]) -> list[Any] | None:
    """The result collection, whatever the backend calls it (`results`, `genes`, `variants`…).

    The longest collection of objects in the envelope. Backends name this field differently and
    the gate must not care; the Response-Envelope Standard fixes the frame, not the payload key.

    GROUPED payloads count too. An adversarial review found the first version of this blind to
    them: orphanet's `map_cross_ontology` returns `mappings` as a dict-of-lists
    (`{"OMIM": [...], "ICD10": [...]}`), not a list. The row-finder saw no list, returned None,
    and EVERY filter probe on that tool silently skipped — so a live silently-empty filter
    (`prefixes=["__BOGUS__"]` -> `success:true, count:0`) sailed through a gate reporting
    "0 failures, 0 UNGATED". A detector that cannot see a payload shape cannot gate it.
    """
    # 1. A GROUPED COLLECTION — a non-`_` key whose value is a dict of lists,
    #    e.g. mappings: {"OMIM": [...], "ICD10": [...]} — IS a collection, and needs no count.
    #    It cannot be confused with a scalar record field (a record's dict field, like
    #    `definition: {id, label, ...}`, has non-list values). Checked FIRST and without the
    #    count guard below, because an empty grouped dict `mappings: {}` is an emptied collection,
    #    not a record — and reading it as "no collection" is exactly how hpo-link's
    #    `map_cross_ontology(prefixes=["__nonsense__"]) -> mappings:{}, success:true` slipped past
    #    an earlier gate that then reported CONFORMANT over a confirmed silent-empty (Codex found
    #    it). A non-empty grouped dict must be all-lists; an empty one reads as an empty collection.
    grouped = _grouped_collection(env)
    if grouped is not None:
        return grouped

    # 2. Otherwise, a top-level list of objects — but a bare list is ambiguous. A COLLECTION
    #    declares how many things it holds (Response-Envelope v1: "Always populate
    #    _meta.pagination: total_count, has_more"); a RECORD does not. Without this count guard the
    #    probe reads hpo-link's `get_term` — a single record whose `children` field holds 23
    #    sub-terms — as a 23-row collection, and then `fields=[...]` (a projection doing its job)
    #    and `response_mode=minimal` (correctly omitting a record's optional detail) both read as
    #    payload destruction. Two false accusations against a server doing nothing wrong.
    if count_of(env) is None:
        return None

    best: list[Any] | None = None
    for key, value in env.items():
        if key.startswith("_") or not isinstance(value, list):
            continue
        if value and not isinstance(value[0], dict):
            continue
        if best is None or len(value) > len(best):
            best = value
    return best


def _grouped_collection(env: dict[str, Any]) -> list[Any] | None:
    """Flatten the largest dict-of-lists in the envelope, or None if there is none.

    Returns `[]` for an empty grouped dict — an emptied collection, not the absence of one.
    """
    best: list[Any] | None = None
    for key, value in env.items():
        if key.startswith("_") or not isinstance(value, dict):
            continue
        # A non-empty grouped collection is lists all the way across; an empty dict qualifies
        # (it is an emptied collection). A dict with any non-list value is a record, not a group.
        if value and not all(isinstance(branch, list) for branch in value.values()):
            continue
        flat = [item for branch in value.values() if isinstance(branch, list) for item in branch]
        if best is None or len(flat) > len(best):
            best = flat
    return best


def count_of(env: dict[str, Any]) -> int | None:
    """How many records this response says it is carrying. Absent on a single-record tool."""
    pagination = (env.get("_meta") or {}).get("pagination") or {}
    for source in (pagination, env):
        for key in ("total_count", "total", "count", "returned", "found_count"):
            value = source.get(key)
            if isinstance(value, int):
                return value
    return None


def total_of(env: dict[str, Any]) -> int | None:
    pagination = (env.get("_meta") or {}).get("pagination") or {}
    for source in (pagination, env):
        for key in ("total_count", "total"):
            value = source.get(key)
            if isinstance(value, int):
                return value
    return None


def more_flag(env: dict[str, Any]) -> bool | None:
    pagination = (env.get("_meta") or {}).get("pagination") or {}
    for source in (pagination, env):
        for key in ("has_more", "truncated"):
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def is_error_envelope(env: dict[str, Any]) -> bool:
    return env.get("success") is False or bool(env.get("error_code"))


def properties(tool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return dict((tool.get("inputSchema") or {}).get("properties") or {})


def _branches(prop: dict[str, Any]) -> list[dict[str, Any]]:
    """The property itself plus every `anyOf` branch — pydantic renders `X | None` as anyOf."""
    out = [prop]
    out.extend(b for b in prop.get("anyOf") or [] if isinstance(b, dict))
    return out


def enum_of(prop: dict[str, Any]) -> list[Any] | None:
    """The closed vocabulary this property constrains its value to, scalar OR per-item.

    `items.enum` matters as much as `enum`: a `list[Literal[...]]` parameter is a closed
    vocabulary too, and the first version of this gate looked only at the scalar case.
    """
    for branch in _branches(prop):
        if isinstance(branch.get("enum"), list):
            return list(branch["enum"])
        items = branch.get("items")
        if isinstance(items, dict) and isinstance(items.get("enum"), list):
            return list(items["enum"])
    return None


def is_stringy(prop: dict[str, Any]) -> bool:
    """True if the property takes a string, or an ARRAY of strings.

    The array case is not a nicety. orphanet's `map_cross_ontology.prefixes` is a `list[str]`
    over a closed vocabulary, and `prefixes=["__BOGUS__"]` returns `success:true, count:0` on the
    live server — the primary bug, shipping today, invisible to a gate that only probed scalars.
    """
    for branch in _branches(prop):
        if branch.get("type") == "string":
            return True
        items = branch.get("items")
        if (
            branch.get("type") == "array"
            and isinstance(items, dict)
            and items.get("type") == "string"
        ):
            return True
    return False


def takes_array(prop: dict[str, Any]) -> bool:
    return any(b.get("type") == "array" for b in _branches(prop))


def perturbed(prop: dict[str, Any]) -> Any:
    """The sentinel, wrapped to match the property's shape."""
    return [SENTINEL] if takes_array(prop) else SENTINEL


def valid_args(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Build a valid call from the schema's own `examples`. None if the schema doesn't say how.

    This is the fixture the Tool-Schema Documentation Standard (S2) exists to provide. A tool
    whose required parameters carry no example cannot be exercised here, and is reported UNGATED.
    """
    args: dict[str, Any] = {}
    props = properties(tool)
    for name in (tool.get("inputSchema") or {}).get("required") or []:
        examples = (props.get(name) or {}).get("examples")
        if not examples:
            return None
        args[name] = examples[0]
    return args


# --------------------------------------------------------------------------- the checks


def _record_error_frame(
    rep: Report, tool: str, result: dict[str, Any], env: dict[str, Any]
) -> None:
    """Every error we provoke is also evidence for the two universal envelope invariants."""
    code = env.get("error_code")

    # Response-Envelope v1: "isError: true is REQUIRED so clients surface the error to the model
    # for self-correction." A returned dict never sets isError; the backend must return a
    # ToolResult(is_error=True). This is the fleet's most widespread protocol violation.
    rep.check(
        f"{tool}: an error envelope carries MCP isError:true",
        result.get("isError") is True,
        f"success={env.get('success')!r} error_code={code!r} but isError={result.get('isError')!r}",
    )
    if code is not None:
        rep.check(
            f"{tool}: error_code is in the closed enum",
            code in ERROR_CODES,
            f"{code!r} is not one of {sorted(ERROR_CODES)}",
        )


def check_argument_error(rep: Report, probe: Probe, tool: dict[str, Any]) -> None:
    """An unusable argument MUST produce an actionable tool error that names the parameter.

    MCP 2025-11-25 (SEP-1303) moved input-validation failures OUT of protocol errors and INTO
    tool-execution errors precisely so the model can self-correct: "Tool Execution Errors contain
    actionable feedback that language models can use to self-correct and retry with adjusted
    parameters."
    """
    name = tool["name"]
    label = f"{name}: unknown argument"
    try:
        result = probe.call(name, {BOGUS_ARG: "x"})
    except ProtocolError as exc:
        rep.check(
            f"{label} is a tool error, not a JSON-RPC protocol error",
            False,
            f"got JSON-RPC {exc.error.get('code')} — the model cannot self-correct from this "
            "(MCP 2025-11-25 SEP-1303)",
        )
        return

    env = envelope(result)
    if not is_error_envelope(env):
        rep.check(label + " is rejected", False, "the server ACCEPTED an unknown argument")
        return

    _record_error_frame(rep, name, result, env)

    code = env.get("error_code")
    rep.check(
        f"{label} → invalid_input (never not_found)",
        code == "invalid_input",
        f"got {code!r} — {str(env.get('message'))[:80]!r}. 'not_found' tells the model the tool "
        "does not exist, so it will never call it again.",
    )

    # "message MUST be specific and actionable — tell the model how to fix the call."
    #
    # The parameter may be named in the PROSE, or carried in the envelope's structured error
    # fields. Both are actionable, and the Response-Envelope error frame explicitly provides
    # `field` / `allowed_values` / `hint` for exactly this. hpo-link answers with
    # "Valid argument names are listed in allowed_values" plus a populated `allowed_values` — a
    # model can act on that perfectly well, and an earlier version of this check called it a
    # failure purely because it only read the prose. Judging a server by the surface you happen
    # to look at is the same mistake as the audit's tautological $ref check.
    prose = " ".join(str(env.get(k) or "") for k in ("message", "recovery_action", "hint"))
    structured = env.get("allowed_values") or env.get("field") or env.get("field_errors")
    names_something = (
        BOGUS_ARG in prose or any(p in prose for p in properties(tool)) or bool(structured)
    )
    rep.check(
        f"{label} names the offending or the valid parameters",
        names_something,
        f"{str(env.get('message'))[:90]!r} names no parameter, and the envelope carries no "
        "`field`/`allowed_values` — the model has nothing to act on",
    )


def check_declared_enums(
    rep: Report, probe: Probe, tool: dict[str, Any], base: dict[str, Any]
) -> None:
    """A value outside a DECLARED enum MUST be rejected — never silently matched to nothing."""
    name = tool["name"]
    for prop_name, prop in properties(tool).items():
        values = enum_of(prop)
        if not values or not is_stringy(prop):
            continue
        label = f"{name}.{prop_name}: out-of-enum value"
        try:
            result = probe.call(name, {**base, prop_name: perturbed(prop)})
        except ProtocolError as exc:
            rep.check(
                f"{label} is a tool error, not a protocol error",
                False,
                f"JSON-RPC {exc.error.get('code')}",
            )
            continue

        env = envelope(result)
        if is_error_envelope(env):
            _record_error_frame(rep, name, result, env)
            rep.check(
                f"{label} → invalid_input",
                env.get("error_code") == "invalid_input",
                f"got {env.get('error_code')!r}",
            )
            continue

        # Accepted. The only defensible outcome now is that it changed nothing; but a filter that
        # accepts a value it does not understand and returns zero rows is the silent-empty bug.
        found = rows(env)
        rep.check(
            f"{label} MUST be rejected, not silently matched to nothing",
            False,
            f"success={env.get('success')!r}, {len(found) if found is not None else '?'} rows, "
            f"total={total_of(env)!r} — indistinguishable from 'the data has none'",
        )


def check_silent_empty_filter(
    rep: Report, probe: Probe, tool: dict[str, Any], base: dict[str, Any], control: list[Any]
) -> None:
    """An OPTIONAL string filter with no declared enum must not silently zero the result set.

    This is the undeclared-enum detector, and it is why the standard can enforce a rule a static
    schema check cannot see. `clinvar-link/get_variants_by_gene.classification` is a bare string
    whose real vocabulary is closed: 'likely_pathogenic' returns 559 BRCA1 variants, ClinVar's own
    published wording 'Likely pathogenic' returns 0 with success:true.

    A free-text query parameter legitimately returns nothing for a nonsense value — which is why
    this probes only OPTIONAL parameters, against a control call already proven to return rows, and
    skips anything named like a search box (see FREE_TEXT).
    """
    name = tool["name"]
    required = set((tool.get("inputSchema") or {}).get("required") or [])
    for prop_name, prop in properties(tool).items():
        if prop_name in required or prop_name in base or enum_of(prop) or not is_stringy(prop):
            continue
        if prop_name in ("cursor", "request_id", "next_cursor", "correlation_id", "session_id"):
            continue
        if any(marker in prop_name for marker in FREE_TEXT):
            continue
        label = f"{name}.{prop_name}: unrecognised filter value"
        try:
            result = probe.call(name, {**base, prop_name: perturbed(prop)})
        except ProtocolError:
            continue

        env = envelope(result)
        if is_error_envelope(env):
            _record_error_frame(rep, name, result, env)
            rep.passed.append(f"{label} → rejected ({env.get('error_code')})")
            continue

        found = rows(env)
        if found:
            rep.passed.append(f"{label} → did not zero the result set")
            continue

        # `found` is now [] OR None. Both are the bug, and conflating None with "fine" is how the
        # first version of this probe passed orphanet's `map_cross_ontology.prefixes`: the bogus
        # value emptied the grouped `mappings` object entirely, so the row-finder saw no collection
        # at all and returned None — which the check then read as "did not zero the result set".
        # The CONTROL call already proved this tool returns rows. If the perturbed call has no rows
        # — whether an empty collection or no collection at all — the filter destroyed them.

        rep.check(
            label + " silently matched nothing",
            False,
            f"the control call returned {len(control)} rows; this returned 0 with "
            f"success={env.get('success')!r} and no error — indistinguishable from 'the data "
            "genuinely has none'. Either declare the closed vocabulary as an `enum` "
            "(TOOL-SCHEMA-DOCUMENTATION-STANDARD S4), or reject the unrecognised value with "
            "invalid_input/not_found naming it. A zero-row success is not an acceptable answer to "
            "a value the server did not understand.",
        )


def check_pagination_honesty(rep: Report, tool: str, env: dict[str, Any], found: list[Any]) -> None:
    """A partial page must say so."""
    total = total_of(env)
    if total is None:
        return
    more = more_flag(env)
    if len(found) < total:
        rep.check(
            f"{tool}: a partial page declares has_more/truncated",
            more is True,
            f"returned {len(found)} of total {total} but has_more/truncated is {more!r} — the "
            "model will conclude it has seen everything",
        )


def check_total_is_not_the_page_size(
    rep: Report, probe: Probe, tool: dict[str, Any], base: dict[str, Any]
) -> None:
    """`total` MUST be invariant under `limit`. (B4)

    THIS CANNOT BE DECIDED FROM ONE RESPONSE, which is why the first version of this gate missed
    it. litvar's `search_genetic_variants` returns `returned=25, total=25, truncated=false` — a
    perfectly self-consistent page. Nothing in it is detectably wrong. Only a second call exposes
    the lie:

        limit=5   -> returned=5,   total=5,   truncated=false
        limit=25  -> returned=25,  total=25,  truncated=false
        limit=100 -> returned=100, total=100, truncated=false

    `total` is echoing `limit`. The true BRCA1 count, which the SAME SERVER reports from a sibling
    tool, is 13,264. An agent reads `total=25, truncated=false` and concludes it has seen every
    BRCA1 variant LitVar knows. It has seen 0.2% of them.

    `total` is a property of the result set, not of the page. If it moves when `limit` moves, it
    is fabricated.
    """
    name = tool["name"]
    limit_prop = properties(tool).get("limit") or {}
    if not limit_prop:
        return

    totals: dict[int, int | None] = {}
    for limit in (2, 4):
        try:
            result = probe.call(name, {**base, "limit": limit})
        except ProtocolError:
            return
        env = envelope(result)
        if is_error_envelope(env):
            return
        totals[limit] = total_of(env)

    small, large = totals.get(2), totals.get(4)
    if small is None or large is None:
        return
    rep.check(
        f"{name}: total is invariant under limit",
        small == large,
        f"limit=2 reported total={small}, limit=4 reported total={large}. `total` is tracking the "
        "page size, not the result set — so it is fabricated, and an agent reading it concludes it "
        "has seen everything.",
    )


def check_response_modes(
    rep: Report, probe: Probe, tool: dict[str, Any], base: dict[str, Any], control: list[Any]
) -> None:
    """`response_mode` narrows a payload; it MUST NOT destroy it.

    Response-Envelope v1 defines `minimal` as "the mandatory envelope plus stable identifiers,
    omitting all optional record detail" — identifiers are explicitly retained. A mode that turns
    N records into zero is a silent-empty by another name: orphanet-link/get_disease_genes at
    response_mode=minimal discards the entire gene list and still returns success:true.
    """
    name = tool["name"]
    modes = enum_of(properties(tool).get("response_mode") or {}) or []
    for mode in modes:
        label = f"{name}: response_mode={mode!r} preserves the payload"
        try:
            result = probe.call(name, {**base, "response_mode": mode})
        except ProtocolError:
            continue
        env = envelope(result)
        if is_error_envelope(env):
            _record_error_frame(rep, name, result, env)
            continue
        found = rows(env)
        rep.check(
            label,
            bool(found),
            f"the default call returned {len(control)} records; response_mode={mode!r} returned "
            f"{0 if found is None else len(found)} with success={env.get('success')!r}",
        )


# --------------------------------------------------------------------------- driver


def run_probe(base_url: str, *, expected_name: str, timeout: float = 60.0) -> Report:
    rep = Report(base_url.rstrip("/"), expected_name)
    probe = Probe(base_url, timeout)
    probe.initialize()

    # Identity FIRST, and fatally. A developer gating a local build will sooner or later point
    # this at a port some sibling container is squatting on, and every finding below would then
    # describe a DIFFERENT SERVER while looking entirely plausible. That happened for real during
    # the litvar-link fix: a stray clinvar-link on :8011 produced a confident, well-formed, wrong
    # litvar report. A probe that cannot prove what it is talking to is worse than no probe.
    served = probe.server_info.get("name")
    if served != expected_name:
        raise WrongServerError(
            f"expected serverInfo.name {expected_name!r} at {base_url}, but the server there "
            f"says it is {served!r}. Refusing to report findings against the wrong server."
        )

    tools = probe.list_tools()
    rep.check("tools/list returns at least one tool", bool(tools), "no tools")

    for tool in tools:
        name = tool["name"]

        # Every tool, even a no-argument one, must reject an unknown argument usefully.
        check_argument_error(rep, probe, tool)

        if any(marker in name for marker in META_TOOLS):
            continue

        base = valid_args(tool)
        if base is None:
            rep.ungate(
                f"{name}: dynamic probes",
                "UNGATED — a required parameter carries no `examples`, so no valid call can be "
                "constructed and NOTHING about this tool's behaviour is verified "
                "(TOOL-SCHEMA-DOCUMENTATION-STANDARD S2)",
            )
            continue

        try:
            control_result = probe.call(name, base)
        except ProtocolError as exc:
            rep.check(f"{name}: its own documented example is callable", False, str(exc)[:100])
            continue

        control_env = envelope(control_result)
        if is_error_envelope(control_env):
            code = control_env.get("error_code")
            _record_error_frame(rep, name, control_result, control_env)
            if code in INCONCLUSIVE:
                rep.skip(f"{name}: dynamic probes", f"upstream inconclusive ({code})")
            else:
                rep.check(
                    f"{name}: its own documented example is accepted",
                    False,
                    f"the call built from the schema's own `examples` was rejected: {code!r} "
                    f"{str(control_env.get('message'))[:70]!r}",
                )
            continue

        check_declared_enums(rep, probe, tool, base)

        control = rows(control_env)
        if control:
            check_pagination_honesty(rep, name, control_env, control)
            check_total_is_not_the_page_size(rep, probe, tool, base)
            check_silent_empty_filter(rep, probe, tool, base, control)
            check_response_modes(rep, probe, tool, base, control)
        else:
            rep.skip(
                f"{name}: filter probes",
                "the control call returned no rows, so a zero result proves nothing",
            )

    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Behaviour Conformance v1 probe")
    parser.add_argument("base_url")
    parser.add_argument("--name", required=True, help="expected serverInfo.name")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--quiet", action="store_true", help="only show failures and skips")
    args = parser.parse_args(argv)

    try:
        rep = run_probe(args.base_url, expected_name=args.name, timeout=args.timeout)
    except WrongServerError as exc:
        print(f"WRONG SERVER: {exc}", file=sys.stderr)
        return 2
    except (httpx.HTTPError, ProtocolError) as exc:
        print(f"TRANSPORT ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        for line in rep.passed:
            print(f"  PASS  {line}")
    for line in rep.skipped:
        print(f"  SKIP  {line}")
    for line in rep.ungated:
        print(f"  UNGATED  {line}")
    for line in rep.failed:
        print(f"  FAIL  {line}")

    verdict = "CONFORMANT" if rep.conformant else "NON-CONFORMANT"
    print(
        f"\n{verdict}: {rep.name} @ {rep.base_url} "
        f"({len(rep.passed)} pass, {len(rep.failed)} fail, {len(rep.ungated)} UNGATED, "
        f"{len(rep.skipped)} inconclusive)"
    )
    if rep.ungated:
        print(
            f"{len(rep.ungated)} tool(s) could not be verified at all and therefore FAIL. Document "
            "their required parameters with `examples` so they can be probed. An unverifiable tool "
            "must never be certified."
        )
    return 0 if rep.conformant else 1


if __name__ == "__main__":
    raise SystemExit(main())
