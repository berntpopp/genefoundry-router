"""Unit tests for the router's server-instructions string (issue #3 fix).

The instructions field is the MCP-native channel that orients a host's model on
the router's two-layer discovery model: only ``search_tools``/``call_tool`` and a
couple of pinned tools are listed, the rest of the fleet is reached through search.
"""

from genefoundry_router.instructions import build_instructions
from genefoundry_router.registry import BackendDef


def _registry() -> list[BackendDef]:
    return [
        BackendDef(
            name="gnomad",
            url_env="X",
            namespace="gnomad",
            entrypoints=["resolve_variant_id", "search_genes"],
        ),
        BackendDef(name="spliceai", url_env="Y", namespace="spliceai"),
        BackendDef(name="hgnc", url_env="Z", namespace="hgnc", enabled=False),
    ]


def test_lists_enabled_namespaces_and_omits_disabled() -> None:
    text = build_instructions(_registry())
    assert "gnomad" in text
    assert "spliceai" in text
    assert "hgnc" not in text  # disabled backend is not advertised


def test_explains_search_then_call_pipeline() -> None:
    text = build_instructions(_registry())
    assert "search_tools" in text
    assert "call_tool" in text


def test_warns_that_unlisted_capability_is_not_missing() -> None:
    # The core failure in issue #3: client-side tool search only sees the entry
    # points, and the model wrongly concluded a capability did not exist.
    text = build_instructions(_registry()).lower()
    assert "does not mean" in text


def test_documents_namespaced_name_format() -> None:
    assert "<namespace>_<tool>" in build_instructions(_registry())


def test_addresses_the_call_tool_eviction_trap() -> None:
    # Issue #3 comment: re-running a host-side tool search evicted call_tool.
    text = build_instructions(_registry()).lower()
    assert "call_tool" in text
    assert "re-run" in text or "reload" in text or "interleave" in text


def test_mirrors_research_use_disclaimer() -> None:
    assert "research use only" in build_instructions(_registry()).lower()


def test_handles_empty_registry() -> None:
    assert isinstance(build_instructions([]), str)


def test_names_canonical_entrypoints_by_namespaced_name() -> None:
    # The BM25-miss fix: canonical resolvers are named directly so the model can
    # call them without relying on relevance-ranked search (deep-research finding).
    text = build_instructions(_registry())
    assert "gnomad_resolve_variant_id" in text
    assert "gnomad_search_genes" in text


def test_entrypoint_section_omits_disabled_backends() -> None:
    reg = [
        BackendDef(
            name="mondo",
            url_env="X",
            namespace="mondo",
            entrypoints=["resolve_disease"],
        ),
        BackendDef(
            name="hgnc",
            url_env="Y",
            namespace="hgnc",
            enabled=False,
            entrypoints=["resolve_symbol"],
        ),
    ]
    text = build_instructions(reg)
    assert "mondo_resolve_disease" in text
    assert "hgnc_resolve_symbol" not in text


def test_instructions_ask_for_one_intent_per_search() -> None:
    """#292: multi-part questions are split into one search per intent."""
    from genefoundry_router.registry import BackendDef

    text = build_instructions([BackendDef(name="hgnc", url_env="X", namespace="hgnc")])
    assert "ONE intent per call" in text


def test_unpinned_tools_are_called_only_through_call_tool() -> None:
    # Issue #299: clients that validate against tools/list reject an unlisted name locally.
    text = build_instructions(_registry())
    assert "directly callable" not in text
    assert "ONLY through `call_tool" in text


def test_search_is_not_described_as_bm25_only() -> None:
    """#303: with the reranker on, "BM25 search" misstates how hits are ranked."""
    text = build_instructions(_registry())
    assert "BM25" not in text
    assert "search over the federated catalog" in text


def test_instructions_follow_the_listed_pins() -> None:
    """#320: GF_PINNED_MODE=none lists no pins, so the text must not promise any."""
    none = build_instructions(_registry(), listed=frozenset())
    assert "COMMON ENTRY POINTS" not in none
    assert "Only `search_tools` and `call_tool` appear" in none
    minimal = build_instructions(_registry(), listed={"gnomad_search_genes"})
    assert "gnomad_search_genes" in minimal and "gnomad_resolve_variant_id" not in minimal


def test_first_paragraph_names_every_served_skill_and_its_call() -> None:
    # #376: hosts truncate instructions, and agents never search for skills.
    from pathlib import Path

    from genefoundry_router.skill_search import skill_tools

    skills = skill_tools(Path(__file__).resolve().parents[2] / "skills")
    first = build_instructions(_registry(), skills=skills).split("\n\n")[0]
    assert 'skill(command="<name>")' in first
    for s in skills:
        assert s.name.removeprefix("skill_") in first
    assert len(first.split("WORKFLOW SKILLS")[1]) < 600  # the added line stays compact


def test_no_skills_no_skills_line() -> None:
    assert "WORKFLOW SKILLS" not in build_instructions(_registry())
    assert "germline" not in build_instructions(_registry())
