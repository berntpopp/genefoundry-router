#!/usr/bin/env python
"""Enforce the GeneFoundry Repository Metadata Standard v1.

An OFFLINE linter over ``fleet-metadata.yaml`` — the declared GitHub "About" box for
all 22 fleet repos. It makes no network call, so it belongs in ``make ci-local``.
Drift between this file and *live* GitHub is a separate, online concern
(``scripts/sync_fleet_metadata.py --check``).

Why this gate exists: GitHub search indexes only a repo's name, description and
topics — never the README by default. The About box is the entire acquisition
surface, and the fleet shipped seven empty descriptions and sixteen repos with no
topics at all. A written convention with no gate decays.

    python scripts/check_fleet_metadata.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
METADATA = ROOT / "fleet-metadata.yaml"
SERVERS = ROOT / "servers.yaml"

# GitHub's REST validator, verified 2026-07-14 (documented nowhere).
DESCRIPTION_CEILING = 350
# Our own target: Google truncates the SERP snippet at ~155 chars and the title at ~60.
DESCRIPTION_TARGET = 220
# Front-load window: the searchable tokens must land inside this many characters.
FRONTLOAD_WINDOW = 100

TOPIC_CEILING = 20  # GitHub guidance: "add no more than 20"
TOPIC_MAX_LEN = 50  # hard, GitHub REST validator
# Must start with a lowercase letter or digit; lowercase letters, digits, hyphens only.
# Underscores are REJECTED by the API, not normalised.
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Vanity adjectives and self-awarded status. README Standard v1 bans these in prose;
# they are worse in a description, where every character is search surface.
FORBIDDEN_WORDS = re.compile(
    r"\b(production|production-ready|unified|deterministic|thin|powerful|comprehensive"
    r"|robust|blazing|state-of-the-art|cutting-edge|seamless|modern)\b",
    re.I,
)
# Hand-typed aggregates drift. gencc-link's live description says "10 MCP tools" — the
# count is already wrong. Intervening words are allowed between the digit and the noun,
# or that exact real-world defect slips through.
#
# Checked against the UNSUBSTITUTED text, so the router's `{n}` placeholder is legal:
# the rule bans a hand-typed digit, and a placeholder resolved from servers.yaml is
# derived, not typed (README Standard v1, Rule 9).
#
# The leading \b matters: without it this fires on the "25 tool" in "BM25 tool search".
FORBIDDEN_FACTS = re.compile(r"\b\d+\s+(?:\w+\s+){0,2}(tools?|tests?|backends?|servers?)\b", re.I)
# The research-use disclaimer is the README's [!IMPORTANT] callout. It is not a search
# keyword, and in a 350-char budget it is pure waste.
FORBIDDEN_DISCLAIMER = re.compile(r"research use only|clinical decision support", re.I)
# The fleet suffix costs ~36 chars to say something the `genefoundry` topic says free.
FORBIDDEN_SUFFIX = re.compile(r"part of the .{0,20}(-link|fleet)", re.I)

REQUIRED_TOKEN = re.compile(r"MCP (server|gateway)")


def load() -> dict[str, Any]:
    return yaml.safe_load(METADATA.read_text(encoding="utf-8"))


def enabled_namespaces() -> list[str]:
    """Namespaces of the enabled backends in servers.yaml — the coverage oracle."""
    reg = yaml.safe_load(SERVERS.read_text(encoding="utf-8"))
    default_enabled = reg.get("defaults", {}).get("enabled", True)
    out = []
    for s in reg["servers"]:
        if s.get("enabled", default_enabled):
            out.append(s["namespace"])
    return out


def check_description(
    label: str, desc: str, errors: list[str], fact_text: str | None = None
) -> None:
    """Validate one description.

    ``fact_text`` is the text used for the hand-typed-aggregate check. The router passes
    its *unsubstituted* template here so that ``{n}`` — derived from servers.yaml — is not
    mistaken for a typed count, while its length and tokens are still checked against the
    resolved string a reader actually sees.
    """
    text = " ".join(desc.split())
    facts = " ".join((fact_text if fact_text is not None else desc).split())
    if not text:
        errors.append(f"{label}: description is empty — it is the Google title and snippet")
        return
    if len(text) > DESCRIPTION_CEILING:
        errors.append(
            f"{label}: description is {len(text)} chars; GitHub's API hard-rejects "
            f"over {DESCRIPTION_CEILING}"
        )
    if len(text) > DESCRIPTION_TARGET:
        errors.append(
            f"{label}: description is {len(text)} chars; the standard targets "
            f"<= {DESCRIPTION_TARGET} so Google does not truncate it"
        )
    if not REQUIRED_TOKEN.search(text):
        errors.append(
            f"{label}: description must contain the literal token 'MCP server' "
            f"(or 'MCP gateway') — it is the phrase users search"
        )
    else:
        head = text[:FRONTLOAD_WINDOW]
        if not REQUIRED_TOKEN.search(head):
            errors.append(
                f"{label}: 'MCP server/gateway' must appear in the first "
                f"{FRONTLOAD_WINDOW} chars; Google truncates the title at ~60"
            )
    for pattern, target, why in (
        (FORBIDDEN_WORDS, text, "vanity adjective — states nothing, and is not searched"),
        (
            FORBIDDEN_FACTS,
            facts,
            "hand-typed aggregate — it will drift (README Standard, Rule 9)",
        ),
        (
            FORBIDDEN_DISCLAIMER,
            text,
            "the research-use disclaimer belongs in the README callout",
        ),
        (FORBIDDEN_SUFFIX, text, "the `genefoundry` topic carries fleet membership for free"),
    ):
        m = pattern.search(target)
        if m:
            errors.append(f"{label}: description contains {m.group(0)!r} — {why}")


def check_topics(label: str, topics: list[str], allowed: set[str], errors: list[str]) -> None:
    if len(topics) > TOPIC_CEILING:
        errors.append(f"{label}: {len(topics)} topics; GitHub's ceiling is {TOPIC_CEILING}")
    seen = set()
    for t in topics:
        if t in seen:
            errors.append(f"{label}: topic {t!r} is duplicated")
        seen.add(t)
        if len(t) > TOPIC_MAX_LEN:
            errors.append(f"{label}: topic {t!r} exceeds GitHub's {TOPIC_MAX_LEN}-char limit")
        if not TOPIC_RE.match(t):
            errors.append(
                f"{label}: topic {t!r} is invalid — GitHub requires it to start with a "
                f"lowercase letter or digit and contain only lowercase letters, digits "
                f"and hyphens (underscores are rejected, not normalised)"
            )
        if t not in allowed:
            errors.append(
                f"{label}: topic {t!r} is not in the closed vocabulary — add it to "
                f"`vocabulary:` in fleet-metadata.yaml if it genuinely belongs"
            )


def main() -> int:
    errors: list[str] = []
    data = load()

    universal = data["universal"]
    uni_topics: list[str] = universal["topics"]
    homepage: str = universal["homepage"]

    if not homepage.startswith("https://"):
        errors.append(f"universal: homepage {homepage!r} must be an https URL")

    vocab = data["vocabulary"]
    allowed = set(uni_topics) | set(vocab["domain"]) | set(vocab["source"])

    # The universal tier is the fleet's reach. Losing any of these is a silent regression.
    for required in ("mcp", "mcp-server", "model-context-protocol", "genefoundry"):
        if required not in uni_topics:
            errors.append(f"universal: topic {required!r} is required on every fleet repo")

    check_topics("universal", uni_topics, allowed, errors)

    # --- router ---
    router = data["router"]
    n = len(enabled_namespaces())
    router_template = " ".join(router["description"].split())
    router_desc = router_template.format(n=n)
    # Length/tokens are checked against what a reader sees; the aggregate-fact rule is
    # checked against the template, where the count is still `{n}` — derived, not typed.
    check_description("router", router_desc, errors, fact_text=router_template)
    check_topics("router", uni_topics + router["topics"], allowed, errors)
    if "{n}" not in router_template:
        errors.append(
            "router: description must use the {n} placeholder for the backend count, "
            "so the fleet's headline number is derived from servers.yaml, never typed"
        )

    # --- backends: coverage parity with servers.yaml ---
    declared = [b["namespace"] for b in data["backends"]]
    expected = enabled_namespaces()
    for missing in sorted(set(expected) - set(declared)):
        errors.append(
            f"backend {missing!r} is enabled in servers.yaml but absent from "
            f"fleet-metadata.yaml — it would ship with no description and no topics"
        )
    for orphan in sorted(set(declared) - set(expected)):
        errors.append(
            f"backend {orphan!r} is in fleet-metadata.yaml but not enabled in servers.yaml"
        )
    dupes = {x for x in declared if declared.count(x) > 1}
    for d in sorted(dupes):
        errors.append(f"backend {d!r} is declared twice in fleet-metadata.yaml")

    for b in data["backends"]:
        label = f"backend {b['namespace']}"
        check_description(label, " ".join(b["description"].split()), errors)
        check_topics(label, uni_topics + b["topics"], allowed, errors)

    if errors:
        print(f"fleet-metadata.yaml: {len(errors)} error(s)\n", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nSee docs/REPO-METADATA-STANDARD-v1.md.",
            file=sys.stderr,
        )
        return 1

    print(
        f"fleet-metadata.yaml: OK — {len(declared)} backends + router, "
        f"{len(uni_topics)} universal topics, homepage {homepage}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
