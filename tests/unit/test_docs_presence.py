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
