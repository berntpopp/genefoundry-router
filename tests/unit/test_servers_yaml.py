from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 13 backends defined
    assert len(backends) == 13
    # hgnc deployed 2026-06-16 (hgnc-link.genefoundry.org); now enabled
    assert by_name["hgnc"].enabled is True
    # whole fleet is live: every backend enabled
    assert all(b.enabled for b in backends)
    # pubtator adopted Tool-Naming Standard v1 (pubtator-link#57, PR #64): it now
    # emits clean leaf names, so the stopgap strip_prefix transform is gone.
    assert by_name["pubtator"].transform is None
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))
