from typer.testing import CliRunner

from genefoundry_router.cli import app

runner = CliRunner()


def test_list_tools_filters_namespace_and_flags_long(monkeypatch, tmp_path):
    yaml = tmp_path / "servers.yaml"
    yaml.write_text("servers:\n  - { name: gnomad, url_env: GF_GNOMAD_URL, namespace: gnomad }\n")

    async def fake_list(settings, registry):
        return [
            "gnomad_get_variant_details",
            "gnomad_" + "x" * 60,  # 67 chars -> over limit
            "gtex_get_gene_information",
        ]

    monkeypatch.setattr("genefoundry_router.cli._list_federated_tools", fake_list)
    result = runner.invoke(
        app, ["list-tools", "--servers-file", str(yaml), "--namespace", "gnomad"]
    )
    assert result.exit_code == 0, result.output
    assert "gnomad_get_variant_details" in result.output
    assert "gtex_get_gene_information" not in result.output  # filtered out
    assert "OVER 64" in result.output  # long name flagged
