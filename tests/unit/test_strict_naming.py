import pytest

from genefoundry_router.cli import check_leaf_name

CANONICAL_VERBS = {"get", "search", "list", "resolve", "find", "compare", "compute", "map"}


def test_compliant_leaf_passes():
    assert check_leaf_name("get_variant_details") == []
    assert check_leaf_name("map_cross_ontology") == []


def test_violations_detected():
    issues = check_leaf_name("pubtator_searchLiterature")  # prefixed + camelCase + non-verb
    assert any("prefix" in i or "verb" in i or "charset" in i for i in issues)


def test_overlong_leaf_flagged():
    issues = check_leaf_name("get_" + "x" * 60)  # >50 chars
    assert any("50" in i for i in issues)


# --- Tool-Naming Standard v1.1 ---


def test_tier2_verbs_pass_check_leaf_name():
    """Ratified Tier-2 verbs must pass check_leaf_name (Tool-Naming Standard v1.1 §Tier-2)."""
    # recode and liftover are domain-legitimate vep verbs; rejected by the pre-v1.1 validator
    assert check_leaf_name("recode_variant") == []
    assert check_leaf_name("liftover_variant") == []
    # score and analyze are also in the ratified Tier-2 set
    assert check_leaf_name("score_variant") == []
    assert check_leaf_name("analyze_sequence") == []


def test_ops_meta_tag_carve_out():
    """Tools tagged ops or meta are exempt from the verb rule (v1.1 tag carve-out)."""
    # check_* / health / warmup / diagnostics verbs are infra, not domain — tag carve-out
    assert check_leaf_name("check_upstream_health", tags=["meta"]) == []
    assert check_leaf_name("warmup", tags=["ops"]) == []
    # charset/length check still applies even with the carve-out tag
    bad_charset = "CheckHealth"  # uppercase → charset violation
    issues = check_leaf_name(bad_charset, tags=["ops"])
    assert any("charset" in i or "50" in i for i in issues)
    # without a tag, a non-canonical verb is still rejected
    assert check_leaf_name("check_upstream_health") != []


# --- Negative guards: verbs that must NEVER silently enter the canon ---


@pytest.mark.parametrize(
    "leaf",
    [
        "build_topic_map",
        "index_review_evidence",
        "stage_research_session",
    ],
)
def test_pubtator_orchestration_verbs_rejected_untagged(leaf: str) -> None:
    """build_*/index_*/stage_* are pubtator orchestration verbs outside Tier-1 and Tier-2 canon.

    They must be REJECTED by check_leaf_name when untagged — a future PR must not silently
    fold them into the canon verb set. An ops/meta tag would grant the carve-out legitimately,
    but without a tag these verbs are naming violations and must remain so.
    """
    issues = check_leaf_name(leaf)
    assert any("verb" in i for i in issues), (
        f"{leaf!r} must be rejected as a naming violation (untagged pubtator orchestration verb),"
        f" but check_leaf_name returned no issues"
    )
