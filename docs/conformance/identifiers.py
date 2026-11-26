"""Foreign-identifier conformance — Response-Envelope Standard v1 §2b, tested against a live server.

Vendored into every -link repo's tests/conformance/ next to behaviour.py (whose MCP client and
envelope readers it reuses), together with identifiers.json:

    python tests/conformance/identifiers.py http://127.0.0.1:8000 --name hpo-link

Exit code: 0 conformant, 1 non-conformant, 2 transport/probe error.

For every tool parameter that takes a GENE, the probe sends each well-formed form of one known
gene (identifiers.json: KRAS as symbol, Ensembl, HGNC and NCBI Gene id) and classifies the answer.

THE POSITIVE CONTROL is the first form (the symbol first) answered with a non-empty success. It
proves the backend holds the gene, and its rows are what every other form is compared against —
most by-gene tools do not repeat the gene in each row, so the rows themselves are the evidence.

  PASS  resolved   the same rows as the control, or rows / `resolved_from` naming the gene
        rejected   `invalid_input` + `field` + `allowed_values` + a `_meta.next_commands` entry
                   that is neither this tool with this value nor a meta tool (i.e. a converter)
        flagged    a success that lists the value under an unsupported/invalid/rejected key
  FAIL  silent_empty   `success: true`, no rows, nothing saying the form is unsupported
        wrong_entity   a non-empty answer that is not the control's and never names the gene
        absence        `not_found` (or another error) although the gene is there under another form
        internal       `internal` while another form of the same gene works
        self_loop      every `next_commands` entry repeats this tool with this value
        unactionable   an `invalid_input` missing `field`, `allowed_values` or a converter
  UNGATED  no form produced a control, so NOTHING about the parameter was verified. This counts
        AGAINST conformance (the `Report.ungate` principle in behaviour.py): a tool that answers
        every form with `not_found` must not be certified. Make a form resolve, or declare the
        parameter `null` with a reason.
  SKIP  only when the upstream is unreachable (`upstream_unavailable`, `rate_limited`).

WHICH PARAMETERS ARE GENES is derived from the tool schema: the parameter name
(`gene`, `gene_symbol`, `genes`, `candidate_genes`, ...) or a description that opens with
"Gene symbol" (the regexes live in identifiers.json, per entity type, so disease/variant/phenotype
are added as data). A backend corrects the derivation in its block:
`"params": {"resolve_identifier.query": "gene", "search_variants.query": null}`, every `null`
with an entry in `"reasons"`. `"forms": {"gene": {...}}` swaps the probe gene for a backend that
cannot hold KRAS (a mitochondrial or repeat-locus source).

KNOWN FAILURES (`known_failures`, keys `tool.param:form`) are expected-fail and strict both ways:
a listed key that passes is an error (XPASS), and so is a key that no longer names a probed
parameter (STALE), so a fix cannot regress and the list cannot rot.

WHO OWNS A BACKEND'S BLOCK. identifiers.json is the router's file, vendored byte-identical; its
`backends.<name>` block is the seed. A backend that changes its block (a fix deletes a key) writes
the block to `identifiers.local.json` beside this file — it replaces the vendored block and is
never overwritten by a re-vendor, so a fix is a one-file edit in the backend's own PR.
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
        META_TOOLS,
        Probe,
        ProtocolError,
        WrongServerError,
        count_of,
        envelope,
        is_error_result,
        is_stringy,
        properties,
        rows,
        takes_array,
    )
except ImportError:  # run as a script
    from behaviour import (  # type: ignore[no-redef]
        META_TOOLS,
        Probe,
        ProtocolError,
        WrongServerError,
        count_of,
        envelope,
        is_error_result,
        is_stringy,
        properties,
        rows,
        takes_array,
    )

CONFIG = Path(__file__).with_name("identifiers.json")
LOCAL = "identifiers.local.json"  # the backend's own block; replaces the vendored seed
PASSING = {"resolved", "rejected", "flagged"}
TRANSIENT = {"upstream_unavailable", "rate_limited"}
# A success may carry the value under a key like these instead of failing the whole call. Keys
# that only claim absence (`not_found`, `unresolved`) do not count: that is the failure itself.
_FLAGGED_KEY = re.compile(r"unsupported|unaccepted|invalid|reject")
# Top-level fields of a single-record answer that restate the request rather than answer it.
_ECHO_KEY = re.compile(r"query|input|identifier|applied|filter|message|hint|note|warning|success$")


class Verdict(NamedTuple):
    kind: str  # one of PASSING, a FAIL class from the module docstring, or "inconclusive"
    detail: str = ""


class Row(NamedTuple):
    tool: str
    param: str
    form: str
    status: str  # PASS | FAIL | XFAIL | XPASS | UNGATED | SKIP | STALE | EXCL
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


def body(env: dict[str, Any], param: str, sent: str) -> list[Any]:
    """The answer itself: the result rows, or a single-record tool's own non-echo fields.

    Mentions are only ever looked for here, never in `_meta`, messages or echo blocks
    (`query`, `applied_filters`): an echo of the request identifies nothing.
    """
    found = rows(env)
    if found is not None:
        return found
    record = {
        k: v
        for k, v in env.items()
        if not k.startswith("_")
        and k not in (param, "resolved_from")
        and not _ECHO_KEY.match(k)
        and v not in (None, False, 0, "", [], {})
        and not (isinstance(v, str) and v.lower() == sent.lower())
    }
    return [record] if record else []


def answered(result: dict[str, Any], param: str, sent: str) -> list[Any] | None:
    """The body of a non-empty success — what makes a form the positive control — else None."""
    env = envelope(result)
    if is_error_result(result, env) or count_of(env) == 0:
        return None
    return body(env, param, sent) or None


def classify(
    tool: str,
    param: str,
    sent: str,
    result: dict[str, Any],
    ids: list[str],
    control: list[Any] | None = None,
    namespace: str = "",
) -> Verdict:
    """Classify one answer to `tool(param=sent)` against the control's rows. Pure: no I/O."""
    env = envelope(result)
    if is_error_result(result, env):
        code = env.get("error_code")
        if code in TRANSIENT:
            return Verdict("inconclusive", f"upstream inconclusive ({code})")
        if code == "internal":
            return Verdict("internal", "internal error for a well-formed identifier")
        if code != "invalid_input":
            return Verdict("absence", f"{code!r} for a well-formed identifier of a gene it holds")
        steps = [
            s for s in (env.get("_meta") or {}).get("next_commands") or [] if isinstance(s, dict)
        ]

        def loops(step: dict[str, Any]) -> bool:
            value = (step.get("arguments") or {}).get(param)
            value = value[0] if isinstance(value, list) and len(value) == 1 else value
            same_tool = step.get("tool") in (tool, f"{namespace}_{tool}")
            return same_tool and isinstance(value, str) and value.lower() == sent.lower()

        def is_meta(step: dict[str, Any]) -> bool:
            return any(m in str(step.get("tool")) for m in META_TOOLS)

        if steps and all(loops(s) for s in steps):
            return Verdict("self_loop", f"every next_commands entry repeats {tool}({param}=...)")
        missing = [
            what
            for what, present in (
                ("field", env.get("field")),
                ("allowed_values", env.get("allowed_values")),
                # §2b: the converter belongs in next_commands; a meta tool is not one.
                (
                    "a converter in _meta.next_commands",
                    [s for s in steps if not loops(s) and not is_meta(s)],
                ),
            )
            if not present
        ]
        if missing:
            return Verdict("unactionable", "invalid_input without " + ", ".join(missing))
        return Verdict("rejected")

    answer = body(env, param, sent)
    others = [i for i in ids if i.lower() != sent.lower()]
    # `resolved_from` and the flagged keys sit at the envelope top level or, where the payload
    # is an object under `result`, inside it. The top level is read first, so its value wins.
    inner = env.get("result")
    frames = [env, inner] if isinstance(inner, dict) else [env]
    resolved_from = [f["resolved_from"] for f in frames if "resolved_from" in f]
    if answer and count_of(env) != 0:
        if answer == control:
            return Verdict("resolved", "same rows as the control")
        if resolved_from and _mentions(resolved_from[0] or "", others):
            return Verdict("resolved", "resolved_from names the gene")
        if _mentions(answer, others):
            return Verdict("resolved", "" if resolved_from else "no resolved_from")
    for frame in frames:
        for key, value in frame.items():
            if _FLAGGED_KEY.search(key) and _mentions(value, [sent]):
                return Verdict("flagged", f"listed under {key}")
    if not answer or count_of(env) == 0:
        return Verdict("silent_empty", "success with no rows and no unsupported-identifier marker")
    return Verdict(
        "wrong_entity", "a non-empty answer that is not the control's and never names the gene"
    )


