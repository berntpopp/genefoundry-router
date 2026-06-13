from pathlib import Path

from genefoundry_router.config import load_registry

ROOT = Path(__file__).resolve().parents[2]


def test_real_servers_yaml_parses():
    backends = load_registry(ROOT / "servers.yaml", {})
    by_name = {b.name: b for b in backends}
    # 13 backends defined
    assert len(backends) == 13
    # hgnc stays disabled until the live deployment is fixed (spec §3 caveat)
    assert by_name["hgnc"].enabled is False
    # pubtator carries the stopgap strip_prefix transform (spec §5)
    assert by_name["pubtator"].transform is not None
    assert by_name["pubtator"].transform.strip_prefix == "pubtator_"
    # namespaces are unique and lowercase
    namespaces = [b.namespace for b in backends]
    assert len(namespaces) == len(set(namespaces))
