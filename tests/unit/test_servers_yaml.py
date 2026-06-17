from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 17 backends, all live: the original fleet + the 4 newest (clinvar, vep,
    # panelapp, mondo) enabled on deploy (commit e6c79b8).
    assert len(backends) == 17
    # hgnc deployed 2026-06-16 (hgnc-link.genefoundry.org); now enabled
    assert by_name["hgnc"].enabled is True
    # the 4 newest backends are now deployed (<name>-link.genefoundry.org) and enabled.
    newest = {"clinvar", "vep", "panelapp", "mondo"}
    assert all(by_name[n].enabled is True for n in newest)
    # every backend is live and enabled
    assert all(b.enabled for b in backends)
    # the new backends are Tool-Naming Standard v1 clean — no router-side transforms.
    assert all(by_name[n].transform is None for n in newest)
    # pubtator adopted Tool-Naming Standard v1 (pubtator-link#57, PR #64): it now
    # emits clean leaf names, so the stopgap strip_prefix transform is gone.
    assert by_name["pubtator"].transform is None
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))
