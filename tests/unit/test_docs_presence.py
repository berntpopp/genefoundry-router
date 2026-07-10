from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_documents_core_commands():
    txt = (ROOT / "README.md").read_text()
    for token in [
        "genefoundry-router run",
        "/health",
        "/mcp",
        "servers.yaml",
        "GF_AUTH_MODE",
        "GF_ALLOWED_ORIGINS",
        "search_tools",
    ]:
        assert token in txt, f"README missing {token!r}"


def test_claude_md_references_agents_md():
    assert "@AGENTS.md" in (ROOT / "CLAUDE.md").read_text()


def test_response_envelope_v11_and_inventory_are_present():
    assert (ROOT / "docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md").is_file()
    assert (ROOT / "docs/conformance/untrusted-text-inventory.yml").is_file()
