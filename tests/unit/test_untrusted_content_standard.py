from pathlib import Path

import yaml

STANDARD = Path("docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md")
INVENTORY = Path("docs/conformance/untrusted-text-inventory.yml")

EXPECTED_BACKENDS = {
    "autopvs1",
    "clingen",
    "clinvar",
    "gencc",
    "genereviews",
    "gnomad",
    "gtex",
    "hgnc",
    "hpo",
    "litvar",
    "mavedb",
    "metadome",
    "mgi",
    "mondo",
    "orphanet",
    "panelapp",
    "pubtator",
    "spliceai",
    "stringdb",
    "uniprot",
    "vep",
}


def test_untrusted_content_standard_is_normative_and_structural() -> None:
    text = STANDARD.read_text()
    for required in (
        '"kind": "untrusted_text"',
        '"raw_sha256"',
        '"provenance"',
        "Unicode NFC",
        "defense in depth",
        "MUST NOT duplicate",
    ):
        assert required in text


def test_inventory_covers_the_fleet_with_explicit_contract_fields() -> None:
    rows = yaml.safe_load(INVENTORY.read_text())["backends"]
    assert {row["backend"] for row in rows} == EXPECTED_BACKENDS
    assert len(rows) == len(EXPECTED_BACKENDS)

    required = {
        "backend",
        "tool",
        "json_pointers",
        "max_text_bytes",
        "max_objects",
        "compatibility",
        "test_vector",
        "evidence",
    }
    for row in rows:
        assert required <= row.keys(), row["backend"]
        assert row["max_text_bytes"] > 0
        assert row["max_objects"] > 0
        assert row["evidence"]
