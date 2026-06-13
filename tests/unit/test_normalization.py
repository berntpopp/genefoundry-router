from genefoundry_router.normalization import build_tool_transform, strip_prefix_name
from genefoundry_router.registry import BackendDef, TransformConfig


def test_strip_prefix_name():
    assert strip_prefix_name("pubtator_search_literature", "pubtator_") == "search_literature"
    assert strip_prefix_name("search_genes", "pubtator_") == "search_genes"


def test_build_tool_transform_none_when_no_transform():
    b = BackendDef(name="gnomad", url_env="X", namespace="gnomad")
    assert build_tool_transform(b, present_tools=["get_variant_details"]) is None


def test_build_tool_transform_strips_prefix_for_namespaced_names():
    b = BackendDef(
        name="pubtator",
        url_env="X",
        namespace="pubtator",
        transform=TransformConfig(strip_prefix="pubtator_"),
    )
    # gateway names after namespacing are pubtator_pubtator_<tool>
    transform = build_tool_transform(b, present_tools=["pubtator_pubtator_search_literature"])
    assert transform is not None
    # the mapping renames the double-prefixed name back to single-prefixed.
    # ToolTransform stores its mapping on the private ``_transforms`` attribute in
    # fastmcp 3.4.2 (no public accessor); read it directly for this white-box check.
    mapping = transform._transforms
    assert "pubtator_pubtator_search_literature" in mapping
    cfg = mapping["pubtator_pubtator_search_literature"]
    assert cfg.name == "pubtator_search_literature"
