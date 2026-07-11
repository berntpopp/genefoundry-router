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


def test_inventory_backend_set_matches_servers_registry() -> None:
    """Every backend federated in servers.yaml MUST have an untrusted-text inventory row.

    This is the completeness gate of Response-Envelope Standard v1.1 (§9.2): a newly
    registered backend cannot ship a free-text tool without an explicit untrusted-content
    classification. Adding a server to servers.yaml without an inventory row fails here.
    """
    from genefoundry_router.config import load_registry

    registry = load_registry("servers.yaml", {})
    registered = {backend.namespace for backend in registry}
    inventoried = {row["backend"] for row in yaml.safe_load(INVENTORY.read_text())["backends"]}
    missing_from_inventory = registered - inventoried
    stale_inventory_rows = inventoried - registered
    assert not missing_from_inventory, (
        f"servers.yaml backends absent from inventory: {missing_from_inventory}"
    )
    assert not stale_inventory_rows, (
        f"inventory rows for unregistered backends: {stale_inventory_rows}"
    )
