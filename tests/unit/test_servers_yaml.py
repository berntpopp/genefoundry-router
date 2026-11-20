from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 26 live backends + 4 staged cancer/somatic backends (enabled: false until deployed)
    assert len(backends) == 30
    staged = {"civic", "cbioportal", "opentargets", "moalmanac"}
    # hgnc deployed 2026-06-16 (hgnc-link.genefoundry.org); now enabled
    assert by_name["hgnc"].enabled is True
    # the 4 newest backends are now deployed (<name>-link.genefoundry.org) and enabled.
    newest = {"clinvar", "vep", "panelapp", "mondo"}
    assert all(by_name[n].enabled is True for n in newest)
    latest = {"hpo", "mavedb", "metadome", "orphanet"}
    assert all(by_name[n].enabled is True for n in latest)
    # every backend except the staged ones is live and enabled
    assert {b.name for b in backends if not b.enabled} == staged
    assert all(b.transform is None for b in backends), (
        "baseline qualification assumes source-standard names; add explicit normalization support"
    )
    # the new backends are Tool-Naming Standard v1 clean — no router-side transforms.
    assert all(by_name[n].transform is None for n in newest)
    assert all(by_name[n].transform is None for n in latest)
    # every enabled backend declares >=1 canonical entry point — pinned + named in the
    # server instructions so each domain's front-door tool is deterministically
    # discoverable regardless of BM25 ranking (discoverability-benchmark follow-up to #3).
    # clinpgx exempt — owner decision 2026-11-19: clinpgx un-pinned (pinned budget); restore when clinpgx-link#56 lands
    # strchive/ucsc/mitomap/pgscatalog search-only — owner decision 2026-11-19 (pinned budget)
    unpinned = {n for n in by_name if not by_name[n].entrypoints}
    assert unpinned == {"clinpgx", "strchive", "ucsc", "mitomap", "pgscatalog"}, (
        "each other backend needs an entrypoint"
    )
    assert "resolve_variant_id" in by_name["gnomad"].entrypoints
    # #320: disease/phenotype front doors are the ranked text searches, not exact resolvers.
    assert by_name["mondo"].entrypoints == ["search_diseases", "resolve_disease"]
    assert by_name["gencc"].entrypoints == ["get_gene_curations"]
    assert by_name["civic"].entrypoints == ["search_molecular_profiles"]
    assert by_name["cbioportal"].entrypoints == ["get_gene_alteration_frequency"]
    assert by_name["opentargets"].entrypoints == ["resolve_target"]
    assert by_name["moalmanac"].entrypoints == ["search_approvals"]
    assert by_name["hpo"].entrypoints == [
        "search_terms",
        "get_phenotypes_for_gene",
        "get_genes_for_phenotype",
        "score_genes_for_phenotypes",  # #341
    ]
    assert by_name["hgnc"].entrypoints == ["resolve_symbol", "resolve_symbols_batch"]  # #341
    assert by_name["mavedb"].entrypoints == ["search_score_sets"]
    assert by_name["metadome"].entrypoints == ["resolve_transcript", "get_tolerance_landscape"]
    assert by_name["orphanet"].entrypoints == ["search_diseases"]
    # pubtator adopted Tool-Naming Standard v1 (pubtator-link#57, PR #64): it now
    # emits clean leaf names, so the stopgap strip_prefix transform is gone.
    assert by_name["pubtator"].transform is None
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing_env_examples = sorted(b.url_env for b in backends if f"{b.url_env}=" not in env_example)
    assert missing_env_examples == []