def entity_params(
    tool: dict[str, Any], entities: dict[str, Any], declared: dict[str, str | None]
) -> dict[str, str | None]:
    """`{param: entity}` for one tool; None marks a parameter the backend declared out."""
    found: dict[str, str | None] = {}
    for param, prop in properties(tool).items():
        key = f"{tool['name']}.{param}"
        if key in declared:
            found[param] = declared[key]
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


def base_args(
    tool: dict[str, Any], param: str, same_entity: dict[str, Any]
) -> dict[str, Any] | None:
    """A valid call around `param`: examples for the other required parameters.

    `param` itself needs no example (the probe fills it), and a required sibling that takes the
    same entity gets the probe gene, not its example — `gene_symbol=SCN1A, hgnc_id=HGNC:6407`
    would be a contradiction, not a probe. None when a remaining parameter has no example.
    """
    args: dict[str, Any] = {}
    props = properties(tool)
    for name in (tool.get("inputSchema") or {}).get("required") or []:
        if name == param:
            continue
        examples = (props.get(name) or {}).get("examples")
        if name in same_entity:
            args[name] = same_entity[name]
        elif examples:
            args[name] = examples[0]
        else:
            return None
    return args


def load_backend(config: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = json.loads(config.read_text())
    local = config.with_name(LOCAL)
    backend = json.loads(local.read_text()) if local.exists() else cfg["backends"].get(name, {})
    return cfg["entities"], backend


def run_probe(
    base_url: str, *, expected_name: str, timeout: float = 60.0, config: Path = CONFIG
) -> list[Row]:
    entities, backend = load_backend(config, expected_name)
    known = set(backend.get("known_failures", []))
    reasons = backend.get("reasons", {})
    namespace = expected_name.removesuffix("-link")
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
        params = entity_params(tool, entities, backend.get("params", {}))
        props = properties(tool)

        def shaped(param: str, value: str, props: dict[str, Any] = props) -> Any:
            return [value] if takes_array(props[param]) else value

        for param, entity in params.items():
            if entity is None:
                why = reasons.get(f"{name}.{param}", "declared null without a reason")
                out.append(Row(name, param, "-", "EXCL", "", why))
                continue
            forms: dict[str, str] = (
                backend.get("forms", {}).get(entity) or entities[entity]["forms"]
            )
            ids = list(forms.values())
            gene = next(iter(forms.values()))
            siblings = {p: shaped(p, gene) for p, e in params.items() if e == entity and p != param}
            base = base_args(tool, param, siblings)
            if base is None:
                detail = "another required parameter has no example, so no call can be built"
                out.append(Row(name, param, "*", "UNGATED", "", detail))
                continue
            results: dict[str, dict[str, Any] | str] = {}
            for form, value in forms.items():
                try:
                    results[form] = probe.call(name, {**base, param: shaped(param, value)})
                except ProtocolError as exc:
                    results[form] = f"JSON-RPC error: {str(exc)[:80]}"
            control_form, control = next(
                (
                    (form, rows_)
                    for form, res in results.items()
                    if isinstance(res, dict) and (rows_ := answered(res, param, forms[form]))
                ),
                (None, None),
            )
            for form, res in results.items():
                key = f"{name}.{param}:{form}"
                if isinstance(res, str):
                    verdict = Verdict("unactionable", res)
                elif form == control_form:
                    verdict = Verdict("resolved", "positive control")
                else:
                    verdict = classify(name, param, forms[form], res, ids, control, namespace)
                if verdict.kind == "inconclusive":
                    status = "SKIP"
                elif control_form is None:
                    status = "UNGATED"
                elif verdict.kind in PASSING:
                    status = "XPASS" if key in known else "PASS"
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
    where = f"this repo's {LOCAL} (create it from your block in identifiers.json if absent)"
    hint = {
        "FAIL": f"fix it, or add the key to known_failures in {where}",
        "XPASS": f"fixed: delete the key from known_failures in {where}",
        "STALE": f"delete the key from known_failures in {where}",
        "UNGATED": "no form of the gene gave a non-empty success, so nothing was verified: make "
        f'one resolve, or declare "tool.param": null under params with a reason in {where}',
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
