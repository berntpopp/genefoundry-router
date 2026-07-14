from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_orients_the_reader():
    """The README's own job (README Standard v1): what it is, how to run it, how to search.

    Deep operator reference — the full GF_* table, auth modes — lives in docs/ and is
    guarded by test_runtime_security_docs.py instead.
    """
    txt = (ROOT / "README.md").read_text()
    for token in [
        "genefoundry-router run",
        "/health",
        "/mcp",
        "servers.yaml",
        "search_tools",
        "call_tool",
    ]:
        assert token in txt, f"README missing {token!r}"


def test_configuration_reference_documents_every_auth_variable():
    txt = (ROOT / "docs/configuration.md").read_text()
    for token in ["GF_AUTH_MODE", "GF_ALLOWED_ORIGINS", "GF_ALLOWED_HOSTS", "GF_RATE_LIMIT_RPM"]:
        assert token in txt, f"docs/configuration.md missing {token!r}"


def test_claude_md_references_agents_md():
    assert "@AGENTS.md" in (ROOT / "CLAUDE.md").read_text()


def test_response_envelope_v11_and_inventory_are_present():
    assert (ROOT / "docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md").is_file()
    assert (ROOT / "docs/conformance/untrusted-text-inventory.yml").is_file()
