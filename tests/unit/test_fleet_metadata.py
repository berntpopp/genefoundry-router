"""The GeneFoundry Repository Metadata Standard v1 gate.

The About box (description + topics + website) is the fleet's entire acquisition
surface: GitHub search indexes a repo's name, description and topics — and by default
never its README. These tests assert the declared metadata obeys the standard, and that
the linter actually rejects the defects the standard exists to prevent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
METADATA = ROOT / "fleet-metadata.yaml"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_fleet_metadata", ROOT / "scripts" / "check_fleet_metadata.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_fleet_metadata"] = mod
    spec.loader.exec_module(mod)
    return mod


checker = _load_checker()


@pytest.fixture(scope="module")
def data() -> dict:
    return yaml.safe_load(METADATA.read_text(encoding="utf-8"))


def test_linter_passes_on_the_committed_file() -> None:
    assert checker.main() == 0


def test_every_enabled_backend_has_metadata(data: dict) -> None:
    """A backend with no entry would ship an empty description — the original sin."""
    declared = {b["namespace"] for b in data["backends"]}
    assert declared == set(checker.enabled_namespaces())


def test_universal_topics_carry_the_reach_tokens(data: dict) -> None:
    topics = data["universal"]["topics"]
    # `mcp-server` alone is 20.7k repos and the fleet previously used it zero times.
    for required in ("mcp", "mcp-server", "model-context-protocol", "genefoundry"):
        assert required in topics


def test_website_is_the_live_fleet_hub(data: dict) -> None:
    assert data["universal"]["homepage"] == "https://genefoundry.org"


def test_every_description_fits_github_and_google(data: dict) -> None:
    entries = [("router", data["router"]["description"])] + [
        (b["namespace"], b["description"]) for b in data["backends"]
    ]
    n = len(checker.enabled_namespaces())
    for name, raw in entries:
        text = " ".join(raw.split()).format(n=n)
        assert text, f"{name}: empty description"
        # GitHub's REST API hard-rejects over 350.
        assert len(text) <= checker.DESCRIPTION_CEILING, f"{name}: {len(text)} chars"
        assert len(text) <= checker.DESCRIPTION_TARGET, f"{name}: {len(text)} chars"
        # The token users actually search for, inside the window Google shows.
        assert checker.REQUIRED_TOKEN.search(text[: checker.FRONTLOAD_WINDOW]), name


def test_every_topic_is_valid_for_githubs_api(data: dict) -> None:
    """GitHub rejects underscores and uppercase outright — it does not normalise them."""
    uni = data["universal"]["topics"]
    groups = [("router", data["router"]["topics"])] + [
        (b["namespace"], b["topics"]) for b in data["backends"]
    ]
    for name, own in groups:
        topics = uni + own
        assert len(topics) <= checker.TOPIC_CEILING, f"{name}: {len(topics)} topics"
        assert len(topics) == len(set(topics)), f"{name}: duplicate topic"
        for t in topics:
            assert checker.TOPIC_RE.match(t), f"{name}: invalid topic {t!r}"
            assert len(t) <= checker.TOPIC_MAX_LEN, f"{name}: topic {t!r} too long"


def test_topics_stay_inside_the_closed_vocabulary(data: dict) -> None:
    """Without this the fleet drifts back into 22 private taxonomies."""
    vocab = data["vocabulary"]
    allowed = set(data["universal"]["topics"]) | set(vocab["domain"]) | set(vocab["source"])
    for b in data["backends"]:
        for t in b["topics"]:
            assert t in allowed, f"{b['namespace']}: {t!r} not in vocabulary"
    for t in data["router"]["topics"]:
        assert t in allowed, f"router: {t!r} not in vocabulary"


def test_each_backend_names_its_upstream_source(data: dict) -> None:
    """The source proper noun is the exact-intent lever: topics/gnomad has 21 repos."""
    sources = set(data["vocabulary"]["source"])
    for b in data["backends"]:
        assert sources & set(b["topics"]), (
            f"{b['namespace']}: carries no source-tier topic, so it is invisible to "
            f"anyone browsing its upstream's topic page"
        )


def test_router_count_is_derived_not_typed(data: dict) -> None:
    """README Standard Rule 9: a derived number must be machine-owned."""
    assert "{n}" in " ".join(data["router"]["description"].split())


# --- negative tests: the gate must actually bite -------------------------------------


@pytest.mark.parametrize(
    ("description", "why"),
    [
        ("", "empty"),
        ("A server for gnomAD variant frequencies.", "missing the 'MCP server' token"),
        ("Production MCP server for gnomAD.", "vanity adjective"),
        ("MCP server for GenCC with 10 MCP tools.", "hand-typed count that will drift"),
        ("MCP server for VEP. Research use only; not clinical decision support.", "disclaimer"),
        ("MCP server for HPO. Part of the GeneFoundry -link fleet.", "redundant fleet suffix"),
        ("MCP server for gnomAD. " + "x" * 400, "over GitHub's 350-char ceiling"),
    ],
)
def test_linter_rejects_bad_descriptions(description: str, why: str) -> None:
    errors: list[str] = []
    checker.check_description("t", description, errors)
    assert errors, f"linter accepted a description it must reject ({why}): {description!r}"


@pytest.mark.parametrize(
    "topic",
    [
        "MCP-Server",  # uppercase — GitHub rejects
        "mcp_server",  # underscore — GitHub rejects
        "-mcp",  # must start with a letter or digit
        "x" * 51,  # over the 50-char limit
        "not-in-vocabulary-at-all",
    ],
)
def test_linter_rejects_bad_topics(topic: str) -> None:
    errors: list[str] = []
    checker.check_topics("t", [topic], {"mcp"}, errors)
    assert errors, f"linter accepted an invalid topic: {topic!r}"


def test_linter_rejects_a_bm25_false_positive_regression() -> None:
    """`\\d+\\s+tool` without a leading \\b fires on the '25 tool' in 'BM25 tool search'."""
    errors: list[str] = []
    checker.check_description("t", "MCP gateway with BM25 tool search.", errors)
    assert not errors, "the aggregate-fact regex must not fire on 'BM25 tool search'"
