from genefoundry_router.registry import (
    MAX_QUALIFIED_NAME_LEN,
    exceeds_name_limit,
    is_client_safe_name,
    qualified_name,
)


def test_qualified_name_uses_single_underscore():
    assert qualified_name("gnomad", "get_variant_details") == "gnomad_get_variant_details"


def test_exceeds_name_limit_false_for_short():
    assert exceeds_name_limit("gnomad", "get_variant_details") is False


def test_exceeds_name_limit_true_for_long():
    long_tool = "x" * MAX_QUALIFIED_NAME_LEN
    assert exceeds_name_limit("gnomad", long_tool) is True


def test_limit_boundary_is_inclusive():
    # namespace(6) + "_"(1) = 7 prefix chars; tool of 57 -> exactly 64 -> OK
    tool = "t" * (MAX_QUALIFIED_NAME_LEN - len("gnomad_"))
    name = qualified_name("gnomad", tool)
    assert len(name) == MAX_QUALIFIED_NAME_LEN
    assert exceeds_name_limit("gnomad", tool) is False


def test_client_safe_name_rejects_dots_and_dashes():
    # R1.10: Gemini wants snake_case, [a-zA-Z0-9_], <=64, leading letter/underscore.
    assert is_client_safe_name("gnomad_get_variant_details") is True
    assert is_client_safe_name("gnomad-get-variant") is False  # dashes
    assert is_client_safe_name("gnomad.get") is False  # dots
    assert is_client_safe_name("1bad") is False  # leading digit
    assert is_client_safe_name("x" * 65) is False  # too long
