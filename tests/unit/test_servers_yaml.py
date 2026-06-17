from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 13 live backends + 4 staged (clinvar, vep, panelapp, mondo) pending VPS deploy
    assert len(backends) == 17
    # hgnc deployed 2026-06-16 (hgnc-link.genefoundry.org); now enabled
    assert by_name["hgnc"].enabled is True
    # the 4 newest backends are registered but disabled until their containers +
    # DNS (<name>-link.genefoundry.org) go live; flip enabled on deploy.
    staged = {"clinvar", "vep", "panelapp", "mondo"}
    assert all(by_name[n].enabled is False for n in staged)
    # every other backend is live and enabled
    assert all(b.enabled for b in backends if b.name not in staged)
    # the new backends are Tool-Naming Standard v1 clean — no router-side transforms.
    assert all(by_name[n].transform is None for n in staged)
    # pubtator adopted Tool-Naming Standard v1 (pubtator-link#57, PR #64): it now
    # emits clean leaf names, so the stopgap strip_prefix transform is gone.
    assert by_name["pubtator"].transform is None
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))
