"""Foreign-identifier conformance — Response-Envelope Standard v1 §2b, tested against a live server.

Vendored into every -link repo's tests/conformance/ next to behaviour.py (whose MCP client and
envelope readers it reuses), together with identifiers.json:

    python tests/conformance/identifiers.py http://127.0.0.1:8000 --name hpo-link

Exit code: 0 conformant, 1 non-conformant, 2 transport/probe error.

For every tool parameter that takes a GENE, the probe sends each well-formed form of one known
gene (identifiers.json: KRAS as symbol, Ensembl, HGNC and NCBI Gene id) and classifies the answer.

  PASS  resolved   the response identifies the gene (and should echo `resolved_from`)
        rejected   `invalid_input` + `field` + `allowed_values` + a `_meta.next_commands` entry
                   that is not the same tool with the same value
        flagged    a success that lists the value under an unresolved/unsupported key
  FAIL  silent_empty   `success: true`, no rows, nothing saying the form is unsupported
        wrong_entity   a confident answer that never names the gene
        absence        `not_found` (or another error) although the gene is there under another form
        self_loop      `next_commands` repeats this tool with this value
        unactionable   an `invalid_input` missing `field`, `allowed_values` or `next_commands`

A parameter is only judged when at least one form resolves: that form is the positive control
proving the backend holds the gene. Otherwise every form is SKIPped.

WHICH PARAMETERS ARE GENES is derived from the tool schema: the parameter name
(`gene`, `gene_symbol`, `genes`, `candidate_genes`, ...) or a description that opens with
"Gene symbol" (the regexes live in identifiers.json, per entity type, so disease/variant/phenotype
are added as data). A backend corrects the derivation in its `backends` block:
`"params": {"resolve_identifier.query": "gene", "search_variants.query": null}`.

KNOWN FAILURES (`backends.<name>.known_failures`, keys `tool.param:form`) are expected-fail and
strict both ways: a listed key that passes is an error (XPASS), and so is a key that no longer
names a probed parameter (STALE), so a fix cannot regress and the list cannot rot.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

import httpx

try:
    from .behaviour import (
        INCONCLUSIVE,
        META_TOOLS,
        Probe,
        ProtocolError,
        WrongServerError,
        count_of,
        envelope,
        is_error_result,
        is_stringy,
        properties,
        takes_array,
        valid_args,
    )
except ImportError:  # run as a script
    from behaviour import (  # type: ignore[no-redef]
        INCONCLUSIVE,
        META_TOOLS,
        Probe,
        ProtocolError,
        WrongServerError,
        count_of,
        envelope,
        is_error_result,
        is_stringy,
        properties,
        takes_array,
        valid_args,
    )

CONFIG = Path(__file__).with_name("identifiers.json")
PASSING = {"resolved", "rejected", "flagged"}
# A success may carry the value under a key like these instead of failing the whole call.
_UNSUPPORTED_KEY = re.compile(r"unresolved|unsupported|unmapped|unmatched|not_found|invalid|reject")


class Verdict(NamedTuple):
    kind: str  # one of PASSING, a FAIL class from the module docstring, or "inconclusive"
    detail: str = ""


class Row(NamedTuple):
    tool: str
    param: str
    form: str
    status: str  # PASS | FAIL | XFAIL | XPASS | SKIP | STALE
    kind: str
    detail: str

    @property
    def key(self) -> str:
        return f"{self.tool}.{self.param}:{self.form}"


def _mentions(value: Any, ids: list[str]) -> bool:
    text = json.dumps(value)
    return any(
        re.search(rf"(?<![A-Za-z0-9]){re.escape(i)}(?![A-Za-z0-9])", text, re.I) for i in ids
    )


def _in_a_record(value: Any, sent: str, param: str, depth: int = 0) -> bool:
    """The sent value as a field of a nested record — a row naming the gene, not an input echo.

    hgnc-link's batch resolver answers a miss with `{"query": <sent>, "unresolved": true}`: the
    value sits in a row and still identifies nothing, so echo-named keys never count.
    """
    if isinstance(value, dict):
        return any(
            (
                depth > 0
                and isinstance(v, str)
                and v.lower() == sent.lower()
                and not re.match(rf"query|input|identifier|{re.escape(param)}$", k)
            )
            or _in_a_record(v, sent, param, depth + 1)
            for k, v in value.items()
        )
    if isinstance(value, list):
        return any(_in_a_record(v, sent, param, depth) for v in value)
    return False


def identifies(env: dict[str, Any], param: str, sent: str, ids: list[str]) -> bool:
    """True when the payload names the entity by more than an echo of what was sent."""
    payload = {k: v for k, v in env.items() if k != "_meta"}
    others = [i for i in ids if i.lower() != sent.lower()]
    return _mentions(payload, others) or _in_a_record(payload, sent, param)


def classify(tool: str, param: str, sent: str, result: dict[str, Any], ids: list[str]) -> Verdict:
    """Classify one answer to `tool(param=sent)`. Pure: no I/O, so it is table-tested."""
    env = envelope(result)
    if is_error_result(result, env):
        code = env.get("error_code")
        if code in INCONCLUSIVE:
            return Verdict("inconclusive", f"upstream inconclusive ({code})")
        if code != "invalid_input":
            return Verdict("absence", f"{code!r} for a well-formed identifier of a gene it holds")
        steps = [
            s for s in (env.get("_meta") or {}).get("next_commands") or [] if isinstance(s, dict)
        ]
        for step in steps:
            name = str(step.get("tool") or "")
            value = (step.get("arguments") or {}).get(param)
            # ponytail: a namespaced name counts as this tool by suffix; exact enough for one fleet.
            if (name == tool or name.endswith(f"_{tool}")) and value in (sent, [sent]):
                return Verdict("self_loop", f"next_commands repeats {name}({param}={sent!r})")
        missing = [
            what
            for what, present in (
                ("field", env.get("field")),
                ("allowed_values", env.get("allowed_values")),
                ("_meta.next_commands", steps),
            )
            if not present
        ]
        if missing:
            return Verdict("unactionable", "invalid_input without " + ", ".join(missing))
        if all(any(m in str(s.get("tool")) for m in META_TOOLS) for s in steps):
            return Verdict("rejected", "next step is only a meta tool, not a converter")
        return Verdict("rejected")

    if identifies(env, param, sent, ids):
        return Verdict("resolved", "" if "resolved_from" in env else "no resolved_from")
    for key, value in env.items():
        if _UNSUPPORTED_KEY.search(key) and _mentions(value, [sent]):
            return Verdict("flagged", f"listed under {key}")
    # Anything beyond booleans, counts and an echo of the input is an answer about SOME entity.
    payload = [
        v
        for k, v in env.items()
        if k != "_meta"
        and ((isinstance(v, (list, dict)) and v) or (isinstance(v, str) and v != sent))
    ]
    if count_of(env) == 0 or not payload:
        return Verdict("silent_empty", "success with no rows and no unsupported-identifier marker")
    return Verdict("wrong_entity", "a non-empty answer that never names the gene")


def entity_params(
    tool: dict[str, Any], entities: dict[str, Any], declared: dict[str, str | None]
) -> dict[str, str]:
    """`{param: entity}` for one tool: derived from name/description, corrected by `declared`."""
    found: dict[str, str] = {}
    for param, prop in properties(tool).items():
        key = f"{tool['name']}.{param}"
        if key in declared:
            if declared[key]:
                found[param] = str(declared[key])
            continue
        if not is_stringy(prop):
            continue
        for entity, spec in entities.items():
            if re.search(spec["param_name"], param) or re.search(
                spec["param_description"], prop.get("description") or "", re.I
            ):
                found[param] = entity
                break
    return found


def run_probe(
    base_url: str, *, expected_name: str, timeout: float = 60.0, config: Path = CONFIG
) -> list[Row]:
    cfg = json.loads(config.read_text())
    backend = cfg.get("backends", {}).get(expected_name, {})
    known = set(backend.get("known_failures", []))
    probe = Probe(base_url, timeout)
    probe.initialize()
    served = probe.server_info.get("name")
    if served != expected_name:
        raise WrongServerError(f"expected {expected_name!r} at {base_url}, found {served!r}")

    out: list[Row] = []
    for tool in probe.list_tools():
        name = tool["name"]
        if any(marker in name for marker in META_TOOLS):
            continue
        for param, entity in entity_params(
            tool, cfg["entities"], backend.get("params", {})
        ).items():
            forms: dict[str, str] = cfg["entities"][entity]["forms"]
            base = valid_args(tool)
            verdicts: dict[str, Verdict] = {}
            for form, value in forms.items():
                if base is None:
                    verdicts[form] = Verdict("inconclusive", "a required parameter has no example")
                    continue
                sent: Any = [value] if takes_array(properties(tool)[param]) else value
                try:
                    result = probe.call(name, {**base, param: sent})
                except ProtocolError as exc:
                    verdicts[form] = Verdict("unactionable", f"JSON-RPC error: {str(exc)[:80]}")
                    continue
                verdicts[form] = classify(name, param, value, result, list(forms.values()))
            judged = any(v.kind == "resolved" for v in verdicts.values())
            for form, verdict in verdicts.items():
                key = f"{name}.{param}:{form}"
                if not judged or verdict.kind == "inconclusive":
                    why = verdict.detail if verdict.kind == "inconclusive" else "no form resolves"
                    out.append(Row(name, param, form, "SKIP", verdict.kind, why))
                elif verdict.kind in PASSING:
                    status = "XPASS" if key in known else "PASS"
                    out.append(Row(name, param, form, status, verdict.kind, verdict.detail))
                else:
                    status = "XFAIL" if key in known else "FAIL"
                    out.append(Row(name, param, form, status, verdict.kind, verdict.detail))
    seen = {row.key for row in out}
    for key in sorted(known - seen):
        tool_param, _, form = key.rpartition(":")
        tool_name, _, param = tool_param.partition(".")
        out.append(Row(tool_name, param, form, "STALE", "", "known_failures names nothing probed"))
    return out


def failures(table: list[Row]) -> list[str]:
    hint = {
        "FAIL": "fix it, or add the key to known_failures",
        "XPASS": "fixed: delete the key from known_failures",
        "STALE": "delete the key from known_failures",
    }
    return [
        f"{r.status} {r.key} [{r.kind}] {r.detail} ({hint[r.status]})"
        for r in table
        if r.status in hint
    ]


def render(table: list[Row]) -> str:
    lines = [f"{'tool':<32}{'parameter':<18}{'form':<10}{'status':<7}{'class':<14}detail"]
    lines += [
        f"{r.tool:<32}{r.param:<18}{r.form:<10}{r.status:<7}{r.kind:<14}{r.detail}" for r in table
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Foreign-identifier conformance (Envelope §2b)")
    parser.add_argument("base_url")
    parser.add_argument("--name", required=True, help="expected serverInfo.name")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        table = run_probe(args.base_url, expected_name=args.name, timeout=args.timeout)
    except WrongServerError as exc:
        print(f"WRONG SERVER: {exc}", file=sys.stderr)
        return 2
    except (httpx.HTTPError, ProtocolError) as exc:
        print(f"TRANSPORT ERROR: {exc}", file=sys.stderr)
        return 2
    print(render(table))
    bad = failures(table)
    print(f"\n{'NON-CONFORMANT' if bad else 'CONFORMANT'}: {args.name} ({len(bad)} to act on)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
