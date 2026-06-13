from genefoundry_router.cli import check_leaf_name

CANONICAL_VERBS = {"get", "search", "list", "resolve", "find", "compare", "compute"}


def test_compliant_leaf_passes():
    assert check_leaf_name("get_variant_details") == []


def test_violations_detected():
    issues = check_leaf_name("pubtator_searchLiterature")  # prefixed + camelCase + non-verb
    assert any("prefix" in i or "verb" in i or "charset" in i for i in issues)


def test_overlong_leaf_flagged():
    issues = check_leaf_name("get_" + "x" * 60)  # >50 chars
    assert any("50" in i for i in issues)
