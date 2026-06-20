from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 21 backends: the deployed fleet plus the four newest staged/ready backends
    # (hpo, mavedb, metadome, orphanet).
    assert len(backends) == 21
    # hgnc deployed 2026-06-16 (hgnc-link.genefoundry.org); now enabled
    assert by_name["hgnc"].enabled is True
    # the 4 newest backends are now deployed (<name>-link.genefoundry.org) and enabled.
    newest = {"clinvar", "vep", "panelapp", "mondo"}
    assert all(by_name[n].enabled is True for n in newest)
    latest = {"hpo", "mavedb", "metadome", "orphanet"}
    assert all(by_name[n].enabled is True for n in latest)
    # every backend is live and enabled
    assert all(b.enabled for b in backends)
    # the new backends are Tool-Naming Standard v1 clean — no router-side transforms.
    assert all(by_name[n].transform is None for n in newest)
    assert all(by_name[n].transform is None for n in latest)
    # every enabled backend declares >=1 canonical entry point — pinned + named in the
    # server instructions so each domain's front-door tool is deterministically
    # discoverable regardless of BM25 ranking (discoverability-benchmark follow-up to #3).
    assert all(by_name[n].entrypoints for n in by_name), "each backend needs an entrypoint"
    assert "resolve_variant_id" in by_name["gnomad"].entrypoints
    assert by_name["mondo"].entrypoints == ["resolve_disease"]
    assert by_name["gencc"].entrypoints == ["resolve_identifier"]
    assert by_name["hpo"].entrypoints == [
        "resolve_term",
        "get_phenotypes_for_gene",
        "get_genes_for_phenotype",
    ]
    assert by_name["mavedb"].entrypoints == ["search_score_sets"]
    assert by_name["metadome"].entrypoints == ["resolve_transcript", "get_tolerance_landscape"]
    assert by_name["orphanet"].entrypoints == ["resolve_disease"]
    # pubtator adopted Tool-Naming Standard v1 (pubtator-link#57, PR #64): it now
    # emits clean leaf names, so the stopgap strip_prefix transform is gone.
    assert by_name["pubtator"].transform is None
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing_env_examples = sorted(b.url_env for b in backends if f"{b.url_env}=" not in env_example)
    assert missing_env_examples == []
