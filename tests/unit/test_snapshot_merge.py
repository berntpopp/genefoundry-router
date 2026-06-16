from genefoundry_router.devtools.fakes import BackendSpec, ToolSpec
from scripts.snapshot_fleet import merge_backend


def test_merge_keeps_prior_when_new_is_none():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    assert merge_backend(prior, None) is prior


def test_merge_prefers_new_when_present():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    fresh = BackendSpec(version="2.0.0", tools=[ToolSpec(name="get_y")])
    merged = merge_backend(prior, fresh)
    assert merged.version == "2.0.0"
    assert [t.name for t in merged.tools] == ["get_y"]
