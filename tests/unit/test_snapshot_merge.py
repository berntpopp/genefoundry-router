import pytest

from genefoundry_router.devtools.fakes import BackendSpec, ToolSpec
from scripts.snapshot_fleet import ReleaseCandidateCaptureError, merge_backend


def test_required_release_candidate_backend_cannot_retain_prior_snapshot():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    with pytest.raises(ReleaseCandidateCaptureError, match="required release-candidate backend"):
        merge_backend(prior, None, release_candidate=True)


def test_merge_prefers_new_when_present():
    prior = BackendSpec(version="1.0.0", tools=[ToolSpec(name="get_x")])
    fresh = BackendSpec(version="2.0.0", tools=[ToolSpec(name="get_y")])
    merged = merge_backend(prior, fresh, release_candidate=True)
    assert merged.version == "2.0.0"
    assert [t.name for t in merged.tools] == ["get_y"]
