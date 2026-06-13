from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def test_validate_flags_missing_url_for_enabled(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text(
        "servers:\n"
        "  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n"
        "  - { name: hgnc, url_env: GF_HGNC_URL, namespace: hgnc, enabled: false }\n"
    )
    monkeypatch.delenv("GF_GNOMAD_URL", raising=False)
    result = runner.invoke(app, ["validate", "--servers-file", str(yaml)])
    assert result.exit_code == 1  # enabled gnomad has no URL
    assert "gnomad" in result.output
    assert "missing URL" in result.output


def test_validate_passes_when_all_enabled_have_urls(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")
    monkeypatch.setenv("GF_GNOMAD_URL", "https://gnomad-link.example.org/mcp")
    result = runner.invoke(app, ["validate", "--servers-file", str(yaml)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
