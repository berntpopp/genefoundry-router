"""Unit tests for the router's server-instructions string (issue #3 fix).

The instructions field is the MCP-native channel that orients a host's model on
the router's two-layer discovery model: only ``search_tools``/``call_tool`` and a
couple of pinned tools are listed, the rest of the fleet is reached through search.
"""

from genefoundry_router.instructions import build_instructions
from genefoundry_router.registry import BackendDef


def _registry() -> list[BackendDef]:
    return [
        BackendDef(name="gnomad", url_env="X", namespace="gnomad"),
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
